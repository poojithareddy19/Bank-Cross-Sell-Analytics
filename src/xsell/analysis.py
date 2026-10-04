"""Run the SQL analysis layer, add Wilson intervals and write the reports.

Each query in sql/analysis writes reports/analysis/<name>.csv. Rates get 95%
Wilson score intervals. reports/customer_analytics.md summarises the results;
every number in it comes from those query results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.stats.proportion import proportion_confint

from xsell.config import Config
from xsell.data.convert import read_manifest
from xsell.db import connect, run_sql_file, sql_path
from xsell.errors import XsellError
from xsell.logging_utils import get_logger
from xsell.reporting import markdown_table, write_text

logger = get_logger("xsell.analysis")

COHORT_OFFSETS = (0, 1, 3, 6, 12)
TOP_N = 10


@dataclass(frozen=True)
class Analysis:
    name: str
    # (successes, trials, rate) columns that get Wilson intervals, if any
    rate: tuple[str, str, str] | None = None


ANALYSES = (
    Analysis("10_product_events"),
    Analysis("11_product_penetration", ("holders", "customers", "penetration")),
    Analysis("12_next_product_transitions"),
    Analysis("15_join_cohort_retention", ("retained", "cohort_size", "retention_rate")),
    Analysis("16_engagement", ("active", "customers", "engagement_rate")),
    Analysis("17_customer_value_proxy"),
)


def add_wilson_interval(frame: pd.DataFrame, successes: str, trials: str, rate: str) -> pd.DataFrame:
    """Insert <rate>_ci_lower and <rate>_ci_upper (95% Wilson) right after the rate column."""
    result = frame.copy()
    counts = result[successes].to_numpy(dtype=float)
    totals = result[trials].to_numpy(dtype=float)
    lower = np.full(len(result), np.nan)
    upper = np.full(len(result), np.nan)
    valid = totals > 0
    if valid.any():
        lower[valid], upper[valid] = proportion_confint(counts[valid], totals[valid], alpha=0.05, method="wilson")
    position = result.columns.get_loc(rate) + 1
    result.insert(position, f"{rate}_ci_lower", lower)
    result.insert(position + 1, f"{rate}_ci_upper", upper)
    return result


def run_analyses(config: Config) -> dict[str, pd.DataFrame]:
    """Run every analysis query (in order) and write one CSV per query."""
    if not config.paths.warehouse.is_file():
        raise XsellError(f"warehouse not found at {config.paths.warehouse}; run `python -m xsell warehouse` first")
    params = {"first_month": f"{config.months.first}-01"}
    output_dir = config.paths.reports_dir / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, pd.DataFrame] = {}
    with connect(config) as connection:
        for analysis in ANALYSES:
            frame = run_sql_file(connection, sql_path(config, f"analysis/{analysis.name}.sql"), params)
            if frame is None:
                raise XsellError(f"analysis/{analysis.name}.sql must end with a SELECT")
            if analysis.rate:
                frame = add_wilson_interval(frame, *analysis.rate)
            frame.to_csv(output_dir / f"{analysis.name}.csv", index=False, float_format="%.6f", lineterminator="\n")
            results[analysis.name] = frame
            logger.info("%s: %d rows", analysis.name, len(frame))
    return results


def percent(rate: float, lower: float | None = None, upper: float | None = None) -> str:
    if pd.isna(rate):
        return ""
    text = f"{rate:.1%}"
    if lower is not None and not pd.isna(lower):
        text += f" ({lower:.1%} to {upper:.1%})"
    return text


def _rate_column(frame: pd.DataFrame, rate: str) -> pd.Series:
    return pd.Series(
        [
            percent(value, lower, upper)
            for value, lower, upper in zip(
                frame[rate], frame[f"{rate}_ci_lower"], frame[f"{rate}_ci_upper"], strict=True
            )
        ],
        index=frame.index,
        dtype=object,
    )


def _events_section(events: pd.DataFrame) -> list[str]:
    totals = events[["adoptions", "attritions", "changes_across_gaps"]].sum()
    by_product = (
        events.groupby("product_name", as_index=False)[["adoptions", "attritions"]]
        .sum()
        .sort_values(["adoptions", "product_name"], ascending=[False, True])
        .head(TOP_N)
    )
    return [
        "## Product events",
        "",
        f"Adoption events: {int(totals['adoptions']):,}. Attrition events: {int(totals['attritions']):,}. "
        f"Flag changes across a month gap (not counted as events): {int(totals['changes_across_gaps']):,}.",
        "",
        f"Top {TOP_N} products by adoptions:",
        "",
        markdown_table(by_product),
    ]


def _penetration_section(penetration: pd.DataFrame) -> list[str]:
    latest_label = penetration["month_label"].max()
    latest = penetration[penetration["month_label"] == latest_label].sort_values(
        ["penetration", "product_name"], ascending=[False, True]
    )
    table = pd.DataFrame(
        {
            "product": latest["product_name"],
            "family": latest["family"],
            "holders": latest["holders"],
            "penetration (95% CI)": _rate_column(latest, "penetration"),
        }
    )
    customers = int(latest["customers"].iloc[0]) if len(latest) else 0
    return [
        f"## Product penetration, {latest_label}",
        "",
        f"Share of the {customers:,} customers present in {latest_label} who hold each product.",
        "",
        markdown_table(table),
    ]


def _transitions_section(transitions: pd.DataFrame) -> list[str]:
    lines = ["## Most common next-product transitions", ""]
    if transitions.empty:
        return lines + ["No customer adopted products in two different months."]
    top = transitions.head(TOP_N)
    table = pd.DataFrame(
        {
            "from": top["from_name"],
            "to": top["to_name"],
            "customers": top["customers"],
            "transitions": top["transitions"],
            "share of transitions from this product": [percent(value) for value in top["share_of_from"]],
            "median months between": top["median_months_between"],
        }
    )
    return lines + [
        "A transition A to B means the customer's most recent earlier adoption month included A.",
        "",
        markdown_table(table),
    ]


def _engagement_section(engagement: pd.DataFrame) -> list[str]:
    lines = ["## Engagement", ""]
    trend = engagement[engagement["dimension"] == "month"]
    if not trend.empty:
        first, last = trend.iloc[0], trend.iloc[-1]
        lines.append(
            f"Engagement rate (active / present): {percent(first['engagement_rate'])} in {first['month_label']}, "
            f"{percent(last['engagement_rate'], last['engagement_rate_ci_lower'], last['engagement_rate_ci_upper'])} "
            f"in {last['month_label']}."
        )
    for dimension, title in (("segment", "segment"), ("tenure_band", "tenure"), ("products_held", "products held")):
        part = engagement[engagement["dimension"] == dimension]
        if part.empty:
            continue
        table = pd.DataFrame(
            {
                title: part["group_value"],
                "customers": part["customers"],
                "active": part["active"],
                "engagement rate (95% CI)": _rate_column(part, "engagement_rate"),
            }
        )
        lines += ["", f"By {title}, {part['month_label'].iloc[0]}:", "", markdown_table(table)]
    return lines


def _cohort_section(cohorts: pd.DataFrame) -> list[str]:
    lines = ["## Join-month cohort retention", ""]
    if cohorts.empty:
        return lines + ["No customers joined on or after the first snapshot month."]
    lines.append(
        "Share of each join-month cohort present and holding at least one product k months after joining "
        "(blank: not yet observable)."
    )
    rows = []
    for join_month, group in cohorts.groupby("join_month", sort=True):
        by_offset = group.set_index("months_since_join")
        row = {"join month": join_month, "cohort size": int(group["cohort_size"].iloc[0])}
        for k in COHORT_OFFSETS:
            row[f"k={k}"] = percent(by_offset.loc[k, "retention_rate"]) if k in by_offset.index else ""
        rows.append(row)
    return lines + ["", markdown_table(pd.DataFrame(rows))]


def _value_section(value: pd.DataFrame) -> list[str]:
    def summarise(column: str) -> pd.DataFrame:
        grouped = value.groupby(column, as_index=False).agg(customers=("customers", "sum"))
        grouped["share"] = [percent(share) for share in grouped["customers"] / grouped["customers"].sum()]
        return grouped

    return [
        "## Customer value proxy",
        "",
        "The data has no revenue fields, so value is a proxy: breadth of relationship (product families held), "
        "income band and activity, measured in the latest month. Full breakdown in "
        "`reports/analysis/17_customer_value_proxy.csv`.",
        "",
        "By product families held:",
        "",
        markdown_table(summarise("families_held")),
        "",
        "By income band (quartiles of known income):",
        "",
        markdown_table(summarise("income_band")),
    ]


def render_summary(results: dict[str, pd.DataFrame], manifest: dict | None) -> str:
    lines = ["# Customer analytics", ""]
    lines.append(
        "Generated by `python -m xsell analyze` from `sql/analysis/`. Rates show 95% Wilson intervals. "
        "All comparisons are descriptive and correlational, not causal."
    )
    if manifest:
        share = manifest.get("sample_share")
        sample = "all customers" if share is None else f"a deterministic {share:.0%} customer sample"
        lines += [
            "",
            f"Data: {manifest['rows']:,} customer-month rows, {manifest['customers']:,} customers, "
            f"{manifest['n_months']} monthly snapshots ({sample}).",
        ]
    sections = (
        _events_section(results["10_product_events"]),
        _penetration_section(results["11_product_penetration"]),
        _transitions_section(results["12_next_product_transitions"]),
        _engagement_section(results["16_engagement"]),
        _cohort_section(results["15_join_cohort_retention"]),
        _value_section(results["17_customer_value_proxy"]),
    )
    for section in sections:
        lines += [""] + section
    return "\n".join(lines)


def run_analysis(config: Config) -> dict[str, pd.DataFrame]:
    results = run_analyses(config)
    report = write_text(
        config.paths.reports_dir / "customer_analytics.md",
        render_summary(results, read_manifest(config.paths.cache_dir)),
    )
    logger.info("wrote %s", report)
    return results
