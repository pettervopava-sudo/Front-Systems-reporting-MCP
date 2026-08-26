"""Leseproxyen: klassifisering, PII-stripping, server og oppstrømsklient.

Ingen av disse testene rører nettverket. Der en ekte HTTP-rundtur trengs,
kjøres en fake oppstrøm på loopback i samme prosess.
"""
import json

import pytest

from front_systems_mcp.proxy import ALLOWED_ENTITIES, CUSTOMER_FIELDS, classify, strip_pii


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


def test_customer_fields_cover_the_odata_denylist():
    from front_systems_mcp.odata import PII_FIELDS
    assert PII_FIELDS <= CUSTOMER_FIELDS
