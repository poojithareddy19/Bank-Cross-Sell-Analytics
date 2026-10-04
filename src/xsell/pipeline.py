"""Pipeline stages in run order, and the run record written by `python -m xsell all`."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from xsell.analysis import run_analysis
from xsell.config import Config
from xsell.data.convert import read_manifest
from xsell.data.fetch import run_fetch
from xsell.logging_utils import get_logger, peak_memory_mb
from xsell.machine import machine_spec
from xsell.performance import run_performance
from xsell.quality import run_quality
from xsell.reporting import markdown_table, write_text
from xsell.train import run_training
from xsell.warehouse import build_warehouse

logger = get_logger("xsell.pipeline")


@dataclass(frozen=True)
class FetchOptions:
    sample_customers: float | None = None
    keep_zip: bool = False
    force: bool = False


@dataclass(frozen=True)
class Stage:
    name: str
    description: str
    run: Callable[[Config, FetchOptions], object]


STAGES = (
    Stage(
        "fetch",
        "download the Kaggle file and build the Parquet cache",
        lambda config, options: run_fetch(
            config, sample_share=options.sample_customers, keep_zip=options.keep_zip, force=options.force
        ),
    ),
    Stage("warehouse", "build the DuckDB staging table and star schema", lambda c, o: build_warehouse(c)),
    Stage("quality", "run data quality checks and write reports/data_quality.md", lambda c, o: run_quality(c)),
    Stage("analyze", "run the SQL analyses and write reports/analysis/", lambda c, o: run_analysis(c)),
    Stage("performance", "time baseline vs candidate query pairs", lambda c, o: run_performance(c)),
    Stage("train", "build features, train and evaluate the propensity model", lambda c, o: run_training(c)),
)
STAGE_NAMES = tuple(stage.name for stage in STAGES)


def run_stages(config: Config, names: tuple[str, ...], options: FetchOptions) -> dict[str, float]:
    """Run the named stages in pipeline order; returns seconds per stage. Errors propagate."""
    durations: dict[str, float] = {}
    for stage in STAGES:
        if stage.name not in names:
            continue
        logger.info("stage %s: starting", stage.name)
        started = time.perf_counter()
        stage.run(config, options)
        durations[stage.name] = time.perf_counter() - started
        logger.info("stage %s: done in %.1fs", stage.name, durations[stage.name])
    return durations


def render_run_report(durations: dict[str, float], config: Config, peak_mb: float | None) -> str:
    spec = machine_spec(config)
    manifest = read_manifest(config.paths.cache_dir)
    lines = [
        "# Pipeline run",
        "",
        "Written by `python -m xsell all`. Stage times are wall-clock seconds on the machine below; a stage "
        "that found its outputs current (for example fetch with a valid cache) is fast.",
    ]
    if manifest:
        share = manifest.get("sample_share")
        sample = "all customers" if share is None else f"a deterministic {share:.0%} customer sample"
        lines += [
            "",
            f"Data: {manifest['rows']:,} customer-month rows, {manifest['customers']:,} customers ({sample}).",
        ]
    stages = pd.DataFrame(
        {
            "stage": [*durations, "total"],
            "seconds": [f"{value:.1f}" for value in [*durations.values(), sum(durations.values())]],
        }
    )
    peak = f"{peak_mb:,.0f} MB" if peak_mb is not None else "unknown"
    lines += [
        "",
        "## Stages",
        "",
        markdown_table(stages),
        "",
        f"Peak memory of the Python process (DuckDB runs inside it): {peak}.",
        "",
        "## Machine",
        "",
        markdown_table(pd.DataFrame({"": list(spec), "value": list(spec.values())})),
    ]
    return "\n".join(lines)


def run_all(config: Config, options: FetchOptions) -> dict[str, float]:
    durations = run_stages(config, STAGE_NAMES, options)
    report = write_text(
        config.paths.reports_dir / "pipeline_run.md", render_run_report(durations, config, peak_memory_mb())
    )
    logger.info("full pipeline finished in %.1fs; wrote %s", sum(durations.values()), report)
    return durations
