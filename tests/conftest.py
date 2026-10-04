from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fixture_data import fixture_csv_text, model_fixture_rows, write_fixture_zip
from xsell.config import Config, build_config
from xsell.data.fetch import run_fetch
from xsell.warehouse import build_warehouse

ROOT = Path(__file__).resolve().parents[1]


def make_config(work_dir: Path, **model_overrides) -> Config:
    """Repository config pointed at work_dir and the fixtures' 8 months (2015-01 to 2015-08)."""
    raw = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    raw_products = yaml.safe_load((ROOT / "config" / "products.yaml").read_text(encoding="utf-8"))
    raw_products["ladder"] = []  # tests that need a ladder set their own
    raw["paths"].update(
        data_dir=str(work_dir / "data"),
        reports_dir=str(work_dir / "reports"),
        artifacts_dir=str(work_dir / "artifacts"),
    )
    raw["duckdb"].update(memory_limit="1GB", threads=2)
    raw["months"] = {"first": "2015-01", "last": "2015-08"}
    raw["model"]["splits"] = {
        "train": ["2015-04", "2015-05"],
        "validation": ["2015-06", "2015-06"],
        "test": ["2015-07", "2015-07"],
    }
    raw["model"].update(model_overrides)
    return build_config(raw, raw_products, root=ROOT, env={})


@pytest.fixture
def fixture_config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture
def fixture_zip(fixture_config: Config) -> Path:
    """The synthetic CSV zipped where the fetch stage expects the Kaggle download."""
    return write_fixture_zip(fixture_config.paths.raw_dir / fixture_config.kaggle.file)


@pytest.fixture
def built_warehouse(fixture_config: Config, fixture_zip: Path) -> Config:
    """Fixture data fetched into the Parquet cache and built into the DuckDB warehouse."""
    assert fixture_zip.is_file()
    run_fetch(fixture_config)
    build_warehouse(fixture_config)
    return fixture_config


MODEL_SETTINGS = {"sample_share": 1.0, "bootstrap_resamples": 50, "permutation_rows": 1000}


def build_model_warehouse(work_dir: Path, **model_overrides) -> Config:
    """The larger model fixture (2,000 customers) fetched and built into a warehouse."""
    config = make_config(work_dir, **{**MODEL_SETTINGS, **model_overrides})
    write_fixture_zip(config.paths.raw_dir / config.kaggle.file, csv_text=fixture_csv_text(model_fixture_rows()))
    run_fetch(config)
    build_warehouse(config)
    return config


@pytest.fixture
def model_warehouse(tmp_path: Path) -> Config:
    """A fresh model-fixture warehouse that a test may modify."""
    return build_model_warehouse(tmp_path)


@pytest.fixture(scope="session")
def trained_model(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, dict]:
    """The model trained once on the model fixture, shared read-only across tests."""
    from xsell.train import run_training

    config = build_model_warehouse(tmp_path_factory.mktemp("trained"))
    return config, run_training(config)
