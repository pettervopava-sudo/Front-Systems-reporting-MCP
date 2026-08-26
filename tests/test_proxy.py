"""Leseproxyen: klassifisering, PII-stripping, server og oppstrømsklient.

Ingen av disse testene rører nettverket. Der en ekte HTTP-rundtur trengs,
kjøres en fake oppstrøm på loopback i samme prosess.
"""
import pytest

from front_systems_mcp.proxy import ALLOWED_ENTITIES, classify


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
