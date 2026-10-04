"""One logging setup for the CLI and library code."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once; later calls only change the level."""
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unknown log level {level!r}")
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(handler)
    root.setLevel(numeric_level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
