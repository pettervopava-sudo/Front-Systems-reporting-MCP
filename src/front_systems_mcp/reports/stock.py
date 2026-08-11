"""Stock levels from Stockstatus.

Stockstatus takes snapshotDateTime as a plain query parameter rather than an
OData filter, and its literal is quoted with a space separator — not the
datetime'...' form the other entities use.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from ..odata import MAX_TOP

OSLO = ZoneInfo("Europe/Oslo")
#: The tenant trades in Norway, so "yesterday 23:00" must mean 23:00 in Oslo.
#: A naive now() would resolve against the server's timezone and, on a UTC host,
#: select the wrong calendar day for part of each day.

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
    snapshot = snapshot or dt.datetime.now(OSLO).replace(
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

        def _field(name: str) -> pd.Series:
            if name in frame.columns:
                return frame[name].fillna("").astype(str).str.casefold()
            return pd.Series("", index=frame.index)

        # Each field is null-filled independently before matching, so a null
        # Name (or Brand) never masks a genuine match in the other field.
        name_matches = _field("Name").str.contains(needle, na=False)
        brand_matches = _field("Brand").str.contains(needle, na=False)
        frame = frame[name_matches | brand_matches]
    if low_stock_threshold is not None:
        if "AvailableQty" not in frame.columns:
            raise ValueError(
                "Stockstatus response is missing the 'AvailableQty' column; "
                "cannot apply low_stock_threshold."
            )
        qty = pd.to_numeric(frame["AvailableQty"], errors="coerce").fillna(0)
        frame = frame[qty <= low_stock_threshold]
    return frame.reset_index(drop=True)
