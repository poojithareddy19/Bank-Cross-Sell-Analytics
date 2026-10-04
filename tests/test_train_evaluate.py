from __future__ import annotations

import json

import joblib
import numpy as np
import pytest
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from conftest import build_model_warehouse
from xsell.evaluate import (
    RankedScores,
    bootstrap_metrics,
    calibration_table,
    capacity_table,
    lift_table,
    weighted_metrics,
)
from xsell.train import ARTIFACT_NAME, comparable, run_training


@pytest.fixture
def scores():
    rng = np.random.default_rng(0)
    target = (rng.random(2000) < 0.08).astype(int)
    # Rounded scores create many ties, which the fast path must handle like sklearn.
    probabilities = np.round(np.clip(0.05 + 0.3 * target + rng.normal(0, 0.15, 2000), 0, 1), 2)
    return target, probabilities


def test_fast_metrics_match_sklearn_with_ties(scores):
    target, probabilities = scores
    metrics = weighted_metrics(RankedScores.build(target, probabilities))
    assert metrics["roc_auc"] == pytest.approx(roc_auc_score(target, probabilities))
    assert metrics["pr_auc"] == pytest.approx(average_precision_score(target, probabilities))
    assert metrics["brier"] == pytest.approx(brier_score_loss(target, probabilities))


def test_fast_metrics_match_sklearn_with_weights(scores):
    target, probabilities = scores
    weights = np.random.default_rng(1).integers(0, 4, len(target)).astype(float)
    metrics = weighted_metrics(RankedScores.build(target, probabilities), weights)
    assert metrics["roc_auc"] == pytest.approx(roc_auc_score(target, probabilities, sample_weight=weights))
    assert metrics["pr_auc"] == pytest.approx(average_precision_score(target, probabilities, sample_weight=weights))
    assert metrics["brier"] == pytest.approx(brier_score_loss(target, probabilities, sample_weight=weights))


def test_capture_and_lift_on_a_perfect_ranking():
    target = np.array([1] * 10 + [0] * 90)
    probabilities = np.linspace(1, 0, 100)
    metrics = weighted_metrics(RankedScores.build(target, probabilities))
    assert metrics["capture_top_10pct"] == pytest.approx(1.0)
    assert metrics["capture_top_5pct"] == pytest.approx(0.5)
    assert metrics["top_decile_lift"] == pytest.approx(10.0)
    table = lift_table(target, probabilities)
    assert table.loc[0, "lift"] == pytest.approx(10.0)
    assert table["adopters"].sum() == 10 and table["cumulative_capture"].iloc[-1] == pytest.approx(1.0)


def test_bootstrap_resamples_customers_and_is_reproducible(scores):
    target, probabilities = scores
    customers = np.repeat(np.arange(1000), 2)  # two rows per customer
    first = bootstrap_metrics(target, probabilities, customers, n_boot=100, random_state=42)
    second = bootstrap_metrics(target, probabilities, customers, n_boot=100, random_state=42)
    assert first == second
    assert first["unit"] == "customer"
    for values in first["metrics"].values():
        assert values["lower"] <= values["estimate"] + 1e-12 <= values["upper"] + 2e-12


def test_capacity_and_calibration_tables(scores):
    target, probabilities = scores
    capacity = capacity_table(target, probabilities, [0.1, 0.5], margin_per_adopter=100, contact_cost=1)
    assert capacity["customers_contacted"].tolist() == [200, 1000]
    assert capacity.loc[0, "expected_adopters"] == pytest.approx(np.sort(probabilities)[::-1][:200].sum())
    assert capacity.loc[0, "assumed_value_eur"] == pytest.approx(capacity.loc[0, "expected_adopters"] * 100 - 200)
    calibration = calibration_table(target, probabilities)
    assert calibration["rows"].sum() == len(target) and len(calibration) == 10
    assert calibration["mean_predicted"].is_monotonic_increasing


def test_model_beats_both_baselines(trained_model):
    _, report = trained_model
    validation = report["validation"]
    assert validation["beats_baselines"] is True
    selected = validation["candidates"][validation["selected"]]["pr_auc"]
    assert selected > validation["baselines"]["base_rate"]["pr_auc"]
    assert selected > validation["baselines"]["rule"]["pr_auc"]
    test_pr = report["test"]["metrics"]["metrics"]["pr_auc"]["estimate"]
    assert test_pr > report["test"]["baselines"]["base_rate"]["pr_auc"]


def test_report_contents(trained_model):
    config, report = trained_model
    assert report["target"]["mode"] == "product"
    assert report["target"]["product"] == "ind_tjcr_fin_ult1"
    assert set(report["data"]["splits"]) == {"train", "validation", "test"}
    assert report["validation"]["calibration"]["brier_after"] < report["validation"]["calibration"]["brier_before"]
    assert report["test"]["metrics"]["n_boot"] == 50
    assert [row["share_contacted"] for row in report["test"]["capacity"]] == list(config.model.capacity_shares)
    assert report["test"]["per_month"][0]["label_month"] == "2015-08"
    segments = {row["segment"]: row for row in report["test"]["by_prior_holding"]}
    assert set(segments) == {"never held before t (first-time)", "held earlier (re-adoption)"}
    assert sum(row["rows"] for row in segments.values()) == report["data"]["splits"]["test"]["rows"]
    assert len(report["test"]["lift"]) == 10
    assert report["interpretation"]["odds_ratios"][0]["feature"] == "ind_ecue_fin_ult1", "planted signal"

    reports = config.paths.reports_dir
    assert json.loads((reports / "model_report.json").read_text(encoding="utf-8"))["git_commit"] == report["git_commit"]
    markdown = (reports / "model_report.md").read_text(encoding="utf-8")
    assert "Propensity is not uplift" in markdown and "ASSUMED" in markdown


def test_artifact_scores_new_rows(trained_model):
    config, report = trained_model
    artifact = joblib.load(config.paths.artifacts_dir / ARTIFACT_NAME)
    assert artifact["git_commit"] == report["git_commit"]
    from xsell.features import load_dataset

    features, _ = load_dataset(config, "product").xy("test")
    probabilities = artifact["model"].predict_proba(features[artifact["feature_columns"]])[:, 1]
    assert ((probabilities >= 0) & (probabilities <= 1)).all()


def test_training_is_deterministic(trained_model, tmp_path):
    _, first = trained_model
    second = run_training(build_model_warehouse(tmp_path))
    assert json.dumps(comparable(second), sort_keys=True, default=float) == json.dumps(
        comparable(first), sort_keys=True, default=float
    )


def test_low_base_rate_switches_to_any_product(tmp_path):
    config = build_model_warehouse(tmp_path, min_base_rate=0.5)
    report = run_training(config)
    assert report["target"]["mode"] == "any"
    assert report["target"]["label"] == "adopts_any"
    assert "switched to 'adopts any new product next month'" in report["target"]["reason"]
