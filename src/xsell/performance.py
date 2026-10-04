"""Time paired baseline vs candidate queries and write reports/performance.md.

Each pair answers the same question two ways: a common baseline and a candidate
optimisation. Both variants must return identical results; each is run once to warm
up, then timed over `analysis.timing_runs` runs and reported as the median. The report
states which variant was faster as measured, including when the baseline wins.
EXPLAIN ANALYZE plans are saved next to the report. Timings depend on the machine,
so the machine spec is recorded with them.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from xsell.config import Config
from xsell.data.convert import read_manifest
from xsell.db import base_params, connect, require_warehouse, run_sql_file, sql_path
from xsell.errors import XsellError
from xsell.logging_utils import get_logger
from xsell.machine import machine_spec
from xsell.reporting import markdown_table, write_text

logger = get_logger("xsell.performance")


@dataclass(frozen=True)
class QueryPair:
    name: str
    title: str
    baseline: str
    candidate: str
    why: str
    # Optional one-off build that the candidate depends on; timed separately.
    build: str | None = None


PAIRS = (
    QueryPair(
        name="01_events",
        title="Adoption events: self-join vs LAG window",
        baseline="performance/01_events_self_join.sql",
        candidate="performance/01_events_window.sql",
        why=(
            "The self-join builds a hash table on (customer_id, month_index) and probes it with month_index - 1: "
            "an equality lookup that DuckDB runs in parallel. LAG needs every row sorted by customer and month "
            "inside the window operator. Row-store intuition says the window wins; in DuckDB's columnar, "
            "parallel engine the equality self-join can be faster, so the measured result decides. The analysis "
            "keeps LAG because the same pass also finds the previous observation across a month gap, which an "
            "exact month - 1 join cannot see."
        ),
    ),
    QueryPair(
        name="02_scan",
        title="Monthly snapshot from Parquet: load then filter vs partition pruning and projection",
        baseline="performance/02_scan_load_then_filter.sql",
        candidate="performance/02_scan_pruned.sql",
        why=(
            "Loading first decompresses every column of every monthly file and writes it into a table. With "
            "the filter on the hive partition column DuckDB opens only the one month=YYYY-MM folder, and "
            "projection pushdown reads only the two columns the query uses from it."
        ),
    ),
    QueryPair(
        name="03_summary",
        title="Customer aggregates: recompute per query vs materialised table",
        baseline="performance/03_summary_recompute.sql",
        candidate="performance/03_summary_materialised.sql",
        build="performance/03_summary_build.sql",
        why=(
            "Recomputing groups every customer-month row each time the question is asked. The materialised "
            "table stores one row per customer, so each query scans far fewer rows. The build is paid once; "
            "the break-even column shows after how many queries it pays for itself."
        ),
    ),
    QueryPair(
        name="04_running_total",
        title="Running count per customer: range self-join vs cumulative window",
        baseline="performance/04_running_total_self_join.sql",
        candidate="performance/04_running_total_window.sql",
        why=(
            "The range join (month_index <= current month) pairs each row with every earlier row of the same "
            "customer, n(n+1)/2 rows for n months, and then aggregates them. The cumulative window sorts each "
            "customer's rows once and keeps a running sum, so the work grows linearly with the rows."
        ),
    ),
)
CLEANUP_SQL = ("DROP TABLE IF EXISTS perf_customer_summary", "DROP TABLE IF EXISTS perf_all_rows")


@dataclass(frozen=True)
class PairResult:
    pair: QueryPair
    baseline_seconds: list[float]
    candidate_seconds: list[float]
    build_seconds: list[float] | None
    result_rows: int

    @property
    def baseline_median(self) -> float:
        return statistics.median(self.baseline_seconds)

    @property
    def candidate_median(self) -> float:
        return statistics.median(self.candidate_seconds)

    @property
    def build_median(self) -> float | None:
        return statistics.median(self.build_seconds) if self.build_seconds else None

    @property
    def faster(self) -> str:
        return "candidate" if self.candidate_median < self.baseline_median else "baseline"

    @property
    def ratio(self) -> float:
        """Slower median divided by faster median (always at least 1)."""
        slow, fast = sorted((self.baseline_median, self.candidate_median), reverse=True)
        return slow / fast if fast > 0 else float("inf")

    @property
    def break_even_queries(self) -> float | None:
        saving = self.baseline_median - self.candidate_median
        if self.build_median is None or saving <= 0:
            return None
        return self.build_median / saving


def performance_params(config: Config) -> dict[str, str]:
    return {"holdings_glob": base_params(config)["holdings_glob"], "month_label": config.months.last}


def timed_runs(connection, path: Path, params: dict, runs: int) -> tuple[list[float], pd.DataFrame | None]:
    """One warm-up run, then `runs` timed runs. Returns the timings and the last result."""
    result = run_sql_file(connection, path, params)
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        result = run_sql_file(connection, path, params)
        timings.append(time.perf_counter() - started)
    return timings, result


def explain_analyze(connection, path: Path, params: dict) -> str:
    """EXPLAIN ANALYZE every statement in the file, in order."""
    plans = []
    for statement in connection.extract_statements(path.read_text(encoding="utf-8")):
        needed = {name: params[name] for name in statement.named_parameters}
        rows = connection.execute("EXPLAIN ANALYZE " + statement.query.strip().rstrip(";"), needed).fetchall()
        plans.append("\n".join(str(row[-1]) for row in rows))
    return "\n\n".join(plans)


def _same_result(left: pd.DataFrame | None, right: pd.DataFrame | None) -> bool:
    if left is None or right is None:
        return False
    try:
        pd.testing.assert_frame_equal(
            left.reset_index(drop=True), right.reset_index(drop=True), check_dtype=False, check_exact=False
        )
    except AssertionError:
        return False
    return True


def run_pairs(config: Config, runs: int) -> list[PairResult]:
    require_warehouse(config)
    params = performance_params(config)
    plans_dir = config.paths.reports_dir / "performance" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with connect(config) as connection:
        try:
            for pair in PAIRS:
                build_seconds = None
                if pair.build:
                    build_seconds, _ = timed_runs(connection, sql_path(config, pair.build), params, runs)
                baseline_path, candidate_path = sql_path(config, pair.baseline), sql_path(config, pair.candidate)
                baseline_seconds, baseline_result = timed_runs(connection, baseline_path, params, runs)
                candidate_seconds, candidate_result = timed_runs(connection, candidate_path, params, runs)
                if not _same_result(baseline_result, candidate_result):
                    raise XsellError(f"{pair.name}: baseline and candidate queries returned different results")
                for variant, path in (("baseline", baseline_path), ("candidate", candidate_path)):
                    write_text(plans_dir / f"{pair.name}_{variant}.txt", explain_analyze(connection, path, params))
                result = PairResult(pair, baseline_seconds, candidate_seconds, build_seconds, len(baseline_result))
                results.append(result)
                logger.info(
                    "%s: baseline %.3fs, candidate %.3fs (%s faster, %.1fx)",
                    pair.name,
                    result.baseline_median,
                    result.candidate_median,
                    result.faster,
                    result.ratio,
                )
        finally:
            for statement in CLEANUP_SQL:
                connection.execute(statement)
    return results


def render_report(results: list[PairResult], spec: dict[str, str], manifest: dict | None, runs: int) -> str:
    lines = ["# Query performance", ""]
    lines.append(
        f"Generated by `python -m xsell performance` from the query pairs in `sql/performance/`. Each pair "
        f"compares a common baseline with a candidate optimisation. Each variant ran once to warm up, then "
        f"{runs} timed runs; tables show the median. Both variants of every pair returned identical results. "
        f"The faster column is what was measured, including when the baseline won. "
        f"EXPLAIN ANALYZE plans: `reports/performance/plans/`."
    )
    if manifest:
        share = manifest.get("sample_share")
        sample = "all customers" if share is None else f"a deterministic {share:.0%} customer sample"
        lines += ["", f"Data: {manifest['rows']:,} customer-month rows, {manifest['n_months']} months ({sample})."]
    lines += ["", "## Machine", "", markdown_table(pd.DataFrame({"": list(spec), "value": list(spec.values())}))]
    summary = pd.DataFrame(
        {
            "pair": [result.pair.title for result in results],
            "baseline (s)": [f"{result.baseline_median:.3f}" for result in results],
            "candidate (s)": [f"{result.candidate_median:.3f}" for result in results],
            "faster": [result.faster for result in results],
            "slower / faster": [f"{result.ratio:.1f}x" for result in results],
            "one-off build (s)": [f"{result.build_median:.3f}" if result.build_median else "" for result in results],
            "break-even (queries)": [
                f"{result.break_even_queries:.1f}" if result.break_even_queries else "" for result in results
            ],
        }
    )
    lines += ["", "## Results", "", markdown_table(summary)]
    for result in results:
        lines += [
            "",
            f"### {result.pair.title}",
            "",
            f"Files: baseline `sql/{result.pair.baseline}`, candidate `sql/{result.pair.candidate}`"
            + (f" (build: `sql/{result.pair.build}`)" if result.pair.build else "")
            + f". Result: {result.result_rows} row{'' if result.result_rows == 1 else 's'}, identical for both.",
            "",
            result.pair.why,
            "",
            f"Measured: the {result.faster} was faster by {result.ratio:.1f}x.",
            "",
            "Runs (s): baseline " + ", ".join(f"{value:.3f}" for value in result.baseline_seconds)
            + "; candidate " + ", ".join(f"{value:.3f}" for value in result.candidate_seconds) + ".",
        ]  # fmt: skip
    return "\n".join(lines)


def run_performance(config: Config) -> list[PairResult]:
    runs = config.analysis.timing_runs
    results = run_pairs(config, runs)
    timings = pd.DataFrame(
        [
            {"pair": result.pair.name, "variant": variant, "run": run, "seconds": seconds}
            for result in results
            for variant, values in (
                ("build", result.build_seconds or []),
                ("baseline", result.baseline_seconds),
                ("candidate", result.candidate_seconds),
            )
            for run, seconds in enumerate(values, start=1)
        ]
    )
    output_dir = config.paths.reports_dir / "performance"
    timings.to_csv(output_dir / "timings.csv", index=False, float_format="%.6f", lineterminator="\n")
    report = write_text(
        config.paths.reports_dir / "performance.md",
        render_report(results, machine_spec(config), read_manifest(config.paths.cache_dir), runs),
    )
    logger.info("wrote %s", report)
    return results
