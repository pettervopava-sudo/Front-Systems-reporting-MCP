import datetime as dt
import pytest
from front_systems_mcp.reports.stock import stock_report


class FakeClient:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def fetch_raw(self, entity, params):
        self.calls.append((entity, dict(params)))
        return self.rows


ROWS = [
    {"Productid": 1, "Stockid": 3229, "Name": "Jacket", "Brand": "X",
     "Qty": 5, "AvailableQty": 4, "StockName": "Høyer Paleet"},
    {"Productid": 2, "Stockid": 3229, "Name": "Scarf", "Brand": "Y",
     "Qty": 0, "AvailableQty": 0, "StockName": "Høyer Paleet"},
    {"Productid": 3, "Stockid": 999, "Name": "Boot", "Brand": "X",
     "Qty": 50, "AvailableQty": 50, "StockName": "Other"},
]


async def test_snapshot_is_sent_as_a_plain_query_param():
    client = FakeClient(ROWS)
    await stock_report(client, snapshot=dt.datetime(2026, 7, 31, 23, 0, 0))
    _, params = client.calls[0]
    assert params["snapshotDateTime"] == "'2026-07-31 23:00:00'"


async def test_filters_to_one_stock():
    frame = await stock_report(FakeClient(ROWS), stock_id=3229)
    assert set(frame["Productid"]) == {1, 2}


async def test_search_matches_name_or_brand_case_insensitively():
    frame = await stock_report(FakeClient(ROWS), search="jack")
    assert frame["Productid"].tolist() == [1]


async def test_low_stock_threshold_filters_at_or_below():
    frame = await stock_report(FakeClient(ROWS), stock_id=3229, low_stock_threshold=0)
    assert frame["Productid"].tolist() == [2]


async def test_empty_rows_yield_an_empty_frame_not_an_error():
    frame = await stock_report(FakeClient([]), stock_id=3229)
    assert frame.empty
