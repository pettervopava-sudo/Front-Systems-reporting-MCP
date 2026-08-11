import datetime as dt
import json
from pathlib import Path
import pandas as pd
import pytest
from front_systems_mcp.reports.sales import (
    aggregate, headers_to_frame, lines_to_frame, sales_report,
)

FIX = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, by_entity): self.by_entity, self.calls = by_entity, []
    async def fetch(self, entity, filters, select):
        self.calls.append((entity, list(filters), list(select)))
        return self.by_entity.get(entity, [])


def line(qty, price, cost=0.0, day="2026-08-01", **extra):
    row = {"Qty": qty, "Price": price, "Cost": cost, "Currency": "NOK",
           "SaleDate": f"{day}T00:00:00", "SALEID": extra.pop("sale", 1),
           "IsVoided": False, "Brand": extra.pop("brand", "B")}
    row.update(extra)
    return row


def test_returns_subtract_from_revenue():
    # The core bug this guards: Price is a unit price and returns carry
    # Qty = -1 with a POSITIVE price, so SUM(Price) adds them as revenue.
    frame = lines_to_frame([line(1, 500.0), line(-1, 200.0)])
    assert frame["LineTotal"].sum() == 300.0
    assert frame["Price"].sum() == 700.0  # the wrong answer, for contrast


def test_multi_unit_lines_multiply():
    frame = lines_to_frame([line(3, 100.0)])
    assert frame["LineTotal"].sum() == 300.0


def test_margin_uses_qty_for_both_price_and_cost():
    frame = lines_to_frame([line(2, 100.0, cost=40.0)])
    assert frame["LineCost"].sum() == 80.0
    assert frame["LineMargin"].sum() == 120.0


def test_returns_reverse_margin_too():
    frame = lines_to_frame([line(-1, 100.0, cost=40.0)])
    assert frame["LineMargin"].sum() == -60.0


def test_headers_exclude_voided():
    frame = headers_to_frame([
        {"SALEID": 1, "Total": 100.0, "IsVoided": False, "SaleDate": "2026-08-01T00:00:00"},
        {"SALEID": 2, "Total": 999.0, "IsVoided": True, "SaleDate": "2026-08-01T00:00:00"},
    ])
    assert frame["Total"].sum() == 100.0


def test_aggregate_by_day():
    frame = lines_to_frame([
        line(1, 100.0, day="2026-08-01"), line(1, 50.0, day="2026-08-01"),
        line(1, 25.0, day="2026-08-02"),
    ])
    out = aggregate(frame, ["day"]).set_index("day")
    assert out.loc["2026-08-01", "revenue"] == 150.0
    assert out.loc["2026-08-02", "revenue"] == 25.0


def test_aggregate_by_brand():
    frame = lines_to_frame([line(1, 100.0, brand="X"), line(1, 40.0, brand="Y")])
    out = aggregate(frame, ["Brand"]).set_index("Brand")
    assert out.loc["X", "revenue"] == 100.0


def test_currencies_are_never_summed_together():
    frame = lines_to_frame([
        line(1, 100.0), {**line(1, 100.0), "Currency": "SEK"},
    ])
    out = aggregate(frame, ["day"])
    assert set(out["Currency"]) == {"NOK", "SEK"}
    assert len(out) == 2


async def test_report_uses_headers_for_periods_before_line_history():
    client = FakeClient({"Sales": [
        {"SALEID": 1, "Total": 100.0, "IsVoided": False, "SaleDate": "2026-07-05T00:00:00"},
    ]})
    result = await sales_report(
        client, dt.date(2026, 7, 1), dt.date(2026, 8, 1), register_ids=[3530],
    )
    assert result.source == "Sales"
    assert client.calls[0][0] == "Sales"
    assert result.totals["revenue"] == 100.0
    assert "Qty" not in result.frame.columns  # headers carry no unit data


async def test_report_uses_lines_when_period_allows_and_stock_given():
    client = FakeClient({"Saleslines": [line(1, 100.0, day="2026-08-02")]})
    result = await sales_report(
        client, dt.date(2026, 8, 1), dt.date(2026, 8, 5), stock_id=3229,
    )
    assert result.source == "Saleslines"
    assert result.totals["revenue"] == 100.0


async def test_report_never_selects_pii():
    client = FakeClient({"Saleslines": [line(1, 100.0, day="2026-08-02")]})
    await sales_report(client, dt.date(2026, 8, 1), dt.date(2026, 8, 5), stock_id=3229)
    _, _, select = client.calls[0]
    assert not ({"Email", "Phone", "FirstName", "Address"} & set(select))


async def test_real_fixture_reconciles_lines_against_headers():
    # SUM(Qty*Price) must equal SUM(Total) over the same period. A mismatch
    # means the revenue formula regressed.
    lines = json.loads((FIX / "saleslines_sample.json").read_text())["value"]
    heads = json.loads((FIX / "sales_sample.json").read_text())["value"]
    lines_total = round(lines_to_frame(lines)["LineTotal"].sum(), 2)
    heads_total = round(headers_to_frame(heads)["Total"].sum(), 2)
    assert lines_total == heads_total, (
        f"lines {lines_total} != headers {heads_total}"
    )
