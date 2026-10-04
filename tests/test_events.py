from __future__ import annotations

import pandas as pd
import pytest

from fixture_data import EXPECTED
from xsell.analysis import run_analyses
from xsell.db import connect

MONTH_LABELS = list(EXPECTED["rows_per_month"])


@pytest.fixture
def analysed(built_warehouse):
    results = run_analyses(built_warehouse)
    return built_warehouse, results


def rows(config, sql: str) -> list[tuple]:
    with connect(config, read_only=True) as connection:
        return connection.execute(sql).fetchall()


def labelled(records) -> set[tuple]:
    return {(customer, product, MONTH_LABELS[month]) for customer, product, month in records}


def events_of_type(config, event_type: str) -> set[tuple]:
    return labelled(
        rows(
            config,
            f"SELECT customer_id, product_code, month_index FROM product_events WHERE event_type = '{event_type}'",
        )
    )


def test_adoptions_are_exactly_the_planted_ones(analysed):
    config, _ = analysed
    assert events_of_type(config, "adoption") == EXPECTED["adoptions"]


def test_attritions_are_exactly_the_planted_ones(analysed):
    config, _ = analysed
    assert events_of_type(config, "attrition") == EXPECTED["attritions"]


def test_month_gap_does_not_create_an_event(analysed):
    config, _ = analysed
    assert rows(config, "SELECT count(*) FROM product_events WHERE customer_id = 1003") == [(0,)]
    gaps = rows(config, "SELECT customer_id, product_code, month_index FROM product_changes_across_gaps")
    assert labelled(gaps) == EXPECTED["changes_across_gaps"]
    assert rows(config, "SELECT months_since_previous, change_type FROM product_changes_across_gaps") == [
        (2, "adoption")
    ]


def test_every_event_has_the_previous_month_present(analysed):
    config, _ = analysed
    orphans = rows(
        config,
        """
        SELECT count(*) FROM product_events AS e
        LEFT JOIN fact_monthly_holdings AS f
          ON f.customer_id = e.customer_id AND f.month_index = e.month_index - 1
        WHERE f.customer_id IS NULL
        """,
    )
    assert orphans == [(0,)]


def test_event_summary_totals(analysed):
    _, results = analysed
    summary: pd.DataFrame = results["10_product_events"]
    assert len(summary) == (EXPECTED["months"] - 1) * 24
    assert summary["adoptions"].sum() == len(EXPECTED["adoptions"])
    assert summary["attritions"].sum() == len(EXPECTED["attritions"])
    assert summary["changes_across_gaps"].sum() == len(EXPECTED["changes_across_gaps"])
    credit_card = summary[(summary["product_code"] == "ind_tjcr_fin_ult1") & (summary["month_label"] == "2015-04")]
    assert credit_card["adoptions"].tolist() == [1]
