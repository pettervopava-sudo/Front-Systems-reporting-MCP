import httpx
import pytest
import respx
from front_systems_mcp.client import (
    ApiError, AuthError, FrontSystemsClient, RateLimitedError,
)
from front_systems_mcp.config import Config

BASE = "https://api.test"
CFG = Config(base_url=BASE, subscription_key="subkey", api_key="apikey")
URL = f"{BASE}/odata/Sales"


@pytest.fixture
async def client():
    c = FrontSystemsClient(CFG, timeout=5.0)
    yield c
    await c.aclose()


@respx.mock
async def test_sends_both_auth_headers(client):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"value": []}))
    await client.fetch("Sales", ["SaleDate ge datetime'2026-08-01T00:00:00'"], ["SALEID"])
    sent = route.calls.last.request
    assert sent.headers["Ocp-Apim-Subscription-Key"] == "subkey"
    assert sent.headers["x-api-key"] == "apikey"


@respx.mock
async def test_returns_the_value_array(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"value": [{"SALEID": 1}]}))
    rows = await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])
    assert rows == [{"SALEID": 1}]


@respx.mock
async def test_401_raises_auth_error_without_leaking_keys(client):
    respx.get(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError) as exc:
        await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])
    message = str(exc.value)
    assert "subkey" not in message and "apikey" not in message


@respx.mock
async def test_odata_error_body_is_surfaced(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json={
        "odata.error": {"message": {"value": "Could not find a property named 'Bogus'"}}
    }))
    with pytest.raises(ApiError) as exc:
        await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])
    assert "Bogus" in str(exc.value)


@respx.mock
async def test_429_retries_then_succeeds(client):
    respx.get(URL).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"value": [{"SALEID": 7}]}),
    ])
    rows = await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])
    assert rows == [{"SALEID": 7}]


@respx.mock
async def test_429_exhausted_raises_rate_limited(client):
    respx.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
    with pytest.raises(RateLimitedError):
        await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])


@respx.mock
async def test_500_retries_then_raises(client):
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ApiError):
        await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])
    assert route.call_count > 1


@respx.mock
async def test_missing_value_key_is_an_error_not_an_empty_result(client):
    # An empty result is a legitimate answer here, so a malformed body must
    # never be allowed to masquerade as one.
    respx.get(URL).mock(return_value=httpx.Response(200, json={"unexpected": 1}))
    with pytest.raises(ApiError):
        await client.fetch("Sales", ["STOREID_FK eq 1"], ["SALEID"])


@respx.mock
async def test_unsafe_filter_is_rejected_before_any_request(client):
    # build_params validates filter shapes; the client must let that raise rather
    # than swallowing it or turning it into an ApiError. Nothing should hit the wire.
    from front_systems_mcp.odata import UnsafeQueryError
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"value": []}))
    with pytest.raises(UnsafeQueryError):
        await client.fetch("Sales", ["Store eq 1"], ["SALEID"])
    assert route.call_count == 0
