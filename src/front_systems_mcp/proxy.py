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

import json
import ssl
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx
import truststore

from .odata import PII_FIELDS

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


#: PII_FIELDS dekker det $select kan be om; en rå Saleslines-rad bærer mer.
#: Alt kundebærende fjernes her, slik at lesetilgangen er trygg å dele.
#: IsEmployee beholdes bevisst — rabatt- og selgerrapportene bruker det til
#: å skille ut ansattekjøp.
CUSTOMER_FIELDS = frozenset(PII_FIELDS) | frozenset({
    "COMPANYID_FK", "AgreedSendEmail", "AgreedSendSMS", "BonusBalance",
    "BonusFactor", "BonusTotal", "CompanyName", "CountryCode", "CustomerGender",
    "IsCompany", "OrgNum", "SaleBonusFactor",
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
