---
name: front-systems-reporting
description: Pull sales, revenue, stock and KPI reports from the Front Systems retail POS API (OData at frontsystemsapis.frontsystems.no). Use this whenever the user asks for sales figures, revenue, turnover, units sold, margin, returns, basket size, stock levels, or store performance for a Front Systems store — including Høyer stores and any request mentioning Front Systems, frontsystems, FS_API_KEY, Saleslines, Stockstatus, or a store name like "Høyer Paleet". Also use when building or debugging an integration against this API, since it has several silent-failure behaviours that produce confidently wrong numbers. Trigger even if the user only names a store and a period ("how did Paleet do last week", "July sales for Storo") without mentioning Front Systems by name.
---

# Front Systems reporting

This API returns **wrong answers silently** rather than erroring. A malformed
query returns HTTP 200 with an empty or truncated array, which looks exactly
like a real "no sales" answer. Every rule below exists because it produced a
confidently wrong number in practice.

Read `references/api-reference.md` before writing any query. Use
`scripts/fs_query.py` rather than hand-rolling curl — it enforces these rules.

## Use the MCP server

The `front-systems` MCP server implements the rules below in tested code:
`list_stores`, `sales_report`, `stock_report`, `raw_query`. Prefer its tools —
its revenue arithmetic is covered by a live reconciliation test, which is
stronger than remembering to follow prose.

`scripts/fs_query.py` remains for environments where the server is not
registered, and for ad-hoc exploration. The domain knowledge below applies to
both, and explains what the server's warnings mean when you see them.

## The four rules that matter most

**1. Revenue is `Qty * Price`, never `SUM(Price)`.**
`Price` is a *unit* price. Returns are ordinary rows with `Qty = -1` and a
*positive* `Price` — there is no separate return flag. Summing `Price` alone
adds returns as revenue instead of subtracting them, so a period with returns is
overstated by twice their value. COGS is `SUM(Qty * Cost)`; margin (BF) is
**netto-based**: `SUM(Qty * Price)/1.25 - SUM(Qty * Cost)` — Price includes
VAT, Cost does not, and mixing them overstates margin.

**2. `$top` is applied BEFORE `$filter`.**
It caps rows *scanned*, not rows returned, so a small `$top` silently returns a
partial result that looks complete. Identical filter, varying `$top`: 5000 → 521
rows; 20000 → 1475 rows. Always set `$top` far above expected source volume
(200000+). **Never page with `$skip`** — with `$top` applied pre-filter, paging
cannot be made consistent.

**3. Filter only on numeric FK columns.**
`Stock`, `Store`, `Brand`, `Name` appear in output but are joined display fields.
Filtering on them returns empty — even with an exact `eq` match on a value you
just read from the data. Resolve names to IDs first (`fs_query.py stores`), then
filter on `STOCKID_FK` / `STOREID_FK` / `PRODUCTID_FK`.

**4. Always pass `$select`.**
`Saleslines` carries `FirstName`, `LastName`, `Email`, `Phone`, `Address`,
`PostalCode`, `City` on every row. Pulling them moves customer personal data into
context and into any file written. This is a GDPR decision, not an optimisation —
request PII only when the user's task genuinely needs it, and say so when you do.

## Which table to query

| Need | Table | History |
|---|---|---|
| Revenue, transactions, basket size | `Sales` (headers) | Deep — 2022 onward |
| Product, brand, size, units, discount, margin | `Saleslines` | Deep — via `from`/`to` params (see api-reference) |
| Stock levels | `Stockstatus` | Snapshot; needs `snapshotDateTime` |

**History needs the `from`/`to` parameters** — plain `$filter` queries see
only the endpoint's default window (looked like a 2026-08-01 retention limit;
it is not). The MCP server, `fs_query.py lines/stores` and
`scripts/line_reports.py` all use the window parameters now and reach full
history (2022→). Margin everywhere is netto-based (BF = brutto/1.25 − kost).

`Sales.Total` and `SUM(Qty * Price)` reconcile exactly, so either is a valid
revenue source. Prefer `Sales` for long periods (deeper history, fewer rows) and
`Saleslines` when the question needs product detail.

## Workflow

1. **Resolve the store**: `python scripts/fs_query.py stores --days 30`
   Returns a name → `STOCKID_FK` / `STOREID_FK` map. Several `STOREID_FK`
   registers map to one `STOCKID_FK` — group by stock, or one store splits into
   four. Watch for similarly-named unrelated stores (`Høyer Paleet` 3229 vs
   `BMB Paleet` 1333).
2. **Check coverage** for the requested period before building anything.
3. **Pull** with `fs_query.py sales` or `fs_query.py lines`.
4. **Reconcile** line-level revenue against `Sales.Total` when both exist. They
   should match to the øre; a mismatch means a rule above was broken.
5. **Report**, stating the period actually covered and any missing days.

## Reporting the numbers

Exclude `IsVoided` rows from `Sales`. (`Saleslines` appears to omit voided lines
rather than flag them, so its `IsVoided` is always false — don't assume symmetry.)

Group by `Currency` and never sum across currencies; a blended total is silently
meaningless.

Useful KPIs, in rough order of how often they're wanted: net revenue, transaction
count, average basket, units, gross margin %, returns (count, value, % of gross),
revenue by day, by weekday, by hour, by brand or product, and by register.

Missing days are usually genuine closures (Sundays, at Norwegian retailers) rather
than data gaps. Show them as zero rather than omitting them, so a closure doesn't
read as a collapse in trade — but confirm rather than assume.

## When the numbers look wrong

Suspect the tooling before the business. In order: was `$top` large enough (does
the row count sit suspiciously near a round number)? Was a display field filtered
on? Was `Qty` applied? For a line-level query: were the `from`/`to` window
parameters set (without them Saleslines serves only its default window)?

A result of exactly zero rows is far more often a broken query than a closed
store. Verify by widening the filter until data appears, then narrowing again.

## Presenting the results

Correct arithmetic can still mislead. Read `references/reporting.md` before
building any report or chart — it carries the standard KPI set and the
presentation rules, each of which exists because the obvious choice produced a
false impression on this tenant's real data.

For a shareable HTML dashboard, run the script rather than hand-building one:

```
python3 scripts/build_dashboard.py --stock 3229 --from 2026-08-01 --to 2026-08-11 \
    --name "Høyer Paleet" --out report.html
```

`--to` is exclusive. It fetches, aggregates, and writes a self-contained page with
the CVD-validated palette, hover tooltips, and both light and dark themes. It
prints the headline figures to stderr so you can quote them without reopening the
file.

The three traps it handles for you, which are easy to get wrong by hand:

- **Weekday is revenue per trading day, never a total.** A window with two
  Saturdays and one Thursday makes Saturday look twice as strong on raw totals
  when the two are actually level.
- **Closed days render as "closed", not as a zero bar** that reads as a
  catastrophe, and are excluded from per-day averages.
- **Output is pure ASCII**, so `Høyer`, `Samsøe Samsøe` and `Yttertøy` survive a
  surface that doesn't declare UTF-8.

For the **monthly chain report** (the Norwegian "Månedsrapport | HØYER-kjeden"
deck), use the dedicated generator in the project repo — it carries the deck's
validated conventions (store composition incl. Shopify merges and Bergen's
register set, BMB/Nydalen/closed-store exclusions, brutto = `SUM(Total)` incl.
mva) and re-validates itself against the June 2026 deck on every run:

```
python3 "~/Front Systems API/scripts/monthly_report.py" --month 2026-08                    # report 01
python3 "~/Front Systems API/scripts/line_reports.py" --from 2026-08-01 --to 2026-09-01 \
    --seller-min 100                                                                       # reports 03-08
python3 "~/Front Systems API/scripts/monthly_suite.py"  --month 2026-08                    # 02 + suite 03/08
python3 "~/Front Systems API/scripts/make_pdf.py"       --month 2026-08 --html             # bound PDF + HTML
```

line_reports must run BEFORE monthly_suite (both write 03 and 08; the suite
versions win). Suite numbering: 01 månedsrapport, 02 Oppsummering,
03 Omsetning og BF, 04 Sesonger, 05 Rabatter, 06 Merker, 07 Selgere,
08 Diverse.

Read `references/manedsrapport.md` first — it carries the deck-validated
conventions (store composition, exclusions, web taxonomy, Bergen closure) and
the **period-purity rule**: a month's edition may contain nothing newer than
the month it covers, not even in explanations or generation timestamps.

Every section is computable for any month back to 2017 — line data is deep
via the from/to parameters. The caches under `reports/cache/` mean periodic
runs fetch only the new month.

For spreadsheets, use the `xlsx` skill, with one caveat specific to this data:
write derived columns (`LineTotal`, `LineCost`, `LineMargin`) as **values, not
formulas**, in raw extracts. openpyxl formulas read back as `None` in pandas until
Excel opens the file, which quietly breaks a file meant for analysis. Formulas are
right for dashboards a human edits, values for data a machine reads.
