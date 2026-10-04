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
from xsell.db import base_params, connect, require_warehouse, run_sql_file, sql_path
from xsell.errors import XsellError
from xsell.logging_utils import get_logger
from xsell.reporting import markdown_table, write_text

logger = get_logger("xsell.analysis")

COHORT_OFFSETS = (0, 1, 3, 6, 12)
TOP_N = 10
PROPOSAL_MAX_STEPS = 6


@dataclass(frozen=True)
class Analysis:
    name: str
    # (successes, trials, rate) columns that get Wilson intervals, if any
    rate: tuple[str, str, str] | None = None
    # Skipped (and its old CSV removed) while products.yaml has no confirmed ladder
    needs_ladder: bool = False


ANALYSES = (
    Analysis("10_product_events"),
    Analysis("11_product_penetration", ("holders", "customers", "penetration")),
    Analysis("12_next_product_transitions"),
    Analysis("13_ladder_funnel", ("reached", "previous_reached", "conversion_rate"), needs_ladder=True),
    Analysis("14_time_to_next_product"),
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
    require_warehouse(config)
    params = {**base_params(config), "top_channels": config.analysis.top_channels}
    output_dir = config.paths.reports_dir / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, pd.DataFrame] = {}
    with connect(config) as connection:
        for analysis in ANALYSES:
            if analysis.needs_ladder and not config.ladder:
                (output_dir / f"{analysis.name}.csv").unlink(missing_ok=True)
                logger.info("%s: skipped, no confirmed ladder in products.yaml", analysis.name)
                continue
            frame = run_sql_file(connection, sql_path(config, f"analysis/{analysis.name}.sql"), params)
            if frame is None:
                raise XsellError(f"analysis/{analysis.name}.sql must end with a SELECT")
            if analysis.rate:
                frame = add_wilson_interval(frame, *analysis.rate)
            frame.to_csv(output_dir / f"{analysis.name}.csv", index=False, float_format="%.6f", lineterminator="\n")
            results[analysis.name] = frame
            logger.info("%s: %d rows", analysis.name, len(frame))
    return results


def propose_ladder(
    penetration: pd.DataFrame, transitions: pd.DataFrame, max_steps: int = PROPOSAL_MAX_STEPS
) -> list[dict]:
    """Greedy ladder proposal from the data, for a human to review.

    Step 1 is the most held product in the latest month. Each next step is the product
    most often adopted right after any product already on the ladder.
    """
    latest = penetration[penetration["month_label"] == penetration["month_label"].max()]
    if latest.empty:
        return []
    start = latest.sort_values(["penetration", "product_code"], ascending=[False, True]).iloc[0]
    names = dict(zip(penetration["product_code"], penetration["product_name"], strict=True))
    steps = [
        {
            "product_code": start["product_code"],
            "product_name": start["product_name"],
            "evidence": f"most held product in {start['month_label']} ({percent(start['penetration'])} of customers)",
        }
    ]
    on_ladder = {start["product_code"]}
    while len(steps) < max_steps:
        candidates = transitions[
            transitions["from_product"].isin(on_ladder) & ~transitions["to_product"].isin(on_ladder)
        ]
        if candidates.empty:
            break
        totals = candidates.groupby("to_product")["transitions"].sum().reset_index()
        best = totals.sort_values(["transitions", "to_product"], ascending=[False, True]).iloc[0]
        steps.append(
            {
                "product_code": best["to_product"],
                "product_name": names[best["to_product"]],
                "evidence": f"{int(best['transitions']):,} transitions from products already on the ladder",
            }
        )
        on_ladder.add(best["to_product"])
    return steps


def render_ladder_proposal(proposal: list[dict], transitions: pd.DataFrame, config: Config) -> str:
    lines = ["# Product ladder proposal", ""]
    lines.append(
        "Generated by `python -m xsell analyze` from `reports/analysis/11_product_penetration.csv` and "
        "`reports/analysis/12_next_product_transitions.csv`. This is a starting point for review, not a "
        "result: the funnel uses only the ladder confirmed in `config/products.yaml`."
    )
    lines += ["", "## Greedy proposal", ""]
    if proposal:
        lines.append(markdown_table(pd.DataFrame(proposal)))
    else:
        lines.append("No data to propose from.")
    lines += ["", f"## Top {TOP_N} transitions", ""]
    if transitions.empty:
        lines.append("No customer adopted products in two different months.")
    else:
        top = transitions.head(TOP_N)[["from_name", "to_name", "customers", "transitions", "median_months_between"]]
        lines.append(markdown_table(top))
    lines += ["", "## Confirmed ladder", ""]
    if config.ladder:
        for number, step in enumerate(config.ladder, start=1):
            lines.append(f"{number}. {step.name}: {', '.join(step.products)}")
    else:
        lines.append("None yet. The ladder funnel is skipped until one is confirmed in `config/products.yaml`.")
    return "\n".join(lines)


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
    counts = ["adoptions", "first_adoptions", "repeat_adoptions", "attritions"]
    totals = events[[*counts, "changes_across_gaps"]].sum()
    by_product = (
        events.groupby("product_name", as_index=False)[counts]
        .sum()
        .sort_values(["first_adoptions", "product_name"], ascending=[False, True])
        .head(TOP_N)
    )
    repeat_share = totals["repeat_adoptions"] / totals["adoptions"] if totals["adoptions"] else 0.0
    return [
        "## Product events",
        "",
        f"Adoption events: {int(totals['adoptions']):,}, of which {int(totals['first_adoptions']):,} first-time and "
        f"{int(totals['repeat_adoptions']):,} repeat ({percent(repeat_share)}: the customer had dropped the same "
        f"product earlier in the window). Attrition events: {int(totals['attritions']):,}. Flag changes across a "
        f"month gap (not counted as events): {int(totals['changes_across_gaps']):,}.",
        "",
        "Journeys (transitions, time to next product) use first-time adoptions only.",
        "",
        f"Top {TOP_N} products by first-time adoptions:",
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
        "A transition A to B: B was adopted for the first time, and the customer's most recent earlier "
        "first-time adoption month included A. Repeat adoptions and A to A are excluded.",
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


def _funnel_section(funnel: pd.DataFrame | None) -> list[str]:
    lines = ["## Product ladder funnel", ""]
    if funnel is None:
        return lines + ["Skipped: no ladder confirmed yet (see `reports/ladder_proposal.md`)."]
    overall = funnel[funnel["dimension"] == "all"]
    table = pd.DataFrame(
        {
            "step": overall["step"],
            "ladder step": overall["step_name"],
            "reached": overall["reached"],
            "conversion from previous step (95% CI)": _rate_column(overall, "conversion_rate"),
            "share of all customers": [percent(value) for value in overall["reached_share"]],
        }
    )
    return lines + [
        "A customer reaches a step by holding one of its products in or after the month they reached the "
        "previous step. Breakdowns by segment and channel are in `reports/analysis/13_ladder_funnel.csv`.",
        "",
        markdown_table(table),
    ]


def _time_to_next_section(times: pd.DataFrame) -> list[str]:
    lines = ["## Time to next product", ""]
    if times.empty:
        return lines + ["No adoption events observed."]
    adopters = int(times["customers"].sum())
    observed = int(times["customers_observed"].iloc[0])
    lines.append(
        f"{adopters:,} of {observed:,} customers ({percent(adopters / observed)}) adopted at least one product "
        f"for the first time during the window. Median months from first observed snapshot to that adoption: "
        f"{times['median_months'].iloc[0]:g}. Customers without an adoption are not in the distribution."
    )
    table = pd.DataFrame(
        {
            "months to first adoption": times["months_to_first_adoption"],
            "customers": times["customers"],
            "share of adopters": [percent(value) for value in times["share_of_adopters"]],
            "cumulative share": [percent(value) for value in times["cumulative_share"]],
        }
    )
    return lines + ["", markdown_table(table)]


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
        _funnel_section(results.get("13_ladder_funnel")),
        _time_to_next_section(results["14_time_to_next_product"]),
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
    transitions = results["12_next_product_transitions"]
    proposal = propose_ladder(results["11_product_penetration"], transitions)
    proposal_path = write_text(
        config.paths.reports_dir / "ladder_proposal.md", render_ladder_proposal(proposal, transitions, config)
    )
    logger.info("wrote %s", proposal_path)
    return results
