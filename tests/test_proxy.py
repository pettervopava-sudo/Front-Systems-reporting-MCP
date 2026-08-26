"""Leseproxyen: klassifisering, PII-stripping, server og oppstrømsklient.

Ingen av disse testene rører nettverket. Der en ekte HTTP-rundtur trengs,
kjøres en fake oppstrøm på loopback i samme prosess.
"""
import inspect
import json
import threading

import httpx
import pytest
import respx

from front_systems_mcp.proxy import (
    ALLOWED_ENTITIES,
    CUSTOMER_FIELDS,
    ReadProxy,
    UpstreamClient,
    UpstreamResponse,
    classify,
    strip_pii,
)


def test_get_on_allowed_entity_is_forwarded():
    d = classify("GET", "/odata/Sales?$top=10")
    assert d.kind == "forward"
    assert d.entity == "Sales"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
def test_write_methods_are_rejected(method):
    d = classify(method, "/odata/Sales")
    assert d.kind == "reject"
    assert d.status == 405


def test_unknown_entity_is_rejected():
    d = classify("GET", "/odata/Customers")
    assert d.kind == "reject"
    assert d.status == 404


def test_path_outside_odata_is_rejected():
    d = classify("GET", "/admin/users")
    assert d.kind == "reject"
    assert d.status == 404


def test_healthz_is_its_own_kind():
    assert classify("GET", "/healthz").kind == "health"


def test_healthz_still_rejects_write_methods():
    assert classify("POST", "/healthz").status == 405


def test_every_known_entity_is_allowed():
    for entity in ALLOWED_ENTITIES:
        assert classify("GET", f"/odata/{entity}").kind == "forward"


def test_customer_fields_are_removed_from_rows():
    body = json.dumps({"value": [{
        "SALEID": 1, "Qty": 2, "Price": 199.0,
        "FirstName": "Kari", "LastName": "Nordmann",
        "Email": "kari@example.com", "Phone": "99887766",
        "CUSTOMERID_FK": 4711,
    }]}).encode()
    out = json.loads(strip_pii(body))
    row = out["value"][0]
    assert row == {"SALEID": 1, "Qty": 2, "Price": 199.0}


def test_reporting_fields_survive():
    body = json.dumps({"value": [{
        "Qty": 1, "IsEmployee": True, "Gender": "f",
        "Stock": "Høyer Bergen", "Email": "x@y.no",
    }]}).encode()
    row = json.loads(strip_pii(body))["value"][0]
    assert row["IsEmployee"] is True
    assert row["Gender"] == "f"
    assert row["Stock"] == "Høyer Bergen"
    assert "Email" not in row


def test_norwegian_characters_survive_reserialisation():
    body = json.dumps({"value": [{"Stock": "Høyer Sjølyst", "Email": "a@b.no"}]},
                      ensure_ascii=False).encode("utf-8")
    out = strip_pii(body)
    assert "Høyer Sjølyst" in out.decode("utf-8")


def test_bare_list_payload_is_handled():
    body = json.dumps([{"SALEID": 1, "Email": "a@b.no"}]).encode()
    assert json.loads(strip_pii(body)) == [{"SALEID": 1}]


def test_non_json_body_passes_through_untouched():
    body = b"<html>gateway error</html>"
    assert strip_pii(body) == body


def test_empty_value_list_passes_through():
    body = json.dumps({"value": []}).encode()
    assert json.loads(strip_pii(body)) == {"value": []}


def test_company_identifier_is_stripped_too():
    """COMPANYID_FK identifies the B2B customer just as CUSTOMERID_FK does."""
    body = json.dumps({"value": [{
        "SALEID": 1, "COMPANYID_FK": 88, "CompanyName": "Acme AS",
        "OrgNum": "999888777", "IsCompany": True,
    }]}).encode()
    assert json.loads(strip_pii(body))["value"][0] == {"SALEID": 1}


def test_customer_fields_cover_the_odata_denylist():
    from front_systems_mcp.odata import PII_FIELDS
    assert PII_FIELDS <= CUSTOMER_FIELDS


@pytest.fixture
def proxy_factory():
    """Starter en ReadProxy på efemer port med en stub-videresender.

    Returnerer (base_url, calls) der calls er en liste av (entity, query).
    """
    servers = []

    def start(response=None):
        calls = []

        def forward(entity, query):
            calls.append((entity, query))
            return response or UpstreamResponse(200, b'{"value": []}')

        srv = ReadProxy(0, forward)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{srv.server_address[1]}", calls

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_binds_only_to_loopback():
    """Loopback binding is a security property, so assert the real socket."""
    srv = ReadProxy(0, lambda entity, query: UpstreamResponse(200, b"{}"))
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_read_proxy_has_no_host_parameter():
    params = list(inspect.signature(ReadProxy.__init__).parameters)
    assert "host" not in params and "address" not in params


def test_healthz_answers_without_touching_upstream(proxy_factory):
    base, calls = proxy_factory()
    r = httpx.get(f"{base}/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert calls == []


def test_post_is_refused_and_never_reaches_upstream(proxy_factory):
    base, calls = proxy_factory()
    r = httpx.post(f"{base}/odata/Sales", json={"x": 1})
    assert r.status_code == 405
    assert calls == []


def test_unknown_entity_never_reaches_upstream(proxy_factory):
    base, calls = proxy_factory()
    assert httpx.get(f"{base}/odata/Customers").status_code == 404
    assert calls == []


def test_query_is_passed_verbatim(proxy_factory):
    base, calls = proxy_factory()
    query = "$select=SALEID,Qty&$top=2000000&from='2026-08-01'&to='2026-08-24'"
    httpx.get(f"{base}/odata/Saleslines?{query}")
    assert calls == [("Saleslines", query)]


def test_saleslines_response_is_stripped(proxy_factory):
    payload = json.dumps({"value": [{"SALEID": 1, "Email": "a@b.no"}]}).encode()
    base, _ = proxy_factory(UpstreamResponse(200, payload))
    row = httpx.get(f"{base}/odata/Saleslines").json()["value"][0]
    assert row == {"SALEID": 1}


def test_other_entities_pass_through_byte_identical(proxy_factory):
    payload = json.dumps({"value": [{"SALEID": 1, "Email": "a@b.no"}]}).encode()
    base, _ = proxy_factory(UpstreamResponse(200, payload))
    assert httpx.get(f"{base}/odata/Sales").content == payload


def test_upstream_error_status_and_body_pass_through(proxy_factory):
    base, _ = proxy_factory(UpstreamResponse(500, b'{"odata.error": "boom"}'))
    r = httpx.get(f"{base}/odata/Saleslines")
    assert r.status_code == 500
    assert r.content == b'{"odata.error": "boom"}'


def test_stdlib_request_logging_stays_suppressed():
    """Standardloggeren skriver hele forespørselslinjen -- query inkludert.

    En $filter kan bære kunde-ID-er, og en logglinje overlever lenger enn
    et svar, så overstyringen maa ikke forsvinne i en senere opprydding.
    """
    from front_systems_mcp.proxy import _Handler
    assert _Handler.log_message(object(), "%s", "GET /odata/Sales?$filter=x") is None


UP = "https://up.test"


def test_sends_both_api_keys():
    with respx.mock:
        route = respx.get(f"{UP}/odata/Sales").mock(
            return_value=httpx.Response(200, json={"value": []}))
        client = UpstreamClient(UP, "subkey", "apikey", timeout=5.0)
        client("Sales", "")
        client.close()
    sent = route.calls.last.request
    assert sent.headers["Ocp-Apim-Subscription-Key"] == "subkey"
    assert sent.headers["x-api-key"] == "apikey"


def test_query_reaches_upstream_verbatim():
    query = "$select=SALEID&$top=2000000&from='2026-08-01'"
    with respx.mock:
        route = respx.get(url__startswith=f"{UP}/odata/Saleslines").mock(
            return_value=httpx.Response(200, json={"value": []}))
        client = UpstreamClient(UP, "s", "a", timeout=5.0)
        client("Saleslines", query)
        client.close()
    assert route.calls.last.request.url.query.decode() == query


def test_status_and_body_are_returned_unchanged():
    with respx.mock:
        respx.get(f"{UP}/odata/Sales").mock(
            return_value=httpx.Response(503, content=b"upstream down"))
        client = UpstreamClient(UP, "s", "a", timeout=5.0)
        result = client("Sales", "")
        client.close()
    assert result.status == 503
    assert result.body == b"upstream down"


def test_timeout_maps_to_504():
    with respx.mock:
        respx.get(f"{UP}/odata/Sales").mock(side_effect=httpx.ReadTimeout("slow"))
        client = UpstreamClient(UP, "s", "a", timeout=1.0)
        result = client("Sales", "")
        client.close()
    assert result.status == 504


def test_connection_failure_maps_to_502():
    with respx.mock:
        respx.get(f"{UP}/odata/Sales").mock(side_effect=httpx.ConnectError("no route"))
        client = UpstreamClient(UP, "s", "a", timeout=1.0)
        result = client("Sales", "")
        client.close()
    assert result.status == 502
