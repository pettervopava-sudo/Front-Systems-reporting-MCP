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

#: Saleslines carries customer PII on every row. Denied in $select regardless
#: of caller (raw_query or otherwise) — this is a GDPR guard, not a UX nicety,
#: so it lives in build_params where every fetch() call passes through it.
PII_FIELDS = frozenset({
    "FirstName", "LastName", "Email", "Phone", "Address", "PostalCode",
    "City", "CUSTOMERID_FK", "PERSONID_FK",
})

#: Allowlist patterns for filter clause shapes. Only accept what the helper
#: functions (eq, any_of, date_range) emit. Anything else is rejected to prevent
#: hand-built filters that bypass the whitelist.
_INT_CLAUSE = re.compile(r"^([A-Za-z_]\w*)\s+eq\s+-?\d+$")
_DATE_CLAUSE = re.compile(
    r"^([A-Za-z_]\w*)\s+(?:ge|gt|le|lt)\s+"
    r"datetime'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'$"
)
_OR_GROUP = re.compile(r"^\((.+)\)$", re.S)


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
    pii_requested = [field.strip() for field in select if field.strip() in PII_FIELDS]
    if pii_requested:
        raise UnsafeQueryError(
            f"$select requests customer PII field(s) {pii_requested!r}, which "
            "this server refuses to move into model context (a GDPR guard). "
            f"Denied fields: {', '.join(sorted(PII_FIELDS))}."
        )
    # Validate filters using an allowlist of shapes that the helpers emit.
    # Anything not matching is rejected — no hand-built filters allowed.
    for filter_str in filters:
        clause = filter_str.strip()

        # Try integer comparison: FIELD eq -?\d+
        int_match = _INT_CLAUSE.match(clause)
        if int_match:
            field = int_match.group(1)
            if field not in FILTERABLE:
                raise UnsafeQueryError(
                    f"{field!r} is not filterable. Display fields such as Stock, Store, "
                    f"Brand and Name are joined columns: filtering on them returns an "
                    f"empty result with HTTP 200 rather than an error. "
                    f"Filter on one of: {', '.join(sorted(FILTERABLE))}."
                )
            continue

        # Try date comparison: FIELD (ge|gt|le|lt) datetime'YYYY-MM-DDTHH:MM:SS'
        date_match = _DATE_CLAUSE.match(clause)
        if date_match:
            field = date_match.group(1)
            if field not in FILTERABLE:
                raise UnsafeQueryError(
                    f"{field!r} is not filterable. Display fields such as Stock, Store, "
                    f"Brand and Name are joined columns: filtering on them returns an "
                    f"empty result with HTTP 200 rather than an error. "
                    f"Filter on one of: {', '.join(sorted(FILTERABLE))}."
                )
            continue

        # Try OR group: (inner_clauses)
        or_match = _OR_GROUP.match(clause)
        if or_match:
            inner = or_match.group(1)
            # Split on " or " and validate each clause
            parts = inner.split(" or ")
            for part in parts:
                part_clause = part.strip()
                int_match = _INT_CLAUSE.match(part_clause)
                if int_match:
                    field = int_match.group(1)
                    if field not in FILTERABLE:
                        raise UnsafeQueryError(
                            f"{field!r} is not filterable. Display fields such as Stock, "
                            f"Store, Brand and Name are joined columns: filtering on them "
                            f"returns an empty result with HTTP 200 rather than an error. "
                            f"Filter on one of: {', '.join(sorted(FILTERABLE))}."
                        )
                else:
                    raise UnsafeQueryError(
                        f"Invalid clause in OR group: {part_clause!r}. "
                        f"Filters must be built with eq, any_of, or date_range."
                    )
            continue

        # None of the allowed shapes matched
        raise UnsafeQueryError(
            f"Invalid filter clause: {clause!r}. "
            f"Filters must be built with eq, any_of, or date_range."
        )

    params = {"$select": ",".join(select), "$top": str(MAX_TOP)}
    if filters:
        params["$filter"] = " and ".join(filters)
    return params
