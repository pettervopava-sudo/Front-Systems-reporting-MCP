"""MCP tool definitions. Wiring only — no business logic lives here."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import FrontSystemsClient
from .config import load_config
from .reports import sales as sales_report_mod
from .reports import stock as stock_mod
from .reports import stores as stores_mod
from .exporters.charts import write_bar_chart
from .exporters.excel import write_workbook

TOOL_NAMES = ("list_stores", "sales_report", "stock_report", "raw_query")

MAX_TABLE_ROWS = 100
#: Aggregation happens in code so that context cost does not scale with the date
#: range. Emitting an unbounded frame would undo that, so wide results are
#: truncated here and the full data offered as a file instead.

mcp = FastMCP("front-systems")
_client: FrontSystemsClient | None = None


def parse_date(value: str) -> dt.date:
    """ISO only.

    The model resolves relative phrasing ("last quarter") and states the dates
    it chose, so the user can see and correct them. Parsing that here would add
    a second interpretation the user never sees.
    """
    try:
        return dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise ValueError(
            f"Could not read {value!r} as a date. Use YYYY-MM-DD."
        ) from None


def get_client() -> FrontSystemsClient:
    global _client
    if _client is None:
        _client = FrontSystemsClient(load_config())
    return _client


def format_result(result: "sales_report_mod.SalesResult") -> str:
    lines = [f"Source: {result.source}", f"Coverage: {result.coverage.summary()}"]
    for warning in result.coverage.warnings:
        lines.append(f"WARNING: {warning}")
    lines.append("")
    for key, value in result.totals.items():
        lines.append(f"{key}: {value:,.2f}" if isinstance(value, float)
                     else f"{key}: {value:,}")
    if not result.frame.empty:
        lines.append("")
        total_rows = len(result.frame)
        if total_rows > MAX_TABLE_ROWS:
            lines.append(result.frame.head(MAX_TABLE_ROWS).to_string(index=False))
            lines.append("")
            lines.append(
                f"(showing {MAX_TABLE_ROWS} of {total_rows} rows) Narrow the date "
                "range, group more coarsely, or request output=[\"excel\"] for the "
                "complete data."
            )
        else:
            lines.append(result.frame.to_string(index=False))
    return "\n".join(lines)


@mcp.tool()
async def list_stores(days: int = 30) -> str:
    """List stores with their stock ids and register ids.

    Harvested from recent sales lines, because the API has no store endpoint.
    """
    entries = await stores_mod.harvest(get_client(), days=days)
    rows = [
        f"{e.stock_id:>7}  {e.display_name:<34} "
        f"{'/'.join(e.legal_entities):<32} registers={e.register_ids}"
        for e in entries
    ]
    return "STOCKID  stock name                         legal entity\n" + "\n".join(rows)


@mcp.tool()
async def sales_report(
    date_from: str,
    date_to: str,
    stock_id: int | None = None,
    register_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    output: list[str] | None = None,
) -> str:
    """Sales revenue, units and margin for a period.

    Dates are ISO YYYY-MM-DD; date_to is exclusive. Give stock_id (preferred,
    from list_stores) for product-level detail. Periods before 2026-08-01 fall
    back to transaction headers, which have no product, unit or margin data.
    Output may include "excel" and "chart".
    """
    result = await sales_report_mod.sales_report(
        get_client(),
        parse_date(date_from),
        parse_date(date_to),
        stock_id=stock_id,
        register_ids=register_ids,
        group_by=tuple(group_by or ["day"]),
    )
    text = format_result(result)
    wanted = set(output or [])
    if wanted & {"excel", "chart"}:
        out_dir = Path.cwd() / "reports"
        stem = f"sales_{date_from}_to_{date_to}"
        if "excel" in wanted:
            path = write_workbook(
                {"Summary": result.frame}, out_dir / f"{stem}.xlsx",
                notes=[
                    f"Source: {result.source}.",
                    "Revenue is Qty * Price; returns are Qty = -1 rows.",
                    result.coverage.summary(),
                    *result.coverage.warnings,
                ])
            text += f"\n\nWorkbook: {path}"
        if "chart" in wanted and not result.frame.empty:
            group = result.frame.columns[0]
            path = write_bar_chart(result.frame, group, "revenue",
                                   f"Revenue by {group}", out_dir / f"{stem}.png")
            text += f"\nChart: {path}"
    return text


@mcp.tool()
async def stock_report(
    stock_id: int | None = None,
    search: str | None = None,
    low_stock_threshold: int | None = None,
    snapshot: str | None = None,
) -> str:
    """Current stock levels. snapshot is ISO YYYY-MM-DD (defaults to yesterday 23:00)."""
    when = (dt.datetime.combine(parse_date(snapshot), dt.time(23, 0))
            if snapshot else None)
    frame = await stock_mod.stock_report(
        get_client(), snapshot=when, stock_id=stock_id,
        search=search, low_stock_threshold=low_stock_threshold)
    if frame.empty:
        return "No stock rows matched."
    total = len(frame)
    shown = frame.head(200)
    note = f" (showing {len(shown)} of {total} rows)" if total > 200 else ""
    return f"{total} rows{note}\n\n{shown.to_string(index=False)}"


@mcp.tool()
async def raw_query(entity: str, filter: str = "", select: str = "") -> str:
    """Read-only escape hatch for an OData entity not covered above.

    Filter only on STOCKID_FK, STOREID_FK, PRODUCTID_FK or SaleDate; display
    fields such as Stock, Store and Brand return empty with HTTP 200.
    """
    rows = await get_client().fetch(
        entity,
        [filter] if filter else [],
        select.split(",") if select else ["SALEID"],
    )
    head = rows[:50]
    note = f" (showing 50 of {len(rows)})" if len(rows) > 50 else ""
    return f"{len(rows)} rows{note}\n\n{head}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
