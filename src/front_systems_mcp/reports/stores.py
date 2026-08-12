"""Store name → id map.

The API has no store dimension endpoint, so the map is harvested from recent
Saleslines rows. Two hazards this must not paper over: several STOREID_FK
registers map to one STOCKID_FK, so grouping by register splits one shop into
several; and similarly-named stores can be unrelated companies.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..odata import date_range  # noqa: F401  (kept for callers)

SELECT = ["Stock", "Store", "STOCKID_FK", "STOREID_FK"]


@dataclass
class StoreEntry:
    stock_id: int
    stock_names: list[str] = field(default_factory=list)
    legal_entities: list[str] = field(default_factory=list)
    register_ids: list[int] = field(default_factory=list)
    line_count: int = 0

    @property
    def display_name(self) -> str:
        return " / ".join(self.stock_names) or f"stock {self.stock_id}"


async def harvest(client, days: int = 30, today: dt.date | None = None) -> list[StoreEntry]:
    today = today or dt.date.today()
    since = today - dt.timedelta(days=days)
    rows = await client.fetch("Saleslines", [], SELECT,
                              window=(since, today + dt.timedelta(days=1)))
    if not rows:
        raise ValueError(
            f"No sales lines in the last {days} days, so no store map could "
            "be built. Widen the window, or use Sales with known register ids."
        )

    grouped: dict[int, StoreEntry] = {}
    for row in rows:
        stock_id = row.get("STOCKID_FK")
        if stock_id is None:
            continue
        entry = grouped.setdefault(stock_id, StoreEntry(stock_id=stock_id))
        entry.line_count += 1
        for value, target in (
            (row.get("Stock"), entry.stock_names),
            (row.get("Store"), entry.legal_entities),
            (row.get("STOREID_FK"), entry.register_ids),
        ):
            if value is not None and value not in target:
                target.append(value)

    for entry in grouped.values():
        entry.stock_names.sort()
        entry.legal_entities.sort()
        entry.register_ids.sort()
    return sorted(grouped.values(), key=lambda e: -e.line_count)


def resolve(entries: Sequence[StoreEntry], query: str) -> list[StoreEntry]:
    """All entries whose stock name or legal entity contains `query`.

    Returns every match rather than a best guess: "Paleet" legitimately matches
    two unrelated companies, and picking one silently would be a wrong answer.
    """
    needle = query.strip().casefold()
    return [
        e for e in entries
        if any(needle in n.casefold() for n in (*e.stock_names, *e.legal_entities))
    ]
