"""Command line entry point: python -m xsell <stage>."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from xsell import __version__
from xsell.config import Config, ConfigError, load_config
from xsell.errors import XsellError
from xsell.logging_utils import get_logger, setup_logging
from xsell.pipeline import STAGES, FetchOptions, run_all, run_stages

logger = get_logger("xsell.cli")


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
    for stage in STAGES:
        subparsers.add_parser(
            stage.name, help=stage.description, parents=[fetch_options] if stage.name == "fetch" else []
        )
    subparsers.add_parser(
        "all", help="run every stage in order and write reports/pipeline_run.md", parents=[fetch_options]
    )
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

    options = FetchOptions(
        sample_customers=getattr(args, "sample_customers", None),
        keep_zip=getattr(args, "keep_zip", False),
        force=getattr(args, "force", False),
    )
    try:
        if args.stage == "all":
            run_all(config, options)
        else:
            run_stages(config, (args.stage,), options)
    except XsellError as error:
        logger.error("stage failed: %s", error)
        return 1
    return 0
