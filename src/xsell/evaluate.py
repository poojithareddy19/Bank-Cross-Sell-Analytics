"""Evaluation for the propensity model: ranking metrics, calibration, lift, bootstrap.

Adapted from the churn project's evaluate.py. Differences: PR-AUC is a headline metric
(positives are rare), calibration bins are quantiles (uniform bins put almost every row
in the first bin when the base rate is a few percent), and the bootstrap resamples
customers rather than rows, because one customer contributes rows in several months.
The bootstrap uses weights over a single sorted copy of the scores, so 1,000 resamples
stay fast on hundreds of thousands of rows.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

CAPTURE_SHARES = (0.05, 0.10, 0.20)


def _rank_order(probabilities: np.ndarray) -> np.ndarray:
    # Highest score first; a stable sort keeps ties in their original order.
    return np.argsort(-np.asarray(probabilities, dtype=float), kind="mergesort")


@dataclass(frozen=True)
class RankedScores:
    """Scores sorted once (descending) with the start index of each distinct score."""

    target: np.ndarray
    probabilities: np.ndarray
    order: np.ndarray
    group_starts: np.ndarray

    @classmethod
    def build(cls, target: np.ndarray, probabilities: np.ndarray) -> RankedScores:
        order = _rank_order(probabilities)
        sorted_probabilities = np.asarray(probabilities, dtype=float)[order]
        changes = np.r_[True, sorted_probabilities[1:] != sorted_probabilities[:-1]]
        return cls(
            target=np.asarray(target, dtype=float)[order],
            probabilities=sorted_probabilities,
            order=order,
            group_starts=np.flatnonzero(changes),
        )


def weighted_metrics(
    ranked: RankedScores, weights: np.ndarray | None = None, shares: Sequence[float] = CAPTURE_SHARES
) -> dict[str, float]:
    """ROC-AUC, PR-AUC (average precision), Brier, top-decile lift and capture at shares.

    weights are per row in the ORIGINAL order (None = all ones). Ties in score are
    handled as sklearn does: tied rows share one threshold.
    """
    w = np.ones(len(ranked.order)) if weights is None else np.asarray(weights, dtype=float)[ranked.order]
    positive_weight = w * ranked.target
    negative_weight = w - positive_weight
    total_positive, total_negative, total = positive_weight.sum(), negative_weight.sum(), w.sum()

    group_positive = np.add.reduceat(positive_weight, ranked.group_starts)
    group_negative = np.add.reduceat(negative_weight, ranked.group_starts)
    positives_above = np.cumsum(group_positive) - group_positive
    roc_auc = float(
        (group_negative * (positives_above + 0.5 * group_positive)).sum() / (total_positive * total_negative)
    )

    true_positives = np.cumsum(group_positive)
    predicted_positive = true_positives + np.cumsum(group_negative)
    precision = np.divide(
        true_positives, predicted_positive, out=np.zeros_like(true_positives), where=predicted_positive > 0
    )
    recall = true_positives / total_positive
    pr_auc = float(np.sum(np.diff(np.r_[0.0, recall]) * precision))

    brier = float(np.sum(w * (ranked.probabilities - ranked.target) ** 2) / total)
    cumulative_weight = np.cumsum(w)
    cumulative_positive = np.cumsum(positive_weight)
    metrics = {"roc_auc": roc_auc, "pr_auc": pr_auc, "brier": brier}
    for share in sorted({0.10, *shares}):
        cut = min(int(np.searchsorted(cumulative_weight, share * total, side="right")), len(w)) - 1
        captured = float(cumulative_positive[cut] / total_positive) if cut >= 0 else 0.0
        if share == 0.10:
            metrics["top_decile_lift"] = captured / 0.10
        if share in shares:
            metrics[f"capture_top_{round(share * 100)}pct"] = captured
    return metrics


def point_metrics(target: np.ndarray, probabilities: np.ndarray, shares: Sequence[float] = CAPTURE_SHARES) -> dict:
    target = np.asarray(target).astype(int)
    if target.min() == target.max():
        return {"rows": int(len(target)), "positives": int(target.sum()), "base_rate": float(target.mean())}
    metrics = weighted_metrics(RankedScores.build(target, probabilities), shares=shares)
    return {"rows": int(len(target)), "positives": int(target.sum()), "base_rate": float(target.mean()), **metrics}


def sklearn_metrics(target: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    """Reference values from scikit-learn (used for model selection and to check the fast path)."""
    return {
        "roc_auc": float(roc_auc_score(target, probabilities)),
        "pr_auc": float(average_precision_score(target, probabilities)),
        "brier": float(brier_score_loss(target, probabilities)),
    }


def bootstrap_metrics(
    target: np.ndarray,
    probabilities: np.ndarray,
    customer_ids: np.ndarray,
    n_boot: int,
    random_state: int,
    shares: Sequence[float] = CAPTURE_SHARES,
) -> dict:
    """95% percentile intervals from a customer-level (cluster) bootstrap."""
    target = np.asarray(target).astype(int)
    ranked = RankedScores.build(target, probabilities)
    point = weighted_metrics(ranked, shares=shares)
    codes, uniques = pd.factorize(np.asarray(customer_ids))
    n_customers = len(uniques)
    rng = np.random.default_rng(random_state)
    samples: dict[str, list[float]] = {name: [] for name in point}
    skipped = 0
    for _ in range(n_boot):
        counts = np.bincount(rng.integers(0, n_customers, n_customers), minlength=n_customers)
        weights = counts[codes].astype(float)
        positives = float((weights * target).sum())
        if positives == 0 or positives == weights.sum():
            skipped += 1
            continue
        for name, value in weighted_metrics(ranked, weights, shares).items():
            samples[name].append(value)
    return {
        "n_boot": n_boot,
        "n_skipped": skipped,
        "unit": "customer",
        "confidence_level": 0.95,
        "random_state": random_state,
        "metrics": {
            name: {
                "estimate": point[name],
                "lower": float(np.percentile(values, 2.5)) if values else float("nan"),
                "upper": float(np.percentile(values, 97.5)) if values else float("nan"),
            }
            for name, values in samples.items()
        },
    }


def lift_table(target: np.ndarray, probabilities: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Lift and gains by equal-size bins of score; decile 1 has the highest scores."""
    target = np.asarray(target).astype(int)
    sorted_target = target[_rank_order(probabilities)]
    overall_rate = float(sorted_target.mean())
    total_positives = int(sorted_target.sum())
    rows = []
    cumulative_rows = cumulative_positives = 0
    for decile, chunk in enumerate(np.array_split(sorted_target, n_bins), start=1):
        rows_in_bin, positives = len(chunk), int(chunk.sum())
        cumulative_rows += rows_in_bin
        cumulative_positives += positives
        rate = positives / rows_in_bin if rows_in_bin else 0.0
        rows.append(
            {
                "decile": decile,
                "rows": rows_in_bin,
                "adopters": positives,
                "adoption_rate": rate,
                "lift": rate / overall_rate if overall_rate else 0.0,
                "cumulative_capture": cumulative_positives / total_positives if total_positives else 0.0,
                "cumulative_lift": (cumulative_positives / cumulative_rows) / overall_rate if overall_rate else 0.0,
            }
        )
    return pd.DataFrame(rows)


def calibration_table(target: np.ndarray, probabilities: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Mean predicted probability vs observed adoption rate in equal-size score bins."""
    frame = pd.DataFrame({"target": np.asarray(target).astype(int), "probability": np.asarray(probabilities)})
    ranks = frame["probability"].rank(method="first")
    frame["bin"] = pd.qcut(ranks, q=min(n_bins, len(frame)), labels=False) + 1
    table = frame.groupby("bin").agg(
        rows=("target", "size"),
        mean_predicted=("probability", "mean"),
        observed_rate=("target", "mean"),
    )
    return table.reset_index()


def capacity_table(
    target: np.ndarray,
    probabilities: np.ndarray,
    shares: Sequence[float],
    margin_per_adopter: float,
    contact_cost: float,
) -> pd.DataFrame:
    """Contact the top share by score: adopters reached (expected and observed) and an
    expected value that rests on ASSUMED margin and contact cost."""
    target = np.asarray(target).astype(int)
    probabilities = np.asarray(probabilities, dtype=float)
    order = _rank_order(probabilities)
    sorted_target, sorted_probabilities = target[order], probabilities[order]
    total, total_positives = len(target), int(target.sum())
    rows = []
    for share in shares:
        contacted = min(total, math.ceil(round(share * total, 9)))
        expected = float(sorted_probabilities[:contacted].sum())
        observed = int(sorted_target[:contacted].sum())
        rows.append(
            {
                "share_contacted": float(share),
                "customers_contacted": int(contacted),
                "expected_adopters": expected,
                "observed_adopters": observed,
                "precision": observed / contacted if contacted else 0.0,
                "capture": observed / total_positives if total_positives else 0.0,
                "assumed_value_eur": expected * margin_per_adopter - contacted * contact_cost,
            }
        )
    return pd.DataFrame(rows)


def odds_ratio_table(fitted_pipeline, top_n: int = 15) -> pd.DataFrame:
    """Largest logistic regression coefficients as odds ratios (numeric inputs standardised,
    so their odds ratio is per one standard deviation)."""
    names = fitted_pipeline.named_steps["preprocess"].get_feature_names_out()
    coefficients = fitted_pipeline.named_steps["model"].coef_[0]
    frame = pd.DataFrame({"feature": names, "log_odds": coefficients, "odds_ratio": np.exp(coefficients)})
    frame["feature"] = (
        frame["feature"].str.replace(r"^[a-z]+__", "", regex=True).str.replace("_infrequent_sklearn", "_other")
    )
    order = frame["log_odds"].abs().sort_values(ascending=False).index
    return frame.loc[order].head(top_n).reset_index(drop=True)


def permutation_importance_table(
    model, features: pd.DataFrame, target: pd.Series, n_rows: int, random_state: int, n_repeats: int = 5
) -> pd.DataFrame:
    """Drop in validation PR-AUC when each input column is shuffled."""
    if len(features) > n_rows:
        sample = features.sample(n=n_rows, random_state=random_state)
        features, target = sample, target.loc[sample.index]
    result = permutation_importance(
        model,
        features,
        target,
        scoring="average_precision",
        n_repeats=n_repeats,
        random_state=random_state,
        n_jobs=1,
    )
    frame = pd.DataFrame(
        {
            "feature": list(features.columns),
            "pr_auc_drop_mean": result.importances_mean,
            "pr_auc_drop_std": result.importances_std,
        }
    )
    return frame.sort_values(["pr_auc_drop_mean", "feature"], ascending=[False, True]).reset_index(drop=True)
