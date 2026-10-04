"""Train, select, calibrate and evaluate the next-month propensity model.

Flow (section 8 of the build spec):
  1. Build the feature table in DuckDB (features.py) and load the sampled rows.
  2. Target: adopts the configured product next month; if its training base rate is
     below model.min_base_rate, fall back to "adopts any new product" and record why.
  3. Fit logistic regression and XGBoost (both class-weighted) on the training months.
  4. Select by PR-AUC on validation; compare with the base-rate and rule baselines.
  5. Calibrate the selected model on validation (sigmoid) and pick the operating
     threshold there.
  6. Score the test months once: metrics with customer-level bootstrap intervals, lift,
     calibration, per-month stability and the capacity table.
  7. Save the artifact (gitignored) and reports/model_report.{json,md}.
"""

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from xsell import __version__
from xsell.config import Config
from xsell.errors import XsellError
from xsell.evaluate import (
    bootstrap_metrics,
    calibration_table,
    capacity_table,
    lift_table,
    odds_ratio_table,
    permutation_importance_table,
    point_metrics,
    sklearn_metrics,
)
from xsell.features import (
    BINARY_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    Dataset,
    build_features,
    load_dataset,
)
from xsell.logging_utils import get_logger
from xsell.reporting import markdown_table, write_text

logger = get_logger("xsell.train")

ARTIFACT_NAME = "propensity_model.joblib"
MAX_CATEGORIES = 11  # top 10 categories per column plus one "infrequent" bucket
RULE_PAYROLL_COLUMN = "ind_nomina_ult1"
RULE_MIN_PRODUCTS = 3
STAMP_KEYS = ("generated_at", "git_commit")


def build_preprocessor(flags: tuple[str, ...]) -> ColumnTransformer:
    numeric = Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)), ("scale", StandardScaler())])
    categorical = OneHotEncoder(
        handle_unknown="infrequent_if_exist", max_categories=MAX_CATEGORIES, sparse_output=False
    )
    return ColumnTransformer(
        [
            ("numeric", numeric, list(NUMERIC_FEATURES)),
            ("binary", SimpleImputer(strategy="most_frequent"), [*flags, *BINARY_FEATURES]),
            ("category", categorical, list(CATEGORICAL_FEATURES)),
        ],
        verbose_feature_names_out=True,
    )


def candidate_models(config: Config, flags: tuple[str, ...], positive_weight: float) -> dict[str, Pipeline]:
    seed = config.model.seed
    xgboost_settings = dict(config.model.xgboost)
    return {
        "logistic_regression": Pipeline(
            [
                ("preprocess", build_preprocessor(flags)),
                ("model", LogisticRegression(class_weight="balanced", max_iter=2000, random_state=seed)),
            ]
        ),
        "xgboost": Pipeline(
            [
                ("preprocess", build_preprocessor(flags)),
                (
                    "model",
                    XGBClassifier(
                        **xgboost_settings,
                        scale_pos_weight=positive_weight,
                        tree_method="hist",
                        eval_metric="aucpr",
                        random_state=seed,
                        n_jobs=config.duckdb.threads,
                    ),
                ),
            ]
        ),
    }


def rule_scores(features: pd.DataFrame) -> np.ndarray:
    """Rule baseline: active customers with payroll and at least 3 products (1 or 0)."""
    rule = (
        (features["is_active"] == 1)
        & (features[RULE_PAYROLL_COLUMN] == 1)
        & (features["n_products"] >= RULE_MIN_PRODUCTS)
    )
    return rule.to_numpy(dtype=float)


def choose_target(config: Config) -> tuple[Dataset, str]:
    dataset = load_dataset(config, "product")
    train_rate = float(dataset.split("train")[dataset.label].mean())
    if train_rate >= config.model.min_base_rate:
        reason = (
            f"training base rate of {config.model.target_product} adoption is {train_rate:.4%}, at or above "
            f"model.min_base_rate {config.model.min_base_rate:.2%}"
        )
        return dataset, reason
    reason = (
        f"training base rate of {config.model.target_product} adoption is {train_rate:.4%}, below "
        f"model.min_base_rate {config.model.min_base_rate:.2%}; switched to 'adopts any new product next month'"
    )
    logger.warning(reason)
    return load_dataset(config, "any"), reason


def git_commit(root: Path) -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}+dirty" if dirty else commit


def _operating_threshold(probabilities: np.ndarray, share: float) -> float:
    ordered = np.sort(probabilities)[::-1]
    return float(ordered[min(len(ordered), math.ceil(share * len(ordered))) - 1])


def _threshold_metrics(target: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, float]:
    selected = probabilities >= threshold
    true_positives = int((selected & (target == 1)).sum())
    return {
        "threshold": threshold,
        "share_selected": float(selected.mean()),
        "precision": true_positives / int(selected.sum()) if selected.any() else 0.0,
        "recall": true_positives / int(target.sum()) if target.sum() else 0.0,
    }


def train_and_evaluate(config: Config) -> tuple[dict[str, Any], Any]:
    build_features(config)
    dataset, target_reason = choose_target(config)
    x_train, y_train = dataset.xy("train")
    x_valid, y_valid = dataset.xy("validation")
    x_test, y_test = dataset.xy("test")
    for name, labels in (("train", y_train), ("validation", y_valid), ("test", y_test)):
        if labels.nunique() < 2:
            raise XsellError(f"the {name} split has only one class; the model cannot be trained or evaluated")

    positive_weight = float((y_train == 0).sum() / (y_train == 1).sum())
    candidates = candidate_models(config, dataset.product_flags, positive_weight)
    validation_scores: dict[str, dict[str, float]] = {}
    fitted: dict[str, Pipeline] = {}
    for name, pipeline in candidates.items():
        logger.info("fitting %s on %d training rows", name, len(x_train))
        fitted[name] = pipeline.fit(x_train, y_train)
        validation_scores[name] = sklearn_metrics(y_valid, fitted[name].predict_proba(x_valid)[:, 1])
        logger.info("%s validation PR-AUC %.4f", name, validation_scores[name]["pr_auc"])
    selected = max(validation_scores, key=lambda name: (validation_scores[name]["pr_auc"], name))

    baselines = {}
    for split_name, features, labels in (("validation", x_valid, y_valid), ("test", x_test, y_test)):
        constant = np.full(len(labels), labels.mean())
        baselines[split_name] = {
            "base_rate": {**sklearn_metrics(labels, constant), "base_rate": float(labels.mean())},
            "rule": {
                **sklearn_metrics(labels, rule_scores(features)),
                # A 0/1 rule is not a probability, so a Brier score would be meaningless.
                "brier": None,
                "share_selected": float(rule_scores(features).mean()),
            },
        }
    best_pr = validation_scores[selected]["pr_auc"]
    beats_baselines = best_pr > max(b["pr_auc"] for b in baselines["validation"].values())

    calibrated = CalibratedClassifierCV(FrozenEstimator(fitted[selected]), method="sigmoid").fit(x_valid, y_valid)
    raw_valid = fitted[selected].predict_proba(x_valid)[:, 1]
    calibrated_valid = calibrated.predict_proba(x_valid)[:, 1]
    operating_share = config.model.capacity_shares[min(1, len(config.model.capacity_shares) - 1)]
    threshold = _operating_threshold(calibrated_valid, operating_share)

    test_probabilities = calibrated.predict_proba(x_test)[:, 1]
    test_part = dataset.split("test")
    y_test_array = y_test.to_numpy()
    per_month = []
    for month, group in test_part.groupby("label_month"):
        mask = (test_part["label_month"] == month).to_numpy()
        per_month.append({"label_month": config.months.labels[int(month)], **point_metrics(
            group[dataset.label].to_numpy(), test_probabilities[mask]
        )})  # fmt: skip

    importance = permutation_importance_table(
        fitted[selected], x_valid, y_valid, config.model.permutation_rows, config.model.seed
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_commit(config.paths.root),
        "xsell_version": __version__,
        "target": {
            "mode": dataset.target_mode,
            "label": dataset.label,
            "product": config.model.target_product if dataset.target_mode == "product" else None,
            "reason": target_reason,
            "population": (
                "customers present at t and t+1 who do not hold the target product at t"
                if dataset.target_mode == "product"
                else "customers present at t and t+1"
            ),
        },
        "data": {
            "sample_share": config.model.sample_share,
            "splits": {
                name: {
                    "feature_months": list(config.model.splits[name]),
                    "rows": int(len(dataset.split(name))),
                    "customers": int(dataset.split(name)["customer_id"].nunique()),
                    "positives": int(dataset.split(name)[dataset.label].sum()),
                    "base_rate": float(dataset.split(name)[dataset.label].mean()),
                }
                for name in ("train", "validation", "test")
            },
            "features": dataset.feature_columns,
        },
        "validation": {
            "candidates": validation_scores,
            "selected": selected,
            "selection_metric": "pr_auc",
            "baselines": baselines["validation"],
            "beats_baselines": bool(beats_baselines),
            "calibration": {
                "method": "sigmoid, fitted on validation",
                "brier_before": float(np.mean((raw_valid - y_valid.to_numpy()) ** 2)),
                "brier_after": float(np.mean((calibrated_valid - y_valid.to_numpy()) ** 2)),
            },
            "operating_point": {"share": operating_share, "threshold": threshold},
        },
        "test": {
            "metrics": bootstrap_metrics(
                y_test_array,
                test_probabilities,
                test_part["customer_id"].to_numpy(),
                n_boot=config.model.bootstrap_resamples,
                random_state=config.model.seed,
            ),
            "baselines": baselines["test"],
            "at_operating_threshold": _threshold_metrics(y_test_array, test_probabilities, threshold),
            "per_month": per_month,
            "lift": lift_table(y_test_array, test_probabilities).to_dict(orient="records"),
            "calibration": calibration_table(y_test_array, test_probabilities).to_dict(orient="records"),
            "capacity": capacity_table(
                y_test_array,
                test_probabilities,
                config.model.capacity_shares,
                config.assumptions.margin_per_adopter_eur,
                config.assumptions.contact_cost_eur,
            ).to_dict(orient="records"),
        },
        "interpretation": {
            "odds_ratios": odds_ratio_table(fitted["logistic_regression"]).to_dict(orient="records"),
            "permutation_importance": importance.head(15).to_dict(orient="records"),
            "permutation_rows": int(min(config.model.permutation_rows, len(x_valid))),
        },
        "assumptions": {
            "margin_per_adopter_eur": config.assumptions.margin_per_adopter_eur,
            "contact_cost_eur": config.assumptions.contact_cost_eur,
            "note": "Assumed values from config/config.yaml, not data. The source has no revenue fields.",
        },
    }
    artifact = {
        "model": calibrated,
        "feature_columns": dataset.feature_columns,
        "target": report["target"],
        "operating_threshold": threshold,
        "generated_at": report["generated_at"],
        "git_commit": report["git_commit"],
    }
    return report, artifact


def _ci(metric: dict[str, float], fmt: str = "{:.4f}") -> str:
    return f"{fmt.format(metric['estimate'])} ({fmt.format(metric['lower'])} to {fmt.format(metric['upper'])})"


def render_report(report: dict[str, Any]) -> str:
    target, data, validation, test = report["target"], report["data"], report["validation"], report["test"]
    metrics = test["metrics"]["metrics"]
    lines = [
        "# Propensity model report",
        "",
        f"Generated {report['generated_at']} from commit `{report['git_commit']}` by `python -m xsell train`.",
        "",
        "## Target",
        "",
        f"Label: `{target['label']}`. Population: {target['population']}. Choice: {target['reason']}.",
        "",
        "## Data",
        "",
        f"Deterministic customer sample: {data['sample_share']:.0%} of customers (same sample for every split). "
        "Time-based split, no shuffling; every feature uses months up to the feature month t only.",
        "",
        markdown_table(
            pd.DataFrame(
                [
                    {
                        "split": name,
                        "feature months": " to ".join(values["feature_months"]),
                        "rows": values["rows"],
                        "customers": values["customers"],
                        "adopters": values["positives"],
                        "base rate": f"{values['base_rate']:.2%}",
                    }
                    for name, values in data["splits"].items()
                ]
            )
        ),
        "",
        "## Model selection (validation)",
        "",
    ]
    rows = [{"model": name, **scores} for name, scores in validation["candidates"].items()]
    rows += [{"model": f"baseline: {name}", **{k: v for k, v in scores.items() if k in ("roc_auc", "pr_auc", "brier")}}
             for name, scores in validation["baselines"].items()]  # fmt: skip
    lines.append(markdown_table(pd.DataFrame(rows)[["model", "pr_auc", "roc_auc", "brier"]]))
    verdict = "beats" if validation["beats_baselines"] else "does NOT beat"
    lines += [
        "",
        f"Selected: **{validation['selected']}** (highest validation PR-AUC). It {verdict} both baselines on "
        "validation PR-AUC. Rule baseline: active customers with payroll and 3 or more products.",
        "",
        f"Calibration ({validation['calibration']['method']}): Brier {validation['calibration']['brier_before']:.4f} "
        f"before, {validation['calibration']['brier_after']:.4f} after (measured on the validation rows used to fit "
        f"it). Operating point chosen on validation: top {validation['operating_point']['share']:.0%}, calibrated "
        f"probability threshold {validation['operating_point']['threshold']:.4f}.",
        "",
        "## Test results (scored once)",
        "",
        f"95% intervals from a customer-level bootstrap with {report['test']['metrics']['n_boot']:,} resamples "
        f"({report['test']['metrics']['n_skipped']} skipped for having one class).",
        "",
        markdown_table(
            pd.DataFrame(
                [{"metric": name, "estimate (95% CI)": _ci(values)} for name, values in metrics.items()]
                + [
                    {"metric": f"baseline {name} pr_auc", "estimate (95% CI)": f"{scores['pr_auc']:.4f}"}
                    for name, scores in test["baselines"].items()
                ]
            )
        ),
        "",
        f"At the validation threshold: {test['at_operating_threshold']['share_selected']:.1%} of test rows selected, "
        f"precision {test['at_operating_threshold']['precision']:.3f}, "
        f"recall {test['at_operating_threshold']['recall']:.3f}.",
        "",
        "### Stability by test month",
        "",
        markdown_table(pd.DataFrame(test["per_month"])),
        "",
        "### Lift by decile",
        "",
        markdown_table(pd.DataFrame(test["lift"])),
        "",
        "### Calibration (equal-size score bins)",
        "",
        markdown_table(pd.DataFrame(test["calibration"])),
        "",
        "## Interpretation",
        "",
        "Logistic regression odds ratios (numeric inputs standardised, so per one standard deviation):",
        "",
        markdown_table(pd.DataFrame(report["interpretation"]["odds_ratios"])),
        "",
        f"Permutation importance of the selected model on {report['interpretation']['permutation_rows']:,} "
        "validation rows (drop in PR-AUC when a column is shuffled):",
        "",
        markdown_table(pd.DataFrame(report["interpretation"]["permutation_importance"])),
        "",
        "## Contact capacity (test months)",
        "",
        f"Expected adopters are the sum of calibrated probabilities. assumed_value_eur uses ASSUMED values from "
        f"config: margin {report['assumptions']['margin_per_adopter_eur']:.2f} EUR per adopter, contact cost "
        f"{report['assumptions']['contact_cost_eur']:.2f} EUR. {report['assumptions']['note']}",
        "",
        markdown_table(pd.DataFrame(test["capacity"])),
        "",
        "## Caveats",
        "",
        "- Propensity is not uplift: the model ranks who is likely to adopt, including customers who would adopt "
        "without contact. Measuring the incremental effect of a campaign needs a randomised targeting experiment.",
        "- Associations in the odds ratios and importances are correlational, not causal.",
        "- Trained on a customer sample and 2015 to 2016 Spanish bank data; performance can drift over time.",
    ]
    return "\n".join(lines)


def comparable(report: dict[str, Any]) -> dict[str, Any]:
    """The report without its run stamp, for determinism checks."""
    return {key: value for key, value in report.items() if key not in STAMP_KEYS}


def run_training(config: Config) -> dict[str, Any]:
    report, artifact = train_and_evaluate(config)
    config.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = config.paths.artifacts_dir / ARTIFACT_NAME
    joblib.dump(artifact, artifact_path)
    write_text(config.paths.reports_dir / "model_report.json", json.dumps(report, indent=2, default=float))
    write_text(config.paths.reports_dir / "model_report.md", render_report(report))
    logger.info(
        "selected %s; test PR-AUC %.4f; wrote %s and reports/model_report.md",
        report["validation"]["selected"],
        report["test"]["metrics"]["metrics"]["pr_auc"]["estimate"],
        artifact_path,
    )
    return report
