import datetime as dt
import json
from pathlib import Path
import pytest
from front_systems_mcp.reports.stores import StoreEntry, harvest, resolve

FIXTURE = Path(__file__).parent / "fixtures" / "saleslines_sample.json"


class FakeClient:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def fetch(self, entity, filters, select, window=None):
        self.calls.append((entity, list(filters), list(select), window))
        return self.rows


ROWS = [
    {"STOCKID_FK": 3229, "STOREID_FK": 3530, "Stock": "Høyer Paleet", "Store": "HC Paleet AS"},
    {"STOCKID_FK": 3229, "STOREID_FK": 3568, "Stock": "Høyer Paleet", "Store": "HC Paleet AS"},
    {"STOCKID_FK": 3229, "STOREID_FK": 3530, "Stock": "Høyer Paleet", "Store": "HC Paleet AS"},
    {"STOCKID_FK": 1333, "STOREID_FK": 1979, "Stock": "BMB Paleet", "Store": "BMB Paleet AS"},
]


async def test_groups_registers_under_one_stock():
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    paleet = next(e for e in entries if e.stock_id == 3229)
    assert sorted(paleet.register_ids) == [3530, 3568]
    assert paleet.line_count == 3


async def test_sorted_by_activity():
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    assert entries[0].stock_id == 3229


async def test_queries_saleslines_with_a_window():
    client = FakeClient(ROWS)
    await harvest(client, days=7, today=dt.date(2026, 8, 11))
    entity, filters, select, window = client.calls[0]
    assert entity == "Saleslines"
    assert window == (dt.date(2026, 8, 4), dt.date(2026, 8, 12))
    assert filters == []
    assert "Stock" in select and "STOCKID_FK" in select


async def test_resolve_matches_case_insensitively_on_either_name():
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    assert [e.stock_id for e in resolve(entries, "høyer paleet")] == [3229]
    assert [e.stock_id for e in resolve(entries, "HC PALEET")] == [3229]


async def test_resolve_returns_all_ambiguous_matches():
    # "Paleet" matches two unrelated companies; returning both lets the caller
    # disambiguate instead of silently picking one.
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    assert sorted(e.stock_id for e in resolve(entries, "Paleet")) == [1333, 3229]


async def test_empty_harvest_raises_with_guidance():
    with pytest.raises(ValueError) as exc:
        await harvest(FakeClient([]), days=30, today=dt.date(2026, 8, 11))
    assert "Widen the window" in str(exc.value)


async def test_harvest_against_real_fixture():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["value"]
    entries = await harvest(FakeClient(rows), days=30, today=dt.date(2026, 8, 11))
    assert entries
    assert all(e.stock_id and e.register_ids for e in entries)
