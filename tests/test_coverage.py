import datetime as dt
import pytest
from front_systems_mcp.coverage import Coverage, describe


def rows_on(*days: str) -> list[dict]:
    return [{"SaleDate": f"{d}T00:00:00"} for d in days]


def test_reports_span_and_day_count():
    cov = describe(
        rows_on("2026-08-01", "2026-08-01", "2026-08-03"),
        dt.date(2026, 8, 1), dt.date(2026, 8, 4),
    )
    assert cov.row_count == 3
    assert cov.first_seen == dt.date(2026, 8, 1)
    assert cov.last_seen == dt.date(2026, 8, 3)
    assert cov.days_present == 2
    assert cov.missing_days == [dt.date(2026, 8, 2)]


def test_empty_result_warns_rather_than_implying_no_trade():
    cov = describe([], dt.date(2026, 8, 1), dt.date(2026, 8, 4))
    assert cov.row_count == 0
    assert cov.first_seen is None
    assert any("0 rows" in w for w in cov.warnings)






def test_data_starting_later_than_requested_is_flagged():
    cov = describe(
        rows_on("2026-08-05"), dt.date(2026, 8, 1), dt.date(2026, 8, 6),
    )
    assert any("later than requested" in w for w in cov.warnings)


def test_summary_mentions_the_actual_span():
    cov = describe(
        rows_on("2026-08-01", "2026-08-02"),
        dt.date(2026, 8, 1), dt.date(2026, 8, 3),
    )
    assert "2026-08-01" in cov.summary() and "2026-08-02" in cov.summary()




def test_one_unreadable_date_does_not_abort_the_report():
    rows = [{"SaleDate": "2026-08-01T00:00:00"},
            {"SaleDate": "not-a-date"},
            {"SaleDate": "2026-08-02T00:00:00"}]
    cov = describe(rows, dt.date(2026, 8, 1), dt.date(2026, 8, 3))
    assert cov.row_count == 3
    assert cov.days_present == 2
    assert any("unreadable" in w.lower() for w in cov.warnings)


def test_data_stopping_early_is_warned():
    # The truncated-scan case this module exists to surface.
    rows = [{"SaleDate": "2026-08-01T00:00:00"}, {"SaleDate": "2026-08-02T00:00:00"}]
    cov = describe(rows, dt.date(2026, 8, 1), dt.date(2026, 8, 11))
    assert any("2026-08-02" in w for w in cov.warnings)


def test_full_coverage_produces_no_gap_warnings():
    rows = [{"SaleDate": "2026-08-01T00:00:00"}, {"SaleDate": "2026-08-02T00:00:00"}]
    cov = describe(rows, dt.date(2026, 8, 1), dt.date(2026, 8, 3))
    assert cov.warnings == []
