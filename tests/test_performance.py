from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from fixture_data import EXPECTED
from xsell.config import AnalysisSettings
from xsell.db import connect, run_sql_file, sql_path
from xsell.performance import PAIRS, _same_result, performance_params, run_performance


@pytest.fixture
def fast_config(built_warehouse):
    analysis = AnalysisSettings(top_channels=built_warehouse.analysis.top_channels, timing_runs=2)
    return dataclasses.replace(built_warehouse, analysis=analysis)


def run_variant(config, relative: str) -> pd.DataFrame:
    with connect(config) as connection:
        return run_sql_file(connection, sql_path(config, relative), performance_params(config))


def test_pairs_return_identical_results(fast_config):
    for pair in PAIRS:
        if pair.build:
            run_variant(fast_config, pair.build)
        baseline, candidate = run_variant(fast_config, pair.baseline), run_variant(fast_config, pair.candidate)
        assert _same_result(baseline, candidate), pair.name
        assert len(baseline) > 0, pair.name


def test_event_pair_matches_the_planted_events(fast_config):
    events = run_variant(fast_config, "performance/01_events_self_join.sql").set_index("product_code")
    assert events["adoptions"].sum() == len(EXPECTED["adoptions"])
    assert events["attritions"].sum() == len(EXPECTED["attritions"])
    assert events.loc["ind_ecue_fin_ult1"].tolist() == [4, 2]  # 2002, 2003, 2006 twice; 1002 and 2006 drop it
    assert "ind_recibo_ult1" in events.index, "2003 adopts direct debit"
    # The gap customer 1003 must not add a direct debit adoption: only 2003 adopts it.
    assert events.loc["ind_recibo_ult1", "adoptions"] == 1


def test_scan_pair_answers_the_latest_month(fast_config):
    result = run_variant(fast_config, "performance/02_scan_pruned.sql")
    assert result.iloc[0].tolist() == [59, 2]  # 59 customers in 2015-08, credit card holders 1001 and 2002


def test_run_performance_writes_reports_and_cleans_up(fast_config):
    results = run_performance(fast_config)
    assert [result.pair.name for result in results] == [pair.name for pair in PAIRS]
    for result in results:
        assert len(result.baseline_seconds) == len(result.candidate_seconds) == 2
        assert result.baseline_median > 0 and result.candidate_median > 0
        assert result.faster in {"baseline", "candidate"} and result.ratio >= 1
    assert results[2].build_seconds is not None and len(results[2].build_seconds) == 2

    reports = fast_config.paths.reports_dir
    report = (reports / "performance.md").read_text(encoding="utf-8")
    assert "## Machine" in report and "Logical cores" in report
    for pair in PAIRS:
        assert pair.title in report
        for variant in ("baseline", "candidate"):
            plan = (reports / "performance" / "plans" / f"{pair.name}_{variant}.txt").read_text(encoding="utf-8")
            assert plan.strip()
    timings = pd.read_csv(reports / "performance" / "timings.csv")
    assert set(timings["variant"]) == {"build", "baseline", "candidate"}
    assert len(timings) == len(PAIRS) * 2 * 2 + 2

    with connect(fast_config, read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert "perf_customer_summary" not in tables


def test_running_total_pair_counts_every_customer_month(fast_config):
    result = run_variant(fast_config, "performance/04_running_total_window.sql")
    assert result["customer_months"].sum() == EXPECTED["rows"]
    # 8 inactive customers (2041-2048) never accumulate an active month.
    assert result.set_index("active_months_so_far").loc[0, "customer_months"] == 8 * 8


def test_faster_and_ratio_are_reported_honestly():
    from xsell.performance import PairResult

    result = PairResult(PAIRS[0], baseline_seconds=[1.0, 1.0], candidate_seconds=[3.0, 3.0], build_seconds=None,
                        result_rows=1)  # fmt: skip
    assert result.faster == "baseline"
    assert result.ratio == pytest.approx(3.0)
    assert result.break_even_queries is None


def test_result_comparison_detects_differences():
    left = pd.DataFrame({"a": [1, 2]})
    assert _same_result(left, pd.DataFrame({"a": [1.0, 2.0]}))
    assert not _same_result(left, pd.DataFrame({"a": [1, 3]}))
    assert not _same_result(left, None)
