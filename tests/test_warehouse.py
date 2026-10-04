from __future__ import annotations

import datetime as dt

import pytest

from fixture_data import EXPECTED
from xsell.db import SqlError, connect, run_sql_file
from xsell.errors import XsellError
from xsell.warehouse import build_warehouse


def query(config, sql: str, params=None):
    with connect(config, read_only=True) as connection:
        return connection.execute(sql, params or []).fetchall()


def scalar(config, sql: str, params=None):
    return query(config, sql, params)[0][0]


def staging_row(config, customer_id: int, month_index: int, column: str):
    return scalar(
        config,
        f"SELECT {column} FROM stg_holdings WHERE customer_id = ? AND month_index = ?",
        [customer_id, month_index],
    )


def test_table_counts_reconcile_with_fixture(built_warehouse):
    counts = build_warehouse(built_warehouse)
    assert counts == {
        "stg_holdings": EXPECTED["rows"],
        "dim_customer": EXPECTED["customers"],
        "dim_product": 24,
        "dim_month": EXPECTED["months"],
        "fact_monthly_holdings": EXPECTED["rows"],
    }


def test_dim_month_maps_snapshots_to_indexes(built_warehouse):
    rows = query(built_warehouse, "SELECT month_index, month_label, customers FROM dim_month ORDER BY 1")
    assert [(index, label) for index, label, _ in rows] == [
        (index, label) for index, label in enumerate(EXPECTED["rows_per_month"])
    ]
    assert [customers for _, _, customers in rows] == list(EXPECTED["rows_per_month"].values())


def test_staging_cleans_raw_quirks(built_warehouse):
    config = built_warehouse
    assert staging_row(config, 1001, 0, "age") == 26
    assert staging_row(config, 1006, 0, "seniority_months") is None
    assert staging_row(config, 1001, 0, "seniority_months") == 31
    assert staging_row(config, 1004, 0, "income") is None
    assert staging_row(config, 1005, 0, "income") is None
    assert staging_row(config, 1005, 0, "income_missing") is True
    assert staging_row(config, 1001, 0, "income_missing") is False
    assert staging_row(config, 1005, 0, "province_name") == "CORUÑA, A"
    assert staging_row(config, 1012, 0, "join_date") is None
    assert staging_row(config, 1008, 2, "join_date") == dt.date(2015, 3, 5)
    assert staging_row(config, 1008, 2, "is_new_customer") == 1
    assert staging_row(config, 1001, 0, "is_resident") is True
    assert staging_row(config, 1001, 0, "is_active") == 1


def test_indrel_1mes_spellings_are_normalised(built_warehouse):
    rows = query(
        built_warehouse, "SELECT month_index, customer_type FROM stg_holdings GROUP BY ALL ORDER BY month_index"
    )
    # Fixture months use '1', '1.0', '1', '2', 'P', '3.0', '1', '4.0'.
    assert rows == [(0, "1"), (1, "1"), (2, "1"), (3, "2"), (4, "P"), (5, "3"), (6, "1"), (7, "4")]


def test_null_flags_become_zero_and_are_counted(built_warehouse):
    config = built_warehouse
    assert staging_row(config, 1007, 2, "ind_nomina_ult1") == 0
    assert staging_row(config, 1007, 2, "ind_nom_pens_ult1") == 0
    assert staging_row(config, 1007, 2, "flags_filled_from_null") == 2
    assert scalar(config, "SELECT sum(flags_filled_from_null) FROM stg_holdings") == EXPECTED["flags_filled_from_null"]


def test_fact_has_24_tinyint_flags_and_product_counts(built_warehouse):
    config = built_warehouse
    columns = query(config, "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'fact_monthly_holdings'")  # fmt: skip
    flag_types = {name: kind for name, kind in columns if name.startswith("ind_")}
    assert set(flag_types) == set(config.product_codes)
    assert set(flag_types.values()) == {"TINYINT"}

    def n_products(customer_id, month_index):
        return scalar(
            config,
            "SELECT n_products FROM fact_monthly_holdings WHERE customer_id = ? AND month_index = ?",
            [customer_id, month_index],
        )

    assert n_products(1001, 2) == 1
    assert n_products(1001, 3) == 2
    assert n_products(1010, 0) == 3
    assert n_products(1008, 5) == 3


def test_dim_customer_attributes(built_warehouse):
    config = built_warehouse
    row = query(
        config,
        "SELECT join_month, first_seen_month_index, last_seen_month_index, months_present "
        "FROM dim_customer WHERE customer_id = 1008",
    )[0]
    assert row == (dt.date(2015, 3, 1), 2, 7, 6)
    assert query(config, "SELECT months_present FROM dim_customer WHERE customer_id = 1003") == [(7,)]
    assert query(config, "SELECT last_seen_month_index FROM dim_customer WHERE customer_id = 1009") == [(4,)]
    assert query(config, "SELECT income FROM dim_customer WHERE customer_id = 1004") == [(None,)]
    assert query(config, "SELECT age_at_first_seen FROM dim_customer WHERE customer_id = 1011") == [(115,)]


def test_dim_product_comes_from_config(built_warehouse):
    config = built_warehouse
    rows = query(config, "SELECT product_code, product_name, family, ladder_step FROM dim_product "
                 "ORDER BY catalogue_order")  # fmt: skip
    assert [row[0] for row in rows] == list(config.product_codes)
    assert ("ind_tjcr_fin_ult1", "Credit card", "card", None) in rows


def test_warehouse_needs_the_cache(fixture_config):
    with pytest.raises(XsellError, match="run `python -m xsell fetch` first"):
        build_warehouse(fixture_config)


def test_run_sql_file_reports_missing_parameters(fixture_config, tmp_path):
    sql_file = tmp_path / "needs_param.sql"
    sql_file.write_text("SELECT $threshold AS value;", encoding="utf-8")
    with connect(fixture_config, database=":memory:") as connection:
        with pytest.raises(SqlError, match=r"missing SQL parameter\(s\) \['threshold'\]"):
            run_sql_file(connection, sql_file, {})
        assert run_sql_file(connection, sql_file, {"threshold": 3, "unused": 1})["value"].tolist() == [3]


def test_run_sql_file_names_the_failing_file(fixture_config, tmp_path):
    sql_file = tmp_path / "broken.sql"
    sql_file.write_text("SELECT * FROM table_that_does_not_exist;", encoding="utf-8")
    with connect(fixture_config, database=":memory:") as connection, pytest.raises(SqlError, match="broken.sql"):
        run_sql_file(connection, sql_file)


def test_locked_warehouse_gives_clear_error(built_warehouse):
    import subprocess
    import sys

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; c = duckdb.connect(sys.argv[1]); print('locked', flush=True); time.sleep(30)",
            str(built_warehouse.paths.warehouse),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(SqlError, match="Close other programs using the file"):
            connect(built_warehouse)
    finally:
        holder.kill()
        holder.wait()
