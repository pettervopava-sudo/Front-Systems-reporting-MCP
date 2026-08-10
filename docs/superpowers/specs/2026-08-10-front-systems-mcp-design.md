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

### Open questions, resolved by the discovery spike

The full endpoint catalog is behind authentication, so the following are unknown
at design time and MUST be established before implementation:

1. Exact paths for sales and stock retrieval.
2. Whether sales are available **pre-aggregated** or only as line items.
3. Pagination mechanism (page/offset, cursor, or `Link` header).
4. Rate limits and whether `Retry-After` is returned on 429.
5. How far back historical data is queryable.
6. Whether store, currency, and product metadata arrive inline or need separate lookups.

**Risk:** if historical sales turn out to be webhook-push-only with no meaningful
pull endpoint, the reporting model changes materially and this design must be
revisited before further work. The spike exists to surface that early.

## Phase 0 — Discovery spike

A throwaway script, run against the live API with real keys, that records into
`docs/api-discovery.md`:

- Every relevant endpoint path, method, and parameter set
- Real sample response payloads (with any customer PII scrubbed)
- Pagination behavior observed across a multi-page fetch
- Observed rate-limit headers and 429 behavior
- Answers to each open question above

The captured payloads become the test fixtures for all later work, so tests
exercise shapes the API genuinely returns rather than shapes we assumed.

The spike is exploratory code and is not retained as production code.

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
Returns store IDs and names. Enables resolving "the Oslo store" to an ID.
Cached for the session.

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

**A partial fetch is never presented as a complete report.** If any page of a
paginated fetch fails after retries, the tool raises an error. It does not return
a short total. Silently wrong numbers are worse than visible failure.

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
