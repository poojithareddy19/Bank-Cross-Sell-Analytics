"""Run the whole pipeline through the CLI on synthetic data in a temporary project folder.

No network: the fixture zip sits where the Kaggle download would land, so fetch only
converts it. This is what CI runs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import joblib
import pytest
import yaml

from fixture_data import fixture_csv_text, model_fixture_rows, write_fixture_zip
from xsell.cli import main

ROOT = Path(__file__).resolve().parents[1]
LADDER = [
    {"name": "Current account", "products": ["ind_cco_fin_ult1"]},
    {"name": "Direct debit or e-account", "products": ["ind_recibo_ult1", "ind_ecue_fin_ult1"]},
    {"name": "Credit card", "products": ["ind_tjcr_fin_ult1"]},
]


def write_project(project: Path, **model_overrides) -> Path:
    """config/config.yaml and config/products.yaml for the fixture's 8 months; returns the config path."""
    raw = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    products = yaml.safe_load((ROOT / "config" / "products.yaml").read_text(encoding="utf-8"))
    raw["paths"]["sql_dir"] = str(ROOT / "sql")  # data, reports and artifacts stay relative to the project
    raw["duckdb"].update(memory_limit="1GB", threads=2)
    raw["months"] = {"first": "2015-01", "last": "2015-08"}
    raw["analysis"]["timing_runs"] = 1
    raw["model"].update(sample_share=1.0, bootstrap_resamples=30, permutation_rows=500, **model_overrides)
    raw["model"]["splits"] = {
        "train": ["2015-04", "2015-05"],
        "validation": ["2015-06", "2015-06"],
        "test": ["2015-07", "2015-07"],
    }
    products["ladder"] = LADDER
    config_dir = project / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    (config_dir / "products.yaml").write_text(yaml.safe_dump(products, sort_keys=False), encoding="utf-8")
    return config_dir / "config.yaml"


@pytest.fixture
def no_kaggle_credentials(monkeypatch, tmp_path):
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_CONFIG_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty_home")


def test_full_pipeline_runs_end_to_end(tmp_path, no_kaggle_credentials, caplog):
    project = tmp_path / "project"
    config_path = write_project(project)
    zip_path = write_fixture_zip(
        project / "data" / "raw" / "train_ver2.csv.zip", csv_text=fixture_csv_text(model_fixture_rows())
    )

    assert main(["--config", str(config_path), "all"]) == 0

    reports = project / "reports"
    for name in (
        "data_quality.md",
        "customer_analytics.md",
        "ladder_proposal.md",
        "performance.md",
        "model_report.md",
        "model_report.json",
        "pipeline_run.md",
    ):
        assert (reports / name).is_file(), name
    analysis_files = {path.name for path in (reports / "analysis").glob("*.csv")}
    assert "13_ladder_funnel.csv" in analysis_files and len(analysis_files) == 8
    assert len(list((reports / "performance" / "plans").glob("*.txt"))) == 8

    run_record = (reports / "pipeline_run.md").read_text(encoding="utf-8")
    for stage in ("fetch", "warehouse", "quality", "analyze", "performance", "train", "total"):
        assert f"| {stage} |" in run_record
    assert "Logical cores" in run_record and "15,584 customer-month rows" in run_record

    report = json.loads((reports / "model_report.json").read_text(encoding="utf-8"))
    assert report["validation"]["beats_baselines"] is True
    artifact = joblib.load(project / "artifacts" / "propensity_model.joblib")
    assert artifact["feature_columns"] == report["data"]["features"]
    assert not zip_path.exists(), "zip removed after conversion"
    assert (project / "data" / "warehouse.duckdb").is_file()

    # A second fetch finds the cache current: no zip and no credentials needed.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert main(["--config", str(config_path), "fetch"]) == 0
    assert "is current" in caplog.text


def test_invalid_config_exits_with_code_2(tmp_path, caplog):
    config_path = write_project(tmp_path / "project", target_product="ind_nope_fin_ult1")
    assert main(["--config", str(config_path), "config"]) == 2
    assert "ind_nope_fin_ult1" in caplog.text


def test_stage_without_its_inputs_explains_what_to_run(tmp_path, caplog):
    config_path = write_project(tmp_path / "project")
    assert main(["--config", str(config_path), "warehouse"]) == 1
    assert "run `python -m xsell fetch` first" in caplog.text
    assert main(["--config", str(config_path), "train"]) == 1
    assert "run `python -m xsell warehouse` first" in caplog.text


def test_fetch_without_data_or_credentials_fails_cleanly(tmp_path, no_kaggle_credentials, caplog):
    config_path = write_project(tmp_path / "project")
    assert main(["--config", str(config_path), "fetch"]) == 1
    assert "Kaggle credentials not found" in caplog.text


def test_all_stops_at_the_first_failing_stage(tmp_path, no_kaggle_credentials, caplog):
    config_path = write_project(tmp_path / "project")
    assert main(["--config", str(config_path), "all"]) == 1
    assert "stage warehouse: starting" not in caplog.text
    assert not (tmp_path / "project" / "reports" / "pipeline_run.md").exists()
