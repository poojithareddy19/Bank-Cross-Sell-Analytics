from __future__ import annotations

import pytest

from fixture_data import fixture_csv_text, fixture_rows, write_fixture_zip
from xsell.data.fetch import run_fetch
from xsell.errors import XsellError
from xsell.quality import QualityError, read_header, run_quality
from xsell.warehouse import build_warehouse

# Planted in tests/fixture_data.py; see its module docstring.
EXPECTED_VIOLATIONS = {
    "q01_duplicate_customer_month": 0,
    "q02_invalid_product_flags": 0,
    "q03_row_count_reconciliation": 0,
    "q04_impossible_age": 8,  # customer 1011, age 115, all 8 months
    "q05_income_out_of_range": 0,
    "q06_customers_with_month_gaps": 1,  # customer 1003
    "q07_flags_filled_from_null": 1,  # customer 1007, month 2
    "q08_missing_join_date": 1,  # customer 1012
}


def test_checks_find_exactly_the_planted_issues(built_warehouse):
    results = {result.name: result for result in run_quality(built_warehouse)}
    checks = {name: result.violations for name, result in results.items() if result.severity != "profile"}
    assert checks == EXPECTED_VIOLATIONS

    assert results["q04_impossible_age"].rows["customer_id"].unique().tolist() == [1011]
    assert results["q06_customers_with_month_gaps"].rows.iloc[0].to_dict() == {
        "customer_id": 1003,
        "first_seen_month_index": 0,
        "last_seen_month_index": 7,
        "months_present": 7,
        "missing_months": 1,
    }
    assert results["q08_missing_join_date"].rows["customer_id"].tolist() == [1012]

    filled = results["p02_flags_filled_summary"].rows.iloc[0]
    assert (filled["flags_filled"], filled["rows_affected"]) == (2, 1)
    nulls = dict(results["p01_null_counts"].rows[["column_name", "null_rows"]].itertuples(index=False))
    assert nulls["income"] == 16  # customers 1004 and 1005, 8 months each
    assert nulls["seniority_months"] == 8  # customer 1006
    assert nulls["join_date"] == 8  # customer 1012


def test_report_is_written(built_warehouse):
    run_quality(built_warehouse)
    report = (built_warehouse.paths.reports_dir / "data_quality.md").read_text(encoding="utf-8")
    for name in EXPECTED_VIOLATIONS:
        assert name in report
    assert "| q04_impossible_age | warning | warn | 8 |" in report
    assert "| 1011 | 0 | 115 |" in report, "ids are printed without thousands separators"
    assert "474 rows, 60 customers, all customers" in report


def test_critical_issues_fail_after_writing_the_report(fixture_config):
    rows = fixture_rows()
    rows.append(dict(rows[0]))  # duplicate customer-month
    rows[5] = {**rows[5], "ind_ahor_fin_ult1": "2"}  # invalid flag
    write_fixture_zip(fixture_config.paths.raw_dir / fixture_config.kaggle.file, csv_text=fixture_csv_text(rows))
    run_fetch(fixture_config)
    build_warehouse(fixture_config)

    with pytest.raises(QualityError, match="q01_duplicate_customer_month, q02_invalid_product_flags"):
        run_quality(fixture_config)
    report = (fixture_config.paths.reports_dir / "data_quality.md").read_text(encoding="utf-8")
    assert "| q01_duplicate_customer_month | critical | FAIL | 1 |" in report
    assert "| q02_invalid_product_flags | critical | FAIL | 1 |" in report


def test_quality_needs_the_warehouse(fixture_config):
    with pytest.raises(XsellError, match="run `python -m xsell warehouse` first"):
        run_quality(fixture_config)


def test_check_header_must_declare_severity(tmp_path):
    path = tmp_path / "q99_bad.sql"
    path.write_text("-- severity: urgent\n-- description: x\nSELECT 1;", encoding="utf-8")
    with pytest.raises(XsellError, match="severity"):
        read_header(path)
    path.write_text("-- severity: warning\nSELECT 1;", encoding="utf-8")
    with pytest.raises(XsellError, match="description"):
        read_header(path)
