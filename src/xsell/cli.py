"""Command line entry point: python -m xsell <stage>."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from xsell import __version__
from xsell.config import Config, ConfigError, load_config
from xsell.logging_utils import get_logger, setup_logging

logger = get_logger("xsell.cli")

# Pipeline stages in run order, with the milestone that implements each one.
STAGES: dict[str, tuple[str, int]] = {
    "fetch": ("download the Kaggle file and build the Parquet cache", 1),
    "warehouse": ("build the DuckDB staging table and star schema", 2),
    "quality": ("run data quality checks and write reports/data_quality.md", 2),
    "analyze": ("run the SQL analyses and write reports/analysis/", 3),
    "performance": ("time naive vs optimised query pairs", 5),
    "train": ("build features, train and evaluate the propensity model", 6),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xsell",
        description="SQL-first cross-sell analytics pipeline over the Santander product holdings data.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default=None, help="path to config.yaml (default: config/config.yaml)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    subparsers = parser.add_subparsers(dest="stage", metavar="<stage>", required=True)

    subparsers.add_parser("config", help="validate the configuration and print a summary")
    for name, (help_text, _) in STAGES.items():
        subparsers.add_parser(name, help=help_text)
    subparsers.add_parser("all", help="run every stage in order")
    return parser


def show_config(config: Config) -> None:
    logger.info("project root: %s", config.paths.root)
    logger.info("data dir: %s", config.paths.data_dir)
    logger.info("duckdb: memory_limit=%s threads=%d", config.duckdb.memory_limit, config.duckdb.threads)
    logger.info(
        "months: %s to %s (%d snapshots)", config.months.first, config.months.last, config.months.count
    )
    logger.info("products: %d, ladder steps: %d", len(config.products), len(config.ladder))
    logger.info(
        "target product: %s, sample share: %.0f%%",
        config.model.target_product,
        config.model.sample_share * 100,
    )
    for name, (start, end) in config.model.splits.items():
        logger.info("split %s: t from %s to %s", name, start, end)


def _not_implemented(stage: str) -> Callable[[Config], None]:
    def run(_: Config) -> None:
        raise NotImplementedError(f"stage '{stage}' is not implemented yet (milestone {STAGES[stage][1]})")

    return run


STAGE_RUNNERS: dict[str, Callable[[Config], None]] = {name: _not_implemented(name) for name in STAGES}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        logger.error("configuration error: %s", error)
        return 2

    if args.stage == "config":
        show_config(config)
        return 0

    stages = list(STAGES) if args.stage == "all" else [args.stage]
    for stage in stages:
        logger.info("stage %s: starting", stage)
        try:
            STAGE_RUNNERS[stage](config)
        except NotImplementedError as error:
            logger.error("%s", error)
            return 2
        logger.info("stage %s: done", stage)
    return 0
