"""What period a result actually covers.

Zero rows is ambiguous on this API: a closed Sunday, a period before the data
starts, and a malformed filter all look the same. Reporting coverage alongside
every result is what lets the caller tell them apart.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

LINES_HISTORY_START = dt.date(2026, 8, 1)


@dataclass
class Coverage:
    requested_from: dt.date
    requested_to: dt.date
    first_seen: dt.date | None
    last_seen: dt.date | None
    days_present: int
    missing_days: list[dt.date]
    row_count: int
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.row_count == 0:
            return (
                f"0 rows for {self.requested_from}..{self.requested_to}. "
                + " ".join(self.warnings)
            )
        return (
            f"{self.row_count} rows, {self.first_seen}..{self.last_seen}, "
            f"{self.days_present} days with data, "
            f"{len(self.missing_days)} without."
        )


def describe(
    rows: list[dict],
    requested_from: dt.date,
    requested_to: dt.date,
    date_field: str = "SaleDate",
    entity: str = "Sales",
) -> Coverage:
    seen = sorted({
        dt.date.fromisoformat(str(r[date_field])[:10])
        for r in rows if r.get(date_field)
    })
    span = [
        requested_from + dt.timedelta(days=i)
        for i in range((requested_to - requested_from).days)
    ]
    missing = [d for d in span if d not in set(seen)]
    warnings: list[str] = []

    if entity == "Saleslines" and requested_from < LINES_HISTORY_START:
        warnings.append(
            f"Saleslines holds no data before {LINES_HISTORY_START}; "
            f"{requested_from} was requested. Use Sales for earlier periods "
            "(revenue and transaction counts, but no product, unit or margin "
            "detail)."
        )
    if not rows:
        warnings.append(
            "0 rows returned. On this API that is more often a malformed query "
            "than an absence of trade — check the date predicate and that only "
            "numeric FK columns were filtered."
        )
    elif seen and seen[0] > requested_from:
        warnings.append(
            f"Earliest data {seen[0]} is later than requested {requested_from}."
        )

    return Coverage(
        requested_from=requested_from,
        requested_to=requested_to,
        first_seen=seen[0] if seen else None,
        last_seen=seen[-1] if seen else None,
        days_present=len(seen),
        missing_days=missing,
        row_count=len(rows),
        warnings=warnings,
    )
