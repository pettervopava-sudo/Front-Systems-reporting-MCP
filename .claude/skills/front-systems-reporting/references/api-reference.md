# Front Systems OData — API reference

Established by direct probing against a live tenant (Høyer), August 2026. The
vendor's published documentation does not cover most of this.

## Connection

| Item | Value |
|---|---|
| Upstream URL | `https://frontsystemsapis.frontsystems.no` — what the local read proxy calls |
| Client URL | `http://127.0.0.1:8812` — the local read proxy, what the tools call |
| Auth | two headers, both required |
| | `Ocp-Apim-Subscription-Key: <subscription key>` |
| | `x-api-key: <integration key>` |
| Protocol | OData **v3** — date literals are `datetime'2026-07-01T00:00:00'` |
| Backend | `fsapiv3.azurewebsites.net`, model namespace `KTKApi.Models` |

Nothing in the repo talks to the upstream URL directly. `FRONT_SYSTEMS_BASE_URL`
points at the local read proxy (`bash scripts/serve_proxy.sh`, one per user),
which holds the keys, allows only `GET` against an entity allowlist, and strips
customer fields from `Sales` and `Saleslines` rows. The proxy reads the upstream
address from `FRONT_SYSTEMS_UPSTREAM_URL`, defaulting to the one above. On macOS
run `bash scripts/install_proxy_service.sh` once and it stays up as a login
service, so the guard rail is standing before any report script runs; that
script also generates the launchd plist for whichever machine it runs on.

Credentials come from `.env` as `FRONT_SYSTEMS_SUBSCRIPTION_KEY` and
`FRONT_SYSTEMS_API_KEY`. Never print their values, and never let request headers
reach an error message or log.

`api.frontsystems.no` is a **different, unrelated legacy API** (titled "KTKApi",
66 REST endpoints for stock-counting and marketplace feeds) that rejects these
keys with 401. Don't confuse the two.

## Entity sets

`$metadata` and the service root both return 404 through the gateway, so the
catalog cannot be enumerated — these were found by probing, and ~35 other
plausible names returned 404.

| Entity set | Grain | History |
|---|---|---|
| `Sales` | one row per transaction | deep (2017 and earlier → today) |
| `Saleslines` | one row per product line | **deep (2017 →) via `from`/`to` params — see below** |
| `Stockstatus` | stock snapshot | requires `snapshotDateTime` param |
| `Stockmovements` | stock flow | sparse; empty for some stock ids |
| `Products` | catalogue | slow (~15 s even for `$top=1`) |

Confirmed absent: `Stores`, `Customers`, `Orders`, `Turnover`, `Settlements`,
`Transactions`, `Receipts`, `Salesstatistics`, and every `*history`, `*archive`,
`*lines` variant tried. **There is no store dimension endpoint** — store names
must be harvested from `Saleslines`.

There are no navigation properties: `$expand` from `Sales` to lines fails with
`Could not find a property named 'Saleslines' on type 'KTKApi.Models.SALE'`.

## Silent-failure behaviours

Each returns HTTP 200 with an empty or truncated array — never an error.

### `Saleslines` needs `from`/`to` parameters for history (user-discovered)

Without parameters the endpoint serves only a DEFAULT WINDOW (observed: the
current period from 2026-08-01). `$filter` on `SaleDate` can only narrow that
window — it looked like "no data before 2026-08-01" and was wrongly recorded
as a retention limit. The truth: undocumented plain query parameters open the
full history, at least back to 2017 (probed: July 2017 lines and headers both present):

```
/odata/Saleslines?from='2026-07-01'&to='2026-07-31'&$select=...&$top=2000000
```

Both dates quoted; `to` is INCLUSIVE; unquoted dates give HTTP 500; from>to
gives 0 rows. **The window spans at most 31 days** — 32 days or more returns
HTTP 400 (probed: 31d OK; 32/59/61/365d all 400), so multi-month pulls go
month by month. `$filter` (e.g. on STOCKID_FK) combines with the window
server-side. Same quoting pattern as `snapshotDateTime` on `Stockstatus`.
Full July 2026 (54,845 lines) reconciled against Sales headers to within
1 NOK.

### `$top` precedes `$filter`

`$top` limits rows **scanned**, not returned. Same filter, varying `$top`:

| `$top` | rows | span |
|---|---|---|
| 100 | 1 | 08-01 |
| 1000 | 112 | 08-01 |
| 5000 | 521 | 08-01…08-05 |
| 20000 | 1475 | 08-01…08-10 |
| 50000 | 1475 | 08-01…08-10 (saturated) |

Set `$top` to 200000+ and confirm the count stops growing. `$skip` paging cannot
be made consistent and must not be used.

### `substringof()` is unsupported

`substringof('Paleet',Stock)` → 0 rows, though `Stock` is literally
`Høyer Paleet`. Avoid all OData string functions.

### Joined display fields are not filterable

`Stock`, `Store`, `Brand`, `Name`, `Employee`, `Pos` are present in output but
filtering on them returns empty even with exact `eq`. Filter only on
`STOCKID_FK`, `STOREID_FK`, `PRODUCTID_FK`, `SaleDate`.

### Date filtering *does* work

`SaleDate ge/lt datetime'...'` behaves correctly. Apparent date-filter failures
are almost always the `$top` behaviour above.

## Field semantics

### Saleslines

| Field | Meaning |
|---|---|
| `Qty` | units; **`-1` for returns**, otherwise 1. No separate return flag |
| `Price` | **unit** price, net of discount (`FullPrice - Discount`) |
| `FullPrice` | unit price before discount |
| `Discount` | per-unit discount amount |
| `Cost` | unit cost |
| `VATPercent` | as a fraction (`0.250` = 25%) |
| `IsVoided` | always false — voided lines appear to be omitted, not flagged |

Derived correctly (Price is VAT-inclusive, Cost is VAT-exclusive):

```
line revenue (brutto) = Qty * Price
netto omsetning       = brutto / 1.25
BF (deck definition)  = netto - SUM(Qty * Cost)     # NOT brutto - cost!
BF %                  = BF / netto
rabatt                = Qty * Discount
```

Validated against the June-2026 deck: BF 17,548k vs deck 17,581k (0.2%),
rabatt 10,417k vs 10,432k (0.15%). `Qty*(Price-Cost)` mixes VAT bases and
overstates margin; all tooling now computes the netto-based BF.

`Gender` codes: `f` = dame, `m` = herre, `"m,f"` = unisex, or blank.
Carrier bags are products named `Høyer Plastpose`/`Høyer Pose`.

PII columns present on every row: `FirstName`, `LastName`, `Email`, `Phone`,
`Address`, `PostalCode`, `City`, plus `CUSTOMERID_FK`. Exclude via `$select`
unless the task requires them.

### Sales

`Total` is the transaction total and **reconciles exactly with
`SUM(Qty * Price)`** from the corresponding lines — verified to 0.00 on all eight
overlapping days of a 1–10 August sample (1,796,298.45 NOK both ways).

`IsVoided` flags cancelled transactions here and must be excluded (631 of 3867
July rows at one store). Other flags observed: `IsComplete`, `IsSetAside`,
`IsDep`, `IsTest`, `IsFailed`, `IsWeb`, `IsInhouse`, `IsOffline`.

`Sales` has **no `Cost`**, so margin is impossible from headers alone.

Header and line transaction counts can differ by one or two where a sale's only
line was voided.

## Store identification

Three easily-conflated concepts:

- **`Stock`** — the location, e.g. `Høyer Paleet`
- **`Store`** — the legal entity, e.g. `HC Paleet AS`
- **`STOREID_FK`** — a register/POS, several of which map to one stock

Grouping by `STOREID_FK` splits a single shop into its registers, which is
usually not what's wanted. Group by `STOCKID_FK`.

Known mappings (harvest fresh rather than trusting this list — it reflects one
tenant at one point in time):

| Stock | `STOCKID_FK` | Registers (`STOREID_FK`) |
|---|---|---|
| Høyer Paleet | 3229 | 3529, 3530, 3431, 3568 |
| BMB Paleet | 1333 | 1979 |
| BMB Paleet Shopify | 1901 | not sampled |
| Høyer Bergen (lines) | 279 | closed 2026-08-01; July lines exact vs registers |
| Outlet Nydalen | 2957 | excluded by the deck |
| Høyer Test Shopify | 5442 | the deck's "Teststore" — excluded |

`Høyer Bergen` (registers 324, 340, 370, 373 in `Sales`) **closed 2026-08-01**
and never appears in `Saleslines` — its line data simply does not exist. Its
header history through July 2026 is real and reportable.

Resolving on the name "Paleet" returns all **three** of these — not two, as an
earlier version of this table claimed. `BMB Paleet` and `BMB Paleet Shopify`
are unrelated companies trading in the same mall as `Høyer Paleet`, not two
views of one company; similar names are a real hazard here. Don't treat the
count of three as fixed either — it comes from whichever stocks the harvest
window catches trading, so a different window can surface a different number
of "Paleet" matches.

Web-channel taxonomy (user-confirmed): `Høyer Online Frontend` (183) is the
chain's webstore (the deck's "Høyer Webshop" line). The `* Shopify` stocks
(Trondheim 2014, Harstad 2794) are separate webstores owned by the physical
store of the same name; the chain's reporting merges them into that store.
`Strømmen Treasure` (5368) is a Strømmen unit, not a webstore.

Other stocks seen: Sørlandssenteret, Arendal, Trondheim (+ Shopify), Grimstad,
Strømmen, Sandefjord, Bodø, Sjølyst dame, Solsiden, Storo, Gulskogen,
Stadionparken, Kvadrat, Harstad, Haugesund, Byporten, Online Frontend.

## Probed 2026-08-24: Stockstatus fields, SID semantics, return reasons

**Stockstatus carries value fields**: `Qty`, `AvailableQty`, `ReservedQty`,
**`Cost`**, `OutPrice`, `RecommendedRetailPrice` plus `Brand`/`Group`/
`Season`/`Stockid`/`Productid`/`EAN`/`SizeLabel`. Inventory value at cost is
computable per store/brand/group/season → sell-through and GMROI are
buildable. `$filter=STOCKID_FK eq N` on Stockstatus returns HTTP 400 —
fetch unfiltered (top applies normally here) and filter client-side.
How far back snapshotDateTime reaches is unverified.

**Saleslines has more fields than the standard SELECT**: `SaleDateTime`
(clock time), `Cat`/`Subroup` (group hierarchy), `DiscountPercent`,
`ReceiptLabel` (line TEXT, not a receipt reference), `OrderLineReasons`
(list; `{Type: SystemReturn}` on most return lines, Discount/promo codes
on sale lines), `RegisteredStoreID` (null in practice), `SID`.

**SID = visit/receipt-chain id, one per sale**: a return sharing SID with
a positive sale is an EXCHANGE in the same visit (juli 2026: 1,178 of
1,719 return lines = 69 % bytte, 0 cross-day matches, 541 rene returer).
SID does NOT link to the original purchase days earlier; no
original-receipt field exists in the API. **Returns→original seller works
heuristically**: same EAN + same stock, latest prior sale → unique seller
for 89 % of juli returns with only 3 months of history (5 % ambiguous,
5 % no candidate). Returns are executed by a mix of shared users
(Webshop 438, "Høyer" 174, Storo 71, store-named users) and real persons.

## Environment notes

Some machines run a TLS-intercepting proxy under which Python's bundled CA store
fails (`CERTIFICATE_VERIFY_FAILED`) while `curl` verifies the real DigiCert chain
fine. Use `curl`, or `truststore`/`certifi` in Python. **Never disable
certificate verification** — that exposes the API keys to the interceptor.

Typical latency: `Saleslines` with a date filter and `$select`, 1–6 s for a few
thousand rows. `Products` is much slower.
