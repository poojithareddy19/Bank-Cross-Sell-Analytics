"""Pipeline errors that the CLI reports as a single clear message."""

from __future__ import annotations


class XsellError(Exception):
    """Base class for expected, user-actionable pipeline failures."""


class DownloadError(XsellError):
    """The Kaggle download could not be completed."""


class ConversionError(XsellError):
    """The zip could not be converted into a valid Parquet cache."""
