"""Command line entry point: python -m xsell <stage>."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from xsell import __version__
from xsell.analysis import run_analysis
from xsell.config import Config, ConfigError, load_config
from xsell.data.fetch import run_fetch
from xsell.errors import XsellError
from xsell.logging_utils import get_logger, setup_logging
from xsell.performance import run_performance
from xsell.quality import run_quality
from xsell.train import run_training
from xsell.warehouse import build_warehouse

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


def _share(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not 0 < value <= 1:
        raise argparse.ArgumentTypeError(f"share must be in (0, 1], got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xsell",
        description="SQL-first cross-sell analytics pipeline over the Santander product holdings data.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default=None, help="path to config.yaml (default: config/config.yaml)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    subparsers = parser.add_subparsers(dest="stage", metavar="<stage>", required=True)

    fetch_options = argparse.ArgumentParser(add_help=False)
    fetch_options.add_argument(
        "--sample-customers",
        type=_share,
        default=None,
        metavar="SHARE",
        help="keep a deterministic share of customers, e.g. 0.1 for a fast dev subset",
    )
    fetch_options.add_argument("--keep-zip", action="store_true", help="keep the zip after conversion")
    fetch_options.add_argument("--force", action="store_true", help="rebuild the cache even if it is current")

    subparsers.add_parser("config", help="validate the configuration and print a summary")
    for name, (help_text, _) in STAGES.items():
        subparsers.add_parser(name, help=help_text, parents=[fetch_options] if name == "fetch" else [])
    subparsers.add_parser("all", help="run every stage in order", parents=[fetch_options])
    return parser


def show_config(config: Config) -> None:
    logger.info("project root: %s", config.paths.root)
    logger.info("data dir: %s", config.paths.data_dir)
    logger.info("duckdb: memory_limit=%s threads=%d", config.duckdb.memory_limit, config.duckdb.threads)
    logger.info("months: %s to %s (%d snapshots)", config.months.first, config.months.last, config.months.count)
    logger.info("products: %d, ladder steps: %d", len(config.products), len(config.ladder))
    logger.info(
        "target product: %s, sample share: %.0f%%",
        config.model.target_product,
        config.model.sample_share * 100,
    )
    for name, (start, end) in config.model.splits.items():
        logger.info("split %s: t from %s to %s", name, start, end)


def run_fetch_stage(config: Config, args: argparse.Namespace) -> None:
    run_fetch(config, sample_share=args.sample_customers, keep_zip=args.keep_zip, force=args.force)


def _not_implemented(stage: str) -> StageRunner:
    def run(config: Config, args: argparse.Namespace) -> None:
        raise NotImplementedError(f"stage '{stage}' is not implemented yet (milestone {STAGES[stage][1]})")

    return run


StageRunner = Callable[[Config, argparse.Namespace], None]
STAGE_RUNNERS: dict[str, StageRunner] = {name: _not_implemented(name) for name in STAGES}
STAGE_RUNNERS["fetch"] = run_fetch_stage
STAGE_RUNNERS["warehouse"] = lambda config, args: build_warehouse(config)
STAGE_RUNNERS["quality"] = lambda config, args: run_quality(config)
STAGE_RUNNERS["analyze"] = lambda config, args: run_analysis(config)
STAGE_RUNNERS["performance"] = lambda config, args: run_performance(config)
STAGE_RUNNERS["train"] = lambda config, args: run_training(config)


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
            STAGE_RUNNERS[stage](config, args)
        except NotImplementedError as error:
            logger.error("%s", error)
            return 2
        except XsellError as error:
            logger.error("stage %s failed: %s", stage, error)
            return 1
        logger.info("stage %s: done", stage)
    return 0
