"""OData v3 query construction.

Separated from transport because this is pure and carries most of the safety
rules. The API answers a malformed query with HTTP 200 and an empty array, so
mistakes are invisible at runtime — the defence has to be at build time.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence

MAX_TOP = 2_000_000

#: The API applies $top BEFORE $filter, so this caps rows *scanned*. Set far
#: above source volume; a smaller value silently truncates.

FILTERABLE = frozenset({"STOCKID_FK", "STOREID_FK", "PRODUCTID_FK", "SaleDate"})

#: Regex to extract field names in comparison expressions. Defence in depth —
#: not a parser, but sufficient to catch hand-built filters that bypass the
#: whitelist-only helpers.
_COMPARISON = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:eq|ne|gt|ge|lt|le)\b")


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
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnsafeQueryError(
            f"{field} id must be an int, got {type(value).__name__}."
        )
    return f"{field} eq {int(value)}"


def any_of(field: str, values: Sequence[int]) -> str:
    _check(field)
    if not values:
        raise UnsafeQueryError(f"any_of({field!r}) needs at least one value.")
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int):
            raise UnsafeQueryError(
                f"{field} id must be an int, got {type(v).__name__}."
            )
    inner = " or ".join(f"{field} eq {int(v)}" for v in values)
    return f"({inner})"


def build_params(
    filters: Sequence[str],
    select: Sequence[str],
) -> dict[str, str]:
    if not select:
        raise UnsafeQueryError(
            "$select is required. Saleslines carries customer PII on every row, "
            "so an unrestricted query moves personal data into context."
        )
    # Validate all field names in filters to prevent hand-built filters that
    # bypass the whitelist. Defence in depth, not a parser.
    unsafe_fields = set()
    for filter_str in filters:
        for match in _COMPARISON.finditer(filter_str):
            field = match.group(1)
            if field not in FILTERABLE:
                unsafe_fields.add(field)
    if unsafe_fields:
        raise UnsafeQueryError(
            f"{', '.join(sorted(unsafe_fields))} {'is' if len(unsafe_fields) == 1 else 'are'} "
            f"not filterable. Display fields such as Stock, Store, Brand and Name are "
            f"joined columns: filtering on them returns an empty result with HTTP 200 "
            f"rather than an error. Filter on one of: {', '.join(sorted(FILTERABLE))}."
        )
    params = {"$select": ",".join(select), "$top": str(MAX_TOP)}
    if filters:
        params["$filter"] = " and ".join(filters)
    return params
