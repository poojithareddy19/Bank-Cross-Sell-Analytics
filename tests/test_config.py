from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from xsell.cli import main
from xsell.config import ConfigError, build_config, load_config, month_offset

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config.yaml"
PRODUCTS_PATH = ROOT / "config" / "products.yaml"


@pytest.fixture
def raw() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def raw_products() -> dict:
    return yaml.safe_load(PRODUCTS_PATH.read_text(encoding="utf-8"))


def build(raw: dict, raw_products: dict, env: dict | None = None):
    return build_config(raw, raw_products, root=ROOT, env=env or {})


def test_repository_config_loads():
    config = load_config(CONFIG_PATH, env={})
    assert len(config.products) == 24
    assert config.model.target_product in config.product_codes
    assert config.months.count == 17
    assert config.months.index("2015-04") == 3
    assert config.paths.warehouse == ROOT / "data" / "warehouse.duckdb"
    assert config.paths.cache_dir == ROOT / "data" / "cache"


def test_environment_overrides(raw, raw_products, tmp_path):
    env = {"XSELL_DATA_DIR": str(tmp_path), "XSELL_DUCKDB_MEMORY_LIMIT": "2GB", "XSELL_THREADS": "3"}
    config = build(raw, raw_products, env)
    assert config.paths.data_dir == tmp_path
    assert config.duckdb.memory_limit == "2GB"
    assert config.duckdb.threads == 3


def test_unknown_target_product_is_named(raw, raw_products):
    raw["model"]["target_product"] = "ind_xxxx_fin_ult1"
    with pytest.raises(ConfigError, match="ind_xxxx_fin_ult1.*not a product code"):
        build(raw, raw_products)


def test_unknown_ladder_product_is_named(raw, raw_products):
    raw_products["ladder"] = ["ind_cco_fin_ult1", "ind_nope_fin_ult1"]
    with pytest.raises(ConfigError, match="ladder contains unknown.*ind_nope_fin_ult1"):
        build(raw, raw_products)


def test_duplicate_product_code(raw, raw_products):
    raw_products["products"].append(copy.deepcopy(raw_products["products"][0]))
    with pytest.raises(ConfigError, match="duplicate product code.*ind_ahor_fin_ult1"):
        build(raw, raw_products)


def test_wrong_product_count(raw, raw_products):
    raw_products["products"].pop()
    with pytest.raises(ConfigError, match="expected 24 products"):
        build(raw, raw_products)


def test_unknown_family(raw, raw_products):
    raw_products["products"][0]["family"] = "crypto"
    with pytest.raises(ConfigError, match="unknown family 'crypto'"):
        build(raw, raw_products)


def test_missing_key_is_named(raw, raw_products):
    del raw["duckdb"]["threads"]
    with pytest.raises(ConfigError, match="missing required setting 'duckdb.threads'"):
        build(raw, raw_products)


@pytest.mark.parametrize("value", ["lots", "4", "-1GB"])
def test_bad_memory_limit(raw, raw_products, value):
    raw["duckdb"]["memory_limit"] = value
    with pytest.raises(ConfigError, match="memory_limit"):
        build(raw, raw_products)


def test_bad_threads_from_environment(raw, raw_products):
    with pytest.raises(ConfigError, match="duckdb.threads must be a whole number"):
        build(raw, raw_products, {"XSELL_THREADS": "many"})


def test_overlapping_splits(raw, raw_products):
    raw["model"]["splits"]["validation"] = ["2015-12", "2016-02"]
    with pytest.raises(ConfigError, match="train must end before model.splits.validation"):
        build(raw, raw_products)


def test_test_split_needs_a_label_month(raw, raw_products):
    raw["model"]["splits"]["test"] = ["2016-03", "2016-05"]
    with pytest.raises(ConfigError, match="labels use t\\+1"):
        build(raw, raw_products)


def test_train_split_needs_feature_history(raw, raw_products):
    raw["model"]["splits"]["train"] = ["2015-02", "2015-12"]
    with pytest.raises(ConfigError, match="full feature history"):
        build(raw, raw_products)


def test_bad_month_format(raw, raw_products):
    raw["months"]["first"] = "2015-13"
    with pytest.raises(ConfigError, match="months.first must be a month"):
        build(raw, raw_products)


@pytest.mark.parametrize("value", [0, 1.5])
def test_sample_share_range(raw, raw_products, value):
    raw["model"]["sample_share"] = value
    with pytest.raises(ConfigError, match="sample_share"):
        build(raw, raw_products)


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "config" / "config.yaml")


def test_invalid_yaml(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("paths: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)


def test_month_offset():
    assert month_offset("2015-01", "2016-05") == 16
    assert month_offset("2015-12", "2016-01") == 1


def test_cli_config_stage():
    assert main(["--config", str(CONFIG_PATH), "config"]) == 0


def test_cli_reports_config_error(tmp_path):
    assert main(["--config", str(tmp_path / "missing.yaml"), "config"]) == 2


def test_month_labels_cover_every_snapshot():
    labels = load_config(CONFIG_PATH, env={}).months.labels
    assert len(labels) == 17
    assert labels[0] == "2015-01" and labels[11] == "2015-12" and labels[-1] == "2016-05"
