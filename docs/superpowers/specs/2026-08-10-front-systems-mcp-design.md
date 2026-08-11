# Front Systems Reporting MCP Server — Design

**Date:** 2026-08-10
**Status:** Approved for planning

## Purpose

A read-only MCP server that lets Claude pull sales and stock reports from the
Front Systems retail API on request, in plain language. Asking "how did the Oslo
store do last quarter?" should produce real numbers, and optionally a spreadsheet
or a chart, without the user writing code or running queries.

## Constraints

- **Read-only.** Every API call is a GET. The server cannot modify POS data.
- **Aggregation happens in code, not in the model's context.** A year of line-item
  sales is hundreds of thousands of rows. The server fetches, paginates, and
  aggregates; Claude only ever sees the compact result. Context cost is therefore
  roughly independent of date range.
- **Both API keys are required** and are supplied by the operator via `.env`.

## Known facts about the Front Systems API

Confirmed from the public developer portal:

- Authentication uses two headers on every request:
  - `Ocp-Apim-Subscription-Key` — one per company, from developer.frontsystems.com
  - `x-api-key` — one per integration, from Backoffice (portal.frontsystems.no)
- HTTPS only; plain HTTP fails.
- The platform is built on Azure API Management.
- Domain objects include sales, stock movements, POS settlements, product
  transfers, deliveries, products, and customers.
- Much of the vendor's integration story is webhook-push (`SaleCreated`,
  `StockMovementCreated`, `POSSettlementCreated`, and others). REST pull endpoints
  exist but the public catalog requires portal sign-in to enumerate.

## Phase 0 — Discovery spike: COMPLETE

Run 2026-08-10/11. Full findings in [`docs/api-discovery.md`](../../api-discovery.md).
Headlines that change this design:

1. **Base URL is `https://frontsystemsapis.frontsystems.no`**, OData v3. Both
   keys authenticate. The webhook-push risk did not materialise — sales are
   pullable.
2. **Two tables, different history.** `Sales` (headers) reaches back to 2022 and
   earlier. `Saleslines` (line items) holds **nothing before 2026-08-01**. So
   product, unit, discount and margin breakdowns are impossible for earlier
   periods; only revenue, transaction count and basket size are.
3. **Revenue is `Qty * Price`.** `Price` is a *unit* price and returns are rows
   with `Qty = -1` and a positive `Price`. `SUM(Price)` overstates any period
   containing returns by twice their value. Verified: `SUM(Qty * Price)` matches
   `Sales.Total` to 0.00 across an eight-day overlap.
4. **`$top` is applied BEFORE `$filter`** — it caps rows *scanned*. A small
   `$top` silently returns a partial result that looks complete. `$skip` paging
   therefore cannot be made consistent and must not be used.
5. **Only numeric FK columns are filterable.** `Stock`, `Store`, `Brand`, `Name`
   are joined display fields; filtering on them returns empty with HTTP 200.
6. **There is no store dimension endpoint.** The name → id map must be harvested
   from `Saleslines`, which means it can only cover stores trading since
   2026-08-01.
7. **`Saleslines` carries customer PII on every row**, so `$select` is mandatory.

Every one of these fails *silently* — HTTP 200 with an empty or truncated array,
never an error. That single characteristic is the dominant design constraint:
the client's job is less about fetching than about refusing to return a plausible
wrong answer.

Real payloads captured during the spike become the test fixtures, so tests
exercise shapes the API genuinely returns.

## Architecture

Four layers, each unaware of the layers above it:

```
server.py      MCP tool definitions — wiring only, no business logic
   |
reports/       sales.py, stock.py — domain logic, return DataFrames
   |
client.py      HTTP: auth headers, pagination, retries, rate limiting
   |
Front Systems API
```

`exporters/` sits to the side of this stack: DataFrame in, file path out. It has
no knowledge of Front Systems.

### Responsibility boundaries

| Module | Knows about | Does NOT know about |
|---|---|---|
| `config.py` | env vars, key validation | HTTP, reports |
| `client.py` | auth headers, pagination, retries, rate limits | what a "sale" means |
| `reports/sales.py` | revenue, units, baskets, discounts, returns | HTTP, Excel, MCP |
| `reports/stock.py` | stock levels and movements | HTTP, Excel, MCP |
| `exporters/excel.py` | openpyxl, sheet layout | Front Systems |
| `exporters/charts.py` | matplotlib | Front Systems |
| `server.py` | MCP tool schemas | how any of it works |

Each layer is independently testable: client against mocked HTTP, reports against
spike fixtures, exporters against synthetic DataFrames. No test requires live API
access; the suite runs offline.

### Project layout

The existing repository root (`~/Front Systems API`) is the project root; no
nested project directory is created.

```
<repo root>/
├── .env                    # both keys — gitignored from first commit
├── .env.example            # committed; documents required vars
├── pyproject.toml
├── src/front_systems_mcp/
│   ├── __init__.py
│   ├── server.py
│   ├── config.py
│   ├── client.py
│   ├── reports/
│   │   ├── sales.py
│   │   └── stock.py
│   └── exporters/
│       ├── excel.py
│       └── charts.py
├── tests/
└── docs/api-discovery.md
```

## Stack

Python, for the first-class MCP SDK and for pandas / openpyxl / matplotlib, which
supply aggregation, Excel export, and charting with little custom code.

## Tool surface

### `list_stores()`
Returns stock ids, stock names, legal entities and their register ids. Because
no store dimension endpoint exists, this is **harvested from `Saleslines`** over
a recent window and cached for the session.

Two traps it must handle: several `STOREID_FK` registers map to one
`STOCKID_FK`, so grouping by register splits one shop into four; and
similarly-named stores can be unrelated companies (`Høyer Paleet` 3229 vs
`BMB Paleet` 1333). The tool returns both ids and both names so the model can
disambiguate rather than guess.

### `sales_report(date_from, date_to, group_by, stores=None, output="summary")`
- `date_from`, `date_to` — ISO dates (`YYYY-MM-DD`), inclusive
- `group_by` — list, e.g. `["store"]`, `["store", "month"]`, `["product"]`
- `stores` — optional list of store IDs; omitted means all
- `output` — list of strings drawn from `"summary"`, `"excel"`, `"chart"`.
  Defaults to `["summary"]`. `"summary"` is always included in the result whether
  or not it is requested, so a reply never consists solely of a file path.

Returns per group: revenue, units sold, transaction count, average basket,
discounts, returns.

### `stock_report(stores=None, search=None, low_stock_threshold=None, output="summary")`
Current stock levels. `search` is a case-insensitive substring match against
product name and SKU. Supplying `low_stock_threshold` filters to items whose
quantity is at or below it, producing a reorder list. `output` behaves as in
`sales_report`.

### `stock_movements(date_from, date_to, stores=None, product=None)`
Stock flow over a period. Deliberately separate from `stock_report`: levels are a
snapshot, movements are a flow. One tool serving both would have two jobs and an
ambiguous signature.

### `raw_request(path, params=None)`
GET-only escape hatch for endpoints not covered above. Large responses are
truncated with an explicit note that truncation occurred.

## Cross-cutting decisions

**Dates are ISO-only at the tool boundary.** Claude resolves relative phrasing
("last quarter") into explicit dates and states the resolved range in its reply.
Parsing natural language inside the tool would add a second, invisible
interpretation layer whose mistakes the user could not see or correct.

**Every call returns a summary, even when it also writes a file.** The user gets
both the headline numbers and the artifact, so Claude can comment on what it
produced rather than only reporting a file path.

**Currency is carried explicitly and never summed across currencies.** If stores
span NOK/SEK/EUR, a blind total is silently wrong. Reports group by currency and
label it.

**A partial fetch is never presented as a complete report.** Silently wrong
numbers are worse than visible failure.

**Revenue is always `Qty * Price`; COGS `Qty * Cost`; margin the difference.**
This lives in one place in `reports/`, never re-derived at a call site, because
the failure is invisible — a period with no returns looks identical either way,
and only a period *with* returns is wrong.

**The client refuses queries it knows to be unsafe**, rather than trusting the
caller:

- a `Saleslines` query with no `SaleDate` predicate, or one reaching before
  2026-08-01, is rejected with an explanation pointing at `Sales`
- `$top` is fixed high (200000+) and `$skip` is never emitted
- filters may only reference the numeric FK whitelist; a display-field filter is
  a programming error, raised at build time
- a row count suspiciously equal to a round `$top`-derived figure is treated as
  probable truncation and surfaced

**Coverage is reported alongside every result** — the period actually returned,
the distinct days present, and any requested-but-missing span. A closed Sunday
and a broken query both produce zero rows; only the caller can tell them apart,
and only if we show them.

## Configuration and secrets

`.env` holds `FRONT_SYSTEMS_SUBSCRIPTION_KEY`, `FRONT_SYSTEMS_API_KEY`, and the
base URL. `.gitignore` excludes it from the first commit onward; `.env.example`
documents the required variables without values.

`config.py` validates presence and non-emptiness of both keys at startup and
fails immediately with a plain-language message naming the missing variable.

**Keys are redacted from all log output and all error messages.** Exception paths
must never serialize request headers. This is an explicit requirement, not an
assumption, because the natural failure mode of an HTTP client is to include
headers in diagnostics.

## Error handling

The client maps failures to actionable messages:

| Condition | Behavior |
|---|---|
| 401 / 403 | Fail with "auth rejected — check subscription key and x-api-key" |
| 429 | Honor `Retry-After`; exponential backoff; bounded retries; then report rate limiting |
| 5xx / timeout | Exponential backoff; bounded retries; then report API unhealthy |
| Partial pagination failure | Raise; never return incomplete data as if complete |

Pagination is followed to completion inside the client, subject to a page cap so
an over-broad date range cannot run unbounded without feedback.

## Testing

Test-driven, using payloads captured by the spike.

- `client.py` — mocked HTTP, including 429 with `Retry-After`, 5xx retry
  exhaustion, and mid-pagination failure
- `reports/*.py` — fixture JSON in, expected aggregates out, including the
  multi-currency case
- `exporters/*.py` — synthetic DataFrames in, valid files out
- `config.py` — missing/blank key handling, and redaction of keys from errors

Live smoke tests against the real API exist but are skipped by default.

## Non-goals

Deliberately excluded; each is a plausible later addition that does not earn its
complexity now:

- Any write or mutation operation
- Webhook receiver
- Local database or scheduled sync
- Web UI
- Multi-company / multi-tenant support
- Customer and CRM reporting (may follow once sales and stock are proven)

## Likely follow-up

A local SQLite mirror with SQL querying (previously "Option C") is the most
probable next step if query volume or slicing needs grow. The layering above
allows it to be added beneath `reports/` without changing the tool surface.

## Deployment

Registered with Claude Code via `claude mcp add`, making the tools available in
any session.
