"""Build the propensity dataset in DuckDB and load it for modelling.

The SQL (sql/features/20_propensity_features.sql) computes every feature from months
up to and including the feature month t; labels come from t+1. Only the deterministic
customer sample is pulled into pandas.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from xsell.config import Config
from xsell.db import connect, run_sql_file, sql_path
from xsell.errors import XsellError
from xsell.logging_utils import get_logger
from xsell.sampling import sample_threshold

logger = get_logger("xsell.features")

TARGET_MODES = ("product", "any")
NUMERIC_FEATURES = (
    "n_products",
    "n_products_change_1m",
    "n_products_change_3m",
    "months_since_first_seen",
    "adoptions_last_3m",
    "attritions_last_3m",
    "active_months_last_3m",
    "seniority_months",
    "log_income",
)
BINARY_FEATURES = ("is_active", "is_new_customer", "income_missing")
CATEGORICAL_FEATURES = ("segment", "relation_type", "age_band", "sex", "channel")
ID_COLUMNS = ("customer_id", "feature_month", "label_month", "split")
LABEL_COLUMNS = ("holds_target_at_t", "adopts_target", "adopts_any")


@dataclass(frozen=True)
class Dataset:
    frame: pd.DataFrame
    label: str
    target_mode: str
    product_flags: tuple[str, ...]

    @property
    def feature_columns(self) -> list[str]:
        return [*self.product_flags, *NUMERIC_FEATURES, *BINARY_FEATURES, *CATEGORICAL_FEATURES]

    def split(self, name: str) -> pd.DataFrame:
        return self.frame[self.frame["split"] == name]

    def xy(self, name: str) -> tuple[pd.DataFrame, pd.Series]:
        part = self.split(name)
        return part[self.feature_columns], part[self.label]


def feature_params(config: Config) -> dict[str, object]:
    index = config.months.index
    splits = config.model.splits
    return {
        "target_product": config.model.target_product,
        "sample_threshold": sample_threshold(config.model.sample_share),
        "train_first": index(splits["train"][0]),
        "train_last": index(splits["train"][1]),
        "validation_first": index(splits["validation"][0]),
        "validation_last": index(splits["validation"][1]),
        "test_first": index(splits["test"][0]),
        "test_last": index(splits["test"][1]),
    }


def build_features(config: Config) -> pd.DataFrame:
    """(Re)build product_events and propensity_dataset; returns row counts per split."""
    if not config.paths.warehouse.is_file():
        raise XsellError(f"warehouse not found at {config.paths.warehouse}; run `python -m xsell warehouse` first")
    with connect(config) as connection:
        # Events feed the 3-month history features; rebuild so they match the current fact table.
        run_sql_file(connection, sql_path(config, "analysis/10_product_events.sql"))
        summary = run_sql_file(
            connection, sql_path(config, "features/20_propensity_features.sql"), feature_params(config)
        )
    for row in summary.itertuples():
        logger.info(
            "%s: %d rows, %d customers, %d target adoptions, %d any-product adoptions",
            row.split,
            row.rows,
            row.customers,
            row.target_adoptions,
            row.any_adoptions,
        )
    if set(summary["split"]) != {"train", "validation", "test"}:
        raise XsellError(f"propensity dataset is missing splits; found {sorted(summary['split'])}")
    return summary


def load_dataset(config: Config, target_mode: str) -> Dataset:
    """Pull the sampled dataset into pandas for one target definition."""
    if target_mode not in TARGET_MODES:
        raise ValueError(f"target_mode must be one of {TARGET_MODES}")
    with connect(config, read_only=True) as connection:
        frame = connection.execute("SELECT * FROM propensity_dataset ORDER BY feature_month, customer_id").df()
    if target_mode == "product":
        # Population: customers who do not hold the target product at t.
        frame = frame[frame["holds_target_at_t"] == 0]
    label = "adopts_target" if target_mode == "product" else "adopts_any"
    flags = tuple(column for column in frame.columns if column.startswith("ind_"))
    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].astype(str)
    numeric = [*flags, *NUMERIC_FEATURES, *BINARY_FEATURES]
    frame[numeric] = frame[numeric].astype("float64")
    frame[label] = frame[label].astype(int)
    return Dataset(frame=frame.reset_index(drop=True), label=label, target_mode=target_mode, product_flags=flags)
