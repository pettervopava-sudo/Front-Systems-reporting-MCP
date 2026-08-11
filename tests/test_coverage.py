import datetime as dt
from front_systems_mcp.coverage import LINES_HISTORY_START, Coverage, describe


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


def test_saleslines_before_history_start_is_warned():
    cov = describe(
        [], dt.date(2026, 7, 1), dt.date(2026, 8, 1), entity="Saleslines",
    )
    joined = " ".join(cov.warnings)
    assert str(LINES_HISTORY_START) in joined
    assert "Sales" in joined


def test_sales_before_history_start_is_not_warned():
    # Sales headers reach back years; only Saleslines is limited.
    cov = describe(
        rows_on("2026-07-01"), dt.date(2026, 7, 1), dt.date(2026, 7, 2),
        entity="Sales",
    )
    assert not any("2026-08-01" in w for w in cov.warnings)


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
