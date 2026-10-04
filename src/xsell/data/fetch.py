"""The fetch stage: download if needed, convert, validate, tidy up."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from xsell.config import Config
from xsell.data.convert import cache_is_current, convert_zip_to_parquet, read_manifest
from xsell.data.download import download_competition_file
from xsell.logging_utils import get_logger

logger = get_logger("xsell.data.fetch")


def run_fetch(
    config: Config,
    sample_share: float | None = None,
    keep_zip: bool = False,
    force: bool = False,
    downloader: Callable[[str, str, Path], Path] = download_competition_file,
) -> dict[str, Any]:
    """Make sure data/cache holds a validated Parquet copy of the Kaggle file."""
    if sample_share is not None and not 0 < sample_share <= 1:
        raise ValueError(f"--sample-customers must be in (0, 1], got {sample_share}")
    cache_dir = config.paths.cache_dir
    zip_path = config.paths.raw_dir / config.kaggle.file

    if not force:
        current, reason = cache_is_current(cache_dir, sample_share, zip_path if zip_path.is_file() else None)
        if current:
            logger.info("Parquet cache in %s is current (%s); nothing to do", cache_dir, reason)
            return read_manifest(cache_dir) or {}
        logger.info("Parquet cache needs (re)building: %s", reason)

    if zip_path.is_file():
        logger.info("using existing %s", zip_path)
    else:
        downloader(config.kaggle.competition, config.kaggle.file, config.paths.raw_dir)

    manifest = convert_zip_to_parquet(
        zip_path,
        cache_dir,
        config.kaggle.csv_member,
        config.product_codes,
        expected_months=config.months.labels,
        sample_share=sample_share,
    )
    if keep_zip:
        logger.info("keeping %s (--keep-zip)", zip_path)
    else:
        zip_path.unlink()
        logger.info("deleted %s after a successful conversion", zip_path)
    return manifest
