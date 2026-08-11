"""OData v3 query construction.

Separated from transport because this is pure and carries most of the safety
rules. The API answers a malformed query with HTTP 200 and an empty array, so
mistakes are invisible at runtime — the defence has to be at build time.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

MAX_TOP = 2_000_000

#: The API applies $top BEFORE $filter, so this caps rows *scanned*. Set far
#: above source volume; a smaller value silently truncates.

FILTERABLE = frozenset({"STOCKID_FK", "STOREID_FK", "PRODUCTID_FK", "SaleDate"})


class UnsafeQueryError(Exception):
    """Raised for a query shape known to fail silently against this API."""


def _check(field: str) -> None:
    if field not in FILTERABLE:
        raise UnsafeQueryError(
            f"{field!r} is not filterable. Display fields such as Stock, Store, "
            f"Brand and Name are joined columns: filtering on them returns an "
            f"empty result with HTTP 200 rather than an error. "
            f"Filter on one of: {', '.join(sorted(FILTERABLE))}."
        )


def _literal(value: dt.date) -> str:
    return f"datetime'{value.isoformat()}T00:00:00'"


def date_range(field: str, start: dt.date | None, end: dt.date | None) -> list[str]:
    """Inclusive start, exclusive end."""
    _check(field)
    parts: list[str] = []
    if start is not None:
        parts.append(f"{field} ge {_literal(start)}")
    if end is not None:
        parts.append(f"{field} lt {_literal(end)}")
    return parts


def eq(field: str, value: int) -> str:
    _check(field)
    return f"{field} eq {int(value)}"


def any_of(field: str, values: Sequence[int]) -> str:
    _check(field)
    if not values:
        raise UnsafeQueryError(f"any_of({field!r}) needs at least one value.")
    inner = " or ".join(f"{field} eq {int(v)}" for v in values)
    return f"({inner})"


def build_params(
    filters: Sequence[str],
    select: Sequence[str],
    top: int = MAX_TOP,
) -> dict[str, str]:
    if not select:
        raise UnsafeQueryError(
            "$select is required. Saleslines carries customer PII on every row, "
            "so an unrestricted query moves personal data into context."
        )
    params = {"$select": ",".join(select), "$top": str(top)}
    if filters:
        params["$filter"] = " and ".join(filters)
    return params
