# Månedsrapporten — conventions and machinery

## Hovedrapporttyper (registry)

Two main report types exist (per 2026-08-24). When the user asks how many
report types are saved, the answer is these two:

1. **Månedsrapport** — the 8-part suite, produced on request the 1st of
   each month for the previous month (`scripts/monthly_run.sh`; routine
   and conventions in this file).
2. **Høyer Nye KPI** — own report type beside the suite
   (`scripts/hoyer_nye_kpi.py --month` → `reports/Hoyer_Nye_KPI.html`,
   also on port 8811; own artifact URL). **HTML only — never PDF.**
   Content defined 2026-08-24: K1 Kapitalavkastning varer (BF per
   innkjøpskrone = transform of BF %; sell-through and GMROI from
   Stockstatus snapshots cached as stockagg_YYYY-MM-DD.json; per
   butikk/merke/varegruppe/sesong) and K2 Returer (one row per user x butikk: per utførende with
   bytte-share via SID and fellesbruker flag; returgrad per opprinnelig
   selger via the EAN+butikk heuristic, ~90 % dekning with 7 months of
   klines_YYYY-MM.json; unntaksliste ≥5k/no-EAN/no-original/felles).
   The del-07 seller-KPI change (exclude returns) activates only on
   Petter's word.

The chain's monthly report ("Månedsrapport | HØYER-kjeden", a Norwegian PPTX
deck) is reproduced as an HTML/PDF suite. Everything here was validated to the
krone against the June 2026 deck's own tables before being trusted.

## Månedsrutinen — user request, not a cron

The månedsrapport is produced ON REQUEST on/after the 1st of each month,
for the PREVIOUS month, when the user says something like "ta ut
Månedsrapport" / "Månedsrapport med aktuelle tall" (they may also ask to
"redigere" it — then change the scripts, rebuild, and keep the same
artifact URL). One command builds everything in the right order:

```
bash "~/Front Systems API/scripts/monthly_run.sh"            # forrige måned
bash "~/Front Systems API/scripts/monthly_run.sh" 2026-08    # eksplisitt
```

It runs monthly_report → line_reports → monthly_suite → make_pdf → 
serve_reports.sh (8811). **PDF is frozen (user rule 2026-08-24): work
HTML-only** — bind the combined file with `make_pdf.py --month ... 
--html-only` (no Chrome, no pypdf); regenerate the PDF only if Petter
asks for it again. Afterwards: republish the artifact from the
conversation — SAME URL (see artifact-links memory), file
`reports/Manedsrapport_<mnd>_<år>.html`, label like "august-2026". Verify
before delivering: the June-validation gate printed OK, the leak gate did
not trip, and spot the nøkkeltall row against last month's edition. The
suite nav, filenames and the period-purity leak gate are all month-aware
(set_suite_month + a regex built from the report month).

## The scripts (in `~/Front Systems API/scripts/`)

| Script | Produces |
|---|---|
| `monthly_report.py --month YYYY-MM` | Report 01: nøkkeltall (incl. BF/rabatt rows from linjeagg), per-store, månedsvis matrix, salgsdager, enkeltsalg — writes `01_Manedsrapport_<mnd>_<år>.html` |
| `line_reports.py --from --to --seller-min 100` | Reports 03–08 from the month's Saleslines window (Omsetning og BF, sesonger, rabatter, merker, selgere, diverse) |
| `monthly_suite.py --month YYYY-MM` | Report 02 (chart pairs 2017→, per-store nkl tables, monthly matrix); BUILDS 08 Selgere KPK; overwrites 03 and 09 with suite versions; INJECTS marker blocks into 03 (eight store charts + rolling + 10-year), 04 (sesong matrices), 05 (six rabatt sections), 06 (YTD brand table + top-5 brand tables w/ logos), 07 (PPK charts + five seller tables) |
| `monthly_run.sh [YYYY-MM]` | Hele rutinen i riktig rekkefølge (default: forrige måned) |
| `make_pdf.py --month YYYY-MM --html` | Binds parts 01–08 into the A4-landscape PDF and the single-file `Manedsrapport_<mnd>_<år>.html` |

**Run order matters: line_reports FIRST, then monthly_suite** — both write 03
and 09, and the suite versions must win. Suite numbering (since
2026-08-25): 01 månedsrapport, 02 Oppsummering, 03 Omsetning og BF,
04 Sesonger, 05 Rabatter, 06 Merker, 07 Selgere PPK, 08 Selgere KPK
(built by monthly_suite; three KPK top-20 tables), 09 Diverse. NB: the
deck's own "KPK juli" page showed JUNE data (verified exactly) -- the
report discloses this; trust our juli numbers.

Monthly header aggregates are cached in `reports/cache/YYYY-MM.json`, chain
line aggregates in `linjeagg_YYYY-MM.json` (built on demand by
`monthly_report.ensure_linjeagg`), and per-store line aggregates incl.
line-based transaction counts in `linjestore_YYYY-MM.json`
(`ensure_linjestore`; unmapped stocks kept by name for composition
questions). Deck composition applies at stock level. Past months never
change, so periodic runs fetch only the new month. NB: the from/to window
takes at most 31 days -- always fetch month by month.

**Seven predecessor/channel units merge into today's stores, each proven
against the deck's own tables:** CR Bergen (2841), Paleet Zalando (3589),
CR Strømmen (2888) and CR Sjølyst dame+herre (3002/3003) — 2022–2023
click-and-reserve/marketplace channels, proven to 0.1M on the BF-i-kroner
2023 page (and the closed-stores bucket lands on the deck's 13.6M once
Zalando moves out of it). Bodø 2 (3456) and Byporten Herre (2559) are
proven to stay OUTSIDE — the deck matches without them. Earlier proven to
the krone: Sjølyst herre (stock 144,
"IKKE BRUK - Høyer Sjølyst herre", traded through 2025-03) into Høyer
Sjølyst, and Kvadrat's predecessor (stock 1584, today's dimension name
"Fashion Outlet", the deck's row label "tidl Collabs", traded through
2024-01) into Høyer Kvadrat. Collabs LAGUNEN (1642) is a different,
closed store and stays excluded. Report 02's Nøkkeltall tables (måned +
hittil i år, bottom of the page) are validated cell by cell against the
juli-2026 deck: every brutto and trans figure exact -- 19 stores x 3
years in both tables, YTD totals 2024/2025 to the krone -- except the
deck's own chain-level residual (2026: 4,646 kr, the known Solsiden June
variance). The monthly matrix validates the same way: 30 of 31 months
2024-01..2026-07 exact in brutto AND trans, the one exception being juni
2026 (+4,645 kr / +2 trans = that same Solsiden residual); year totals
2024/2025 exact. The matrix leaves post-report months blank (the BI's
"-100 %" artefacts are not reproduced) and measures the report year's
Sum change YTD-vs-YTD, not YTD-vs-full-year as the BI pivot does. BF kr and rabatt totals differ 0.1-0.7 % (their BI's cost
basis; single stores can differ more). **Harstad is the one store whose BF %
departs from the deck by several points every period** (YTD 2026: 29.3 %
vs deck 38.0 %): its Shopify stock 2794 out-sells the physical store and
carries BF 25.5 % in the API's Cost field, so the deck's BI must cost the
Shopify channel differently. Disclosed in the BF % chart intros; nothing
to fix on our side. BF % rounds identically everywhere else.

**Report 02 (Oppsummering) shows every year with TODAY's store composition —
user decision (2026-08-13), do not revisit without asking.** Measured
three-way for juli 2021: whole chain (all then-trading stores) 82.0M,
today's composition 57.6M (incl. all predecessor units), the PPT's own chart 61M — the deck's BI counts
some but not all since-closed stores, per a store attribute the API does
not carry, so the PPT chart cannot be reproduced exactly under any
composition. The report's note discloses this with the numbers. BF %
matches the deck within ~0.4 pp everywhere; from 2025 the definitions
coincide. (The per-stock probe data was session-scratch and is gone; the
comparison reruns from the API in minutes if ever needed.)

## Deck conventions (validated against the June 2026 edition)

- **"Brutto omsetning" = `SUM(Sales.Total)` over non-voided sales, VAT-inclusive.**
  Matched the deck per store to the krone. "Netto omsetning" = brutto / 1.25.
- **BF = netto − `SUM(Qty*Cost)`; BF % of netto; rabatt = `SUM(Qty*Discount)`.**
  Validated vs the June deck (0.2 % / 0.15 % off). Line history is DEEP via the
  `from`/`to` parameters (user-discovered) — all sections computable for any
  month; the linjeagg_YYYY-MM.json cache feeds the KPI rows.
- **A deck "butikk" is a stock, with web merges**: Trondheim = 181 + 2014
  (Shopify), Harstad = 2239 + 2794 (Shopify), Strømmen = 151 + 5368 (Treasure —
  not a webstore). "Høyer Webshop" = Online Frontend (183) alone — the CHAIN
  webstore. Store-owned Shopify webstores merge into the eponymous store
  (user-confirmed taxonomy).
- **Høyer Bergen = registers {324, 340, 370, 373}** in `Sales`, stock 279
  in `Saleslines` (line history complete 2017→juli 2026, exact vs registers
  every July). Closed 2026-08-01 (user-confirmed): include it in months it
  traded; exclude after closure.
- **Excluded, matching the deck's stated filter**: BMB stocks 1333/1901,
  register 3207 (Outlet Nydalen, antatt), and closed-store registers
  (188, 348, 210, 172, 2898, 4628 — all idle since mid-2025).
- **Known residual variances vs the deck**: chain revenue differs by 4,645 kr
  (one store, Solsiden, June sample); transaction counts differ ~0.5 % (their
  BI applies an extra filter we cannot see). Revenue is exact everywhere else.
- The deck's "beste selger" thresholds are ≥100 transactions/month — scale
  proportionally for shorter windows and say so. System sellers (`Webshop`,
  `Shopify Integrasjon`) are excluded from selger tables.
- Season codes on lines are real: `2601 Main` = 2026 season 1 main collection,
  `Pre` = pre-collection; `0`/`1`/empty = basis/uspesifisert.
- **Største kunder is deliberately never produced** — it needs customer
  identifiers, which this pipeline never fetches. Reopening that is a user
  decision, not a default.

## The period-purity rule (twice enforced by the user — treat as absolute)

A month's edition may contain **nothing newer than the month it covers** — not
as data, not in explanatory text, not as a generation timestamp. "Linjedata
starter 2026-08-01" inside a July report is a violation; so is "Generert
2026-08-12" in a footer. Phrase around it ("finnes ikke i API-et for
perioden"; "siste driftsmåned" instead of a closure date). `make_pdf.py`
enforces this with a leak gate that refuses to bind any part containing
post-period date strings — keep that gate.

**Every figure must be the report month's own**, except historical
comparison periods: prior-year same month, YTD, and rolling windows
(user rule, 2026-08-12, final). Another month's figures may never stand in
for the report month — a version of report 01 that showed the June deck's
BF/rabatt in the July edition was rejected. When a metric cannot be computed
for the period, the row states "ikke tilgjengelig" with a period-pure reason.

**The suite is one bound monthly unit.** Every report numbered into the
månedsrapport series (01–08, shared nav) must cover the SAME month. Ad-hoc
line-window reports for a different period are legitimate deliverables but
live OUTSIDE the series — separate folder (e.g. `reports/linjedata-august/`),
nav linking only among themselves (`line_reports.py --only ... --outdir ...`
does this automatically). Numbering an other-period report into the suite was
rejected by the user even with the window clearly labelled.

## Validation gates — keep them alive

- `monthly_report.py` re-validates its June-2026 computation against the deck
  grand total (48,840,661) on every run and warns past ±10k.
- It also warns when unmapped registers carry revenue in the report month —
  that is how a new till or a new store surfaces.
- The register→store map is harvested live from Saleslines (there is no store
  endpoint). It is dimensional metadata, proven correct on pre-August data;
  using it does not count as using post-period figures.

## Sesongmatrisene i del 04 (injisert av monthly_suite)

`inject_sesong` appends two per-store matrices (andel pr sesongkode +
BF % pr kode; buckets = the six newest YYSS codes, Eldre, Base,
Carry-overs; Pre/Main/High collapse into the code) into
`04_Sesonger.html` between SESONG-BUTIKK markers — line_reports must run
first, injection is idempotent. Data: klines caches, which since
2026-08-24 also keep closed-Høyer-store rows (for Nedlagte rows in years
they traded). Two proven deviations vs the deck, disclosed in the
report's note: their BI enriches basis-coded ('1') items with the
product register's true season (~1-2 % moves, mostly Base→Eldre — the
attribute is not in the API), and their "YTD" sesong page covers a
longer window than the calendar year (their Nedlagte row is 2401-heavy,
so 2024 is inside it); ours is strictly januar–report month.

## Rabattseksjonene i del 05 (injisert av monthly_suite)

`inject_rabatter` appends six sections into `05_Rabatter.html` (RABATT-
BUTIKK markers, idempotent): nøkkeltall-charts (4 lanes: rabatt av
fullpris, rabatt kr, brutto, BF %; sorted by BF %) for måned + YTD,
rabatt-%-per-butikk year charts (metric 'rabpct' = rab/full) for måned +
YTD siste 3 år, and two discount-band matrices (sesongvarer = current SS
code; basevarer = Base + Carry-overs). **Band convention proven against
the deck (sumavvik 1.2 pp): pct = Discount/FullPrice, column label is
the UPPER edge — "20%" = [10,20), "Inntil 10%" = (0,10), "70% >" = ≥70.**
Employee purchases excluded via the IsEmployee line flag. linjestore
caches carry "full" (FullPrice) since 2026-08-24 (2024-01→ refetched;
2017–2023 files lack it — rabatt% only computable 2024→). klines carry
Discount/FullPrice/IsEmployee. Price == FullPrice − Discount holds
exactly; StdDiscount/DiscountPercent are unused by the POS.

## Del 06 — merker (proven conventions)

Top-5 brand per-store tables are DYNAMIC: the report month's five biggest
brands, each as a three-year matrix (brutto + BF % per year, Nedlagte
butikker aggregated, Sum pinned), month + YTD. "Merker med høyest
omsetning — YTD": threshold 2.3M applies PER CELL (sub-threshold values
blank, as the deck does), rows require a visible report-year cell (user
rule), closed stores COUNT in brand figures (PRL 2024 = today + nedlagte
= deck to the krone). Brand logos: scripts/assets/brandlogos/*.img,
fetched once via Google's favicon service THROUGH CURL (python-ssl dies
under the TLS proxy; clearbit is dead), embedded as data URIs on a white
chip; a file already in the cache is never overwritten — NN.07 uses the
user's own navy tile. BRAND_DOMAINS map in monthly_suite.

## Del 07/08 — selger definitions (probed and calibrated)

PPK = net item count EXCLUDING carrier bags (Name matching
'^h[øo]yer .*pose' — do NOT use a generic bag regex, it catches real
handbags) divided by distinct receipts. Gender codes on lines: f=dame,
m=herre, "m,f"=unisex (outside both), blank. Seller tables: per
seller×store, felles/system users excluded (is_felles in
hoyer_nye_kpi), thresholds 100–500 / ≥500 within the window, top 20;
rank_by 'ppk' or 'kpk' (omsetning pr transaksjon) swaps first/last
columns. Deck deviations stay under ±0.03 PPK / a few receipts (their
BI counts web orders by another key); juli #1 rows validated exact.

## Deck errata (proven deck-side errors — do not "fix" our numbers)

1. Sesong-YTD page covers a window including 2024, not calendar YTD.
2. "KPK beste selger — JULI" page shows JUNE data (Ingrid/Trondheim
   135/889,568 = juni exactly; contradicts the deck's own PPK juli page).
3. Rabatt band labels are upper-exclusive edges ("20%" = 10–20 %).
4. Their BI re-seasons basis-coded items via the product register.
5. Harstad BF % differs several pp every period (their Shopify cost
   basis; our API cost is authoritative for us).
6. Solsiden juni residual 4,645/4,646 kr appears in every window
   containing juni 2026.
All are disclosed in the reports' notes where relevant.
