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


async def test_search_matches_when_the_other_field_is_null():
    rows = [
        {"Productid": 1, "Stockid": 1, "Name": None, "Brand": "Polo Ralph Lauren",
         "AvailableQty": 5},
        {"Productid": 2, "Stockid": 1, "Name": "Polo Shirt", "Brand": None,
         "AvailableQty": 3},
        {"Productid": 3, "Stockid": 1, "Name": "Scarf", "Brand": "Acne",
         "AvailableQty": 1},
    ]
    frame = await stock_report(FakeClient(rows), search="polo")
    assert sorted(frame["Productid"]) == [1, 2]


async def test_missing_quantity_column_explains_itself():
    rows = [{"Productid": 1, "Stockid": 1, "Name": "X", "Brand": "Y"}]
    with pytest.raises(ValueError) as exc:
        await stock_report(FakeClient(rows), low_stock_threshold=0)
    assert "AvailableQty" in str(exc.value)


async def test_threshold_includes_zero_and_negative_quantities():
    rows = [
        {"Productid": 1, "Stockid": 1, "Name": "A", "Brand": "B", "AvailableQty": 0},
        {"Productid": 2, "Stockid": 1, "Name": "C", "Brand": "D", "AvailableQty": 1},
        {"Productid": 3, "Stockid": 1, "Name": "E", "Brand": "F", "AvailableQty": -2},
    ]
    frame = await stock_report(FakeClient(rows), low_stock_threshold=0)
    assert sorted(frame["Productid"]) == [1, 3]


async def test_default_snapshot_is_oslo_time_not_server_time():
    import datetime as dt
    from zoneinfo import ZoneInfo

    class Capture:
        def __init__(self): self.params = None
        async def fetch_raw(self, entity, params):
            self.params = params
            return []

    client = Capture()
    await stock_report(client)
    expected = (dt.datetime.now(ZoneInfo("Europe/Oslo")) - dt.timedelta(days=1)).date()
    assert client.params["snapshotDateTime"] == f"'{expected} 23:00:00'"


async def test_explicit_snapshot_is_passed_through_unchanged():
    import datetime as dt

    class Capture:
        def __init__(self): self.params = None
        async def fetch_raw(self, entity, params):
            self.params = params
            return []

    client = Capture()
    await stock_report(client, snapshot=dt.datetime(2026, 7, 31, 23, 0, 0))
    assert client.params["snapshotDateTime"] == "'2026-07-31 23:00:00'"
