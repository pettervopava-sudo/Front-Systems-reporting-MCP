# Presenting Front Systems numbers

Getting the arithmetic right is `SKILL.md`'s job. This file is about not
misleading the reader once the arithmetic is correct — a true number in the wrong
frame still tells a lie.

Each rule below exists because the obvious presentation produced a false
impression on real data from this tenant.

## Normalise any grouping whose buckets aren't equal-sized

The trap: a 1–10 August window contains **two** Mondays and **two** Saturdays but
only one of every other weekday. Plotting weekday totals showed:

```
Saturday 571,320    Thursday 281,593     "Saturday is twice Thursday"
```

Per trading day it is:

```
Saturday 285,660    Thursday 281,593     essentially level
```

The totals chart is arithmetically correct and completely misleading. Whenever
buckets can hold different numbers of days — weekday, month-over-month, any
partial period — divide by the bucket's day count and label the axis
"per trading day". Say which normalisation you used; a reader cannot infer it.

The same applies to hour-of-day if the shop's opening hours vary across the
window, and to any store comparison where the stores traded different numbers of
days.

## Draw a closed day as closed, not as zero

A zero bar reads as "we opened and sold nothing" — a catastrophe. A gap reads as
a data problem. Neither is true of a Norwegian retailer on a Sunday.

Mark non-trading days explicitly (a muted "closed" chip at the axis works) and
exclude them from any per-day average. Confirm the reason before labelling:
Sundays are the usual case here, but a public holiday or a refit is not something
to assume.

## State coverage above the fold, not in a footnote

On this API an empty or short result has several causes that look identical, so
every report opens with what it actually covers: the period requested, the
trading days present, the row count, and whether the figures reconcile. If the
window reaches before **2026-08-01** there is no line-level data — say so in the
header rather than quietly reporting header-only metrics as though they were the
whole picture.

Also state plainly when a comparison period is unavailable. Line-level history
begins 2026-08-01, so week-over-week and year-over-year cannot be computed for
early windows. A report with no comparison is fine; a report that silently omits
one invites the reader to assume there was nothing to compare.

## Surface discounting — it is usually the biggest lever

`Price` is already net of discount, so discounting is invisible unless you
compute it: `SUM(Qty * Discount)` against `SUM(Qty * FullPrice)`. In the 1–10
August sample that was 785,725 NOK off list, **30.4%** — larger than any other
single factor in the period and the thing a manager can actually act on. Report
the amount, the share of lines carrying one, and the percentage off list.

Returns deserve the same treatment: count, value, and share of gross. They are
small in value but they are the reason the revenue formula matters.

## The standard KPI set

Lead with these; they answer the first questions anyone asks.

| KPI | Formula |
|---|---|
| Net revenue | `SUM(Qty * Price)` |
| Gross margin | `SUM(Qty * (Price - Cost))`, and as % of revenue |
| Transactions | distinct `SALEID` |
| Average basket | revenue / transactions |
| Units | `SUM(Qty)` |
| Average unit price | revenue / units |
| Items per basket | units / transactions |
| Returns | count, value, % of gross |
| Discount | value, % off list, share of lines |

Then breakdowns, in the order they usually get asked for: by day, by hour, by
weekday (normalised), by brand, by category, by till.

Margin varies far more across brands than revenue does — a brand table without a
margin column hides the more interesting half of the story.

## Charts

Use `scripts/build_dashboard.py`, which encodes everything below. Read on only if
you are building something it does not cover.

- **Never a dual axis.** Revenue and margin are both NOK and margin is a
  component of revenue, so a stacked bar (margin + cost) is legitimate and
  informative. Two measures on different scales get two charts.
- **The palette is already validated** — copper `#C2701A` and blue `#1D6FBF` on
  light, `#D07E22` and `#3B82D9` on dark. These passed a CVD check at ΔE 24.5;
  the muted brass/petrol pairing that looks nicer **fails the chroma floor and
  reads as grey**. Don't re-pick by eye.
- Numbers in a monospace face with `tabular-nums` so columns align.
- Every chart gets a hover tooltip; a single-series chart needs no legend because
  the title names it.

## Escape non-ASCII output

Store, brand and category names are Norwegian — `Høyer`, `Samsøe Samsøe`,
`Yttertøy`, `Kjole`. If the delivery surface does not declare UTF-8, every one of
those renders as mojibake and the report looks broken. For HTML, emit pure ASCII:
HTML entities in markup, `\uXXXX` escapes inside `<script>`. The build script
does this.

## Spreadsheets

Derived columns (`LineTotal`, `LineCost`, `LineMargin`) go in as **values, not
formulas**, in raw extracts: openpyxl writes formulas without cached values, so
`pandas.read_excel` sees `None` until Excel opens the file. Formulas are right
for dashboards a human will edit, values for data a machine will read.

`recalc.py` needs LibreOffice. Without it, say the workbook's formulas are
unverified rather than implying they were checked.

## PDF and print

Screens scroll; paper clips. Every `overflow-x:auto` container becomes a
silent amputation in print, so wide tables must FIT the page: A4 landscape
gives ~1047px of printable width at 96dpi. The suite's print shell
(`make_pdf.py`) forces the light theme, hides the nav, applies a global
`zoom:.88`, and shrinks the 14-column month matrix specifically via
`section:has(svg#monthly) table{font-size:9px}` — both clipping bugs were
found only by rendering the PDF and LOOKING at the pages (the Read tool
renders PDF pages; poppler is installed for it).

Chrome headless prints with JS executed, so SVG charts render:
`--headless --print-to-pdf=out.pdf --no-pdf-header-footer
--virtual-time-budget=4000`, then merge parts with `pypdf`. The builder's
period-leak gate (refuses parts containing post-period date strings) is what
keeps a bound month edition compliant — do not bypass it.


## Sortable tables (global convention, user rule 2026-08-24)

Every table header in every report must be click-sortable. The shared
sorter lives in `line_reports.py` (`SORT_CSS` + `SORT_JS`) and is injected
by LR.page(), by monthly_report (template placeholder carries SORT_CSS,
script appended at write), and by hoyer_nye_kpi. It understands Norwegian
number formats (thin spaces, comma decimals, k/M/% suffixes, U+2212),
ISO dates sort as text, dashes sort last, `tr.total` rows stay pinned at
the bottom, and tables containing rowspan (pivot matrices) are skipped —
sorting grouped rows would interleave the blocks. New report types must
include the same assets; binding is idempotent (data-srt guard) so the
combined single-file page can run every part's copy safely.

The store CHARTS in report 03 sort too (CHART_SORT_JS): each chart row is
a movable `g.srow` group carrying `data-vals`, the lane titles under each
year are `text.chead` click targets (desc first, then asc), and rows with
class `pin` (Nedlagte butikker, Snitt/Kjeden) stay at the bottom. Charts
without srow groups (the JS bar charts, the 02 line pairs) are untouched.
