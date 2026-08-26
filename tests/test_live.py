"""Live API checks. Skipped unless FS_LIVE=1, since they need credentials."""
import datetime as dt
import os
import pytest

from front_systems_mcp.client import FrontSystemsClient
from front_systems_mcp.config import load_config
from front_systems_mcp.reports.sales import (
    headers_to_frame, lines_to_frame, sales_report,
)
from front_systems_mcp.reports.stores import harvest, resolve

pytestmark = pytest.mark.skipif(
    os.environ.get("FS_LIVE") != "1", reason="set FS_LIVE=1 to run live tests")

PALEET_STOCK = 3229
PALEET_REGISTERS = [3529, 3530, 3431, 3568]
AUG_FROM, AUG_TO = dt.date(2026, 8, 1), dt.date(2026, 8, 11)


@pytest.fixture
async def client():
    c = FrontSystemsClient(load_config())
    yield c
    await c.aclose()


async def test_store_map_contains_paleet(client):
    entries = await harvest(client, days=30)
    matches = resolve(entries, "Paleet")
    assert PALEET_STOCK in {e.stock_id for e in matches}
    # Two unrelated companies share the name; both must surface.
    assert len(matches) >= 2


async def test_registers_group_under_one_stock(client):
    entries = await harvest(client, days=30)
    paleet = next(e for e in entries if e.stock_id == PALEET_STOCK)
    assert set(PALEET_REGISTERS) <= set(paleet.register_ids)


async def test_lines_and_headers_agree_on_revenue(client):
    """The regression that matters most: Qty*Price must equal Sales.Total."""
    # Registers are derived from live data (harvest), not the PALEET_REGISTERS
    # constant, so the lines side (filtered by STOCKID_FK) and the headers side
    # (filtered by STOREID_FK) describe the same store by construction. A
    # hardcoded list would only coincidentally match today's registers; adding
    # a till would then fail this test for a reason unrelated to the revenue
    # formula it exists to guard. Do not "simplify" this back to PALEET_REGISTERS.
    entries = await harvest(client, days=30)
    paleet = next(e for e in entries if e.stock_id == PALEET_STOCK)
    registers = paleet.register_ids

    lines = await client.fetch(
        "Saleslines",
        [f"STOCKID_FK eq {PALEET_STOCK}",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Qty", "Price", "Cost", "Currency", "IsVoided"],
    )
    register_filter = " or ".join(f"STOREID_FK eq {r}" for r in registers)
    heads = await client.fetch(
        "Sales",
        [f"({register_filter})",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Total", "IsVoided"],
    )
    lines_total = round(float(lines_to_frame(lines)["LineTotal"].sum()), 2)
    heads_total = round(float(headers_to_frame(heads)["Total"].sum()), 2)
    assert lines_total == heads_total, f"{lines_total} != {heads_total}"


async def test_period_before_line_history_falls_back_to_headers(client):
    # Same reasoning as the reconciliation test above: derive registers from
    # live data rather than the PALEET_REGISTERS constant, so this test can't
    # develop the same drift if a register is added or removed.
    entries = await harvest(client, days=30)
    paleet = next(e for e in entries if e.stock_id == PALEET_STOCK)
    result = await sales_report(
        client, dt.date(2026, 7, 1), dt.date(2026, 8, 1),
        register_ids=paleet.register_ids,
    )
    assert result.source == "Sales"
    assert result.totals["revenue"] > 0
    assert result.totals["transactions"] > 0


async def test_returns_are_present_and_reduce_revenue(client):
    """If this ever finds no returns, the reconciliation test above is toothless."""
    rows = await client.fetch(
        "Saleslines",
        [f"STOCKID_FK eq {PALEET_STOCK}",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Qty", "Price", "Cost", "Currency", "IsVoided"],
    )
    frame = lines_to_frame(rows)
    returns = frame[frame["Qty"].astype(float) < 0]
    assert len(returns) > 0
    assert returns["LineTotal"].sum() < 0


async def test_saleslines_contains_no_voided_rows(client):
    """The reconciliation assumes voided sales produce no lines.

    headers_to_frame drops voided headers while lines_to_frame does not, so the
    two totals can only agree if the API omits voided lines rather than flagging
    them. Assert that directly: if it ever changes, this says so plainly instead
    of the reconciliation failing for an unexplained reason.
    """
    rows = await client.fetch(
        "Saleslines",
        [f"STOCKID_FK eq {PALEET_STOCK}",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Qty", "Price", "IsVoided"],
    )
    assert rows, "fixture precondition: the window must contain lines"
    voided = [r for r in rows if r.get("IsVoided")]
    assert not voided, (
        f"{len(voided)} voided line(s) returned — reconciliation assumes none. "
        "lines_to_frame would need to filter them."
    )


import json
import threading

import httpx as _httpx

from front_systems_mcp.proxy import (
    CUSTOMER_FIELDS, ReadProxy, UpstreamClient, upstream_url,
)


def test_proxy_returns_the_same_rows_as_a_direct_call_and_no_pii():
    """Én dags Saleslines direkte og via proxyen: like mange rader, null PII."""
    config = load_config()
    client = UpstreamClient(upstream_url(), config.subscription_key,
                            config.api_key, timeout=120.0)
    server = ReadProxy(0, client)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    query = "from='2026-07-01'&to='2026-07-01'&$top=2000000"
    try:
        direct = client("Saleslines", query)
        direct_rows = json.loads(direct.body)["value"]
        via = _httpx.get(f"{base}/odata/Saleslines?{query}", timeout=120.0)
        via_rows = via.json()["value"]
    finally:
        server.shutdown()
        server.server_close()
        client.close()

    assert len(via_rows) == len(direct_rows) > 0
    # Forutsetning: baseline-radene MAA baere kundefelter. Uten denne sjekken
    # ville paastanden under passert selv om strippingen var fjernet, paa en
    # dag der radene tilfeldigvis mangler kundefeltene.
    unstripped = {key for row in direct_rows for key in row}
    assert unstripped & CUSTOMER_FIELDS, (
        "baseline carries no customer fields, so the stripping assertion "
        "below would prove nothing")
    seen = {key for row in via_rows for key in row}
    assert not (seen & CUSTOMER_FIELDS)


def test_proxy_refuses_a_write_against_the_real_api():
    config = load_config()
    client = UpstreamClient(upstream_url(), config.subscription_key,
                            config.api_key, timeout=30.0)
    server = ReadProxy(0, client)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        response = _httpx.post(f"{base}/odata/Sales", json={}, timeout=30.0)
    finally:
        server.shutdown()
        server.server_close()
        client.close()
    assert response.status_code == 405
