# API Discovery Notes

**Spike run:** 2026-08-10
**Status:** Blocked — gateway base URL not yet established

## Summary

The documented Front Systems API (the one the supplied keys belong to) could not
be reached, because its gateway hostname is not publicly discoverable. A
different, unrelated Front Systems API *was* found and mapped. No sales data was
retrieved.

## Confirmed findings

### 1. `api.frontsystems.no` is a legacy partner API, not the documented one

- Serves an ASP.NET application; backend identifies itself as
  `fsapiv3.azurewebsites.net`.
- Publishes a full Swagger 2.0 definition, unauthenticated, at
  `https://api.frontsystems.no/swagger/docs/v1` (~42 KB, titled **"KTKApi"**).
- 66 endpoints across these groups:

  | Group | Count | Purpose |
  |---|---|---|
  | Stockcount | 27 | Stock-counting application |
  | ProductTransfer | 20 | Inter-store transfers, deliveries, orders |
  | Miinto V1–V5 | 9 | Marketplace product feeds |
  | Google feeds | 2 | Local inventory / product feeds |
  | MendoApi | 2 | `Day`, `LastDay` — partner push |
  | WebSale | 2 | `GetWebSales`, `PickWeborder` |
  | Other | 4 | FrontOPS, version, Sensorline |

- **The supplied keys return HTTP 401 here** (tested against
  `/api/Stockcount/GetStocks`, which requires no parameters). Empty response body.
- `securityDefinitions` is absent from its Swagger, so its auth scheme is
  undocumented — but it is evidently not the two-header scheme in the developer
  portal docs.
- **No general sales or turnover endpoint exists on this API.** `WebSale` covers
  web orders only. `MendoApi/Day` requires a `postUrl` parameter, meaning it
  *pushes* data to a caller-supplied URL rather than returning it — not a usable
  pull, and not something to invoke without deliberate intent.

Conclusion: this API is out of scope for reporting.

### 2. TLS interception is present in the local environment

Python's `urllib` fails with `CERTIFICATE_VERIFY_FAILED` (self-signed cert in
chain) against these hosts; `curl` succeeds and reports `SSL certificate verify
ok` with a legitimate DigiCert/GeoTrust issuer.

Implication for implementation: the HTTP client must use the system trust store
(e.g. `certifi` explicitly, or `truststore`). **Verification must not be
disabled** — credentials would be exposed to the intercepting proxy.

### 3. Hosting topology

`developer.frontsystems.com` and `api.frontsystems.no` resolve to the *same*
Azure Front Door endpoint (`frontsystems-g9g5ctabdzanf6d2.z01.azurefd.net`),
so routing is by host plus path. `portal.frontsystems.no` is a separate App
Service (`keystone-portal.azurewebsites.net`).

### 4. Gateway hostname not found

No DNS record exists for any of: `api.frontsystems.com`, `apim.frontsystems.com`,
`frontsystems.azure-api.net`, `fsapi.azure-api.net`, `front-systems.azure-api.net`,
`fs-api.azure-api.net`, `api2.frontsystems.no`, `api.frontsystems.se`.

The developer portal homepage contains no gateway reference in its HTML, and its
API catalog requires sign-in.

Documented paths (`/api/Stores`, `/api/stock/adjust`, `/api/WebhooksEvents`) all
return 404 on both reachable hosts, under several routing prefixes.

## Unresolved — blocking

1. **Gateway base URL for the documented API.** Must be read from the API details
   page in the developer portal while signed in. Every other question depends on
   this.
2. **Whether the supplied keys authenticate at all.** No authenticated request
   has yet returned 2xx, so the credentials remain unverified.
3. **Whether historical sales are pullable.** The vendor's documentation
   emphasises webhook push (`SaleCreated`). The design risk noted in the spec —
   that sales may be push-only with no historical pull — is still open, and is
   the single most important thing to settle next.
4. Pagination mechanism, rate limits, OData support, and history depth — all
   still unknown.

## Next step

Obtain the base URL from the developer portal, set `FRONT_SYSTEMS_BASE_URL`, and
re-run the spike starting with a store listing.
