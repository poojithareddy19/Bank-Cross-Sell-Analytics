from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds
import pytest
import requests

from fixture_data import EXPECTED, fixture_csv_text, fixture_rows, write_fixture_zip
from xsell.cli import main
from xsell.data.convert import cache_is_current, convert_zip_to_parquet, count_csv_rows, read_manifest
from xsell.data.download import download_competition_file, find_kaggle_credentials
from xsell.data.fetch import run_fetch
from xsell.errors import ConversionError, DownloadError
from xsell.sampling import in_sample


def read_holdings(cache_dir: Path):
    return ds.dataset(cache_dir / "holdings", format="parquet", partitioning="hive").to_table().to_pandas()


def fail_download(*_args):
    raise AssertionError("download must not be called")


def test_fetch_converts_fixture_and_matches_known_counts(fixture_config, fixture_zip):
    manifest = run_fetch(fixture_config, downloader=fail_download)

    assert manifest["rows"] == EXPECTED["rows"]
    assert manifest["source"]["csv_rows"] == EXPECTED["rows"]
    assert manifest["customers"] == EXPECTED["customers"]
    assert manifest["n_months"] == EXPECTED["months"]
    assert manifest["rows_per_month"] == EXPECTED["rows_per_month"]
    assert manifest["columns"] == 48
    assert len(manifest["source"]["zip_sha256"]) == 64
    assert not fixture_zip.exists(), "zip is deleted after a successful conversion"

    cache_dir = fixture_config.paths.cache_dir
    assert json.loads((cache_dir / "manifest.json").read_text()) == manifest
    partitions = sorted(path.name for path in (cache_dir / "holdings").iterdir())
    assert partitions == [f"month={month}" for month in EXPECTED["rows_per_month"]]


def test_raw_text_is_preserved_for_staging(fixture_config, fixture_zip):
    run_fetch(fixture_config, downloader=fail_download)
    frame = read_holdings(fixture_config.paths.cache_dir).set_index(["ncodpers", "fecha_dato"]).sort_index()
    by_customer = frame.groupby(level=0).first()

    assert by_customer.loc[1005, "renta"] == "         NA"
    assert by_customer.loc[1005, "nomprov"] == "CORUÑA, A"
    assert pd.isna(by_customer.loc[1004, "renta"])
    assert by_customer.loc[1006, "antiguedad"] == "-999999"
    assert pd.isna(by_customer.loc[1012, "fecha_alta"])
    assert by_customer.loc[1001, "age"] == " 26"
    assert set(frame["indrel_1mes"]) == {"1", "1.0", "2", "P", "3.0", "4.0"}
    assert int(frame[["ind_nomina_ult1", "ind_nom_pens_ult1"]].isna().sum().sum()) == 2
    assert frame["ind_cco_fin_ult1"].dtype.name == "int8"


def test_rerun_skips_download_and_conversion(fixture_config, fixture_zip):
    first = run_fetch(fixture_config, keep_zip=True, downloader=fail_download)
    parquet_file = fixture_config.paths.cache_dir / "holdings" / "month=2015-01" / "part-0.parquet"
    modified = parquet_file.stat().st_mtime_ns

    second = run_fetch(fixture_config, keep_zip=True, downloader=fail_download)
    assert second == first
    assert parquet_file.stat().st_mtime_ns == modified

    fixture_zip.unlink()
    assert run_fetch(fixture_config, downloader=fail_download) == first, "no zip needed once cached"


def test_missing_zip_triggers_download(fixture_config):
    calls = []

    def fake_download(competition, file_name, dest_dir):
        calls.append((competition, file_name))
        return write_fixture_zip(dest_dir / file_name)

    manifest = run_fetch(fixture_config, downloader=fake_download)
    assert calls == [("santander-product-recommendation", "train_ver2.csv.zip")]
    assert manifest["rows"] == EXPECTED["rows"]


def test_customer_sample_is_deterministic_and_whole_customer(fixture_config, fixture_zip):
    manifest = run_fetch(fixture_config, sample_share=0.5, keep_zip=True, downloader=fail_download)
    frame = read_holdings(fixture_config.paths.cache_dir)

    rows = fixture_rows()
    ids = sorted({int(row["ncodpers"]) for row in rows})
    chosen = {customer for customer, keep in zip(ids, in_sample(ids, 0.5), strict=True) if keep}
    assert 0 < len(chosen) < len(ids)
    assert set(frame["ncodpers"]) == chosen
    assert manifest["rows"] == sum(int(row["ncodpers"]) in chosen for row in rows)
    assert manifest["source"]["csv_rows"] == EXPECTED["rows"]
    assert manifest["sample_share"] == 0.5

    current, reason = cache_is_current(fixture_config.paths.cache_dir, None, fixture_zip)
    assert not current and "sample share" in reason
    again = run_fetch(fixture_config, sample_share=0.5, keep_zip=True, force=True, downloader=fail_download)
    assert again["rows"] == manifest["rows"]


def test_changed_zip_invalidates_cache(fixture_config, fixture_zip):
    run_fetch(fixture_config, keep_zip=True, downloader=fail_download)
    write_fixture_zip(fixture_zip, csv_text=fixture_csv_text(fixture_rows()[:-1]))
    current, reason = cache_is_current(fixture_config.paths.cache_dir, None, fixture_zip)
    assert not current and "differs" in reason


def test_garbage_file_is_rejected(tmp_path):
    bad = tmp_path / "train_ver2.csv.zip"
    bad.write_bytes(b"this is not a zip file" * 100)
    with pytest.raises(ConversionError, match="not a valid zip"):
        convert_zip_to_parquet(bad, tmp_path / "cache", "train_ver2.csv", [])


def test_corrupted_zip_content_is_rejected(fixture_config, tmp_path):
    path = write_fixture_zip(tmp_path / "train_ver2.csv.zip")
    data = bytearray(path.read_bytes())
    # Flip bytes inside the compressed stream; the central directory stays intact.
    for offset in range(200, 260):
        data[offset] ^= 0xFF
    path.write_bytes(bytes(data))
    cache_dir = tmp_path / "cache"
    with pytest.raises(ConversionError):
        convert_zip_to_parquet(path, cache_dir, "train_ver2.csv", fixture_config.product_codes)
    assert not (cache_dir / "holdings").exists()
    assert not (cache_dir / "holdings.tmp").exists()
    assert read_manifest(cache_dir) is None


def test_missing_member_lists_contents(fixture_config, tmp_path):
    path = write_fixture_zip(tmp_path / "train_ver2.csv.zip", member="other.csv")
    with pytest.raises(ConversionError, match=r"does not contain 'train_ver2.csv'.*other.csv"):
        convert_zip_to_parquet(path, tmp_path / "cache", "train_ver2.csv", fixture_config.product_codes)


def test_unexpected_header_is_rejected(fixture_config, tmp_path):
    from fixture_data import COLUMNS

    columns = [column for column in COLUMNS if column != "renta"]
    path = write_fixture_zip(tmp_path / "train_ver2.csv.zip", csv_text=fixture_csv_text(columns=columns))
    with pytest.raises(ConversionError, match=r"missing columns \['renta'\]"):
        convert_zip_to_parquet(path, tmp_path / "cache", "train_ver2.csv", fixture_config.product_codes)


def test_row_count_mismatch_is_detected(fixture_config, tmp_path):
    rows = fixture_rows()
    rows[0] = {**rows[0], "nomprov": "LINE\nBREAK"}
    path = write_fixture_zip(tmp_path / "train_ver2.csv.zip", csv_text=fixture_csv_text(rows))
    with pytest.raises(ConversionError, match="data lines but the parser read"):
        convert_zip_to_parquet(path, tmp_path / "cache", "train_ver2.csv", fixture_config.product_codes)


def test_unexpected_months_are_rejected(fixture_config, fixture_zip):
    with pytest.raises(ConversionError, match="expected snapshot months"):
        convert_zip_to_parquet(
            fixture_zip,
            fixture_config.paths.cache_dir,
            "train_ver2.csv",
            fixture_config.product_codes,
            expected_months=["2015-01"],
        )


def test_count_csv_rows_without_trailing_newline(tmp_path):
    path = write_fixture_zip(tmp_path / "x.zip", csv_text="a,b\n1,2\n3,4")
    assert count_csv_rows(path, "train_ver2.csv") == 2


def test_missing_credentials_give_clear_error(tmp_path):
    assert find_kaggle_credentials(env={}, home=tmp_path) is None
    expected = "Kaggle credentials not found.*santander-product-recommendation/rules"
    with pytest.raises(DownloadError, match=expected):
        download_competition_file(
            "santander-product-recommendation", "train_ver2.csv.zip", tmp_path / "raw", env={}, home=tmp_path
        )


def test_credential_source_never_contains_the_secret(tmp_path):
    secret = "abc123-very-secret"
    env = {"KAGGLE_USERNAME": "someone", "KAGGLE_KEY": secret}
    assert secret not in find_kaggle_credentials(env=env, home=tmp_path)
    assert "KAGGLE_KEY" in find_kaggle_credentials(env=env, home=tmp_path)

    token_file = tmp_path / ".kaggle" / "access_token"
    token_file.parent.mkdir()
    token_file.write_text(secret)
    source = find_kaggle_credentials(env={}, home=tmp_path)
    assert source == f"file {token_file}" and secret not in source


@pytest.mark.parametrize(
    ("status", "message"), [(401, "HTTP 401"), (403, "Accept the competition rules"), (404, "could not find")]
)
def test_http_errors_are_explained(tmp_path, status, message):
    class RefusingApi:
        def competition_download_file(self, *args, **kwargs):
            response = requests.Response()
            response.status_code = status
            raise requests.HTTPError("refused", response=response)

    with pytest.raises(DownloadError, match=message):
        download_competition_file(
            "santander-product-recommendation",
            "train_ver2.csv.zip",
            tmp_path / "raw",
            api_factory=RefusingApi,
            env={"KAGGLE_API_TOKEN": "x"},
            home=tmp_path,
        )


def test_cli_fetch_reports_missing_credentials(tmp_path, monkeypatch):
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_CONFIG_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XSELL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert main(["fetch"]) == 1


def test_cli_rejects_bad_sample_share():
    with pytest.raises(SystemExit):
        main(["fetch", "--sample-customers", "1.5"])
