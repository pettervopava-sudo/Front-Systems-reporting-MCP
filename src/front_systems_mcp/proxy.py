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
from dataclasses import dataclass
from urllib.parse import urlparse

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
