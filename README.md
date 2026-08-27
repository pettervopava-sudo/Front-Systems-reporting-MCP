# Front Systems Reporting MCP Server

Read-only MCP server for sales and stock reporting against the Front Systems
retail POS API.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env          # then fill in the keys
bash scripts/serve_proxy.sh   # local read proxy on 127.0.0.1:8812
python -m pytest
```

On macOS, install the proxy as a login service so the guard rail is up
before anything can call the API:

```bash
bash scripts/install_proxy_service.sh
```

Fill in `.env` first: the installer refuses to run without complete
credentials, because the proxy reads them before it binds the port, and a
service that cannot start would otherwise be left crash-looping at every
login. Re-run the script after moving the repo, changing python, or editing
the proxy; `--uninstall` removes it again.

`.env` needs three values and startup fails if any is missing:
`FRONT_SYSTEMS_BASE_URL` (the local proxy), `FRONT_SYSTEMS_SUBSCRIPTION_KEY`
and `FRONT_SYSTEMS_API_KEY`. It is gitignored and must stay that way.

Front Systems cannot issue read-only keys, so the subscription key and the API
key carry write access to the POS. The local read proxy is the guard rail: it holds the keys, accepts
only `GET` against a small entity allowlist, and strips customer fields out of
the responses. Every user runs their own on their own machine — it is not a
shared service. `FRONT_SYSTEMS_BASE_URL` is what points the clients at it
(`http://127.0.0.1:8812`); leave it pointing at Front Systems and every call
goes around the guard rail, which is what the proxy warns about on startup.
`FRONT_SYSTEMS_UPSTREAM_URL` is the real Front Systems address the proxy itself
calls, and can be left unset.

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
