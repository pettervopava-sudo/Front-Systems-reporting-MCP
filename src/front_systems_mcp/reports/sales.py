"""Sales aggregation.

The revenue formula lives here and nowhere else. Its failure mode is invisible:
a period without returns gives the same answer either way, and only a period
*with* returns is wrong, so a duplicated formula would drift undetected.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from ..coverage import LINES_HISTORY_START, Coverage, describe
from ..odata import any_of, date_range, eq

LINE_SELECT = [
    "SALESLINEID", "SALEID", "STOREID_FK", "STOCKID_FK", "PRODUCTID_FK",
    "SaleDate", "SaleDateTime", "Qty", "Price", "FullPrice", "Discount",
    "Cost", "VATPercent", "Currency", "IsVoided", "Brand", "Group", "Name",
    "SizeLabel", "Store", "Stock",
]

HEADER_SELECT = [
    "SALEID", "STOREID_FK", "POSID_FK", "SaleDate",
    "SaleDateTime", "Total", "IsVoided", "IsComplete",
]


@dataclass
class SalesResult:
    frame: pd.DataFrame
    totals: dict[str, float]
    coverage: Coverage
    source: str


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series([0.0] * len(frame), index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def lines_to_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[*LINE_SELECT, "LineTotal", "LineCost",
                                     "LineMargin", "day"])
    qty = _numeric(frame, "Qty")
    # Price and Cost are UNIT figures; returns are Qty = -1 with a positive
    # Price, so multiplying is what makes a return subtract.
    frame["LineTotal"] = (qty * _numeric(frame, "Price")).round(2)
    frame["LineCost"] = (qty * _numeric(frame, "Cost")).round(2)
    frame["LineMargin"] = (frame["LineTotal"] - frame["LineCost"]).round(2)
    frame["day"] = frame["SaleDate"].astype(str).str[:10]
    if "Currency" not in frame:
        frame["Currency"] = "UNKNOWN"
    return frame


def headers_to_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[*HEADER_SELECT, "day", "Currency"])
    if "IsVoided" in frame:
        frame = frame[~frame["IsVoided"].astype(bool)].copy()
    frame["Total"] = _numeric(frame, "Total")
    frame["day"] = frame["SaleDate"].astype(str).str[:10]
    frame["Currency"] = "NOK"  # headers carry no currency column
    return frame


def aggregate(frame: pd.DataFrame, group_by: Sequence[str]) -> pd.DataFrame:
    """Grouped totals. Currency is always a grouping key — a blended total
    across currencies is silently meaningless."""
    if frame.empty:
        return pd.DataFrame(columns=[*group_by, "Currency", "revenue",
                                     "units", "transactions", "margin"])
    keys = [*group_by, "Currency"]
    revenue = "LineTotal" if "LineTotal" in frame else "Total"
    out = frame.groupby(keys, dropna=False).apply(
        lambda g: pd.Series({
            "revenue": round(_numeric(g, revenue).sum(), 2),
            "units": round(_numeric(g, "Qty").sum(), 2) if "Qty" in g else float(len(g)),
            "transactions": int(g["SALEID"].nunique()) if "SALEID" in g else len(g),
            "margin": round(_numeric(g, "LineMargin").sum(), 2)
            if "LineMargin" in g else float("nan"),
        }),
        include_groups=False,
    ).reset_index()
    out["avg_basket"] = (
        out["revenue"] / out["transactions"].replace(0, pd.NA)
    ).round(2)
    return out


async def sales_report(
    client,
    date_from: dt.date,
    date_to: dt.date,
    stock_id: int | None = None,
    register_ids: Sequence[int] | None = None,
    group_by: Sequence[str] = ("day",),
    prefer_lines: bool = True,
) -> SalesResult:
    """Pick the table that can answer the question, then aggregate.

    Saleslines gives product detail but starts 2026-08-01; Sales reaches back
    years but has no product, unit or cost columns.
    """
    use_lines = prefer_lines and date_from >= LINES_HISTORY_START and stock_id is not None

    if use_lines:
        filters = [*date_range("SaleDate", date_from, date_to), eq("STOCKID_FK", stock_id)]
        rows = await client.fetch("Saleslines", filters, LINE_SELECT)
        frame = lines_to_frame(rows)
        coverage = describe(rows, date_from, date_to, entity="Saleslines")
        source = "Saleslines"
        totals = {
            "revenue": round(float(frame["LineTotal"].sum()), 2) if len(frame) else 0.0,
            "units": round(float(pd.to_numeric(frame["Qty"]).sum()), 2) if len(frame) else 0.0,
            "margin": round(float(frame["LineMargin"].sum()), 2) if len(frame) else 0.0,
            "transactions": int(frame["SALEID"].nunique()) if len(frame) else 0,
        }
    else:
        filters = list(date_range("SaleDate", date_from, date_to))
        if register_ids:
            filters.append(any_of("STOREID_FK", list(register_ids)))
        rows = await client.fetch("Sales", filters, HEADER_SELECT)
        frame = headers_to_frame(rows)
        coverage = describe(rows, date_from, date_to, entity="Sales")
        source = "Sales"
        totals = {
            "revenue": round(float(frame["Total"].sum()), 2) if len(frame) else 0.0,
            "transactions": int(len(frame)),
        }

    if totals.get("transactions"):
        totals["avg_basket"] = round(totals["revenue"] / totals["transactions"], 2)
    return SalesResult(
        frame=aggregate(frame, list(group_by)),
        totals=totals,
        coverage=coverage,
        source=source,
    )
