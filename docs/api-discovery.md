# API Discovery Notes

**Last updated:** 2026-08-11
**Status:** Connected and mapped. Key limitation found: line-level history is very short.

> This file previously stated that the sales feed ended 2026-08-05 and that
> `$filter` on dates was unreliable. **Both claims were wrong** — artefacts of the
> `$top` behaviour documented below. Corrected here.

## Connection

| Item | Value |
|---|---|
| Base URL | `https://frontsystemsapis.frontsystems.no` |
| Auth | `Ocp-Apim-Subscription-Key` + `x-api-key` headers — confirmed working |
| Protocol | OData **v3** (`datetime'...'` literals) |
| Backend | `fsapiv3.azurewebsites.net` |

`api.frontsystems.no` is an unrelated legacy partner API (KTKApi) that rejects
these keys with 401. Out of scope.

## Entity sets

`$metadata` and the service root return 404 through the gateway, so the catalog
cannot be enumerated. Found by probing; ~35 other plausible names all 404.

| Entity set | History available | Notes |
|---|---|---|
| `Sales` | **Deep** — 2022 and earlier through today | Transaction headers |
| `Saleslines` | **2026-08-01 onward only** | Product lines; see below |
| `Stockstatus` | point-in-time | Requires `snapshotDateTime` |
| `Stockmovements` | n/a | Returns nothing for stock 3229 |
| `Products` | n/a | Slow (~15 s for `$top=1`) |

Confirmed absent: `Stores`, `Customers`, `Orders`, `Turnover`, `Settlements`,
`Transactions`, `Receipts`, `Salesstatistics`, and every `*history` / `*archive` /
`*lines` variant tried. **There is no store dimension endpoint.**

### The critical limitation

`Saleslines` contains **only 2026-08-01 onward**. Verified exhaustively: all
stores, no stock filter, `$top=2000000` → 14,235 rows spanning 2026-08-01 to
2026-08-11, and zero rows for any earlier date or single earlier day.

It gained 2026-08-11 while these notes were being written, so it is
**accumulating from 2026-08-01**, not a fixed-length rolling window. Line-level
history before August 2026 appears simply not to exist in this API.

**Consequence:** for any period before 2026-08-01, only transaction headers are
available. No product, brand, size, unit, discount, or margin breakdown is
possible for those periods. This is the single biggest constraint on the
reporting tool and should be raised with Front Systems.

## Traps — read before writing any query

Each fails **silently**: HTTP 200 with an empty or truncated result, never an error.

### 1. `$top` is applied BEFORE `$filter` — the most dangerous behaviour

`$top` limits rows **scanned**, not rows returned. Identical filter, varying `$top`:

| `$top` | rows returned | date span |
|---|---|---|
| 100 | 1 | 2026-08-01 |
| 1000 | 112 | 2026-08-01 |
| 5000 | 521 | 2026-08-01..08-05 |
| 20000 | 1475 | 2026-08-01..08-10 |

A too-small `$top` silently returns a *partial* result that looks complete. This
caused two wrong conclusions during the spike.

**Rules:** always set `$top` far above the expected source volume (200000+).
**Never page with `$skip`** — with `$top` applied pre-filter, paging cannot be
made consistent. Treat a result whose size equals a round `$top`-derived figure
as suspect.

### 2. `substringof()` is unsupported → returns empty

`substringof('Paleet',Stock)` → 0 rows despite `Stock = 'Høyer Paleet'`. Avoid all
OData string functions.

### 3. Joined display fields are not filterable → returns empty

`Stock`, `Store`, `Brand`, `Name` appear in output, but filtering on them returns
nothing even with exact `eq`. **Filter only on numeric FK columns**
(`STOCKID_FK`, `STOREID_FK`, `PRODUCTID_FK`).

Consequence: the MCP server must resolve store *names* to IDs itself, from a
sampled name→ID map. Name filtering cannot be pushed to the API.

### 4. `$filter` on dates *does* work

Contrary to the earlier note here: `SaleDate ge/lt datetime'...'` filters
correctly. Apparent failures were trap #1.

### 5. `Saleslines` embeds customer PII

Every row carries `FirstName`, `LastName`, `Email`, `Phone`, `Address`,
`PostalCode`, `City`. **Always pass `$select`** limited to needed fields. GDPR
default, not an optimisation.

## Field semantics

- **`Price` is a UNIT price, not a line total.** Line revenue is
  **`Qty * Price`**. `Price = FullPrice - Discount` per unit; `VATPercent`
  carried separately.
- **Returns are rows with `Qty = -1`** and a positive `Price`. Using `SUM(Price)`
  therefore adds returns as revenue instead of subtracting them — a
  double-counting error worth twice the return value.
- **`Sales.Total` reconciles EXACTLY with `SUM(Qty * Price)`** — 0.00 difference
  on all 8 days of the 1–10 August overlap, and 1,796,298.45 NOK in total. An
  earlier note here claimed an unexplained 4–7% gap; that gap was entirely caused
  by summing `Price` without `Qty` (25 return lines, 48,158.70 NOK, counted
  positive rather than negative). **Both sources are trustworthy.**
- COGS is likewise `SUM(Qty * Cost)`, and margin `SUM(Qty * (Price - Cost))`.
- `Sales` flags voided rows via `IsVoided` (631 of 3867 in July at Paleet).
  `Saleslines` returned **zero** voided rows, so it appears to exclude them
  rather than flag them. Do not assume symmetric void handling.
- `Currency` per line (NOK observed). Group by it; never sum across currencies.
- `Cost` exists on `Saleslines` (margin computable) but **not** on `Sales`.

## Store identification

Three easily-conflated concepts:

- `Stock` — location, e.g. `Høyer Paleet`
- `Store` — legal entity, e.g. `HC Paleet AS`
- `STOREID_FK` — register/POS id; **several map to one stock**

**Høyer Paleet = `STOCKID_FK` 3229**, registers `3529, 3530, 3431, 3568`
(derived from August line data; a register active only in an earlier period
would not be captured). `BMB Paleet` is an unrelated store (`STOCKID_FK` 1333).

~20 stocks visible, including Sørlandssenteret, Arendal, Trondheim, Grimstad,
Strømmen, Sandefjord, Bodø, Sjølyst, Solsiden, Storo, Gulskogen, Stadionparken,
Kvadrat, Harstad, Haugesund, Byporten, Online.

## Environment

Local TLS interception: Python `urllib` fails `CERTIFICATE_VERIFY_FAILED` where
`curl` verifies a valid DigiCert chain. The client must use the system trust
store (`truststore`/`certifi`). **Never disable verification** — that exposes
credentials to the proxy.

LibreOffice is **not installed**, so `recalc.py` cannot verify workbook formulas
on this machine. Either install it or compute values in Python and cross-check.

Performance: `Saleslines` with date filter and `$select` returns 1–6 s.
`Products` is slow (~15 s).
