"""Build the staging table and star schema in data/warehouse.duckdb."""

from __future__ import annotations

import time

import pandas as pd

from xsell.config import Config
from xsell.data.convert import holdings_path, read_manifest
from xsell.db import connect, run_sql_file, sql_path, table_count
from xsell.errors import XsellError
from xsell.logging_utils import get_logger

logger = get_logger("xsell.warehouse")

WAREHOUSE_SQL = (
    ("stg_holdings", "staging/00_stg_holdings.sql"),
    ("dim_customer", "warehouse/01_dim_customer.sql"),
    ("dim_product", "warehouse/02_dim_product.sql"),
    ("dim_month", "warehouse/03_dim_month.sql"),
    ("fact_monthly_holdings", "warehouse/04_fact_monthly_holdings.sql"),
)


def products_frame(config: Config) -> pd.DataFrame:
    steps = {code: (number, step.name) for number, step in enumerate(config.ladder, start=1) for code in step.products}
    return pd.DataFrame(
        {
            "product_code": [product.code for product in config.products],
            "product_name": [product.name for product in config.products],
            "family": [product.family for product in config.products],
            "ladder_step": pd.array([steps.get(p.code, (None,))[0] for p in config.products], dtype="Int64"),
            "ladder_step_name": [steps.get(p.code, (None, None))[1] for p in config.products],
            "catalogue_order": range(1, len(config.products) + 1),
        }
    )


def warehouse_params(config: Config) -> dict[str, str]:
    return {
        "holdings_glob": (holdings_path(config.paths.cache_dir) / "*" / "*.parquet").as_posix(),
        "first_month": f"{config.months.first}-01",
    }


def build_warehouse(config: Config) -> dict[str, int]:
    """Rebuild every warehouse table from the Parquet cache; returns row counts."""
    if read_manifest(config.paths.cache_dir) is None:
        raise XsellError(f"no Parquet cache in {config.paths.cache_dir}; run `python -m xsell fetch` first")

    params = warehouse_params(config)
    counts: dict[str, int] = {}
    with connect(config) as connection:
        connection.register("products_config", products_frame(config))
        for table, relative in WAREHOUSE_SQL:
            started = time.perf_counter()
            run_sql_file(connection, sql_path(config, relative), params)
            counts[table] = table_count(connection, table)
            logger.info("built %s: %d rows in %.1fs", table, counts[table], time.perf_counter() - started)
        connection.unregister("products_config")
    return counts
