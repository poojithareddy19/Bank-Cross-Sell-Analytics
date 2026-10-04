from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fixture_data import write_fixture_zip
from xsell.config import Config, build_config
from xsell.data.fetch import run_fetch
from xsell.warehouse import build_warehouse

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture_config(tmp_path: Path) -> Config:
    """Repository config pointed at a temp data dir and the fixture's 8 months."""
    raw = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    raw_products = yaml.safe_load((ROOT / "config" / "products.yaml").read_text(encoding="utf-8"))
    raw["paths"].update(
        data_dir=str(tmp_path / "data"),
        reports_dir=str(tmp_path / "reports"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    raw["duckdb"].update(memory_limit="1GB", threads=2)
    raw["months"] = {"first": "2015-01", "last": "2015-08"}
    raw["model"]["splits"] = {
        "train": ["2015-04", "2015-05"],
        "validation": ["2015-06", "2015-06"],
        "test": ["2015-07", "2015-07"],
    }
    return build_config(raw, raw_products, root=ROOT, env={})


@pytest.fixture
def fixture_zip(fixture_config: Config) -> Path:
    """The synthetic CSV zipped where the fetch stage expects the Kaggle download."""
    return write_fixture_zip(fixture_config.paths.raw_dir / fixture_config.kaggle.file)


@pytest.fixture
def built_warehouse(fixture_config: Config, fixture_zip: Path) -> Config:
    """Fixture data fetched into the Parquet cache and built into the DuckDB warehouse."""
    run_fetch(fixture_config)
    build_warehouse(fixture_config)
    return fixture_config
