"""DuckDB connections and running .sql files with named parameters."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from xsell.config import Config
from xsell.errors import XsellError
from xsell.logging_utils import get_logger

logger = get_logger("xsell.db")


class SqlError(XsellError):
    """A SQL file failed or was given the wrong parameters."""


def connect(config: Config, database: Path | str | None = None, read_only: bool = False):
    """Open the warehouse (or another database) with the configured memory and thread limits."""
    target = config.paths.warehouse if database is None else database
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = duckdb.connect(str(target), read_only=read_only)
    except duckdb.IOException as error:
        raise SqlError(
            f"could not open {target}: {error}. Close other programs using the file (another "
            "python -m xsell run, a notebook or a DuckDB shell) and retry."
        ) from error
    spill_dir = config.paths.data_dir / "duckdb_tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    connection.execute(f"SET memory_limit = '{config.duckdb.memory_limit}'")
    connection.execute(f"SET threads = {int(config.duckdb.threads)}")
    connection.execute(f"SET temp_directory = '{spill_dir.as_posix()}'")
    # Row order inside a table is never relied on; dropping it lets DuckDB stream with less memory.
    connection.execute("SET preserve_insertion_order = false")
    return connection


def sql_path(config: Config, relative: str) -> Path:
    path = config.paths.sql_dir / relative
    if not path.is_file():
        raise SqlError(f"SQL file not found: {path}")
    return path


def run_sql_file(
    connection,
    path: Path,
    params: Mapping[str, Any] | None = None,
) -> pd.DataFrame | None:
    """Run every statement in a file; each statement receives only the parameters it uses.

    Returns the result of the last statement as a DataFrame when it produces rows.
    """
    params = dict(params or {})
    started = time.perf_counter()
    try:
        statements = connection.extract_statements(path.read_text(encoding="utf-8"))
    except duckdb.Error as error:
        raise SqlError(f"{path.name}: could not parse SQL: {error}") from error

    result = None
    for statement in statements:
        needed = set(statement.named_parameters)
        missing = needed - params.keys()
        if missing:
            raise SqlError(f"{path.name}: missing SQL parameter(s) {sorted(missing)}")
        try:
            relation = connection.execute(statement, {name: params[name] for name in needed})
            result = relation.df() if relation.description else None
        except duckdb.Error as error:
            raise SqlError(f"{path.name}: {error}") from error
    logger.debug("ran %s in %.2fs", path.name, time.perf_counter() - started)
    return result


def table_count(connection, table: str) -> int:
    # Table names come from our own code, never from user input.
    return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
