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
