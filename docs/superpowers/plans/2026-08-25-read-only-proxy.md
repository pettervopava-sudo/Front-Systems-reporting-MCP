# Leseproxy for Front Systems — implementasjonsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** En lokal proxy som gjør at alt verktøy i dette repoet fysisk kun kan lese fra Front Systems — GET, godkjente entiteter, uten kundedata.

**Architecture:** Én modul i pakken (`front_systems_mcp.proxy`) med stdlib `ThreadingHTTPServer` bundet til loopback. Ren beslutningslogikk (`classify`, `strip_pii`) skilles fra transport (`UpstreamClient`) og injiseres i serveren, så alt kan testes uten nettverk. Klientene peker `FRONT_SYSTEMS_BASE_URL` på proxyen; nøklene brukes kun av proxyen selv.

**Tech Stack:** Python 3.13, stdlib `http.server`, `httpx` (sync-klient), `truststore`, `python-dotenv` — alle allerede avhengigheter. Tester: pytest + respx.

**Spec:** `docs/superpowers/specs/2026-08-25-read-only-proxy-design.md`

## Global Constraints

- **Ingen nye avhengigheter.** `httpx`, `truststore`, `python-dotenv` finnes i `pyproject.toml`; ikke legg til flere.
- **Loopback-only.** Serveren binder alltid `127.0.0.1`. Ingen parameter, ingen miljøvariabel, ingen CLI-flagg skal kunne endre adressen. Kun porten er konfigurerbar.
- **Aldri logg nøkler eller query-innhold.** Loggen inneholder tidspunkt, metode, entitet, status, varighet, antall byte — ingenting annet.
- **TLS via truststore:** `ssl.SSLContext` fra `truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` — maskinens TLS-proxy gjør at Pythons innebygde CA-lager feiler. Aldri `verify=False`.
- **Testkonvensjoner:** `pytest`, `respx` for httpx-mocking, `asyncio_mode = "auto"` (sync-tester går fint), live-tester gates på `FS_LIVE=1` via `pytestmark`.
- **Commit-meldinger på engelsk i ren ASCII**, med `Co-Authored-By:`-trailer, slik resten av historikken er.
- Kjør tester med `python3 -m pytest` fra repo-roten.

## File Structure

| Fil | Ansvar |
|---|---|
| `src/front_systems_mcp/proxy.py` (ny) | Hele proxyen: klassifisering, PII-stripping, server, oppstrømsklient, CLI |
| `tests/test_proxy.py` (ny) | Enhets- og integrasjonstester uten nettverk |
| `tests/test_live.py` (endres) | Én live røyktest via proxyen |
| `scripts/serve_proxy.sh` (ny) | Start/restart av proxyen, samme mønster som `serve_reports.sh` |
| `.env.example` (endres) | Skillet mellom `BASE_URL` (proxy) og `UPSTREAM_URL` (Front Systems) |
| `.gitignore` (endres) | `proxy.log` |
| `CLAUDE.md` (endres) | Kort avsnitt om at proxyen er standard lesevei |

Alt bor i én modul med vilje: delene er små, henger tett sammen og leses best samlet. Grensesnittene mellom dem (`classify`, `strip_pii`, `forward`) er rene funksjoner som kan testes hver for seg.

---

### Task 1: Klassifisering av forespørsler

**Files:**
- Create: `src/front_systems_mcp/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: ingenting
- Produces: `Decision(kind: str, entity: str, status: int, reason: str)` og `classify(method: str, path: str) -> Decision`. `kind` er `"forward"`, `"health"` eller `"reject"`. Konstantene `ALLOWED_ENTITIES: frozenset[str]`, `DEFAULT_PORT: int = 8812`, `DEFAULT_UPSTREAM: str`.

- [ ] **Step 1: Write the failing test**

Opprett `tests/test_proxy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_proxy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.proxy'`

- [ ] **Step 3: Write minimal implementation**

Opprett `src/front_systems_mcp/proxy.py`:

```python
"""Lokal leseproxy foran Front Systems.

Front Systems kan ikke utstede API-nøkler med kun lesetilgang, så nøklene i
.env gir skrivetilgang til kassasystemet. Denne proxyen er sperren: den
kjører på brukerens egen maskin, tar imot kun GET mot en liten allowlist av
OData-entiteter, sender spørringen videre uendret, og fjerner kundefelter
fra hver Saleslines-rad på vei tilbake. Klientene peker
FRONT_SYSTEMS_BASE_URL hit, og da finnes det ingen kodevei fra verktøyene i
repoet til en skrivemetode.

Bindingen er loopback og ikke konfigurerbar: en 0.0.0.0-binding ved et uhell
ville gjort en personlig sperre om til en åpen salgsdatafeed på kontornettet.

Kjør:
  python3 -m front_systems_mcp.proxy [--port 8812]
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_PORT = 8812
DEFAULT_UPSTREAM = "https://frontsystemsapis.frontsystems.no"

#: Entitetene rapportserien og MCP-verktøyene faktisk bruker. Alt utenfor
#: listen svares med 404 — en ukjent sti er like gjerne en skrivefeil som et
#: nytt endepunkt, og proxyen skal ikke gjette.
ALLOWED_ENTITIES = frozenset({
    "Sales", "Saleslines", "Stockstatus", "Stockmovements", "Products",
})


@dataclass(frozen=True)
class Decision:
    """Hva proxyen skal gjøre med en forespørsel, avgjort uten I/O."""
    kind: str            # "forward" | "health" | "reject"
    entity: str = ""
    status: int = 0
    reason: str = ""


def classify(method: str, path: str) -> Decision:
    if method != "GET":
        return Decision(
            "reject", status=405,
            reason=f"{method} er ikke tillatt: denne proxyen er kun lesing.")
    route = urlparse(path).path.rstrip("/")
    if route == "/healthz":
        return Decision("health")
    parts = [p for p in route.split("/") if p]
    if len(parts) != 2 or parts[0] != "odata":
        return Decision(
            "reject", status=404,
            reason="Kun /odata/<entitet> serveres av denne proxyen.")
    entity = parts[1]
    if entity not in ALLOWED_ENTITIES:
        return Decision(
            "reject", status=404,
            reason=f"{entity!r} er ikke en av: "
                   f"{', '.join(sorted(ALLOWED_ENTITIES))}.")
    return Decision("forward", entity=entity)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_proxy.py -v`
Expected: PASS — 12 tester (7 funksjoner, `write_methods` parametrisert i 6)

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/proxy.py tests/test_proxy.py
git commit -m "feat: read-proxy request classification (GET + entity allowlist)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: PII-stripping av Saleslines-svar

**Files:**
- Modify: `src/front_systems_mcp/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `front_systems_mcp.odata.PII_FIELDS`
- Produces: `CUSTOMER_FIELDS: frozenset[str]`, `STRIP_ENTITIES: frozenset[str]`, `strip_pii(body: bytes) -> bytes`

- [ ] **Step 1: Write the failing test**

Legg til i `tests/test_proxy.py`:

```python
import json

from front_systems_mcp.proxy import CUSTOMER_FIELDS, strip_pii


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_proxy.py -k strip -v`
Expected: FAIL — `ImportError: cannot import name 'strip_pii'`

- [ ] **Step 3: Write minimal implementation**

Legg til i `src/front_systems_mcp/proxy.py` (import øverst, resten under `classify`):

```python
import json

from .odata import PII_FIELDS
```

```python
#: PII_FIELDS dekker det $select kan be om; en rå Saleslines-rad bærer mer.
#: Alt kundebærende fjernes her, slik at lesetilgangen er trygg å dele.
#: IsEmployee beholdes bevisst — rabatt- og selgerrapportene bruker det til
#: å skille ut ansattekjøp.
CUSTOMER_FIELDS = frozenset(PII_FIELDS) | frozenset({
    "CustomerGender", "CompanyName", "OrgNum", "IsCompany", "CountryCode",
    "AgreedSendEmail", "AgreedSendSMS", "BonusBalance", "BonusFactor",
    "BonusTotal", "SaleBonusFactor",
})

#: Kun Saleslines bærer kundefelter; andre entiteter strømmes uparsede.
STRIP_ENTITIES = frozenset({"Saleslines"})


def strip_pii(body: bytes) -> bytes:
    """Fjern kundefelter fra et OData-svar.

    Returnerer kroppen uendret når den ikke er JSON vi kjenner igjen — en
    gateway-feilside skal videre urørt, ikke bli til en parse-feil.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    if isinstance(payload, dict) and isinstance(payload.get("value"), list):
        rows = payload["value"]
    elif isinstance(payload, list):
        rows = payload
    else:
        return body
    for row in rows:
        if isinstance(row, dict):
            for field_name in CUSTOMER_FIELDS:
                row.pop(field_name, None)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_proxy.py -v`
Expected: PASS — alle tester fra Task 1 og 2

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/proxy.py tests/test_proxy.py
git commit -m "feat: strip customer fields from Saleslines responses

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Serveren med injisert videresender

**Files:**
- Modify: `src/front_systems_mcp/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `classify`, `strip_pii`, `STRIP_ENTITIES`
- Produces: `UpstreamResponse(status: int, body: bytes, content_type: str)` og `ReadProxy(port: int, forward: Callable[[str, str], UpstreamResponse])`. `forward` kalles med `(entity, query_string)` — query uten `?`. `ReadProxy` er en `ThreadingHTTPServer`; `port=0` gir efemer port, og `server_address[1]` er den faktiske porten.

- [ ] **Step 1: Write the failing test**

Legg til i `tests/test_proxy.py`:

```python
import inspect
import threading

import httpx

from front_systems_mcp.proxy import ReadProxy, UpstreamResponse


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


def test_binds_only_to_loopback(proxy_factory):
    base, _ = proxy_factory()
    assert base.startswith("http://127.0.0.1:")


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_proxy.py -k proxy -v`
Expected: FAIL — `ImportError: cannot import name 'ReadProxy'`

- [ ] **Step 3: Write minimal implementation**

Legg til imports øverst i `src/front_systems_mcp/proxy.py`:

```python
import sys
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
```

Og etter `strip_pii`:

```python
@dataclass(frozen=True)
class UpstreamResponse:
    """Svaret fra Front Systems, redusert til det proxyen sender videre."""
    status: int
    body: bytes
    content_type: str = "application/json"


Forwarder = Callable[[str, str], UpstreamResponse]


class _Handler(BaseHTTPRequestHandler):
    server_version = "FrontSystemsReadProxy/1.0"

    # Alle metoder går gjennom samme port; classify() avgjør skjebnen, så
    # en ny HTTP-metode kan ikke smette forbi ved at do_X mangler.
    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PUT(self): self._handle("PUT")
    def do_PATCH(self): self._handle("PATCH")
    def do_DELETE(self): self._handle("DELETE")
    def do_HEAD(self): self._handle("HEAD")
    def do_OPTIONS(self): self._handle("OPTIONS")

    def _handle(self, method: str) -> None:
        started = time.monotonic()
        decision = classify(method, self.path)
        if decision.kind == "health":
            label, status, body = "healthz", 200, b'{"ok": true}'
        elif decision.kind == "reject":
            label, status = "avvist", decision.status
            body = json.dumps({"error": decision.reason},
                              ensure_ascii=False).encode("utf-8")
        else:
            label = decision.entity
            query = urlparse(self.path).query
            upstream = self.server.forward(decision.entity, query)
            status, body = upstream.status, upstream.body
            if decision.entity in STRIP_ENTITIES and status == 200:
                body = strip_pii(body)
            self._send(status, body, upstream.content_type)
            self._log(method, label, status, started, len(body))
            return
        self._send(status, body, "application/json")
        self._log(method, label, status, started, len(body))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log(self, method: str, label: str, status: int,
             started: float, size: int) -> None:
        # Aldri query-innhold eller feltverdier: en $filter kan inneholde
        # kunde-ID-er, og en logglinje overlever lenger enn et svar.
        ms = (time.monotonic() - started) * 1000
        print(f"{time.strftime('%H:%M:%S')} {method} {label} {status} "
              f"{ms:.0f}ms {size}b", file=sys.stderr, flush=True)

    def log_message(self, fmt, *args):
        return  # stdlib-loggeren skriver hele forespørselslinjen, query inkludert


class ReadProxy(ThreadingHTTPServer):
    """HTTP-server bundet til loopback, med injisert videresender.

    Adressen er hardkodet med vilje: se modul-docstringen.
    """
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, forward: Forwarder) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.forward = forward
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_proxy.py -v`
Expected: PASS — alle tester

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/proxy.py tests/test_proxy.py
git commit -m "feat: loopback read-proxy server with injected forwarder

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Oppstrømsklienten

**Files:**
- Modify: `src/front_systems_mcp/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `UpstreamResponse`
- Produces: `UpstreamClient(base_url: str, subscription_key: str, api_key: str, timeout: float = 600.0)` med `__call__(entity: str, query: str) -> UpstreamResponse` og `close() -> None`. Oppfyller `Forwarder`-signaturen fra Task 3.

- [ ] **Step 1: Write the failing test**

Legg til i `tests/test_proxy.py`:

```python
import respx

from front_systems_mcp.proxy import UpstreamClient

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_proxy.py -k upstream -v`
Expected: FAIL — `ImportError: cannot import name 'UpstreamClient'`

- [ ] **Step 3: Write minimal implementation**

Legg til imports øverst:

```python
import ssl

import httpx
import truststore
```

Og etter `ReadProxy`:

```python
class UpstreamClient:
    """Videresender til Front Systems med de ekte nøklene.

    Synkron med vilje: serveren er trådbasert, og en async-klient ville
    krevd en hendelsesløkke per tråd uten å gi noe tilbake.
    """

    def __init__(self, base_url: str, subscription_key: str, api_key: str,
                 timeout: float = 600.0) -> None:
        # Maskinens TLS-proxy gjør at Pythons innebygde CA-lager feiler på en
        # gyldig kjede; truststore bruker systemets. Aldri verify=False —
        # det ville eksponert nøklene for den som avlytter.
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(
            timeout=timeout, verify=ctx,
            headers={
                "Ocp-Apim-Subscription-Key": subscription_key,
                "x-api-key": api_key,
                "Accept": "application/json",
            })

    def __call__(self, entity: str, query: str) -> UpstreamResponse:
        # Spørringen settes på som rå streng: httpx' parameter-koding ville
        # kunnet omskrive $filter-sitater og datoformatene from/to bruker.
        url = f"{self._base}/odata/{entity}" + (f"?{query}" if query else "")
        try:
            response = self._http.get(url)
        except httpx.TimeoutException:
            return UpstreamResponse(
                504, b'{"error": "Front Systems svarte ikke i tide."}')
        except httpx.TransportError:
            return UpstreamResponse(
                502, b'{"error": "Naar ikke Front Systems."}')
        return UpstreamResponse(
            response.status_code, response.content,
            response.headers.get("Content-Type", "application/json"))

    def close(self) -> None:
        self._http.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_proxy.py -v`
Expected: PASS — alle tester

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/proxy.py tests/test_proxy.py
git commit -m "feat: upstream client with verbatim query and 502/504 mapping

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: CLI, oppstrøms-URL og ende-til-ende

**Files:**
- Modify: `src/front_systems_mcp/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `ReadProxy`, `UpstreamClient`, `DEFAULT_PORT`, `DEFAULT_UPSTREAM`, `load_config`
- Produces: `upstream_url() -> str`, `check_not_self(upstream: str, port: int) -> None` (kaster `SystemExit`), `main(argv: list[str] | None = None) -> None`

- [ ] **Step 1: Write the failing test**

Legg til i `tests/test_proxy.py`:

```python
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from front_systems_mcp.proxy import DEFAULT_UPSTREAM, check_not_self, upstream_url


def test_upstream_defaults_to_front_systems(monkeypatch, tmp_path):
    monkeypatch.delenv("FRONT_SYSTEMS_UPSTREAM_URL", raising=False)
    assert upstream_url(tmp_path / "missing.env") == DEFAULT_UPSTREAM


def test_upstream_is_read_from_the_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("FRONT_SYSTEMS_UPSTREAM_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("FRONT_SYSTEMS_UPSTREAM_URL=https://from-file.test/\n")
    assert upstream_url(env_file) == "https://from-file.test"


def test_process_environment_wins_over_the_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONT_SYSTEMS_UPSTREAM_URL", "https://from-env.test/")
    env_file = tmp_path / ".env"
    env_file.write_text("FRONT_SYSTEMS_UPSTREAM_URL=https://from-file.test\n")
    assert upstream_url(env_file) == "https://from-env.test"


def test_pointing_upstream_at_the_proxy_itself_is_refused():
    with pytest.raises(SystemExit):
        check_not_self("http://127.0.0.1:8812", 8812)


def test_pointing_upstream_elsewhere_is_fine():
    check_not_self("https://frontsystemsapis.frontsystems.no", 8812)


@pytest.fixture
def fake_upstream():
    """En ekte HTTP-server på loopback som spiller Front Systems."""
    state = {"status": 200, "body": b'{"value": []}',
             "content_type": "application/json", "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"].append(
                {"path": self.path, "headers": dict(self.headers)})
            body = state["body"]
            self.send_response(state["status"])
            self.send_header("Content-Type", state["content_type"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def live_proxy(fake_upstream):
    """Ekte proxy + ekte UpstreamClient mot fake_upstream."""
    client = UpstreamClient(fake_upstream["url"], "subkey", "apikey", timeout=5.0)
    srv = ReadProxy(0, client)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", fake_upstream
    srv.shutdown()
    srv.server_close()
    client.close()


def test_end_to_end_query_arrives_verbatim(live_proxy):
    base, upstream = live_proxy
    query = "$select=SALEID,Qty&$top=2000000&from='2026-08-01'&to='2026-08-24'"
    httpx.get(f"{base}/odata/Saleslines?{query}")
    assert upstream["calls"][-1]["path"] == f"/odata/Saleslines?{query}"


def test_end_to_end_client_credentials_are_never_forwarded(live_proxy):
    base, upstream = live_proxy
    httpx.get(f"{base}/odata/Sales",
              headers={"x-api-key": "leaked", "Ocp-Apim-Subscription-Key": "leaked"})
    sent = upstream["calls"][-1]["headers"]
    assert sent["x-api-key"] == "apikey"
    assert sent["Ocp-Apim-Subscription-Key"] == "subkey"


def test_end_to_end_pii_is_stripped(live_proxy):
    base, upstream = live_proxy
    upstream["body"] = json.dumps({"value": [
        {"SALEID": 1, "Qty": 2, "Email": "a@b.no", "FirstName": "Kari",
         "Stock": "Høyer Bergen"},
    ]}, ensure_ascii=False).encode("utf-8")
    row = httpx.get(f"{base}/odata/Saleslines").json()["value"][0]
    assert row == {"SALEID": 1, "Qty": 2, "Stock": "Høyer Bergen"}


def test_end_to_end_write_never_reaches_upstream(live_proxy):
    base, upstream = live_proxy
    before = len(upstream["calls"])
    assert httpx.post(f"{base}/odata/Sales", json={}).status_code == 405
    assert len(upstream["calls"]) == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_proxy.py -k "upstream_defaults or not_self or end_to_end" -v`
Expected: FAIL — `ImportError: cannot import name 'upstream_url'`

- [ ] **Step 3: Write minimal implementation**

Legg til imports øverst:

```python
import argparse
import os
from pathlib import Path

from dotenv import dotenv_values

from .config import load_config
```

Og nederst i modulen:

```python
def upstream_url(env_file: Path | None = None) -> str:
    """Front Systems-adressen proxyen selv snakker med.

    Leses fra prosessmiljøet eller .env i repo-roten; BASE_URL peker på
    proxyen og kan derfor ikke brukes her. env_file er kun for tester —
    ellers ville de lest utviklerens egen .env.
    """
    if env_file is None:
        env_file = Path(__file__).resolve().parents[2] / ".env"
    values = (dotenv_values(env_file, encoding="utf-8-sig")
              if env_file.exists() else {})
    raw = (os.environ.get("FRONT_SYSTEMS_UPSTREAM_URL")
           or values.get("FRONT_SYSTEMS_UPSTREAM_URL")
           or DEFAULT_UPSTREAM)
    return raw.strip().rstrip("/")


def check_not_self(upstream: str, port: int) -> None:
    """Stopp den vanligste feilkonfigurasjonen: proxyen som sin egen kilde."""
    parsed = urlparse(upstream)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if loopback and (parsed.port or 80) == port:
        raise SystemExit(
            f"FRONT_SYSTEMS_UPSTREAM_URL peker paa proxyen selv ({upstream}). "
            f"Sett den til Front Systems, f.eks. {DEFAULT_UPSTREAM}.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Leseproxy foran Front Systems.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"lokal port (default {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    upstream = upstream_url()
    check_not_self(upstream, args.port)
    config = load_config()
    client = UpstreamClient(upstream, config.subscription_key, config.api_key)
    server = ReadProxy(args.port, client)
    print(f"leseproxy paa http://127.0.0.1:{args.port} -> {upstream}",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        client.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_proxy.py -v`
Expected: PASS — alle tester

- [ ] **Step 5: Verify the whole suite still passes**

Run: `python3 -m pytest -q`
Expected: alle eksisterende tester passerer uendret (142 + de nye)

- [ ] **Step 6: Commit**

```bash
git add src/front_systems_mcp/proxy.py tests/test_proxy.py
git commit -m "feat: proxy CLI, upstream resolution and end-to-end tests

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Startskript, konfigurasjon og dokumentasjon

**Files:**
- Create: `scripts/serve_proxy.sh`
- Modify: `.env.example`, `.gitignore`, `CLAUDE.md`

**Interfaces:**
- Consumes: `python3 -m front_systems_mcp.proxy --port <n>`, `/healthz`
- Produces: ingenting for kode; oppsettet en ny bruker følger

- [ ] **Step 1: Write the start script**

Opprett `scripts/serve_proxy.sh`:

```bash
#!/bin/bash
# Leseproxy foran Front Systems — kjøres lokalt hos hver bruker.
# Alt klientverktøy peker på http://127.0.0.1:8812 via FRONT_SYSTEMS_BASE_URL,
# så ingen kodevei går til en skrivemetode. Nøklene leses fra .env og
# forlater aldri denne maskinen.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8812}"
pkill -f "front_systems_mcp.proxy" 2>/dev/null || true
sleep 0.5
cd "$ROOT"
# PYTHONPATH gjør at en fersk klone virker uten pip install -e .
PYTHONPATH="$ROOT/src:${PYTHONPATH:-}" nohup python3 -m front_systems_mcp.proxy \
  --port "$PORT" >> "$ROOT/proxy.log" 2>&1 & disown
sleep 1
if curl -sf "http://127.0.0.1:$PORT/healthz" > /dev/null; then
  echo "leseproxy kjører på http://127.0.0.1:$PORT (logg: proxy.log)"
else
  echo "proxyen startet ikke — se $ROOT/proxy.log" >&2
  exit 1
fi
```

- [ ] **Step 2: Verify the script starts and answers**

```bash
chmod +x scripts/serve_proxy.sh
bash scripts/serve_proxy.sh
curl -s http://127.0.0.1:8812/healthz
```

Expected: `leseproxy kjører på http://127.0.0.1:8812` og `{"ok": true}`

- [ ] **Step 3: Verify the guard rail end to end against the real API**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8812/odata/Sales
curl -s "http://127.0.0.1:8812/odata/Sales?\$top=1&\$select=SALEID" | head -c 200
```

Expected: `405` på første, en ekte JSON-rad på andre.

- [ ] **Step 4: Update .env.example**

Erstatt de tre variabel-linjene nederst i `.env.example` med:

```
# Klientene (MCP-server, fs_query, rapportskript) snakker med den lokale
# leseproxyen — ikke direkte med Front Systems. Start den med
# `bash scripts/serve_proxy.sh`.
FRONT_SYSTEMS_BASE_URL=http://127.0.0.1:8812

# Proxyen selv snakker med Front Systems. Kan utelates; da brukes denne
# adressen automatisk.
FRONT_SYSTEMS_UPSTREAM_URL=https://frontsystemsapis.frontsystems.no

FRONT_SYSTEMS_SUBSCRIPTION_KEY=
FRONT_SYSTEMS_API_KEY=
```

- [ ] **Step 5: Ignore the proxy log**

Legg til i `.gitignore` etter `.DS_Store`:

```
# Leseproxyens logg (tidspunkt, entitet, status — men hold den utenfor git)
proxy.log
```

- [ ] **Step 6: Document the guard rail in CLAUDE.md**

Legg inn dette avsnittet i `CLAUDE.md` rett før `## Praktisk`:

```markdown
## Leseproxyen (standard vei mot API-et)

Front Systems kan ikke gi lese-nøkler, så alle kall går gjennom en lokal
proxy som kun slipper gjennom GET og fjerner kundefelter:
`bash scripts/serve_proxy.sh` (port 8812), og `.env` peker
`FRONT_SYSTEMS_BASE_URL` dit. Hver bruker kjører sin egen — ingen maskin
er avhengig av noen annen. Design:
`docs/superpowers/specs/2026-08-25-read-only-proxy-design.md`.
```

- [ ] **Step 7: Commit**

```bash
git add scripts/serve_proxy.sh .env.example .gitignore CLAUDE.md
git commit -m "feat: serve_proxy.sh, split env config and document the guard rail

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Live røyktest

**Files:**
- Modify: `tests/test_live.py`

**Interfaces:**
- Consumes: `ReadProxy`, `UpstreamClient`, `upstream_url`, `load_config`
- Produces: ingenting

- [ ] **Step 1: Write the live test**

Legg til nederst i `tests/test_live.py`:

```python
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
```

- [ ] **Step 2: Run the live tests**

Run: `FS_LIVE=1 python3 -m pytest tests/test_live.py -k proxy -v`
Expected: PASS — samme radantall begge veier, ingen PII-nøkler, 405 på skriv

- [ ] **Step 3: Confirm the offline suite is unaffected**

Run: `python3 -m pytest -q`
Expected: alle offline-tester passerer; live-testene hoppes over uten `FS_LIVE=1`

- [ ] **Step 4: Commit**

```bash
git add tests/test_live.py
git commit -m "test: live smoke test proving proxy parity and PII removal

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Etter planen

Når alle oppgavene er grønne: bytt `.env` til den nye oppdelingen, start proxyen, og kjør en rapportkommando (`python3 scripts/monthly_report.py --month 2026-07`) for å bekrefte at hele verktøykjeden virker gjennom sperren. Push til GitHub, så har kollegene den.
