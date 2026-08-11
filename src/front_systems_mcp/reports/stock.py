"""Stock levels from Stockstatus.

Stockstatus takes snapshotDateTime as a plain query parameter rather than an
OData filter, and its literal is quoted with a space separator — not the
datetime'...' form the other entities use.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..odata import MAX_TOP

STOCK_SELECT = [
    "Productid", "Stockid", "Name", "Number", "Brand", "Group", "SizeLabel",
    "EAN", "Qty", "ReservedQty", "AvailableQty", "Cost", "OutPrice",
    "StockName", "StockExtId", "Season",
]


async def stock_report(
    client,
    snapshot: dt.datetime | None = None,
    stock_id: int | None = None,
    search: str | None = None,
    low_stock_threshold: int | None = None,
) -> pd.DataFrame:
    snapshot = snapshot or dt.datetime.now().replace(
        hour=23, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
    params = {
        "snapshotDateTime": f"'{snapshot.strftime('%Y-%m-%d %H:%M:%S')}'",
        "$select": ",".join(STOCK_SELECT),
        "$top": str(MAX_TOP),
    }
    rows = await client.fetch_raw("Stockstatus", params)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    # Filtered client-side: Stockstatus is a snapshot endpoint and its filter
    # support has not been verified, so pushing predicates risks the silent
    # empty result seen elsewhere on this API.
    if stock_id is not None:
        frame = frame[frame["Stockid"] == stock_id]
    if search:
        needle = search.casefold()
        haystack = (
            frame.get("Name", pd.Series(dtype=str)).astype(str).str.casefold()
            + " "
            + frame.get("Brand", pd.Series(dtype=str)).astype(str).str.casefold()
        )
        frame = frame[haystack.str.contains(needle, na=False)]
    if low_stock_threshold is not None:
        qty = pd.to_numeric(frame["AvailableQty"], errors="coerce").fillna(0)
        frame = frame[qty <= low_stock_threshold]
    return frame.reset_index(drop=True)
