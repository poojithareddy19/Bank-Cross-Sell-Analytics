"""Small helpers for writing Markdown reports."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd


def format_value(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA:
        return ""
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") if abs(value) < 1e6 else f"{value:,.0f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value).replace("|", "\\|")


def markdown_table(frame: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub Markdown table (no extra dependency).

    Identifier columns (ending in _id or _index) are printed without thousands separators.
    """
    columns = [str(column) for column in frame.columns]
    plain = [column.endswith(("_id", "_index")) for column in columns]
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in frame.itertuples(index=False):
        cells = [
            str(_python(value)) if is_plain and not pd.isna(value) else format_value(_python(value))
            for value, is_plain in zip(row, plain, strict=True)
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _python(value: object) -> object:
    # numpy scalars -> Python scalars so int/float formatting applies.
    return value.item() if hasattr(value, "item") and not isinstance(value, str) else value


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return path
