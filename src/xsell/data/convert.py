"""Stream train_ver2.csv out of its zip into month-partitioned Parquet.

The CSV is never extracted to disk. It is read in blocks with pyarrow, each
block is split by snapshot month, and rows are buffered per month and written
as zstd-compressed row groups. Peak memory stays at a few blocks plus one
row-group buffer per month, independent of file size.

Raw text is kept as text (padded ages, "   NA" incomes, mixed indrel_1mes
codes); cleaning happens in the SQL staging layer where it is visible and
tested. Only the customer id, the snapshot date and the 24 product flags are
typed here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
import zlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from xsell import __version__
from xsell.errors import ConversionError
from xsell.logging_utils import get_logger, peak_memory_mb
from xsell.sampling import in_sample

logger = get_logger("xsell.data.convert")

MANIFEST_SCHEMA_VERSION = 1
HOLDINGS_DIR = "holdings"
MANIFEST_NAME = "manifest.json"
# Measured on 1.9M and 7.6M synthetic rows: 4 MB blocks with 100k-row groups kept peak
# memory flat at about 400 MB, while 16 MB blocks with 250k-row groups grew past 1 GB.
BLOCK_SIZE_BYTES = 4 << 20
ROW_GROUP_ROWS = 100_000
_READ_CHUNK_BYTES = 8 << 20

# The 24 customer attribute columns of train_ver2.csv (the other 24 are product flags).
ATTRIBUTE_COLUMNS = (
    "fecha_dato", "ncodpers", "ind_empleado", "pais_residencia", "sexo", "age", "fecha_alta",
    "ind_nuevo", "antiguedad", "indrel", "ult_fec_cli_1t", "indrel_1mes", "tiprel_1mes", "indresi",
    "indext", "conyuemp", "canal_entrada", "indfall", "tipodom", "cod_prov", "nomprov",
    "ind_actividad_cliente", "renta", "segmento",
)  # fmt: skip


def column_types(product_codes: Sequence[str]) -> dict[str, pa.DataType]:
    types: dict[str, pa.DataType] = {column: pa.string() for column in ATTRIBUTE_COLUMNS}
    types["fecha_dato"] = pa.date32()
    types["ncodpers"] = pa.int64()
    types.update({code: pa.int8() for code in product_codes})
    return types


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(cache_dir: Path) -> Path:
    return cache_dir / MANIFEST_NAME


def holdings_path(cache_dir: Path) -> Path:
    return cache_dir / HOLDINGS_DIR


def read_manifest(cache_dir: Path) -> dict[str, Any] | None:
    path = manifest_path(cache_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def cache_is_current(cache_dir: Path, sample_share: float | None, zip_path: Path | None) -> tuple[bool, str]:
    """Whether the cache matches the manifest and the requested sample; also returns the reason."""
    manifest = read_manifest(cache_dir)
    if manifest is None:
        return False, "no valid manifest"
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return False, "manifest schema version changed"
    cached_share = manifest.get("sample_share")
    if cached_share != sample_share:
        return False, f"cached sample share {cached_share} differs from requested {sample_share}"
    holdings = holdings_path(cache_dir)
    for relative, size in manifest.get("files", {}).items():
        path = holdings / relative
        if not path.is_file() or path.stat().st_size != size:
            return False, f"cached file {relative} is missing or changed"
    if not manifest.get("files"):
        return False, "manifest lists no files"
    if zip_path is not None and sha256_file(zip_path) != manifest["source"]["zip_sha256"]:
        return False, f"{zip_path.name} differs from the zip the cache was built from"
    return True, "manifest matches"


def _open_member(archive: zipfile.ZipFile, member: str):
    names = archive.namelist()
    if member not in names:
        raise ConversionError(f"zip does not contain {member!r}; it contains {names}")
    return archive.open(member)


def count_csv_rows(zip_path: Path, member: str) -> int:
    """Count data rows by newlines, independently of the CSV parser."""
    newlines = 0
    last_byte = b""
    with zipfile.ZipFile(zip_path) as archive, _open_member(archive, member) as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            newlines += chunk.count(b"\n")
            last_byte = chunk[-1:]
    lines = newlines + (1 if last_byte not in (b"", b"\n") else 0)
    return max(lines - 1, 0)


class _MonthWriters:
    """Buffers rows per month and writes them as reasonably sized row groups."""

    def __init__(self, root: Path, schema: pa.Schema) -> None:
        self.root = root
        self.schema = schema
        self.buffers: dict[str, list[pa.Table]] = {}
        self.buffered_rows: dict[str, int] = {}
        self.writers: dict[str, pq.ParquetWriter] = {}
        self.rows: dict[str, int] = {}

    def add(self, month: str, table: pa.Table) -> None:
        self.buffers.setdefault(month, []).append(table)
        self.buffered_rows[month] = self.buffered_rows.get(month, 0) + table.num_rows
        self.rows[month] = self.rows.get(month, 0) + table.num_rows
        if self.buffered_rows[month] >= ROW_GROUP_ROWS:
            self._flush(month)

    def _flush(self, month: str) -> None:
        if not self.buffers.get(month):
            return
        if month not in self.writers:
            folder = self.root / f"month={month}"
            folder.mkdir(parents=True, exist_ok=True)
            self.writers[month] = pq.ParquetWriter(folder / "part-0.parquet", self.schema, compression="zstd")
        self.writers[month].write_table(pa.concat_tables(self.buffers[month]), row_group_size=ROW_GROUP_ROWS)
        self.buffers[month] = []
        self.buffered_rows[month] = 0

    def close(self) -> None:
        for month in list(self.buffers):
            self._flush(month)
        self.abort()

    def abort(self) -> None:
        """Release file handles without writing buffered rows (used on failure)."""
        for writer in self.writers.values():
            writer.close()
        self.writers.clear()


def _split_by_month(table: pa.Table, writers: _MonthWriters) -> None:
    dates = table.column("fecha_dato")
    for date in pc.unique(dates).to_pylist():
        writers.add(date.strftime("%Y-%m"), table.filter(pc.equal(dates, pa.scalar(date, pa.date32()))))


def _check_header(names: Sequence[str], expected: Sequence[str]) -> None:
    missing = [column for column in expected if column not in names]
    extra = [column for column in names if column not in expected]
    if missing or extra:
        raise ConversionError(f"unexpected CSV header: missing columns {missing}, unexpected columns {extra}")


def _stream_to_parquet(
    zip_path: Path,
    csv_member: str,
    product_codes: Sequence[str],
    output_dir: Path,
    sample_share: float | None,
    block_size: int,
) -> tuple[int, dict[str, int]]:
    types = column_types(product_codes)
    expected_columns = list(types)
    rows_read = 0
    writers: _MonthWriters | None = None
    try:
        with zipfile.ZipFile(zip_path) as archive, _open_member(archive, csv_member) as handle:
            reader = pv.open_csv(
                handle,
                read_options=pv.ReadOptions(block_size=block_size),
                convert_options=pv.ConvertOptions(column_types=types, strings_can_be_null=True),
            )
            _check_header(reader.schema.names, expected_columns)
            schema = pa.schema([pa.field(name, types[name]) for name in expected_columns])
            writers = _MonthWriters(output_dir, schema)
            for batch in reader:
                table = pa.Table.from_batches([batch]).select(expected_columns)
                rows_read += table.num_rows
                if table.column("fecha_dato").null_count or table.column("ncodpers").null_count:
                    raise ConversionError("found rows without fecha_dato or ncodpers; the file looks damaged")
                if sample_share is not None:
                    ids = table.column("ncodpers").to_numpy()
                    table = table.filter(pa.array(in_sample(ids, sample_share)))
                _split_by_month(table, writers)
            writers.close()
    except BaseException:
        if writers is not None:
            writers.abort()
        raise
    return rows_read, dict(sorted(writers.rows.items()))


def _stream_with_clear_errors(
    zip_path: Path,
    csv_member: str,
    product_codes: Sequence[str],
    output_dir: Path,
    sample_share: float | None,
    block_size: int,
) -> tuple[int, dict[str, int]]:
    try:
        return _stream_to_parquet(zip_path, csv_member, product_codes, output_dir, sample_share, block_size)
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise ConversionError(
            f"{zip_path.name} is not a valid zip ({error}); delete it and run fetch again"
        ) from error
    except pa.ArrowInvalid as error:
        raise ConversionError(f"could not parse {csv_member}: {error}") from error


def convert_zip_to_parquet(
    zip_path: Path,
    cache_dir: Path,
    csv_member: str,
    product_codes: Sequence[str],
    expected_months: Sequence[str] | None = None,
    sample_share: float | None = None,
    block_size: int = BLOCK_SIZE_BYTES,
) -> dict[str, Any]:
    """Convert, validate and write the manifest. Returns the manifest."""
    started = time.perf_counter()
    if not zipfile.is_zipfile(zip_path):
        raise ConversionError(f"{zip_path} is not a valid zip (corrupt or incomplete download); delete it")

    logger.info("hashing %s", zip_path.name)
    zip_sha256 = sha256_file(zip_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = cache_dir / f"{HOLDINGS_DIR}.tmp"
    shutil.rmtree(staging_dir, ignore_errors=True)

    logger.info("streaming %s from %s into Parquet", csv_member, zip_path.name)
    try:
        rows_read, rows_per_month = _stream_with_clear_errors(
            zip_path, csv_member, product_codes, staging_dir, sample_share, block_size
        )
        csv_rows = count_csv_rows(zip_path, csv_member)
        if csv_rows != rows_read:
            raise ConversionError(
                f"the CSV has {csv_rows} data lines but the parser read {rows_read} rows; "
                "the file may contain embedded line breaks or be truncated"
            )
        if expected_months is not None and list(rows_per_month) != list(expected_months):
            raise ConversionError(
                f"expected snapshot months {list(expected_months)}, found {list(rows_per_month)}"
            )
        rows_written, customers = _validate_parquet(staging_dir)
        if rows_written != sum(rows_per_month.values()):
            raise ConversionError(
                f"Parquet holds {rows_written} rows but {sum(rows_per_month.values())} were written"
            )
        if sample_share is None and rows_written != csv_rows:
            raise ConversionError(f"Parquet holds {rows_written} rows but the CSV has {csv_rows}")

        final_dir = holdings_path(cache_dir)
        shutil.rmtree(final_dir, ignore_errors=True)
        staging_dir.rename(final_dir)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    parquet_files = sorted(final_dir.rglob("*.parquet"))
    files = {path.relative_to(final_dir).as_posix(): path.stat().st_size for path in parquet_files}
    peak = peak_memory_mb()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "xsell_version": __version__,
        "source": {
            "zip_name": zip_path.name,
            "zip_bytes": zip_path.stat().st_size,
            "zip_sha256": zip_sha256,
            "csv_member": csv_member,
            "csv_rows": csv_rows,
        },
        "sample_share": sample_share,
        "rows": rows_written,
        "customers": customers,
        "n_months": len(rows_per_month),
        "rows_per_month": rows_per_month,
        "columns": len(column_types(product_codes)),
        "files": files,
        "parquet_bytes": sum(files.values()),
        "seconds": round(time.perf_counter() - started, 1),
        "peak_memory_mb": round(peak, 1) if peak is not None else None,
    }
    temporary = manifest_path(cache_dir).with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path(cache_dir))
    logger.info(
        "wrote %d rows, %d customers, %d months, %.1f MB Parquet in %.1fs (peak memory %s MB)",
        rows_written,
        customers,
        len(rows_per_month),
        manifest["parquet_bytes"] / 1e6,
        manifest["seconds"],
        manifest["peak_memory_mb"],
    )
    return manifest


def _validate_parquet(holdings: Path) -> tuple[int, int]:
    """Read the written files back: total rows and distinct customers."""
    dataset = ds.dataset(holdings, format="parquet", partitioning="hive")
    rows = dataset.count_rows()
    customers = pc.count_distinct(dataset.to_table(columns=["ncodpers"]).column("ncodpers")).as_py()
    return rows, customers
