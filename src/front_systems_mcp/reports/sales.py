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
    # Sales headers carry no currency column at all, so this is a placeholder,
    # not a fact. Labelling it "NOK" would assert a currency the API never
    # told us; "UNKNOWN" is honest. A multi-currency tenant needs the lines
    # path (Saleslines has a real Currency column) to see a true breakdown.
    frame["Currency"] = "UNKNOWN"
    return frame


#: group_by keys that are always synthesised by lines_to_frame/headers_to_frame,
#: so they're valid even when a caller passes an empty frame with a reduced
#: column set.
_SYNTHESIZED_GROUP_KEYS = frozenset({"day", "Currency"})


def _validate_group_by(frame: pd.DataFrame, group_by: Sequence[str]) -> None:
    available = set(frame.columns) | _SYNTHESIZED_GROUP_KEYS
    unknown = [key for key in group_by if key not in available]
    if unknown:
        raise ValueError(
            f"Cannot group by {unknown!r}: not present in this data. Available "
            f"keys for this source: {sorted(available)}. Product dimensions "
            "such as Brand need line-level data — pass a stock_id and a period "
            f"from {LINES_HISTORY_START} onward so sales_report uses Saleslines."
        )


def aggregate(frame: pd.DataFrame, group_by: Sequence[str]) -> pd.DataFrame:
    """Grouped totals. Currency is always a grouping key — a blended total
    across currencies is silently meaningless."""
    _validate_group_by(frame, group_by)
    # Fixed column set so a zero-row result (e.g. a closed Sunday) has exactly
    # the same shape as a populated one — no downstream KeyError on avg_basket.
    columns = [*group_by, "Currency", "revenue", "units", "transactions",
               "margin", "avg_basket"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    if "LineTotal" not in frame and "Total" not in frame:
        # This is the last line of defence for the revenue invariant: without
        # it, a frame built some other way than lines_to_frame/headers_to_frame
        # silently reports 0.0 revenue, and the tempting "fix" is to point
        # this at Price directly — which reintroduces the original returns bug
        # (Price is a unit price; SUM(Price) double-counts returns as revenue).
        raise ValueError(
            "Cannot compute revenue: this frame has neither 'LineTotal' nor "
            "'Total'. Line-level data must be built with lines_to_frame, which "
            "computes LineTotal = Qty * Price (never sum Price directly — "
            "returns are Qty = -1 rows with a positive Price). Header-level "
            "data must be built with headers_to_frame, which provides Total."
        )
    keys = [*group_by, "Currency"]
    revenue = "LineTotal" if "LineTotal" in frame else "Total"
    has_qty = "Qty" in frame
    out = frame.groupby(keys, dropna=False).apply(
        lambda g: pd.Series({
            "revenue": round(_numeric(g, revenue).sum(), 2),
            # A row count is not a unit count — report nan rather than a
            # mislabelled number when the source (headers) has no Qty.
            "units": round(_numeric(g, "Qty").sum(), 2) if has_qty else float("nan"),
            "transactions": int(g["SALEID"].nunique()) if "SALEID" in g else len(g),
            "margin": round(_numeric(g, "LineMargin").sum(), 2)
            if "LineMargin" in g else float("nan"),
        }),
        include_groups=False,
    ).reset_index()
    out["avg_basket"] = (
        out["revenue"] / out["transactions"].replace(0, pd.NA)
    ).round(2)
    return out[columns]


def _blended_total_note(frame: pd.DataFrame) -> str | None:
    """None if a single-currency (or currency-less) total is safe to sum.

    A blended NOK+EUR total is not a smaller mistake than the returns bug this
    project exists to catch — it is the same class of confidently wrong
    number. When more than one currency is present, refuse to print one.
    """
    if "Currency" not in frame or frame.empty:
        return None
    currencies = sorted(str(c) for c in frame["Currency"].dropna().unique())
    if len(currencies) <= 1:
        return None
    return (
        f"multiple currencies present ({', '.join(currencies)}); a blended "
        "total would be meaningless. See the per-currency breakdown in the "
        "table below."
    )


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
        if register_ids:
            # Both constraints must hold: stock_id narrows to the shop,
            # register_ids narrows further to specific tills within it. Dropping
            # this clause silently widens a till-level question to shop-wide.
            filters.append(any_of("STOREID_FK", list(register_ids)))
        rows = await client.fetch("Saleslines", filters, LINE_SELECT)
        frame = lines_to_frame(rows)
        coverage = describe(rows, date_from, date_to, entity="Saleslines")
        source = "Saleslines"
        blended_note = _blended_total_note(frame)
        totals = {
            "revenue": blended_note or (
                round(float(frame["LineTotal"].sum()), 2) if len(frame) else 0.0
            ),
            "units": round(float(pd.to_numeric(frame["Qty"]).sum()), 2) if len(frame) else 0.0,
            "margin": blended_note or (
                round(float(frame["LineMargin"].sum()), 2) if len(frame) else 0.0
            ),
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
        # headers_to_frame's Currency is always a single synthetic "UNKNOWN"
        # marker (Sales carries no real currency column), so this can never
        # fire today. Kept for symmetry with the lines branch and so a future
        # change that adds a real Currency column here is covered for free.
        blended_note = _blended_total_note(frame)
        totals = {
            "revenue": blended_note or (
                round(float(frame["Total"].sum()), 2) if len(frame) else 0.0
            ),
            "transactions": int(len(frame)),
        }

        # The table choice above is otherwise silent: a caller cannot tell a
        # "no product breakdown available" answer from a "no product
        # breakdown exists" one. Only relevant when part of the period could
        # have used Saleslines at all (i.e. it reaches 2026-08-01 or later).
        if prefer_lines and date_to > LINES_HISTORY_START:
            if stock_id is None:
                coverage.warnings.append(
                    "No stock_id was given, so this period (which reaches "
                    f"{LINES_HISTORY_START} or later) was served entirely from "
                    "Sales headers, with no product, unit or margin detail. "
                    "Pass a stock_id to use Saleslines for the part of the "
                    "range on or after that date."
                )
            elif date_from < LINES_HISTORY_START:
                coverage.warnings.append(
                    f"Line-level data exists only from {LINES_HISTORY_START} "
                    f"onward, so this entire request ({date_from}..{date_to}) "
                    "was served from Sales headers with no product, unit or "
                    "margin detail, even though a stock_id was given. Split "
                    f"the request at {LINES_HISTORY_START} and query "
                    "Saleslines separately for the part from that date onward "
                    "if product detail is wanted."
                )

    if totals.get("transactions") and isinstance(totals.get("revenue"), (int, float)):
        # avg_basket divides revenue, so it is just as meaningless as a
        # blended revenue total when currencies were mixed; skip it too.
        totals["avg_basket"] = round(totals["revenue"] / totals["transactions"], 2)
    return SalesResult(
        frame=aggregate(frame, list(group_by)),
        totals=totals,
        coverage=coverage,
        source=source,
    )
