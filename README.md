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
