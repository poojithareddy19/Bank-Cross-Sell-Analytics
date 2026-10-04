"""Load and validate config/config.yaml and config/products.yaml.

Every path, month, product code, threshold and assumption used by the pipeline
comes from here. Problems are reported as ConfigError with the offending key.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config") / "config.yaml"
EXPECTED_PRODUCT_COUNT = 24
FAMILIES = frozenset({"core", "savings", "investment", "lending", "card", "digital", "services", "other"})
SPLIT_NAMES = ("train", "validation", "test")
# Features look back three months (change in products held over the last 3 months).
FEATURE_LOOKBACK_MONTHS = 3

_PRODUCT_CODE = re.compile(r"^ind_[a-z_]+_ult1$")
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_MEMORY_LIMIT = re.compile(r"^\d+(\.\d+)?\s*(KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE)


class ConfigError(ValueError):
    """The configuration is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    family: str


@dataclass(frozen=True)
class Paths:
    root: Path
    data_dir: Path
    sql_dir: Path
    reports_dir: Path
    artifacts_dir: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def warehouse(self) -> Path:
        return self.data_dir / "warehouse.duckdb"


@dataclass(frozen=True)
class KaggleSettings:
    competition: str
    file: str
    csv_member: str


@dataclass(frozen=True)
class DuckDBSettings:
    memory_limit: str
    threads: int


@dataclass(frozen=True)
class MonthRange:
    first: str
    last: str

    def index(self, month: str) -> int:
        """Months since the first snapshot (first month is 0)."""
        return month_offset(self.first, month)

    @property
    def count(self) -> int:
        return self.index(self.last) + 1

    @property
    def labels(self) -> list[str]:
        """Every snapshot month from first to last as YYYY-MM."""
        year, month = _parse_month(self.first, "months.first")
        labels = []
        for offset in range(self.count):
            total = month - 1 + offset
            labels.append(f"{year + total // 12}-{total % 12 + 1:02d}")
        return labels


@dataclass(frozen=True)
class ModelSettings:
    target_product: str
    min_base_rate: float
    sample_share: float
    seed: int
    bootstrap_resamples: int
    splits: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class Assumptions:
    margin_per_adopter_eur: float
    contact_cost_eur: float


@dataclass(frozen=True)
class Config:
    paths: Paths
    kaggle: KaggleSettings
    duckdb: DuckDBSettings
    months: MonthRange
    model: ModelSettings
    assumptions: Assumptions
    products: tuple[Product, ...]
    ladder: tuple[str, ...]

    @property
    def product_codes(self) -> tuple[str, ...]:
        return tuple(product.code for product in self.products)


def month_offset(start: str, end: str) -> int:
    """Whole months from start to end, both given as YYYY-MM."""
    start_year, start_month = _parse_month(start, "month")
    end_year, end_month = _parse_month(end, "month")
    return (end_year - start_year) * 12 + (end_month - start_month)


def load_config(
    config_path: Path | str | None = None,
    products_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Read both YAML files, apply XSELL_* environment overrides and validate."""
    config_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    products_path = Path(products_path) if products_path is not None else config_path.parent / "products.yaml"
    root = config_path.resolve().parent.parent
    return build_config(
        _read_yaml(config_path),
        _read_yaml(products_path),
        root=root,
        env=os.environ if env is None else env,
    )


def build_config(
    raw: Mapping[str, Any],
    raw_products: Mapping[str, Any],
    root: Path,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Validate already-parsed YAML content. Relative paths resolve against root."""
    env = env or {}
    products = _build_products(raw_products)
    codes = {product.code for product in products}

    ladder = tuple(str(code) for code in (raw_products.get("ladder") or []))
    unknown = [code for code in ladder if code not in codes]
    if unknown:
        raise ConfigError(f"products.yaml: ladder contains unknown product code(s): {', '.join(unknown)}")
    if len(set(ladder)) != len(ladder):
        raise ConfigError("products.yaml: ladder lists the same product more than once")

    paths_raw = _section(raw, "paths")
    data_dir = env.get("XSELL_DATA_DIR") or _require(paths_raw, "data_dir", "paths")
    paths = Paths(
        root=root,
        data_dir=_resolve(root, data_dir),
        sql_dir=_resolve(root, _require(paths_raw, "sql_dir", "paths")),
        reports_dir=_resolve(root, _require(paths_raw, "reports_dir", "paths")),
        artifacts_dir=_resolve(root, _require(paths_raw, "artifacts_dir", "paths")),
    )

    kaggle_raw = _section(raw, "kaggle")
    kaggle = KaggleSettings(
        competition=str(_require(kaggle_raw, "competition", "kaggle")),
        file=str(_require(kaggle_raw, "file", "kaggle")),
        csv_member=str(_require(kaggle_raw, "csv_member", "kaggle")),
    )

    duckdb_raw = _section(raw, "duckdb")
    memory_limit = str(env.get("XSELL_DUCKDB_MEMORY_LIMIT") or _require(duckdb_raw, "memory_limit", "duckdb"))
    if not _MEMORY_LIMIT.match(memory_limit.strip()):
        raise ConfigError(f"duckdb.memory_limit must look like '4GB' or '512MB', got {memory_limit!r}")
    threads = _as_int(env.get("XSELL_THREADS") or _require(duckdb_raw, "threads", "duckdb"), "duckdb.threads")
    if threads < 1:
        raise ConfigError(f"duckdb.threads must be at least 1, got {threads}")
    duckdb_settings = DuckDBSettings(memory_limit=memory_limit.strip(), threads=threads)

    months_raw = _section(raw, "months")
    months = MonthRange(
        first=_month(_require(months_raw, "first", "months"), "months.first"),
        last=_month(_require(months_raw, "last", "months"), "months.last"),
    )
    if months.count < 2:
        raise ConfigError("months.last must be after months.first")

    model = _build_model(_section(raw, "model"), months, codes)

    assumptions_raw = _section(raw, "assumptions")
    assumptions = Assumptions(
        margin_per_adopter_eur=_as_float(
            _require(assumptions_raw, "margin_per_adopter_eur", "assumptions"),
            "assumptions.margin_per_adopter_eur",
        ),
        contact_cost_eur=_as_float(
            _require(assumptions_raw, "contact_cost_eur", "assumptions"), "assumptions.contact_cost_eur"
        ),
    )
    if assumptions.margin_per_adopter_eur < 0 or assumptions.contact_cost_eur < 0:
        raise ConfigError("assumptions: margin and contact cost must not be negative")

    return Config(
        paths=paths,
        kaggle=kaggle,
        duckdb=duckdb_settings,
        months=months,
        model=model,
        assumptions=assumptions,
        products=products,
        ladder=ladder,
    )


def _build_products(raw_products: Mapping[str, Any]) -> tuple[Product, ...]:
    entries = raw_products.get("products")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("products.yaml: 'products' must be a non-empty list")
    products = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"products.yaml: entry {position} must be a mapping with code, name, family")
        code = str(_require(entry, "code", f"products[{position}]"))
        name = str(_require(entry, "name", f"products[{position}]"))
        family = str(_require(entry, "family", f"products[{position}]"))
        if not _PRODUCT_CODE.match(code):
            raise ConfigError(f"products.yaml: {code!r} does not look like a Santander flag (ind_..._ult1)")
        if family not in FAMILIES:
            raise ConfigError(
                f"products.yaml: {code} has unknown family {family!r}; expected one of {sorted(FAMILIES)}"
            )
        products.append(Product(code=code, name=name, family=family))
    codes = [product.code for product in products]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        raise ConfigError(f"products.yaml: duplicate product code(s): {', '.join(duplicates)}")
    if len(products) != EXPECTED_PRODUCT_COUNT:
        raise ConfigError(
            f"products.yaml: expected {EXPECTED_PRODUCT_COUNT} products (one per flag column), "
            f"got {len(products)}"
        )
    return tuple(products)


def _build_model(model_raw: Mapping[str, Any], months: MonthRange, codes: set[str]) -> ModelSettings:
    target = str(_require(model_raw, "target_product", "model"))
    if target not in codes:
        raise ConfigError(f"model.target_product {target!r} is not a product code in products.yaml")

    min_base_rate = _as_float(_require(model_raw, "min_base_rate", "model"), "model.min_base_rate")
    if not 0 < min_base_rate < 1:
        raise ConfigError(f"model.min_base_rate must be between 0 and 1, got {min_base_rate}")
    sample_share = _as_float(_require(model_raw, "sample_share", "model"), "model.sample_share")
    if not 0 < sample_share <= 1:
        raise ConfigError(f"model.sample_share must be in (0, 1], got {sample_share}")
    resamples = _as_int(_require(model_raw, "bootstrap_resamples", "model"), "model.bootstrap_resamples")
    if resamples < 1:
        raise ConfigError(f"model.bootstrap_resamples must be at least 1, got {resamples}")

    splits_raw = _section(model_raw, "splits", "model")
    splits: dict[str, tuple[str, str]] = {}
    for name in SPLIT_NAMES:
        bounds = _require(splits_raw, name, "model.splits")
        if not isinstance(bounds, list | tuple) or len(bounds) != 2:
            raise ConfigError(f"model.splits.{name} must be [first_month, last_month]")
        start = _month(bounds[0], f"model.splits.{name}[0]")
        end = _month(bounds[1], f"model.splits.{name}[1]")
        if months.index(start) > months.index(end):
            raise ConfigError(f"model.splits.{name}: {start} is after {end}")
        splits[name] = (start, end)

    if months.index(splits["train"][0]) < FEATURE_LOOKBACK_MONTHS:
        raise ConfigError(
            f"model.splits.train must start at least {FEATURE_LOOKBACK_MONTHS} months after months.first "
            f"so every row has a full feature history"
        )
    for earlier, later in pairwise(SPLIT_NAMES):
        if months.index(splits[earlier][1]) >= months.index(splits[later][0]):
            raise ConfigError(f"model.splits.{earlier} must end before model.splits.{later} starts")
    # The label for feature month t comes from t+1, which must exist.
    if months.index(splits["test"][1]) >= months.index(months.last):
        raise ConfigError("model.splits.test must end at least one month before months.last (labels use t+1)")

    return ModelSettings(
        target_product=target,
        min_base_rate=min_base_rate,
        sample_share=sample_share,
        seed=_as_int(_require(model_raw, "seed", "model"), "model.seed"),
        bootstrap_resamples=resamples,
        splits=splits,
    )


def _read_yaml(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path} (run from the project root or pass --config)")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(content, Mapping):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return content


def _section(raw: Mapping[str, Any], key: str, parent: str | None = None) -> Mapping[str, Any]:
    value = _require(raw, key, parent)
    if not isinstance(value, Mapping):
        name = f"{parent}.{key}" if parent else key
        raise ConfigError(f"'{name}' must be a mapping")
    return value


def _require(raw: Mapping[str, Any], key: str, parent: str | None = None) -> Any:
    if key not in raw or raw[key] is None:
        name = f"{parent}.{key}" if parent else key
        raise ConfigError(f"missing required setting '{name}'")
    return raw[key]


def _resolve(root: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def _month(value: Any, name: str) -> str:
    text = str(value).strip()
    _parse_month(text, name)
    return text


def _parse_month(text: str, name: str) -> tuple[int, int]:
    match = _MONTH.match(text)
    if not match:
        raise ConfigError(f"{name} must be a month like '2015-01', got {text!r}")
    return int(match.group(1)), int(match.group(2))


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a whole number, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a whole number, got {value!r}") from None
    if not number.is_integer():
        raise ConfigError(f"{name} must be a whole number, got {value!r}")
    return int(number)


def _as_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number, got {value!r}") from None
