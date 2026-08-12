# Front Systems Reporting MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only MCP server that answers sales and stock questions about Front Systems retail stores in plain language, returning compact summaries plus optional Excel and chart files.

**Architecture:** Four layers, each ignorant of those above it — `server.py` (MCP tool definitions, wiring only) → `reports/` (domain logic, returns DataFrames) → `client.py` (HTTP, auth, safety guards) → the OData API. `exporters/` sits to the side: DataFrame in, file path out. Aggregation happens in pandas, never in model context, so a year of data costs the same context as a day.

**Tech Stack:** Python 3.13, `mcp` (official SDK), `httpx`, `pandas`, `openpyxl`, `matplotlib`, `truststore`, `pytest`, `respx`.

## Global Constraints

- **Read-only.** Every request is a GET. No tool may mutate Front Systems data.
- **Revenue is `Qty * Price`.** Never `SUM(Price)`. Returns are rows with `Qty = -1` and a positive `Price`. COGS is `Qty * Cost`; margin is `Qty * (Price - Cost)`.
- **`$top` is applied BEFORE `$filter`.** Fixed at 2_000_000. `$skip` is never emitted.
- **Filter only on numeric FK columns:** `STOCKID_FK`, `STOREID_FK`, `PRODUCTID_FK`, `SaleDate`. Display fields (`Stock`, `Store`, `Brand`, `Name`, `Employee`, `Pos`) return empty when filtered.
- **`$select` is mandatory** on `Saleslines`; PII columns (`FirstName`, `LastName`, `Email`, `Phone`, `Address`, `PostalCode`, `City`) are excluded unless explicitly requested.
- **`Saleslines` has no data before 2026-08-01.** `Sales` reaches back to 2022.
- **Never disable TLS verification.** Use `truststore` — some machines run an intercepting proxy where Python's bundled CA store fails but the real chain is valid.
- **Credentials never appear in logs, errors, or tracebacks.**
- Base URL `https://frontsystemsapis.frontsystems.no`, OData v3, date literals `datetime'YYYY-MM-DDT00:00:00'`, headers `Ocp-Apim-Subscription-Key` and `x-api-key`.
- Currency is carried explicitly and never summed across currencies.
- Python 3.13. Line length 100. Type hints on all public functions.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Deps, pytest config, package metadata |
| `src/front_systems_mcp/config.py` | Load and validate `.env`; redact secrets |
| `src/front_systems_mcp/odata.py` | Build OData query strings; enforce filter whitelist |
| `src/front_systems_mcp/client.py` | HTTP, auth headers, retries, truncation detection |
| `src/front_systems_mcp/coverage.py` | Describe what period a result actually covers |
| `src/front_systems_mcp/reports/stores.py` | Harvest store name → id map |
| `src/front_systems_mcp/reports/sales.py` | Revenue/units/margin aggregation |
| `src/front_systems_mcp/reports/stock.py` | Stock levels |
| `src/front_systems_mcp/exporters/excel.py` | DataFrame → .xlsx |
| `src/front_systems_mcp/exporters/charts.py` | DataFrame → .png |
| `src/front_systems_mcp/server.py` | MCP tool definitions |
| `tests/fixtures/*.json` | Real captured payloads |

Split by responsibility, not layer: `odata.py` is separate from `client.py` because query *construction* is pure and heavily tested, while transport is I/O and mocked. `coverage.py` is separate because it is the answer to "is zero rows real?", used by every report.

---

### Task 1: Project scaffolding and configuration

**Files:**
- Create: `pyproject.toml`, `src/front_systems_mcp/__init__.py`, `src/front_systems_mcp/config.py`
- Create: `tests/test_config.py`, `tests/__init__.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Config` dataclass with fields `base_url: str`, `subscription_key: str`, `api_key: str`; `load_config(env_path: Path | None = None) -> Config`; `ConfigError(Exception)`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "front-systems-mcp"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "mcp>=1.2.0",
    "httpx>=0.27",
    "pandas>=2.2",
    "openpyxl>=3.1",
    "matplotlib>=3.9",
    "truststore>=0.9",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21", "pytest-asyncio>=0.23"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.hatch.build.targets.wheel]
packages = ["src/front_systems_mcp"]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest
from pathlib import Path
from front_systems_mcp.config import load_config, Config, ConfigError


def write_env(tmp_path: Path, **kv: str) -> Path:
    p = tmp_path / ".env"
    p.write_text("\n".join(f"{k}={v}" for k, v in kv.items()))
    return p


def test_loads_all_three_values(tmp_path):
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://example.test/",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="sub123",
        FRONT_SYSTEMS_API_KEY="api456",
    )
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.base_url == "https://example.test"  # trailing slash stripped
    assert cfg.subscription_key == "sub123"
    assert cfg.api_key == "api456"


def test_missing_key_names_the_variable(tmp_path):
    p = write_env(tmp_path, FRONT_SYSTEMS_BASE_URL="https://example.test")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "FRONT_SYSTEMS_SUBSCRIPTION_KEY" in str(exc.value)


def test_blank_value_treated_as_missing(tmp_path):
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://example.test",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="   ",
        FRONT_SYSTEMS_API_KEY="api456",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "FRONT_SYSTEMS_SUBSCRIPTION_KEY" in str(exc.value)


def test_secrets_never_appear_in_repr(tmp_path):
    p = write_env(
        tmp_path,
        FRONT_SYSTEMS_BASE_URL="https://example.test",
        FRONT_SYSTEMS_SUBSCRIPTION_KEY="supersecretsub",
        FRONT_SYSTEMS_API_KEY="supersecretapi",
    )
    cfg = load_config(p)
    rendered = f"{cfg!r} {cfg!s}"
    assert "supersecretsub" not in rendered
    assert "supersecretapi" not in rendered
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.config'`

- [ ] **Step 4: Write the implementation**

Create `src/front_systems_mcp/__init__.py` (empty file), then `src/front_systems_mcp/config.py`:

```python
"""Credential loading and validation.

Secrets are kept off __repr__ and __str__ deliberately: the natural failure
mode of an HTTP client is an exception that renders its own config, and a key
pasted into a transcript cannot be un-leaked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = (
    "FRONT_SYSTEMS_BASE_URL",
    "FRONT_SYSTEMS_SUBSCRIPTION_KEY",
    "FRONT_SYSTEMS_API_KEY",
)


class ConfigError(Exception):
    """Raised when credentials are missing or unusable."""


@dataclass(frozen=True)
class Config:
    base_url: str
    subscription_key: str = field(repr=False)
    api_key: str = field(repr=False)

    def __str__(self) -> str:
        return f"Config(base_url={self.base_url!r}, keys=<redacted>)"


def _parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_config(env_path: Path | None = None) -> Config:
    path = Path(env_path) if env_path else Path.cwd() / ".env"
    if not path.exists():
        raise ConfigError(
            f"No .env at {path}. It must define: {', '.join(REQUIRED)}."
        )
    values = _parse(path)
    missing = [k for k in REQUIRED if not values.get(k, "").strip()]
    if missing:
        raise ConfigError(
            f"Missing or blank in {path}: {', '.join(missing)}."
        )
    return Config(
        base_url=values["FRONT_SYSTEMS_BASE_URL"].strip().rstrip("/"),
        subscription_key=values["FRONT_SYSTEMS_SUBSCRIPTION_KEY"].strip(),
        api_key=values["FRONT_SYSTEMS_API_KEY"].strip(),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/front_systems_mcp/__init__.py src/front_systems_mcp/config.py tests/
git commit -m "feat: config loading with redacted credentials"
```

---

### Task 2: OData query builder with filter whitelist

**Files:**
- Create: `src/front_systems_mcp/odata.py`, `tests/test_odata.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `FILTERABLE: frozenset[str]` — `{"STOCKID_FK", "STOREID_FK", "PRODUCTID_FK", "SaleDate"}`
  - `UnsafeQueryError(Exception)`
  - `date_range(field: str, start: date | None, end: date | None) -> list[str]`
  - `eq(field: str, value: int) -> str`
  - `any_of(field: str, values: Sequence[int]) -> str`
  - `build_params(filters: Sequence[str], select: Sequence[str], top: int = MAX_TOP) -> dict[str, str]`
  - `MAX_TOP: int = 2_000_000`

- [ ] **Step 1: Write the failing test**

Create `tests/test_odata.py`:

```python
import datetime as dt
import pytest
from front_systems_mcp.odata import (
    MAX_TOP, UnsafeQueryError, any_of, build_params, date_range, eq,
)


def test_date_range_emits_v3_datetime_literals():
    parts = date_range("SaleDate", dt.date(2026, 7, 1), dt.date(2026, 8, 1))
    assert parts == [
        "SaleDate ge datetime'2026-07-01T00:00:00'",
        "SaleDate lt datetime'2026-08-01T00:00:00'",
    ]


def test_end_is_exclusive_start_is_inclusive():
    parts = date_range("SaleDate", dt.date(2026, 7, 1), None)
    assert parts == ["SaleDate ge datetime'2026-07-01T00:00:00'"]


def test_eq_on_numeric_fk_is_allowed():
    assert eq("STOCKID_FK", 3229) == "STOCKID_FK eq 3229"


@pytest.mark.parametrize("field", ["Stock", "Store", "Brand", "Name", "Employee"])
def test_filtering_a_display_field_is_rejected(field):
    # These return HTTP 200 with an empty array rather than erroring, so a
    # silent wrong answer is the default. Fail loudly at build time instead.
    with pytest.raises(UnsafeQueryError) as exc:
        eq(field, 1)
    assert field in str(exc.value)


def test_any_of_builds_an_or_group():
    assert any_of("STOREID_FK", [3529, 3530]) == (
        "(STOREID_FK eq 3529 or STOREID_FK eq 3530)"
    )


def test_any_of_rejects_empty_values():
    with pytest.raises(UnsafeQueryError):
        any_of("STOREID_FK", [])


def test_build_params_pins_top_high_and_never_emits_skip():
    params = build_params(["SaleDate ge datetime'2026-08-01T00:00:00'"], ["SALEID"])
    assert params["$top"] == str(MAX_TOP)
    assert "$skip" not in params
    assert params["$select"] == "SALEID"
    assert params["$filter"] == "SaleDate ge datetime'2026-08-01T00:00:00'"


def test_build_params_joins_filters_with_and():
    params = build_params(["A eq 1", "B eq 2"], ["X"])
    assert params["$filter"] == "A eq 1 and B eq 2"


def test_build_params_requires_a_select():
    # Saleslines carries customer PII on every row; an absent $select pulls it.
    with pytest.raises(UnsafeQueryError):
        build_params(["A eq 1"], [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_odata.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.odata'`

- [ ] **Step 3: Write the implementation**

Create `src/front_systems_mcp/odata.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_odata.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/odata.py tests/test_odata.py
git commit -m "feat: OData query builder rejecting silently-failing filters"
```

---

### Task 3: Capture real API payloads as test fixtures

**Files:**
- Create: `scripts/capture_fixtures.py`
- Create: `tests/fixtures/saleslines_sample.json`, `tests/fixtures/sales_sample.json`

**Interfaces:**
- Consumes: `load_config` from Task 1
- Produces: fixture files used by Tasks 5, 6, 7. Each is a JSON object `{"value": [ ... ]}` matching the API's own envelope.

The fixtures must come from the live API rather than being hand-written, so tests exercise shapes the API genuinely returns. PII fields are stripped on capture.

- [ ] **Step 1: Write the capture script**

Create `scripts/capture_fixtures.py`:

```python
"""Capture real API payloads as test fixtures, with PII removed.

Run once against a live tenant. Fixtures are committed; credentials are not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from front_systems_mcp.config import load_config  # noqa: E402

PII = {"FirstName", "LastName", "Email", "Phone", "Address", "PostalCode", "City",
       "CUSTOMERID_FK", "PERSONID_FK"}
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

LINE_FIELDS = [
    "SALESLINEID", "SALEID", "STOREID_FK", "STOCKID_FK", "SaleDate", "SaleDateTime",
    "Qty", "Price", "FullPrice", "Discount", "Cost", "VATPercent", "Currency",
    "IsVoided", "Brand", "Group", "Name", "SizeLabel", "Store", "Stock",
]
SALES_FIELDS = [
    "SALEID", "STOREID_FK", "POSID_FK", "SaleDate",
    "SaleDateTime", "Total", "IsVoided", "IsComplete",
]


def fetch(cfg, entity: str, filt: str, select: list[str], top: int) -> list[dict]:
    cmd = [
        "curl", "-sS", "--max-time", "600", "--get",
        "-H", f"Ocp-Apim-Subscription-Key: {cfg.subscription_key}",
        "-H", f"x-api-key: {cfg.api_key}",
        "-H", "Accept: application/json",
        "--data-urlencode", f"$filter={filt}",
        "--data-urlencode", "$select=" + ",".join(select),
        "--data-urlencode", f"$top={top}",
        f"{cfg.base_url}/odata/{entity}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"curl failed for {entity} (rc={res.returncode})")
    return json.loads(res.stdout)["value"]


def main() -> None:
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)

    lines = fetch(
        cfg, "Saleslines",
        "STOCKID_FK eq 3229 and SaleDate ge datetime'2026-08-01T00:00:00' "
        "and SaleDate lt datetime'2026-08-04T00:00:00'",
        LINE_FIELDS, 2_000_000,
    )
    sales = fetch(
        cfg, "Sales",
        "(STOREID_FK eq 3530 or STOREID_FK eq 3568 or STOREID_FK eq 3529 "
        "or STOREID_FK eq 3431) and SaleDate ge datetime'2026-08-01T00:00:00' "
        "and SaleDate lt datetime'2026-08-04T00:00:00'",
        SALES_FIELDS, 2_000_000,
    )

    for rows in (lines, sales):
        for row in rows:
            for key in PII:
                row.pop(key, None)

    (OUT / "saleslines_sample.json").write_text(
        json.dumps({"value": lines}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "sales_sample.json").write_text(
        json.dumps({"value": sales}, ensure_ascii=False, indent=1), encoding="utf-8")

    returns = sum(1 for r in lines if float(r["Qty"]) < 0)
    print(f"saleslines: {len(lines)} rows ({returns} returns)")
    print(f"sales:      {len(sales)} rows")
    if returns == 0:
        print("WARNING: no return rows captured. Widen the date range — the "
              "Qty=-1 case is the one most likely to regress.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the capture**

Run: `python scripts/capture_fixtures.py`
Expected: prints row counts for both files, with a non-zero return count. If returns are zero, widen the end date to `2026-08-06` and rerun — a fixture without a `Qty = -1` row cannot protect the revenue formula.

- [ ] **Step 3: Verify no PII survived**

Run:
```bash
python -c "
import json,pathlib
pii={'FirstName','LastName','Email','Phone','Address','PostalCode','City'}
for p in pathlib.Path('tests/fixtures').glob('*.json'):
    rows=json.loads(p.read_text())['value']
    found=pii & set(rows[0]) if rows else set()
    print(p.name, len(rows), 'PII:', found or 'none')
    assert not found
"
```
Expected: `PII: none` for both files.

- [ ] **Step 4: Commit**

```bash
git add scripts/capture_fixtures.py tests/fixtures/
git commit -m "test: capture real API payloads as fixtures, PII stripped"
```

---

### Task 4: HTTP client with truncation detection

**Files:**
- Create: `src/front_systems_mcp/client.py`, `tests/test_client.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `build_params`/`MAX_TOP` (Task 2)
- Produces:
  - `FrontSystemsClient(config: Config, timeout: float = 600.0)`
  - `async FrontSystemsClient.fetch(entity: str, filters: Sequence[str], select: Sequence[str]) -> list[dict]`
  - `async FrontSystemsClient.aclose() -> None`
  - `ApiError(Exception)`, `AuthError(ApiError)`, `RateLimitedError(ApiError)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
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
    rows = await client.fetch("Sales", ["A eq 1"], ["SALEID"])
    assert rows == [{"SALEID": 1}]


@respx.mock
async def test_401_raises_auth_error_without_leaking_keys(client):
    respx.get(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError) as exc:
        await client.fetch("Sales", ["A eq 1"], ["SALEID"])
    message = str(exc.value)
    assert "subkey" not in message and "apikey" not in message


@respx.mock
async def test_odata_error_body_is_surfaced(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json={
        "odata.error": {"message": {"value": "Could not find a property named 'Bogus'"}}
    }))
    with pytest.raises(ApiError) as exc:
        await client.fetch("Sales", ["A eq 1"], ["SALEID"])
    assert "Bogus" in str(exc.value)


@respx.mock
async def test_429_retries_then_succeeds(client):
    respx.get(URL).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"value": [{"SALEID": 7}]}),
    ])
    rows = await client.fetch("Sales", ["A eq 1"], ["SALEID"])
    assert rows == [{"SALEID": 7}]


@respx.mock
async def test_429_exhausted_raises_rate_limited(client):
    respx.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
    with pytest.raises(RateLimitedError):
        await client.fetch("Sales", ["A eq 1"], ["SALEID"])


@respx.mock
async def test_500_retries_then_raises(client):
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ApiError):
        await client.fetch("Sales", ["A eq 1"], ["SALEID"])
    assert route.call_count > 1


@respx.mock
async def test_missing_value_key_is_an_error_not_an_empty_result(client):
    # An empty result is a legitimate answer here, so a malformed body must
    # never be allowed to masquerade as one.
    respx.get(URL).mock(return_value=httpx.Response(200, json={"unexpected": 1}))
    with pytest.raises(ApiError):
        await client.fetch("Sales", ["A eq 1"], ["SALEID"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.client'`

- [ ] **Step 3: Write the implementation**

Create `src/front_systems_mcp/client.py`:

```python
"""HTTP transport for the Front Systems OData API.

No paging: $top is applied before $filter on this API, so $skip cannot produce
consistent pages. One request per query, with $top pinned high.
"""
from __future__ import annotations

import asyncio
import ssl
from collections.abc import Sequence

import httpx
import truststore

from .config import Config
from .odata import build_params

MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5


class ApiError(Exception):
    """Base class for API failures."""


class AuthError(ApiError):
    """Credentials rejected."""


class RateLimitedError(ApiError):
    """Rate limited after exhausting retries."""


class FrontSystemsClient:
    def __init__(self, config: Config, timeout: float = 600.0) -> None:
        self._config = config
        # System trust store: some hosts run a TLS-intercepting proxy under
        # which Python's bundled CA bundle fails on a genuinely valid chain.
        # Disabling verification would expose the keys to the interceptor.
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._http = httpx.AsyncClient(
            timeout=timeout,
            verify=ctx,
            headers={
                "Ocp-Apim-Subscription-Key": config.subscription_key,
                "x-api-key": config.api_key,
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(
        self,
        entity: str,
        filters: Sequence[str],
        select: Sequence[str],
    ) -> list[dict]:
        params = build_params(filters, select)
        url = f"{self._config.base_url}/odata/{entity}"
        last: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._http.get(url, params=params)
            except httpx.TimeoutException as exc:
                last = ApiError(f"{entity}: request timed out.")
                await self._sleep(attempt)
                continue
            except httpx.TransportError as exc:
                last = ApiError(f"{entity}: network error ({type(exc).__name__}).")
                await self._sleep(attempt)
                continue

            if response.status_code in (401, 403):
                raise AuthError(
                    f"{entity}: authentication rejected (HTTP "
                    f"{response.status_code}). Check the subscription key and "
                    "x-api-key; keys are not shown here."
                )
            if response.status_code == 429:
                last = RateLimitedError(f"{entity}: rate limited.")
                await self._sleep(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code >= 500:
                last = ApiError(f"{entity}: server error {response.status_code}.")
                await self._sleep(attempt)
                continue
            if response.status_code != 200:
                raise ApiError(f"{entity}: unexpected HTTP {response.status_code}.")

            return self._parse(entity, response)

        raise last or ApiError(f"{entity}: request failed.")

    @staticmethod
    def _parse(entity: str, response: httpx.Response) -> list[dict]:
        try:
            payload = response.json()
        except ValueError:
            raise ApiError(f"{entity}: response was not JSON.") from None
        if "odata.error" in payload:
            message = payload["odata.error"].get("message", {})
            detail = message.get("value") if isinstance(message, dict) else message
            raise ApiError(f"{entity}: OData error — {detail}")
        if "value" not in payload:
            raise ApiError(
                f"{entity}: response had no 'value' array. Treating this as an "
                "error rather than an empty result, since empty is a legitimate "
                "answer and must not be faked by a malformed body."
            )
        return payload["value"]

    @staticmethod
    async def _sleep(attempt: int, retry_after: str | None = None) -> None:
        if retry_after is not None:
            try:
                await asyncio.sleep(float(retry_after))
                return
            except ValueError:
                pass
        await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_client.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/client.py tests/test_client.py
git commit -m "feat: async HTTP client with retries and redacted auth errors"
```

---

### Task 5: Coverage reporting

**Files:**
- Create: `src/front_systems_mcp/coverage.py`, `tests/test_coverage.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `LINES_HISTORY_START: date = date(2026, 8, 1)`
  - `Coverage` dataclass: `requested_from: date`, `requested_to: date`, `first_seen: date | None`, `last_seen: date | None`, `days_present: int`, `missing_days: list[date]`, `row_count: int`, `warnings: list[str]`
  - `describe(rows: list[dict], requested_from: date, requested_to: date, date_field: str = "SaleDate", entity: str = "Sales") -> Coverage`
  - `Coverage.summary() -> str`

This exists because zero rows is ambiguous — a closed Sunday and a broken query look identical, and only the caller can tell them apart if we show the difference.

- [ ] **Step 1: Write the failing test**

Create `tests/test_coverage.py`:

```python
import datetime as dt
from front_systems_mcp.coverage import LINES_HISTORY_START, Coverage, describe


def rows_on(*days: str) -> list[dict]:
    return [{"SaleDate": f"{d}T00:00:00"} for d in days]


def test_reports_span_and_day_count():
    cov = describe(
        rows_on("2026-08-01", "2026-08-01", "2026-08-03"),
        dt.date(2026, 8, 1), dt.date(2026, 8, 4),
    )
    assert cov.row_count == 3
    assert cov.first_seen == dt.date(2026, 8, 1)
    assert cov.last_seen == dt.date(2026, 8, 3)
    assert cov.days_present == 2
    assert cov.missing_days == [dt.date(2026, 8, 2)]


def test_empty_result_warns_rather_than_implying_no_trade():
    cov = describe([], dt.date(2026, 8, 1), dt.date(2026, 8, 4))
    assert cov.row_count == 0
    assert cov.first_seen is None
    assert any("0 rows" in w for w in cov.warnings)


def test_saleslines_before_history_start_is_warned():
    cov = describe(
        [], dt.date(2026, 7, 1), dt.date(2026, 8, 1), entity="Saleslines",
    )
    joined = " ".join(cov.warnings)
    assert str(LINES_HISTORY_START) in joined
    assert "Sales" in joined


def test_sales_before_history_start_is_not_warned():
    # Sales headers reach back years; only Saleslines is limited.
    cov = describe(
        rows_on("2026-07-01"), dt.date(2026, 7, 1), dt.date(2026, 7, 2),
        entity="Sales",
    )
    assert not any("2026-08-01" in w for w in cov.warnings)


def test_data_starting_later_than_requested_is_flagged():
    cov = describe(
        rows_on("2026-08-05"), dt.date(2026, 8, 1), dt.date(2026, 8, 6),
    )
    assert any("later than requested" in w for w in cov.warnings)


def test_summary_mentions_the_actual_span():
    cov = describe(
        rows_on("2026-08-01", "2026-08-02"),
        dt.date(2026, 8, 1), dt.date(2026, 8, 3),
    )
    assert "2026-08-01" in cov.summary() and "2026-08-02" in cov.summary()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.coverage'`

- [ ] **Step 3: Write the implementation**

Create `src/front_systems_mcp/coverage.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_coverage.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/coverage.py tests/test_coverage.py
git commit -m "feat: coverage reporting so zero rows is never ambiguous"
```

---

### Task 6: Store map harvesting

**Files:**
- Create: `src/front_systems_mcp/reports/__init__.py`, `src/front_systems_mcp/reports/stores.py`, `tests/test_stores.py`

**Interfaces:**
- Consumes: `FrontSystemsClient.fetch` (Task 4), `odata.date_range` (Task 2)
- Produces:
  - `StoreEntry` dataclass: `stock_id: int`, `stock_names: list[str]`, `legal_entities: list[str]`, `register_ids: list[int]`, `line_count: int`
  - `async harvest(client, days: int = 30, today: date | None = None) -> list[StoreEntry]`
  - `resolve(entries: Sequence[StoreEntry], query: str) -> list[StoreEntry]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_stores.py`:

```python
import datetime as dt
import json
from pathlib import Path
import pytest
from front_systems_mcp.reports.stores import StoreEntry, harvest, resolve

FIXTURE = Path(__file__).parent / "fixtures" / "saleslines_sample.json"


class FakeClient:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def fetch(self, entity, filters, select):
        self.calls.append((entity, list(filters), list(select)))
        return self.rows


ROWS = [
    {"STOCKID_FK": 3229, "STOREID_FK": 3530, "Stock": "Høyer Paleet", "Store": "HC Paleet AS"},
    {"STOCKID_FK": 3229, "STOREID_FK": 3568, "Stock": "Høyer Paleet", "Store": "HC Paleet AS"},
    {"STOCKID_FK": 3229, "STOREID_FK": 3530, "Stock": "Høyer Paleet", "Store": "HC Paleet AS"},
    {"STOCKID_FK": 1333, "STOREID_FK": 1979, "Stock": "BMB Paleet", "Store": "BMB Paleet AS"},
]


async def test_groups_registers_under_one_stock():
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    paleet = next(e for e in entries if e.stock_id == 3229)
    assert sorted(paleet.register_ids) == [3530, 3568]
    assert paleet.line_count == 3


async def test_sorted_by_activity():
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    assert entries[0].stock_id == 3229


async def test_queries_saleslines_with_a_date_filter():
    client = FakeClient(ROWS)
    await harvest(client, days=7, today=dt.date(2026, 8, 11))
    entity, filters, select = client.calls[0]
    assert entity == "Saleslines"
    assert any("SaleDate ge datetime'2026-08-04T00:00:00'" == f for f in filters)
    assert "Stock" in select and "STOCKID_FK" in select


async def test_resolve_matches_case_insensitively_on_either_name():
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    assert [e.stock_id for e in resolve(entries, "høyer paleet")] == [3229]
    assert [e.stock_id for e in resolve(entries, "HC PALEET")] == [3229]


async def test_resolve_returns_all_ambiguous_matches():
    # "Paleet" matches two unrelated companies; returning both lets the caller
    # disambiguate instead of silently picking one.
    entries = await harvest(FakeClient(ROWS), days=30, today=dt.date(2026, 8, 11))
    assert sorted(e.stock_id for e in resolve(entries, "Paleet")) == [1333, 3229]


async def test_empty_harvest_raises_with_guidance():
    with pytest.raises(ValueError) as exc:
        await harvest(FakeClient([]), days=30, today=dt.date(2026, 8, 11))
    assert "2026-08-01" in str(exc.value)


async def test_harvest_against_real_fixture():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["value"]
    entries = await harvest(FakeClient(rows), days=30, today=dt.date(2026, 8, 11))
    assert entries
    assert all(e.stock_id and e.register_ids for e in entries)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_stores.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.reports'`

- [ ] **Step 3: Write the implementation**

Create `src/front_systems_mcp/reports/__init__.py` (empty file), then `src/front_systems_mcp/reports/stores.py`:

```python
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

from ..coverage import LINES_HISTORY_START
from ..odata import date_range

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
    rows = await client.fetch("Saleslines", date_range("SaleDate", since, None), SELECT)
    if not rows:
        raise ValueError(
            f"No sales lines in the last {days} days, so no store map could be "
            f"built. Saleslines holds no data before {LINES_HISTORY_START}; "
            "widen the window, or use Sales with known register ids."
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_stores.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/reports/ tests/test_stores.py
git commit -m "feat: harvest store map from Saleslines with ambiguity preserved"
```

---

### Task 7: Sales aggregation

**Files:**
- Create: `src/front_systems_mcp/reports/sales.py`, `tests/test_sales.py`

**Interfaces:**
- Consumes: `FrontSystemsClient.fetch` (Task 4), `odata` helpers (Task 2), `coverage.describe` (Task 5)
- Produces:
  - `LINE_SELECT: list[str]`, `HEADER_SELECT: list[str]`
  - `lines_to_frame(rows: list[dict]) -> pandas.DataFrame` — adds `LineTotal`, `LineCost`, `LineMargin`
  - `headers_to_frame(rows: list[dict]) -> pandas.DataFrame` — drops voided rows
  - `aggregate(frame, group_by: Sequence[str]) -> pandas.DataFrame`
  - `SalesResult` dataclass: `frame`, `totals: dict[str, float]`, `coverage`, `source: str`
  - `async sales_report(client, date_from, date_to, stock_id=None, register_ids=None, group_by=("day",), prefer_lines=True) -> SalesResult`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sales.py`:

```python
import datetime as dt
import json
from pathlib import Path
import pandas as pd
import pytest
from front_systems_mcp.reports.sales import (
    aggregate, headers_to_frame, lines_to_frame, sales_report,
)

FIX = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, by_entity): self.by_entity, self.calls = by_entity, []
    async def fetch(self, entity, filters, select):
        self.calls.append((entity, list(filters), list(select)))
        return self.by_entity.get(entity, [])


def line(qty, price, cost=0.0, day="2026-08-01", **extra):
    row = {"Qty": qty, "Price": price, "Cost": cost, "Currency": "NOK",
           "SaleDate": f"{day}T00:00:00", "SALEID": extra.pop("sale", 1),
           "IsVoided": False, "Brand": extra.pop("brand", "B")}
    row.update(extra)
    return row


def test_returns_subtract_from_revenue():
    # The core bug this guards: Price is a unit price and returns carry
    # Qty = -1 with a POSITIVE price, so SUM(Price) adds them as revenue.
    frame = lines_to_frame([line(1, 500.0), line(-1, 200.0)])
    assert frame["LineTotal"].sum() == 300.0
    assert frame["Price"].sum() == 700.0  # the wrong answer, for contrast


def test_multi_unit_lines_multiply():
    frame = lines_to_frame([line(3, 100.0)])
    assert frame["LineTotal"].sum() == 300.0


def test_margin_uses_qty_for_both_price_and_cost():
    frame = lines_to_frame([line(2, 100.0, cost=40.0)])
    assert frame["LineCost"].sum() == 80.0
    assert frame["LineMargin"].sum() == 120.0


def test_returns_reverse_margin_too():
    frame = lines_to_frame([line(-1, 100.0, cost=40.0)])
    assert frame["LineMargin"].sum() == -60.0


def test_headers_exclude_voided():
    frame = headers_to_frame([
        {"SALEID": 1, "Total": 100.0, "IsVoided": False, "SaleDate": "2026-08-01T00:00:00"},
        {"SALEID": 2, "Total": 999.0, "IsVoided": True, "SaleDate": "2026-08-01T00:00:00"},
    ])
    assert frame["Total"].sum() == 100.0


def test_aggregate_by_day():
    frame = lines_to_frame([
        line(1, 100.0, day="2026-08-01"), line(1, 50.0, day="2026-08-01"),
        line(1, 25.0, day="2026-08-02"),
    ])
    out = aggregate(frame, ["day"]).set_index("day")
    assert out.loc["2026-08-01", "revenue"] == 150.0
    assert out.loc["2026-08-02", "revenue"] == 25.0


def test_aggregate_by_brand():
    frame = lines_to_frame([line(1, 100.0, brand="X"), line(1, 40.0, brand="Y")])
    out = aggregate(frame, ["Brand"]).set_index("Brand")
    assert out.loc["X", "revenue"] == 100.0


def test_currencies_are_never_summed_together():
    frame = lines_to_frame([
        line(1, 100.0), {**line(1, 100.0), "Currency": "SEK"},
    ])
    out = aggregate(frame, ["day"])
    assert set(out["Currency"]) == {"NOK", "SEK"}
    assert len(out) == 2


async def test_report_uses_headers_for_periods_before_line_history():
    client = FakeClient({"Sales": [
        {"SALEID": 1, "Total": 100.0, "IsVoided": False, "SaleDate": "2026-07-05T00:00:00"},
    ]})
    result = await sales_report(
        client, dt.date(2026, 7, 1), dt.date(2026, 8, 1), register_ids=[3530],
    )
    assert result.source == "Sales"
    assert client.calls[0][0] == "Sales"
    assert result.totals["revenue"] == 100.0
    assert "Qty" not in result.frame.columns  # headers carry no unit data


async def test_report_uses_lines_when_period_allows_and_stock_given():
    client = FakeClient({"Saleslines": [line(1, 100.0, day="2026-08-02")]})
    result = await sales_report(
        client, dt.date(2026, 8, 1), dt.date(2026, 8, 5), stock_id=3229,
    )
    assert result.source == "Saleslines"
    assert result.totals["revenue"] == 100.0


async def test_report_never_selects_pii():
    client = FakeClient({"Saleslines": [line(1, 100.0, day="2026-08-02")]})
    await sales_report(client, dt.date(2026, 8, 1), dt.date(2026, 8, 5), stock_id=3229)
    _, _, select = client.calls[0]
    assert not ({"Email", "Phone", "FirstName", "Address"} & set(select))


async def test_real_fixture_reconciles_lines_against_headers():
    # SUM(Qty*Price) must equal SUM(Total) over the same period. A mismatch
    # means the revenue formula regressed.
    lines = json.loads((FIX / "saleslines_sample.json").read_text())["value"]
    heads = json.loads((FIX / "sales_sample.json").read_text())["value"]
    lines_total = round(lines_to_frame(lines)["LineTotal"].sum(), 2)
    heads_total = round(headers_to_frame(heads)["Total"].sum(), 2)
    assert lines_total == heads_total, (
        f"lines {lines_total} != headers {heads_total}"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_sales.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.reports.sales'`

- [ ] **Step 3: Write the implementation**

Create `src/front_systems_mcp/reports/sales.py`:

```python
"""Sales aggregation.

The revenue formula lives here and nowhere else. Its failure mode is invisible:
a period without returns gives the same answer either way, and only a period
*with* returns is wrong, so a duplicated formula would drift undetected.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from ..coverage import LINES_HISTORY_START, Coverage, describe
from ..odata import any_of, date_range, eq

LINE_SELECT = [
    "SALESLINEID", "SALEID", "STOREID_FK", "STOCKID_FK", "PRODUCTID_FK",
    "SaleDate", "SaleDateTime", "Qty", "Price", "FullPrice", "Discount",
    "Cost", "VATPercent", "Currency", "IsVoided", "Brand", "Group", "Name",
    "SizeLabel", "Store", "Stock",
]

HEADER_SELECT = [
    "SALEID", "STOREID_FK", "POSID_FK", "SaleDate",
    "SaleDateTime", "Total", "IsVoided", "IsComplete",
]


@dataclass
class SalesResult:
    frame: pd.DataFrame
    totals: dict[str, float]
    coverage: Coverage
    source: str


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series([0.0] * len(frame), index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def lines_to_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[*LINE_SELECT, "LineTotal", "LineCost",
                                     "LineMargin", "day"])
    qty = _numeric(frame, "Qty")
    # Price and Cost are UNIT figures; returns are Qty = -1 with a positive
    # Price, so multiplying is what makes a return subtract.
    frame["LineTotal"] = (qty * _numeric(frame, "Price")).round(2)
    frame["LineCost"] = (qty * _numeric(frame, "Cost")).round(2)
    frame["LineMargin"] = (frame["LineTotal"] - frame["LineCost"]).round(2)
    frame["day"] = frame["SaleDate"].astype(str).str[:10]
    if "Currency" not in frame:
        frame["Currency"] = "UNKNOWN"
    return frame


def headers_to_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[*HEADER_SELECT, "day", "Currency"])
    if "IsVoided" in frame:
        frame = frame[~frame["IsVoided"].astype(bool)].copy()
    frame["Total"] = _numeric(frame, "Total")
    frame["day"] = frame["SaleDate"].astype(str).str[:10]
    frame["Currency"] = "NOK"  # headers carry no currency column
    return frame


def aggregate(frame: pd.DataFrame, group_by: Sequence[str]) -> pd.DataFrame:
    """Grouped totals. Currency is always a grouping key — a blended total
    across currencies is silently meaningless."""
    if frame.empty:
        return pd.DataFrame(columns=[*group_by, "Currency", "revenue",
                                     "units", "transactions", "margin"])
    keys = [*group_by, "Currency"]
    revenue = "LineTotal" if "LineTotal" in frame else "Total"
    out = frame.groupby(keys, dropna=False).apply(
        lambda g: pd.Series({
            "revenue": round(_numeric(g, revenue).sum(), 2),
            "units": round(_numeric(g, "Qty").sum(), 2) if "Qty" in g else float(len(g)),
            "transactions": int(g["SALEID"].nunique()) if "SALEID" in g else len(g),
            "margin": round(_numeric(g, "LineMargin").sum(), 2)
            if "LineMargin" in g else float("nan"),
        }),
        include_groups=False,
    ).reset_index()
    out["avg_basket"] = (
        out["revenue"] / out["transactions"].replace(0, pd.NA)
    ).round(2)
    return out


async def sales_report(
    client,
    date_from: dt.date,
    date_to: dt.date,
    stock_id: int | None = None,
    register_ids: Sequence[int] | None = None,
    group_by: Sequence[str] = ("day",),
    prefer_lines: bool = True,
) -> SalesResult:
    """Pick the table that can answer the question, then aggregate.

    Saleslines gives product detail but starts 2026-08-01; Sales reaches back
    years but has no product, unit or cost columns.
    """
    use_lines = prefer_lines and date_from >= LINES_HISTORY_START and stock_id is not None

    if use_lines:
        filters = [*date_range("SaleDate", date_from, date_to), eq("STOCKID_FK", stock_id)]
        rows = await client.fetch("Saleslines", filters, LINE_SELECT)
        frame = lines_to_frame(rows)
        coverage = describe(rows, date_from, date_to, entity="Saleslines")
        source = "Saleslines"
        totals = {
            "revenue": round(float(frame["LineTotal"].sum()), 2) if len(frame) else 0.0,
            "units": round(float(pd.to_numeric(frame["Qty"]).sum()), 2) if len(frame) else 0.0,
            "margin": round(float(frame["LineMargin"].sum()), 2) if len(frame) else 0.0,
            "transactions": int(frame["SALEID"].nunique()) if len(frame) else 0,
        }
    else:
        filters = list(date_range("SaleDate", date_from, date_to))
        if register_ids:
            filters.append(any_of("STOREID_FK", list(register_ids)))
        rows = await client.fetch("Sales", filters, HEADER_SELECT)
        frame = headers_to_frame(rows)
        coverage = describe(rows, date_from, date_to, entity="Sales")
        source = "Sales"
        totals = {
            "revenue": round(float(frame["Total"].sum()), 2) if len(frame) else 0.0,
            "transactions": int(len(frame)),
        }

    if totals.get("transactions"):
        totals["avg_basket"] = round(totals["revenue"] / totals["transactions"], 2)
    return SalesResult(
        frame=aggregate(frame, list(group_by)),
        totals=totals,
        coverage=coverage,
        source=source,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_sales.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/reports/sales.py tests/test_sales.py
git commit -m "feat: sales aggregation with Qty*Price revenue and currency split"
```

---

### Task 8: Excel and chart exporters

**Files:**
- Create: `src/front_systems_mcp/exporters/__init__.py`, `src/front_systems_mcp/exporters/excel.py`, `src/front_systems_mcp/exporters/charts.py`, `tests/test_exporters.py`

**Interfaces:**
- Consumes: a `pandas.DataFrame` — nothing Front Systems specific
- Produces:
  - `write_workbook(frames: dict[str, pandas.DataFrame], path: Path, notes: Sequence[str] = ()) -> Path`
  - `write_bar_chart(frame, x: str, y: str, title: str, path: Path) -> Path`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exporters.py`:

```python
import pandas as pd
from openpyxl import load_workbook
from front_systems_mcp.exporters.charts import write_bar_chart
from front_systems_mcp.exporters.excel import write_workbook


def frame():
    return pd.DataFrame({"day": ["2026-08-01", "2026-08-02"], "revenue": [100.0, 250.5]})


def test_writes_one_sheet_per_frame(tmp_path):
    path = write_workbook({"Daily": frame(), "Totals": frame()}, tmp_path / "out.xlsx")
    wb = load_workbook(path)
    assert set(wb.sheetnames) >= {"Daily", "Totals"}


def test_values_are_written_not_formulas(tmp_path):
    # A data extract read by pandas must not contain formulas: openpyxl writes
    # them without cached values, so pandas would see None until Excel opens it.
    path = write_workbook({"Daily": frame()}, tmp_path / "out.xlsx")
    back = pd.read_excel(path, sheet_name="Daily")
    assert back["revenue"].tolist() == [100.0, 250.5]


def test_notes_sheet_records_assumptions(tmp_path):
    path = write_workbook({"Daily": frame()}, tmp_path / "out.xlsx",
                          notes=["Revenue is Qty * Price."])
    wb = load_workbook(path)
    assert "Notes" in wb.sheetnames
    text = " ".join(str(c.value) for row in wb["Notes"].iter_rows() for c in row)
    assert "Qty * Price" in text


def test_chart_file_is_created_and_non_trivial(tmp_path):
    path = write_bar_chart(frame(), "day", "revenue", "Revenue", tmp_path / "c.png")
    assert path.exists() and path.stat().st_size > 1000


def test_empty_frame_still_produces_a_readable_workbook(tmp_path):
    path = write_workbook({"Daily": pd.DataFrame(columns=["day", "revenue"])},
                          tmp_path / "e.xlsx")
    assert load_workbook(path)["Daily"].max_row >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_exporters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.exporters'`

- [ ] **Step 3: Write the implementations**

Create `src/front_systems_mcp/exporters/__init__.py` (empty file), then `src/front_systems_mcp/exporters/excel.py`:

```python
"""DataFrame → .xlsx. Knows nothing about Front Systems."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="1F3864")


def write_workbook(
    frames: dict[str, pd.DataFrame],
    path: Path,
    notes: Sequence[str] = (),
) -> Path:
    """Write one sheet per frame, as values.

    Values rather than formulas: these workbooks are read back by pandas, and
    openpyxl writes formulas with no cached value, so a formula cell reads as
    None until Excel opens the file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
        if notes:
            pd.DataFrame({"Notes & assumptions": list(notes)}).to_excel(
                writer, sheet_name="Notes", index=False)

        for sheet in writer.book.worksheets:
            for cell in sheet[1]:
                cell.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
                cell.fill = HEADER_FILL
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
            for column in sheet.columns:
                width = max((len(str(c.value)) for c in column if c.value), default=8)
                sheet.column_dimensions[column[0].column_letter].width = min(
                    max(width + 3, 10), 24)
            sheet.freeze_panes = "A2"
    return path
```

Then `src/front_systems_mcp/exporters/charts.py`:

```python
"""DataFrame → .png. Knows nothing about Front Systems."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display in a server process
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def write_bar_chart(frame: pd.DataFrame, x: str, y: str, title: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(frame[x].astype(str), pd.to_numeric(frame[y], errors="coerce"))
    ax.set_title(title)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.grid(axis="y", alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_exporters.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/front_systems_mcp/exporters/ tests/test_exporters.py
git commit -m "feat: Excel and chart exporters writing values not formulas"
```

---

### Task 9: Stock report

**Files:**
- Create: `src/front_systems_mcp/reports/stock.py`, `tests/test_stock.py`

**Interfaces:**
- Consumes: `FrontSystemsClient.fetch` (Task 4)
- Produces:
  - `STOCK_SELECT: list[str]`
  - `async stock_report(client, snapshot: datetime | None = None, stock_id: int | None = None, search: str | None = None, low_stock_threshold: int | None = None) -> pandas.DataFrame`

`Stockstatus` takes `snapshotDateTime` as an ordinary query parameter, not an OData filter, so it bypasses `build_params` and needs its own path.

- [ ] **Step 1: Write the failing test**

Create `tests/test_stock.py`:

```python
import datetime as dt
import pytest
from front_systems_mcp.reports.stock import stock_report


class FakeClient:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def fetch_raw(self, entity, params):
        self.calls.append((entity, dict(params)))
        return self.rows


ROWS = [
    {"Productid": 1, "Stockid": 3229, "Name": "Jacket", "Brand": "X",
     "Qty": 5, "AvailableQty": 4, "StockName": "Høyer Paleet"},
    {"Productid": 2, "Stockid": 3229, "Name": "Scarf", "Brand": "Y",
     "Qty": 0, "AvailableQty": 0, "StockName": "Høyer Paleet"},
    {"Productid": 3, "Stockid": 999, "Name": "Boot", "Brand": "X",
     "Qty": 50, "AvailableQty": 50, "StockName": "Other"},
]


async def test_snapshot_is_sent_as_a_plain_query_param():
    client = FakeClient(ROWS)
    await stock_report(client, snapshot=dt.datetime(2026, 7, 31, 23, 0, 0))
    _, params = client.calls[0]
    assert params["snapshotDateTime"] == "'2026-07-31 23:00:00'"


async def test_filters_to_one_stock():
    frame = await stock_report(FakeClient(ROWS), stock_id=3229)
    assert set(frame["Productid"]) == {1, 2}


async def test_search_matches_name_or_brand_case_insensitively():
    frame = await stock_report(FakeClient(ROWS), search="jack")
    assert frame["Productid"].tolist() == [1]


async def test_low_stock_threshold_filters_at_or_below():
    frame = await stock_report(FakeClient(ROWS), stock_id=3229, low_stock_threshold=0)
    assert frame["Productid"].tolist() == [2]


async def test_empty_rows_yield_an_empty_frame_not_an_error():
    frame = await stock_report(FakeClient([]), stock_id=3229)
    assert frame.empty
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_stock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.reports.stock'`

- [ ] **Step 3: Add `fetch_raw` to the client**

In `src/front_systems_mcp/client.py`, add this method to `FrontSystemsClient`, directly after `fetch`:

```python
    async def fetch_raw(self, entity: str, params: dict[str, str]) -> list[dict]:
        """Escape hatch for endpoints whose parameters are not OData filters.

        Stockstatus takes snapshotDateTime as an ordinary query parameter, so it
        cannot go through build_params.
        """
        url = f"{self._config.base_url}/odata/{entity}"
        response = await self._http.get(url, params=params)
        if response.status_code in (401, 403):
            raise AuthError(f"{entity}: authentication rejected.")
        if response.status_code != 200:
            raise ApiError(f"{entity}: HTTP {response.status_code}.")
        return self._parse(entity, response)
```

- [ ] **Step 4: Write the report implementation**

Create `src/front_systems_mcp/reports/stock.py`:

```python
"""Stock levels from Stockstatus.

Stockstatus takes snapshotDateTime as a plain query parameter rather than an
OData filter, and its literal is quoted with a space separator — not the
datetime'...' form the other entities use.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..odata import MAX_TOP

STOCK_SELECT = [
    "Productid", "Stockid", "Name", "Number", "Brand", "Group", "SizeLabel",
    "EAN", "Qty", "ReservedQty", "AvailableQty", "Cost", "OutPrice",
    "StockName", "StockExtId", "Season",
]


async def stock_report(
    client,
    snapshot: dt.datetime | None = None,
    stock_id: int | None = None,
    search: str | None = None,
    low_stock_threshold: int | None = None,
) -> pd.DataFrame:
    snapshot = snapshot or dt.datetime.now().replace(
        hour=23, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
    params = {
        "snapshotDateTime": f"'{snapshot.strftime('%Y-%m-%d %H:%M:%S')}'",
        "$select": ",".join(STOCK_SELECT),
        "$top": str(MAX_TOP),
    }
    rows = await client.fetch_raw("Stockstatus", params)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    # Filtered client-side: Stockstatus is a snapshot endpoint and its filter
    # support has not been verified, so pushing predicates risks the silent
    # empty result seen elsewhere on this API.
    if stock_id is not None:
        frame = frame[frame["Stockid"] == stock_id]
    if search:
        needle = search.casefold()
        haystack = (
            frame.get("Name", pd.Series(dtype=str)).astype(str).str.casefold()
            + " "
            + frame.get("Brand", pd.Series(dtype=str)).astype(str).str.casefold()
        )
        frame = frame[haystack.str.contains(needle, na=False)]
    if low_stock_threshold is not None:
        qty = pd.to_numeric(frame["AvailableQty"], errors="coerce").fillna(0)
        frame = frame[qty <= low_stock_threshold]
    return frame.reset_index(drop=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_stock.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/front_systems_mcp/reports/stock.py src/front_systems_mcp/client.py tests/test_stock.py
git commit -m "feat: stock report with snapshot parameter handling"
```

---

### Task 10: MCP server wiring

**Files:**
- Create: `src/front_systems_mcp/server.py`, `tests/test_server.py`
- Create: `.env.example` (update if present), `README.md`

**Interfaces:**
- Consumes: everything above
- Produces: MCP tools `list_stores`, `sales_report`, `stock_report`, `raw_query`; `main()` console entry point

- [ ] **Step 1: Write the failing test**

Create `tests/test_server.py`:

```python
import datetime as dt
import pytest
from front_systems_mcp import server


def test_parse_date_accepts_iso():
    assert server.parse_date("2026-07-01") == dt.date(2026, 7, 1)


@pytest.mark.parametrize("bad", ["last month", "01/07/2026", "2026-13-01", ""])
def test_parse_date_rejects_non_iso_with_guidance(bad):
    # Relative phrasing is resolved by the model, which shows the user the dates
    # it chose; parsing it here would add a second, invisible interpretation.
    with pytest.raises(ValueError) as exc:
        server.parse_date(bad)
    assert "YYYY-MM-DD" in str(exc.value)


def test_format_result_reports_source_and_coverage():
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult, lines_to_frame, aggregate

    rows = [{"Qty": 1, "Price": 100.0, "Cost": 40.0, "Currency": "NOK",
             "SALEID": 1, "IsVoided": False, "SaleDate": "2026-08-01T00:00:00"}]
    frame = lines_to_frame(rows)
    result = SalesResult(
        frame=aggregate(frame, ["day"]),
        totals={"revenue": 100.0, "transactions": 1},
        coverage=describe(rows, dt.date(2026, 8, 1), dt.date(2026, 8, 2),
                          entity="Saleslines"),
        source="Saleslines",
    )
    text = server.format_result(result)
    assert "Saleslines" in text
    assert "100" in text
    assert "2026-08-01" in text


def test_format_result_surfaces_warnings_prominently():
    from front_systems_mcp.coverage import describe
    from front_systems_mcp.reports.sales import SalesResult
    import pandas as pd

    result = SalesResult(
        frame=pd.DataFrame(),
        totals={"revenue": 0.0, "transactions": 0},
        coverage=describe([], dt.date(2026, 7, 1), dt.date(2026, 8, 1),
                          entity="Saleslines"),
        source="Saleslines",
    )
    text = server.format_result(result)
    assert "2026-08-01" in text  # the history-start warning must be visible


def test_every_registered_tool_is_read_only():
    names = set(server.TOOL_NAMES)
    assert names == {"list_stores", "sales_report", "stock_report", "raw_query"}
    assert not any(
        w in n for n in names
        for w in ("create", "update", "delete", "insert", "post", "adjust")
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'front_systems_mcp.server'`

- [ ] **Step 3: Write the implementation**

Create `src/front_systems_mcp/server.py`:

```python
"""MCP tool definitions. Wiring only — no business logic lives here."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import FrontSystemsClient
from .config import load_config
from .reports import sales as sales_report_mod
from .reports import stock as stock_mod
from .reports import stores as stores_mod
from .exporters.charts import write_bar_chart
from .exporters.excel import write_workbook

TOOL_NAMES = ("list_stores", "sales_report", "stock_report", "raw_query")

mcp = FastMCP("front-systems")
_client: FrontSystemsClient | None = None
_store_cache: list[stores_mod.StoreEntry] | None = None


def parse_date(value: str) -> dt.date:
    """ISO only.

    The model resolves relative phrasing ("last quarter") and states the dates
    it chose, so the user can see and correct them. Parsing that here would add
    a second interpretation the user never sees.
    """
    try:
        return dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise ValueError(
            f"Could not read {value!r} as a date. Use YYYY-MM-DD."
        ) from None


def get_client() -> FrontSystemsClient:
    global _client
    if _client is None:
        _client = FrontSystemsClient(load_config())
    return _client


def format_result(result: "sales_report_mod.SalesResult") -> str:
    lines = [f"Source: {result.source}", f"Coverage: {result.coverage.summary()}"]
    for warning in result.coverage.warnings:
        lines.append(f"WARNING: {warning}")
    lines.append("")
    for key, value in result.totals.items():
        lines.append(f"{key}: {value:,.2f}" if isinstance(value, float)
                     else f"{key}: {value:,}")
    if not result.frame.empty:
        lines.append("")
        lines.append(result.frame.to_string(index=False))
    return "\n".join(lines)


@mcp.tool()
async def list_stores(days: int = 30) -> str:
    """List stores with their stock ids and register ids.

    Harvested from recent sales lines, because the API has no store endpoint.
    """
    global _store_cache
    _store_cache = await stores_mod.harvest(get_client(), days=days)
    rows = [
        f"{e.stock_id:>7}  {e.display_name:<34} "
        f"{'/'.join(e.legal_entities):<32} registers={e.register_ids}"
        for e in _store_cache
    ]
    return "STOCKID  stock name                         legal entity\n" + "\n".join(rows)


@mcp.tool()
async def sales_report(
    date_from: str,
    date_to: str,
    stock_id: int | None = None,
    register_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    output: list[str] | None = None,
) -> str:
    """Sales revenue, units and margin for a period.

    Dates are ISO YYYY-MM-DD; date_to is exclusive. Give stock_id (preferred,
    from list_stores) for product-level detail. Periods before 2026-08-01 fall
    back to transaction headers, which have no product, unit or margin data.
    Output may include "excel" and "chart".
    """
    result = await sales_report_mod.sales_report(
        get_client(),
        parse_date(date_from),
        parse_date(date_to),
        stock_id=stock_id,
        register_ids=register_ids,
        group_by=tuple(group_by or ["day"]),
    )
    text = format_result(result)
    wanted = set(output or [])
    if wanted & {"excel", "chart"}:
        out_dir = Path.cwd() / "reports"
        stem = f"sales_{date_from}_to_{date_to}"
        if "excel" in wanted:
            path = write_workbook(
                {"Summary": result.frame}, out_dir / f"{stem}.xlsx",
                notes=[
                    f"Source: {result.source}.",
                    "Revenue is Qty * Price; returns are Qty = -1 rows.",
                    result.coverage.summary(),
                    *result.coverage.warnings,
                ])
            text += f"\n\nWorkbook: {path}"
        if "chart" in wanted and not result.frame.empty:
            group = result.frame.columns[0]
            path = write_bar_chart(result.frame, group, "revenue",
                                   f"Revenue by {group}", out_dir / f"{stem}.png")
            text += f"\nChart: {path}"
    return text


@mcp.tool()
async def stock_report(
    stock_id: int | None = None,
    search: str | None = None,
    low_stock_threshold: int | None = None,
    snapshot: str | None = None,
) -> str:
    """Current stock levels. snapshot is ISO YYYY-MM-DD (defaults to yesterday 23:00)."""
    when = (dt.datetime.combine(parse_date(snapshot), dt.time(23, 0))
            if snapshot else None)
    frame = await stock_mod.stock_report(
        get_client(), snapshot=when, stock_id=stock_id,
        search=search, low_stock_threshold=low_stock_threshold)
    if frame.empty:
        return "No stock rows matched."
    return f"{len(frame)} rows\n\n{frame.head(200).to_string(index=False)}"


@mcp.tool()
async def raw_query(entity: str, filter: str = "", select: str = "") -> str:
    """Read-only escape hatch for an OData entity not covered above.

    Filter only on STOCKID_FK, STOREID_FK, PRODUCTID_FK or SaleDate; display
    fields such as Stock, Store and Brand return empty with HTTP 200.
    """
    rows = await get_client().fetch(
        entity,
        [filter] if filter else [],
        select.split(",") if select else ["SALEID"],
    )
    head = rows[:50]
    note = f" (showing 50 of {len(rows)})" if len(rows) > 50 else ""
    return f"{len(rows)} rows{note}\n\n{head}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: all tests pass (≈61 across 8 files)

- [ ] **Step 6: Write the README**

Create `README.md`:

````markdown
# Front Systems Reporting MCP Server

Read-only MCP server for sales and stock reporting against the Front Systems
retail POS API.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # then fill in the three values
python -m pytest
```

`.env` needs `FRONT_SYSTEMS_BASE_URL`, `FRONT_SYSTEMS_SUBSCRIPTION_KEY` and
`FRONT_SYSTEMS_API_KEY`. It is gitignored and must stay that way.

## Register with Claude Code

```bash
claude mcp add front-systems -- python -m front_systems_mcp.server
```

## Tools

| Tool | Purpose |
|---|---|
| `list_stores` | Stock ids, names and register ids, harvested from recent lines |
| `sales_report` | Revenue, units, margin; optional Excel and chart output |
| `stock_report` | Stock levels, with search and low-stock filtering |
| `raw_query` | Read-only escape hatch |

## Things this API does that will surprise you

- **Revenue is `Qty * Price`.** `Price` is a unit price and returns are rows
  with `Qty = -1` and a positive price, so `SUM(Price)` adds returns as revenue.
- **`$top` is applied before `$filter`**, so it caps rows *scanned*. A small
  `$top` silently returns a partial result. `$skip` paging is therefore unusable.
- **Only numeric FK columns are filterable.** `Stock`, `Store`, `Brand` and
  `Name` return an empty result with HTTP 200 when filtered.
- **`Saleslines` holds nothing before 2026-08-01.** Earlier periods have
  transaction headers only.
- **`Saleslines` carries customer PII on every row**, so `$select` is mandatory.

Every one of these fails silently with HTTP 200. See `docs/api-discovery.md`.
````

- [ ] **Step 7: Commit**

```bash
git add src/front_systems_mcp/server.py tests/test_server.py README.md .env.example
git commit -m "feat: MCP server exposing store, sales and stock tools"
```

---

### Task 11: End-to-end verification against the live API

**Files:**
- Create: `tests/test_live.py`

**Interfaces:**
- Consumes: everything. These tests hit the real API and are skipped unless `FS_LIVE=1`.

The unit tests prove the code is self-consistent. Only this task proves it agrees with reality — and reality is where every error in this project came from.

- [ ] **Step 1: Write the live tests**

Create `tests/test_live.py`:

```python
"""Live API checks. Skipped unless FS_LIVE=1, since they need credentials."""
import datetime as dt
import os
import pytest

from front_systems_mcp.client import FrontSystemsClient
from front_systems_mcp.config import load_config
from front_systems_mcp.reports.sales import (
    headers_to_frame, lines_to_frame, sales_report,
)
from front_systems_mcp.reports.stores import harvest, resolve

pytestmark = pytest.mark.skipif(
    os.environ.get("FS_LIVE") != "1", reason="set FS_LIVE=1 to run live tests")

PALEET_STOCK = 3229
PALEET_REGISTERS = [3529, 3530, 3431, 3568]
AUG_FROM, AUG_TO = dt.date(2026, 8, 1), dt.date(2026, 8, 11)


@pytest.fixture
async def client():
    c = FrontSystemsClient(load_config())
    yield c
    await c.aclose()


async def test_store_map_contains_paleet(client):
    entries = await harvest(client, days=30)
    matches = resolve(entries, "Paleet")
    assert PALEET_STOCK in {e.stock_id for e in matches}
    # Two unrelated companies share the name; both must surface.
    assert len(matches) >= 2


async def test_registers_group_under_one_stock(client):
    entries = await harvest(client, days=30)
    paleet = next(e for e in entries if e.stock_id == PALEET_STOCK)
    assert set(PALEET_REGISTERS) <= set(paleet.register_ids)


async def test_lines_and_headers_agree_on_revenue(client):
    """The regression that matters most: Qty*Price must equal Sales.Total."""
    lines = await client.fetch(
        "Saleslines",
        [f"STOCKID_FK eq {PALEET_STOCK}",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Qty", "Price", "Cost", "Currency", "IsVoided"],
    )
    registers = " or ".join(f"STOREID_FK eq {r}" for r in PALEET_REGISTERS)
    heads = await client.fetch(
        "Sales",
        [f"({registers})",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Total", "IsVoided"],
    )
    lines_total = round(float(lines_to_frame(lines)["LineTotal"].sum()), 2)
    heads_total = round(float(headers_to_frame(heads)["Total"].sum()), 2)
    assert lines_total == heads_total, f"{lines_total} != {heads_total}"


async def test_period_before_line_history_falls_back_to_headers(client):
    result = await sales_report(
        client, dt.date(2026, 7, 1), dt.date(2026, 8, 1),
        register_ids=PALEET_REGISTERS,
    )
    assert result.source == "Sales"
    assert result.totals["revenue"] > 0
    assert result.totals["transactions"] > 0


async def test_returns_are_present_and_reduce_revenue(client):
    """If this ever finds no returns, the reconciliation test above is toothless."""
    rows = await client.fetch(
        "Saleslines",
        [f"STOCKID_FK eq {PALEET_STOCK}",
         f"SaleDate ge datetime'{AUG_FROM}T00:00:00'",
         f"SaleDate lt datetime'{AUG_TO}T00:00:00'"],
        ["SALEID", "SaleDate", "Qty", "Price", "Cost", "Currency", "IsVoided"],
    )
    frame = lines_to_frame(rows)
    returns = frame[frame["Qty"].astype(float) < 0]
    assert len(returns) > 0
    assert returns["LineTotal"].sum() < 0
```

- [ ] **Step 2: Run the live tests**

Run: `FS_LIVE=1 python -m pytest tests/test_live.py -v`
Expected: 5 passed. If `test_lines_and_headers_agree_on_revenue` fails, the revenue formula has regressed — fix that before anything else, since every reported number depends on it.

- [ ] **Step 3: Confirm the offline suite still passes without credentials**

Run: `python -m pytest -v`
Expected: all pass, with the 5 live tests reported as skipped.

- [ ] **Step 4: Commit**

```bash
git add tests/test_live.py
git commit -m "test: live reconciliation checks, skipped without FS_LIVE=1"
```

---

### Task 12: Point the skill at the server

**Files:**
- Modify: `~/.claude/skills/front-systems-reporting/SKILL.md`

Once the server exists, the skill and the server both implement the revenue rule. Two implementations of a formula whose errors are invisible will drift, so the skill must defer rather than duplicate.

- [ ] **Step 1: Register the server**

Run: `claude mcp add front-systems -- python -m front_systems_mcp.server`
Expected: the server appears in `claude mcp list`.

- [ ] **Step 2: Verify the tools respond**

In a new Claude Code session, ask: "list Front Systems stores". Expect a table including stock 3229 with registers 3529, 3530, 3431, 3568.

Then ask: "sales for Høyer Paleet 1–10 August 2026". Expect revenue **1,796,298.45 NOK** and source `Saleslines`. A different figure means the formula regressed.

- [ ] **Step 3: Edit the skill to defer to the server**

In `SKILL.md`, replace the "Prefer the MCP server when it exists" section with:

```markdown
## Use the MCP server

The `front-systems` MCP server implements the rules below in tested code:
`list_stores`, `sales_report`, `stock_report`, `raw_query`. Prefer its tools —
its revenue arithmetic is covered by a live reconciliation test, which is
stronger than remembering to follow prose.

`scripts/fs_query.py` remains for environments where the server is not
registered, and for ad-hoc exploration. The domain knowledge below applies to
both, and explains what the server's warnings mean when you see them.
```

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills/front-systems-reporting && git add -A 2>/dev/null || true
cd "$OLDPWD" && git add -A && git commit -m "docs: point reporting skill at the MCP server"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: config → 1; OData safety and filter whitelist → 2; fixtures from real payloads → 3; client, retries, redaction → 4; coverage reporting → 5; `list_stores` → 6; `sales_report`, revenue formula, currency handling → 7; Excel and charts → 8; `stock_report` → 9; server wiring, `raw_query`, README → 10; live reconciliation → 11; skill/server overlap → 12.

**Deliberately deferred.** `stock_movements` from the spec's tool list is dropped: discovery found `Stockmovements` returns nothing for the one stock tested, so building a tool for it would be speculative. Add it once the entity is shown to carry usable data.

**Type consistency.** `fetch` and `fetch_raw` are both defined on `FrontSystemsClient` (Tasks 4 and 9) and used with those exact names in Tasks 6, 7 and 9. `lines_to_frame`, `headers_to_frame` and `aggregate` are defined in Task 7 and reused in Tasks 10 and 11. `Coverage.summary()` and `Coverage.warnings` are defined in Task 5 and used in Tasks 7 and 10. `StoreEntry.display_name` is defined in Task 6 and used in Task 10. `MAX_TOP` is defined in Task 2 and used in Tasks 4 and 9.

**Ordering.** Task 3 must run before Tasks 6, 7 and 8, which read its fixtures. Task 9 modifies `client.py` from Task 4. Task 12 requires Task 10.
