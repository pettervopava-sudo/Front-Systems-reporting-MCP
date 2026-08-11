import datetime as dt
import pytest
from front_systems_mcp import server


def test_parse_date_accepts_iso():
    assert server.parse_date("2026-07-01") == dt.date(2026, 7, 1)


@pytest.mark.parametrize("bad", ["last month", "01/07/2026", "2026-13-01", ""])
def test_parse_date_rejects_non_iso_with_guidance(bad):
    # Relative phrasing is resolved by the model, which shows the user the dates
    # it chose; parsing it here would add a second, invisible interpretation.
    with pytest.raises(ValueError) as exc:
        server.parse_date(bad)
    assert "YYYY-MM-DD" in str(exc.value)


def test_format_result_reports_source_and_coverage():
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult, lines_to_frame, aggregate

    rows = [{"Qty": 1, "Price": 100.0, "Cost": 40.0, "Currency": "NOK",
             "SALEID": 1, "IsVoided": False, "SaleDate": "2026-08-01T00:00:00"}]
    frame = lines_to_frame(rows)
    result = SalesResult(
        frame=aggregate(frame, ["day"]),
        totals={"revenue": 100.0, "transactions": 1},
        coverage=describe(rows, dt.date(2026, 8, 1), dt.date(2026, 8, 2),
                          entity="Saleslines"),
        source="Saleslines",
    )
    text = server.format_result(result)
    assert "Saleslines" in text
    assert "100" in text
    assert "2026-08-01" in text


def test_format_result_surfaces_warnings_prominently():
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult
    import pandas as pd

    result = SalesResult(
        frame=pd.DataFrame(),
        totals={"revenue": 0.0, "transactions": 0},
        coverage=describe([], dt.date(2026, 7, 1), dt.date(2026, 8, 1),
                          entity="Saleslines"),
        source="Saleslines",
    )
    text = server.format_result(result)
    assert "2026-08-01" in text  # the history-start warning must be visible


async def test_every_registered_tool_is_read_only():
    # Ask the MCP server itself what's registered, rather than trusting the
    # hand-maintained TOOL_NAMES constant. Without this, adding a mutating
    # tool (e.g. delete_sale) and forgetting to update TOOL_NAMES would pass
    # silently: the old version of this test only checked TOOL_NAMES against
    # itself, so it could never fail.
    tools = await server.mcp.list_tools()
    registered_names = {t.name for t in tools}
    assert registered_names == set(server.TOOL_NAMES), (
        "TOOL_NAMES has drifted from what's actually registered with the "
        "MCP server"
    )
    assert not any(
        w in n for n in registered_names
        for w in ("create", "update", "delete", "insert", "post", "adjust")
    )


def test_wide_frames_are_truncated_with_the_true_total_shown():
    import pandas as pd
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult

    frame = pd.DataFrame({
        "product": [f"P{i}" for i in range(5000)],
        "Currency": ["NOK"] * 5000,
        "revenue": [1.0] * 5000,
    })
    result = SalesResult(
        frame=frame,
        totals={"revenue": 5000.0, "transactions": 5000},
        coverage=describe([], dt.date(2026, 8, 1), dt.date(2026, 8, 2), entity="Sales"),
        source="Sales",
    )
    text = server.format_result(result)
    assert len(text) < 50_000, "an unbounded dump would flood the caller's context"
    assert "5000" in text, "the true row count must still be reported"
    assert "excel" in text.lower(), "the caller needs a route to the full data"


def test_small_frames_are_not_truncated():
    import pandas as pd
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult

    frame = pd.DataFrame({"day": ["2026-08-01"], "Currency": ["NOK"], "revenue": [100.0]})
    result = SalesResult(
        frame=frame,
        totals={"revenue": 100.0, "transactions": 1},
        coverage=describe([], dt.date(2026, 8, 1), dt.date(2026, 8, 2), entity="Sales"),
        source="Sales",
    )
    text = server.format_result(result)
    assert "2026-08-01" in text
    assert "showing" not in text.lower()


def test_chart_x_axis_uses_the_single_group_by_key_directly():
    import pandas as pd

    frame = pd.DataFrame({"day": ["2026-08-01", "2026-08-02"], "revenue": [1.0, 2.0]})
    out_frame, x = server.chart_x_axis(frame, ["day"])
    assert x == "day"
    assert out_frame is frame


def test_chart_x_axis_joins_a_multi_key_group_by_so_bars_do_not_collide():
    import pandas as pd

    # The bug this guards: with group_by=["day", "Brand"], using only the
    # first column as x left two rows both labelled "2026-08-01" — bars for
    # different brands collided on one tick instead of getting their own.
    frame = pd.DataFrame({
        "day": ["2026-08-01", "2026-08-01"],
        "Brand": ["Nike", "Adidas"],
        "revenue": [100.0, 50.0],
    })
    out_frame, x = server.chart_x_axis(frame, ["day", "Brand"])
    assert x not in ("day", "Brand")  # a new, distinct label column
    assert list(out_frame[x]) == ["2026-08-01 · Nike", "2026-08-01 · Adidas"]
    assert len(set(out_frame[x])) == 2, "x-values must not repeat"


def test_truncation_never_drops_the_warnings():
    import pandas as pd
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult

    frame = pd.DataFrame({
        "product": [f"P{i}" for i in range(5000)],
        "Currency": ["NOK"] * 5000,
        "revenue": [1.0] * 5000,
    })
    coverage = describe([], dt.date(2026, 7, 1), dt.date(2026, 8, 1), entity="Saleslines")
    result = SalesResult(frame=frame, totals={"revenue": 0.0, "transactions": 0},
                         coverage=coverage, source="Saleslines")
    text = server.format_result(result)
    assert coverage.warnings, "fixture precondition: this case must produce warnings"
    for warning in coverage.warnings:
        assert warning[:40] in text, "warnings must survive truncation"
