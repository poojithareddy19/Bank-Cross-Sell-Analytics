from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from xsell.analysis import propose_ladder, run_analysis
from xsell.config import AnalysisSettings, LadderStep
from xsell.warehouse import build_warehouse

FIXTURE_LADDER = (
    LadderStep("Current account", ("ind_cco_fin_ult1",)),
    LadderStep("Direct debit or e-account", ("ind_recibo_ult1", "ind_ecue_fin_ult1")),
    LadderStep("Credit card", ("ind_tjcr_fin_ult1",)),
)

# Hand-counted from tests/fixture_data.py:
#   step 1: all 60 customers hold a current account from their first month
#   step 2: 12 fillers with direct debit, plus 1002, 1003 (after its gap), 1010, 2002, 2003 = 17
#   step 3: only 2002 holds a credit card after reaching step 2 (1001 never reaches step 2)
EXPECTED_FUNNEL = [(1, 60, 60), (2, 60, 17), (3, 17, 1)]  # (step, previous_reached, reached)


def with_ladder(config, top_channels: int = 10):
    return dataclasses.replace(config, ladder=FIXTURE_LADDER, analysis=AnalysisSettings(top_channels))


@pytest.fixture
def ladder_results(built_warehouse):
    config = with_ladder(built_warehouse)
    build_warehouse(config)  # dim_product picks up the ladder steps
    return config, run_analysis(config)


def test_funnel_overall_matches_hand_count(ladder_results):
    _, results = ladder_results
    funnel = results["13_ladder_funnel"]
    overall = funnel[funnel["dimension"] == "all"]
    assert list(zip(overall["step"], overall["previous_reached"], overall["reached"], strict=True)) == EXPECTED_FUNNEL
    assert overall["step_name"].tolist() == [step.name for step in FIXTURE_LADDER]
    assert overall["conversion_rate"].tolist() == pytest.approx([1.0, 17 / 60, 1 / 17])
    assert (overall["conversion_rate_ci_lower"] <= overall["conversion_rate"]).all()


def test_funnel_counts_never_increase_down_the_ladder(ladder_results):
    _, results = ladder_results
    for _, group in results["13_ladder_funnel"].groupby(["dimension", "group_value"]):
        reached = group.sort_values("step")["reached"].tolist()
        assert reached == sorted(reached, reverse=True)


def test_funnel_breakdowns_add_up(ladder_results):
    _, results = ladder_results
    funnel = results["13_ladder_funnel"]
    overall = funnel[funnel["dimension"] == "all"].set_index("step")["reached"]
    for dimension in ("segment", "channel"):
        part = funnel[funnel["dimension"] == dimension].groupby("step")["reached"].sum()
        assert part.to_dict() == overall.to_dict()
    assert set(funnel.loc[funnel["dimension"] == "channel", "group_value"]) == {"KAT", "KFC", "KHE"}


def test_small_channels_are_grouped_as_other(built_warehouse):
    config = with_ladder(built_warehouse, top_channels=2)
    build_warehouse(config)
    funnel = run_analysis(config)["13_ladder_funnel"]
    channels = funnel.loc[funnel["dimension"] == "channel", "group_value"]
    assert "other" in set(channels) and channels.nunique() == 3


def test_dim_product_carries_ladder_steps(ladder_results):
    config, _ = ladder_results
    from xsell.db import connect

    with connect(config, read_only=True) as connection:
        rows = connection.execute(
            "SELECT product_code, ladder_step, ladder_step_name FROM dim_product "
            "WHERE ladder_step IS NOT NULL ORDER BY ladder_step, product_code"
        ).fetchall()
    assert rows == [
        ("ind_cco_fin_ult1", 1, "Current account"),
        ("ind_ecue_fin_ult1", 2, "Direct debit or e-account"),
        ("ind_recibo_ult1", 2, "Direct debit or e-account"),
        ("ind_tjcr_fin_ult1", 3, "Credit card"),
    ]


def test_time_to_next_product(ladder_results):
    _, results = ladder_results
    times = results["14_time_to_next_product"]
    # First adoptions: 1001 at m3 and 1008 at m5 (first seen m2) -> 3 months; 2002 and 2003 at m2 -> 2 months.
    assert dict(zip(times["months_to_first_adoption"], times["customers"], strict=True)) == {2: 2, 3: 2}
    assert times["cumulative_share"].tolist() == pytest.approx([0.5, 1.0])
    assert times["median_months"].iloc[0] == pytest.approx(2.5)
    assert times["customers_observed"].iloc[0] == 60


def test_summary_and_proposal_reports(ladder_results):
    config, _ = ladder_results
    summary = (config.paths.reports_dir / "customer_analytics.md").read_text(encoding="utf-8")
    assert "| 3 | Credit card | 1 | 5.9% (1.0% to 27.0%) | 1.7% |" in summary
    assert "4 of 60 customers (6.7%) adopted at least one product" in summary
    proposal = (config.paths.reports_dir / "ladder_proposal.md").read_text(encoding="utf-8")
    assert "most held product in 2015-08 (100.0% of customers)" in proposal
    assert "3. Credit card: ind_tjcr_fin_ult1" in proposal


def test_funnel_is_skipped_without_a_ladder(built_warehouse):
    stale = built_warehouse.paths.reports_dir / "analysis" / "13_ladder_funnel.csv"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("old", encoding="utf-8")
    results = run_analysis(built_warehouse)
    assert "13_ladder_funnel" not in results
    assert not stale.exists(), "a stale funnel CSV is removed when the ladder is empty"
    summary = (built_warehouse.paths.reports_dir / "customer_analytics.md").read_text(encoding="utf-8")
    assert "Skipped: no ladder confirmed yet" in summary
    proposal = (built_warehouse.paths.reports_dir / "ladder_proposal.md").read_text(encoding="utf-8")
    assert "None yet." in proposal


def test_propose_ladder_follows_the_strongest_transitions():
    penetration = pd.DataFrame(
        {
            "month_label": ["2016-05"] * 4,
            "product_code": ["cco", "ecue", "tjcr", "recibo"],
            "product_name": ["Current account", "E-account", "Credit card", "Direct debit"],
            "penetration": [0.9, 0.1, 0.05, 0.2],
        }
    )
    transitions = pd.DataFrame(
        {
            "from_product": ["cco", "cco", "ecue", "recibo"],
            "to_product": ["ecue", "recibo", "tjcr", "tjcr"],
            "transitions": [10, 5, 8, 1],
        }
    )
    proposal = propose_ladder(penetration, transitions)
    # cco first; ecue (10) beats recibo (5); then tjcr (8 + 0 from cco) beats recibo (5); then recibo.
    assert [step["product_code"] for step in proposal] == ["cco", "ecue", "tjcr", "recibo"]
    assert propose_ladder(penetration, transitions, max_steps=2)[-1]["product_code"] == "ecue"
