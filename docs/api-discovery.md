# API Discovery Notes

**Spike run:** 2026-08-10
**Status:** Connected. Sales and stock data reachable. Several API traps identified.

## Connection

| Item | Value |
|---|---|
| Base URL | `https://frontsystemsapis.frontsystems.no` |
| Auth | `Ocp-Apim-Subscription-Key` + `x-api-key` headers — **confirmed working** |
| Protocol | OData **v3** (`odata.metadata` in responses; `datetime'...'` literals) |
| Backend | `fsapiv3.azurewebsites.net` |

`api.frontsystems.no` is a *different*, legacy partner API (KTKApi) where these
keys return 401. Ignore it. Notes on it retained at the end of this file.

## Entity sets

`$metadata` and the service root both return 404 through the gateway, so the
catalog cannot be enumerated. Found by probing:

| Entity set | Notes |
|---|---|
| `Saleslines` | **Primary reporting source.** Line-level sales, ~70 fields |
| `Sales` | Sale headers: `SALEID`, `SaleDate`, `Total`, `STOREID_FK`, void/test flags |
| `Stockstatus` | Point-in-time stock; requires `snapshotDateTime` |
| `Stockmovements` | Stock flow |
| `Products` | Catalog (slow: ~15 s for `$top=1`) |

Confirmed **absent**: `Stores`, `Store`, `Customers`, `Orders`, `Turnover`,
`Settlements`, `Transactions`, `Receipts`, `WebSales`, `Salesstatistics`.
There is **no store dimension endpoint** — store names come from `Saleslines`.

## Traps — read before writing any query

These caused wrong answers during the spike. Each fails **silently**, returning
an empty result set with HTTP 200 rather than an error.

### 1. A `SaleDate` filter is mandatory

A filter on any other field returns **zero rows unless combined with a
`SaleDate` predicate**.

```
$filter=STOCKID_FK eq 3229                                    -> 0 rows    (WRONG)
$filter=STOCKID_FK eq 3229 and SaleDate ge datetime'...'      -> 521 rows  (correct)
```

### 2. `$orderby` without a date filter is unreliable

`$orderby=SaleDateTime desc` with no date filter reported the newest sale as
2026-08-01, while a date-filtered query proved sales existed through 2026-08-05.
**Never determine recency without a date filter.**

### 3. `substringof()` is not supported and returns empty

```
$filter=substringof('Paleet',Stock)   -> 0 rows, despite Stock='Høyer Paleet'
```

No error is raised. Avoid all OData string functions; verify any operator before
relying on it.

### 4. Joined display fields are not filterable

`Stock`, `Store`, `Brand`, `Name` appear in output but filtering on them returns
empty — even with exact `eq` and a date filter. **Filter on numeric FK columns
only** (`STOCKID_FK`, `STOREID_FK`, `PRODUCTID_FK`).

Consequence for the MCP server: it must resolve store *names* to IDs itself, by
sampling `Saleslines` over a date window and building a name→ID map. Store
filtering cannot be pushed to the API by name.

### 5. `Saleslines` embeds customer PII

Every row carries `FirstName`, `LastName`, `Email`, `Phone`, `Address`,
`PostalCode`, `City`. **Always pass `$select`** limited to the fields a report
needs, so personal data is neither transferred nor placed into model context.
This is a GDPR-relevant default, not an optimisation.

## Field semantics

- `Price` is the **net line amount** after discount: confirmed
  `Price = FullPrice - Discount` (e.g. FullPrice 79.00, Discount 79.00, Price 0.00).
  Revenue = `SUM(Price)`. `VATPercent` is carried separately.
- `Qty` was `1` on every row sampled at Paleet in August.
- `IsVoided` must be excluded from totals. `Sales` also has `IsTest`, `IsFailed`,
  `IsComplete` — semantics not yet verified for `Saleslines`.
- `Currency` present per line (NOK observed). Group by it; never sum across.

## Store identification

Three distinct concepts, easily conflated:

- `Stock` — physical/logical stock location, e.g. `Høyer Paleet`
- `Store` — legal entity, e.g. `HC Paleet AS`
- `STOREID_FK` — register/POS-level id; **several map to one stock**
  (3530, 3568, 3529, 3431 all → stock 3229)

**Høyer Paleet = `STOCKID_FK` 3229** (matches `Stockid` in `Stockstatus`).
`BMB Paleet` is an unrelated store (`STOCKID_FK` 1333) — do not conflate.

Roughly 20+ stocks are visible, including Sørlandssenteret, Arendal, Trondheim,
Grimstad, Strømmen, Sandefjord, Bodø, Sjølyst, Solsiden, Storo, Gulskogen,
Stadionparken, Kvadrat, Harstad, Haugesund, Byporten, Online.

## Data freshness

**The sales feed ends 2026-08-05 12:30:08.** Date-filtered queries for
`SaleDate >= 2026-08-06` return zero rows for Paleet *and for every store*.
Cause unknown — stalled sync, or a lag inherent to this dataset. Worth
establishing before anyone relies on "today" figures.

## Environment

Local TLS interception is present: Python `urllib` fails
`CERTIFICATE_VERIFY_FAILED` where `curl` verifies a valid DigiCert chain
successfully. The client must use the system trust store (`truststore` or
`certifi`). **Do not disable verification** — that would expose credentials.

Performance observed: `Saleslines` with a date filter and `$select`, ~500–2000
rows, returns in 1–3 s. `Products` is slow (~15 s).

## Appendix — legacy `api.frontsystems.no`

Separate API titled "KTKApi", public Swagger at `/swagger/docs/v1`, 66 endpoints
(Stockcount 27, ProductTransfer 20, Miinto feeds 9, Google feeds 2, MendoApi 2,
WebSale 2). Supplied keys return 401. No general sales endpoint. Out of scope.
