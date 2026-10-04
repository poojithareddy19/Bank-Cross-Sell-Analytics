from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from fixture_data import EXPECTED
from xsell.analysis import add_wilson_interval, run_analysis
from xsell.errors import XsellError

LATEST = list(EXPECTED["rows_per_month"])[-1]


@pytest.fixture
def results(built_warehouse):
    return run_analysis(built_warehouse)


def wilson(successes: int, trials: int, z: float = 1.959964) -> tuple[float, float]:
    p = successes / trials
    centre = (p + z**2 / (2 * trials)) / (1 + z**2 / trials)
    half = z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / (1 + z**2 / trials)
    return centre - half, centre + half


def assert_rates_are_valid(frame: pd.DataFrame, rate: str) -> None:
    assert frame[rate].between(0, 1).all()
    assert (frame[f"{rate}_ci_lower"] <= frame[rate] + 1e-12).all()
    assert (frame[rate] <= frame[f"{rate}_ci_upper"] + 1e-12).all()


def test_wilson_interval_matches_formula():
    frame = pd.DataFrame({"hits": [15, 0, 3], "n": [59, 10, 0]})
    frame["rate"] = frame["hits"] / frame["n"].replace(0, np.nan)
    result = add_wilson_interval(frame, "hits", "n", "rate")
    assert list(result.columns) == ["hits", "n", "rate", "rate_ci_lower", "rate_ci_upper"]
    lower, upper = wilson(15, 59)
    assert result.loc[0, "rate_ci_lower"] == pytest.approx(lower, abs=1e-6)
    assert result.loc[0, "rate_ci_upper"] == pytest.approx(upper, abs=1e-6)
    assert result.loc[1, "rate_ci_lower"] == pytest.approx(0, abs=1e-12)
    assert np.isnan(result.loc[2, "rate_ci_lower"]), "no interval without trials"


def test_penetration(results):
    penetration = results["11_product_penetration"]
    assert len(penetration) == EXPECTED["months"] * 24
    assert_rates_are_valid(penetration, "penetration")
    per_month = penetration.groupby("month_label")["customers"].first().to_dict()
    assert per_month == EXPECTED["rows_per_month"]
    latest = penetration[penetration["month_label"] == LATEST].set_index("product_code")
    for product, holders in EXPECTED["latest_holders"].items():
        assert latest.loc[product, "holders"] == holders
        assert latest.loc[product, "penetration"] == pytest.approx(holders / 59)


def test_next_product_transitions(results):
    transitions = results["12_next_product_transitions"]
    found = {
        (row.from_product, row.to_product): (row.customers, row.median_months_between)
        for row in transitions.itertuples()
    }
    assert found == EXPECTED["transitions"]
    assert transitions.groupby("from_product")["share_of_from"].sum().round(9).eq(1).all()


def test_join_cohort_retention(results):
    cohorts = results["15_join_cohort_retention"]
    assert_rates_are_valid(cohorts, "retention_rate")
    found = {
        month: (int(group["cohort_size"].iloc[0]), group.sort_values("months_since_join")["retained"].tolist())
        for month, group in cohorts.groupby("join_month")
    }
    assert found == EXPECTED["cohorts"]
    # Cohort sizes add up to the customers who joined on or after the first snapshot month.
    assert sum(size for size, _ in found.values()) == 3
    assert (cohorts["retained"] <= cohorts["cohort_size"]).all()


def test_engagement(results):
    engagement = results["16_engagement"]
    assert_rates_are_valid(engagement, "engagement_rate")
    trend = engagement[engagement["dimension"] == "month"]
    assert dict(zip(trend["group_value"], trend["customers"], strict=True)) == EXPECTED["rows_per_month"]
    by_products = engagement[engagement["dimension"] == "products_held"]
    found = {row.group_value: (row.customers, row.active) for row in by_products.itertuples()}
    assert found == EXPECTED["engagement_by_products"]
    for dimension in ("segment", "tenure_band", "products_held"):
        part = engagement[engagement["dimension"] == dimension]
        assert part["customers"].sum() == EXPECTED["rows_per_month"][LATEST]
        assert set(part["month_label"]) == {LATEST}


def test_customer_value_proxy(results):
    value = results["17_customer_value_proxy"]
    assert value["customers"].sum() == EXPECTED["rows_per_month"][LATEST]
    assert value["share_of_customers"].sum() == pytest.approx(1)
    assert value.groupby("families_held")["customers"].sum().to_dict() == EXPECTED["families_held"]
    assert value.loc[value["income_band"] == "missing", "customers"].sum() == 2  # customers 1004 and 1005


def test_reports_are_written(results, built_warehouse):
    reports = built_warehouse.paths.reports_dir
    for name in results:
        assert (reports / "analysis" / f"{name}.csv").is_file()
    summary = (reports / "customer_analytics.md").read_text(encoding="utf-8")
    assert "Adoption events: 9, of which 8 first-time and 1 repeat" in summary
    assert "| E-account | Credit card | 1 | 1 | 50.0% | 3 |" in summary
    assert "| 2015-01 | 2 | 100.0% | 100.0% | 100.0% | 50.0% |  |" in summary


def test_reports_are_deterministic(built_warehouse):
    run_analysis(built_warehouse)
    reports = built_warehouse.paths.reports_dir
    first = {path.name: path.read_bytes() for path in reports.rglob("*.*")}
    run_analysis(built_warehouse)
    assert {path.name: path.read_bytes() for path in reports.rglob("*.*")} == first


def test_analysis_needs_the_warehouse(fixture_config):
    with pytest.raises(XsellError, match="run `python -m xsell warehouse` first"):
        run_analysis(fixture_config)
