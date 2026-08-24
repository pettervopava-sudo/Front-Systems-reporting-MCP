#!/usr/bin/env python3
"""Månedsrapport for HØYER-kjeden — HTML edition of the monthly PPTX report.

Reproduces the header-derived sections of the chain's monthly report
("Månedsrapport | HØYER-kjeden") directly from the Front Systems API:
nøkkeltall, omsetning pr butikk, månedsvis matrix, høyeste salgsdager and
høyeste enkeltsalg — for the month, YTD, and the two trailing-twelve windows.

BF and rabatt rows come from the linjeagg caches (Saleslines, full history
via the from/to window parameters); the detailed line sections (sesonger,
rabatter, merker, selgere) live in reports 04-07 of the suite.

Conventions -- validated to the krone against the June 2026 deck:
  * "Brutto omsetning" == SUM(Sales.Total) over non-voided sales (VAT incl).
    Verified per store against the deck's June table; 17 of 19 stores exact,
    Trondheim/Harstad/Strommen exact once their Shopify/Treasure stocks are
    merged in, Bergen exact as register set {324, 340, 370, 373}.
  * Netto omsetning = brutto / 1.25 (the deck's own netto/brutto ratio).
  * Excluded, matching the deck's stated filter ("excludes BMB, Nedlagte
    butikker, Outlet Nydalen and Teststore"): BMB stocks 1333/1901, register
    3207, and any register with no mapping that stopped trading (closed
    stores). Exclusions are disclosed in the report with their magnitudes.
  * Transaction counts differ from the deck by ~0.5% (their BI applies an
    extra filter we cannot see); revenue does not.

Usage:
  python3 scripts/monthly_report.py --month 2026-07 [--out reports/...html]

The cache under reports/cache/ holds one JSON per month of per-register
aggregates; past months never change, so periodic runs only fetch the new
month. Bump CACHE_VERSION to force a refetch after schema changes.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from front_systems_mcp.client import FrontSystemsClient  # noqa: E402
from front_systems_mcp.config import load_config  # noqa: E402
from front_systems_mcp.odata import date_range  # noqa: E402
from front_systems_mcp.reports.stores import harvest  # noqa: E402

CACHE = ROOT / "reports" / "cache"
CACHE_VERSION = 2  # v2: per_day split by register, so exclusions apply to day tables
SELECT = ["SALEID", "STOREID_FK", "SaleDate", "Total", "IsVoided"]
HISTORY_START = (2024, 1)
VAT = 1.25

#: Deck store composition. Stocks come from the live harvest; the register
#: extras are stores absent from the harvest window (Bergen's line data lives
#: under stock 279 and ends with juli 2026).
#: Validated against the June 2026 deck per-store table.
STORE_STOCKS = {
    "Høyer Arendal": [146], "Høyer Bodø": [444],
    "Høyer Byporten": [157], "Høyer Grimstad": [145],
    "Høyer Gulskogen": [2175], "Høyer Harstad": [2239, 2794],
    "Høyer Haugesund": [213], "Høyer Kvadrat": [3856],
    "Høyer Paleet": [3229], "Høyer Sandefjord": [2100],
    "Høyer Sjølyst": [148], "Høyer Solsiden": [153],
    "Høyer Sørlandssenteret": [203], "Høyer Stadionparken": [193],
    "Høyer Storo": [150], "Høyer Strømmen": [151, 5368],
    "Høyer Trondheim": [181, 2014], "Høyer Webshop": [183],
}
STORE_REG_EXTRAS = {"Høyer Bergen": [324, 340, 370, 373]}
#: Stores with a known closure date: included in months they traded, excluded
#: from later months, and footnoted. Bergen closed 2026-08-01 (user-confirmed).
STORE_CLOSED = {"Høyer Bergen": "2026-08-01"}
EXCLUDED_STOCKS = {1333, 1901}          # BMB / BMB Shopify
EXCLUDED_REGS = {3207}                  # Outlet Nydalen (antatt)
MND = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
       "august", "september", "oktober", "november", "desember"]


def esc(s) -> str:
    return html.escape(str(s), quote=True).encode("ascii", "xmlcharrefreplace").decode()


def nf(n) -> str:
    return f"{round(n):,}".replace(",", " ").encode("ascii", "xmlcharrefreplace").decode()


def pct(new, old) -> str:
    if not old:
        return "&ndash;"
    return f"{(new / old - 1) * 100:+.1f} %".encode("ascii", "xmlcharrefreplace").decode()


def month_iter(start, end):
    y, m = start
    while (y, m) < end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


async def ensure_cache(until_excl):
    client = FrontSystemsClient(load_config())
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        for y, m in month_iter(HISTORY_START, until_excl):
            out = CACHE / f"{y:04d}-{m:02d}.json"
            if out.exists() and json.load(open(out)).get("v") == CACHE_VERSION:
                continue
            d0 = dt.date(y, m, 1)
            d1 = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
            rows = await client.fetch("Sales", date_range("SaleDate", d0, d1), SELECT)
            net = [r for r in rows if not r["IsVoided"]]
            per_reg = collections.defaultdict(lambda: [0, 0.0])
            per_day_reg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0]))
            for r in net:
                t = round(float(r["Total"]), 2)
                a = per_reg[r["STOREID_FK"]]; a[0] += 1; a[1] += t
                b = per_day_reg[r["SaleDate"][:10]][r["STOREID_FK"]]; b[0] += 1; b[1] += t
            top = sorted(net, key=lambda r: -float(r["Total"]))[:15]
            json.dump({
                "v": CACHE_VERSION, "month": f"{y:04d}-{m:02d}", "net": len(net),
                "per_reg": {str(k): [v[0], round(v[1], 2)] for k, v in per_reg.items()},
                "per_day_reg": {d: {str(k): [v[0], round(v[1], 2)] for k, v in regs.items()}
                                for d, regs in per_day_reg.items()},
                "top_sales": [{"total": float(r["Total"]), "reg": r["STOREID_FK"],
                               "date": r["SaleDate"][:10]} for r in top],
            }, open(out, "w"))
            print(f"  cached {y:04d}-{m:02d}: {len(net):,} sales", file=sys.stderr)
        # Register -> store map from live line data (registers can drift).
        entries = await harvest(client, days=30)
        return entries
    finally:
        await client.aclose()


def build_reg_to_store(entries):
    stock_regs = {e.stock_id: list(e.register_ids) for e in entries}
    reg_to_store, excluded_regs = {}, set(EXCLUDED_REGS)
    for stock in EXCLUDED_STOCKS:
        excluded_regs.update(stock_regs.get(stock, []))
    for store, stocks in STORE_STOCKS.items():
        for stock in stocks:
            for reg in stock_regs.get(stock, []):
                reg_to_store[reg] = store
    for store, regs in STORE_REG_EXTRAS.items():
        for reg in regs:
            reg_to_store[reg] = store
    return reg_to_store, excluded_regs


def linjeagg(month_list):
    """Sum line aggregates over months; None if any month is missing.

    BF is netto-based: BF = rev/1.25 - cost (Price incl VAT, Cost excl) --
    validated against the June-2026 deck (17,548 vs deck 17,581 TNOK, 0.2%).
    """
    tot = {"rev": 0.0, "cost": 0.0, "rab": 0.0}
    for y, m in month_list:
        f = CACHE / f"linjeagg_{y:04d}-{m:02d}.json"
        if not f.exists():
            return None
        d = json.load(open(f))
        for k in tot:
            tot[k] += d[k]
    tot["bf"] = tot["rev"] / VAT - tot["cost"]
    tot["netto"] = tot["rev"] / VAT
    return tot


LINJEAGG_SELECT = ["STOCKID_FK", "Qty", "Price", "Cost", "Discount", "FullPrice"]


async def ensure_linjeagg(months):
    """Fetch and cache line aggregates for months missing from the cache.

    Deck composition applied at line level: only stocks in the suite's
    stock->store map count (line_reports.STOCK_STORE), which excludes BMB,
    Outlet Nydalen, Teststore and nedlagte butikker for every year. The
    builder reproduces the existing linjeagg_2026-07 cache to the oere.
    """
    import line_reports as LR
    missing = [(y, m) for y, m in months
               if not (CACHE / f"linjeagg_{y:04d}-{m:02d}.json").exists()]
    if not missing:
        return
    client = FrontSystemsClient(load_config())
    sem = asyncio.Semaphore(3)

    async def one(y, m):
        d0 = dt.date(y, m, 1)
        nxt = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
        async with sem:
            rows = await client.fetch_raw("Saleslines", {
                "from": f"'{d0}'", "to": f"'{nxt - dt.timedelta(days=1)}'",
                "$select": ",".join(LINJEAGG_SELECT), "$top": "2000000"})
        tot = {"rev": 0.0, "cost": 0.0, "rab": 0.0, "full": 0.0}
        kept = 0
        for r in rows:
            if r["STOCKID_FK"] not in LR.STOCK_STORE:
                continue
            kept += 1
            q = float(r["Qty"] or 0)
            tot["rev"] += round(q * float(r["Price"] or 0), 2)
            tot["cost"] += round(q * float(r["Cost"] or 0), 2)
            tot["rab"] += round(q * float(r["Discount"] or 0), 2)
            tot["full"] += round(q * float(r["FullPrice"] or 0), 2)
        json.dump({"month": f"{y:04d}-{m:02d}", "lines": kept,
                   **{k: round(v, 2) for k, v in tot.items()}},
                  open(CACHE / f"linjeagg_{y:04d}-{m:02d}.json", "w"))
        print(f"  linjeagg {y:04d}-{m:02d}: {kept:,} lines, "
              f"rev {tot['rev']:,.0f}", file=sys.stderr)

    try:
        await asyncio.gather(*(one(y, m) for y, m in missing))
    finally:
        await client.aclose()


LINJESTORE_SELECT = ["SALEID", "STOCKID_FK", "Qty", "Price", "Cost",
                     "Discount", "FullPrice"]


def linjestore(y, m):
    f = CACHE / f"linjestore_{y:04d}-{m:02d}.json"
    return json.load(open(f)) if f.exists() else None


async def ensure_linjestore(months):
    """Per-deck-store line aggregates for the given months, cached.

    Same composition as linjeagg (stocks in line_reports.STOCK_STORE), but
    split per store and with a line-derived transaction count (distinct
    SALEID). Stocks outside the map are kept under 'unmapped' with their
    display names, so composition questions can be answered from the cache.
    """
    import line_reports as LR
    missing = [(y, m) for y, m in months if linjestore(y, m) is None]
    if not missing:
        return
    client = FrontSystemsClient(load_config())
    sem = asyncio.Semaphore(3)

    async def one(y, m):
        d0 = dt.date(y, m, 1)
        nxt = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
        async with sem:
            rows = await client.fetch_raw("Saleslines", {
                "from": f"'{d0}'", "to": f"'{nxt - dt.timedelta(days=1)}'",
                "$select": ",".join(LINJESTORE_SELECT + ["Stock"]),
                "$top": "2000000"})
        stores, unmapped = {}, {}
        for r in rows:
            stock = r["STOCKID_FK"]
            name = LR.STOCK_STORE.get(stock)
            if name is None:
                a = unmapped.setdefault(str(stock), {
                    "name": r.get("Stock") or "?", "rev": 0.0, "cost": 0.0,
                    "rab": 0.0, "full": 0.0, "sales": set()})
            else:
                a = stores.setdefault(name, {"rev": 0.0, "cost": 0.0,
                                             "rab": 0.0, "full": 0.0,
                                             "sales": set()})
            q = float(r["Qty"] or 0)
            a["rev"] += round(q * float(r["Price"] or 0), 2)
            a["cost"] += round(q * float(r["Cost"] or 0), 2)
            a["rab"] += round(q * float(r["Discount"] or 0), 2)
            a["full"] += round(q * float(r["FullPrice"] or 0), 2)
            a["sales"].add(r["SALEID"])
        def pack(d):
            return {k: {"rev": round(v["rev"], 2), "cost": round(v["cost"], 2),
                        "rab": round(v["rab"], 2), "full": round(v["full"], 2),
                        "trans": len(v["sales"]),
                        **({"name": v["name"]} if "name" in v else {})}
                    for k, v in d.items()}
        json.dump({"month": f"{y:04d}-{m:02d}", "stores": pack(stores),
                   "unmapped": pack(unmapped)},
                  open(CACHE / f"linjestore_{y:04d}-{m:02d}.json", "w"))
        print(f"  linjestore {y:04d}-{m:02d}: {len(stores)} butikker",
              file=sys.stderr)

    try:
        await asyncio.gather(*(one(y, m) for y, m in missing))
    finally:
        await client.aclose()


class Agg:
    """Per-store [trans, revenue] over a set of months, deck conventions applied."""

    def __init__(self, reg_to_store, excluded_regs):
        self.map, self.skip = reg_to_store, excluded_regs
        self.stores = collections.defaultdict(lambda: [0, 0.0])
        self.excluded = [0, 0.0]
        self.unmapped = [0, 0.0]
        self.unmapped_regs = collections.Counter()

    def add_month(self, data):
        for reg_s, (n, s) in data["per_reg"].items():
            reg = int(reg_s)
            if reg in self.skip:
                self.excluded[0] += n; self.excluded[1] += s
            elif reg in self.map:
                a = self.stores[self.map[reg]]; a[0] += n; a[1] += s
            else:
                self.unmapped[0] += n; self.unmapped[1] += s
                self.unmapped_regs[reg] += s

    @property
    def revenue(self): return sum(v[1] for v in self.stores.values())
    @property
    def trans(self): return sum(v[0] for v in self.stores.values())


def aggregate(months, reg_to_store, excluded_regs):
    agg = Agg(reg_to_store, excluded_regs)
    for y, m in months:
        f = CACHE / f"{y:04d}-{m:02d}.json"
        if f.exists():
            agg.add_month(json.load(open(f)))
    return agg


def month_data(y, m):
    f = CACHE / f"{y:04d}-{m:02d}.json"
    return json.load(open(f)) if f.exists() else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", required=True, help="YYYY-MM, the report month")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    ry, rm = int(args.month[:4]), int(args.month[5:7])
    out = pathlib.Path(args.out or ROOT / "reports" /
                       f"01_Manedsrapport_{MND[rm-1]}_{ry}.html")

    nxt = (ry + 1, 1) if rm == 12 else (ry, rm + 1)
    entries = asyncio.run(ensure_cache(nxt))
    reg_to_store, excluded_regs = build_reg_to_store(entries)

    A = lambda ms: aggregate(ms, reg_to_store, excluded_regs)
    this_m = A([(ry, rm)])
    prev_y = A([(ry - 1, rm)])
    prev2_y = A([(ry - 2, rm)])
    ytd = A([(ry, m) for m in range(1, rm + 1)])
    ytd_prev = A([(ry - 1, m) for m in range(1, rm + 1)])
    ytd_prev2 = A([(ry - 2, m) for m in range(1, rm + 1)])
    # trailing twelve: the 12 months ending with the report month
    r12_start = (ry, rm + 1 - 12) if rm >= 12 else (ry - 1, rm + 1)
    r12 = A(list(month_iter(r12_start, nxt)))
    r24_start = (r12_start[0] - 1, r12_start[1])
    r24_12 = A(list(month_iter(r24_start, r12_start)))

    la = {
        "m": linjeagg([(ry, rm)]), "pm": linjeagg([(ry - 1, rm)]),
        "ytd": linjeagg([(ry, x) for x in range(1, rm + 1)]),
        "pytd": linjeagg([(ry - 1, x) for x in range(1, rm + 1)]),
        "r12": linjeagg(list(month_iter(r12_start, nxt))),
        "r24": linjeagg(list(month_iter(r24_start, r12_start))),
    }

    # Månedsvis matrix, post-exclusion
    matrix = {}
    for y in (ry - 2, ry - 1, ry):
        for m in range(1, 13):
            if (y, m) >= nxt:
                continue
            a = A([(y, m)])
            if a.revenue or a.trans:
                matrix[(y, m)] = a

    # Høyeste salgsdager, YTD, post-exclusion
    def top_days(year, upto_m, k=10):
        days = collections.defaultdict(lambda: [0, 0.0])
        for m in range(1, upto_m + 1):
            d = month_data(year, m)
            if not d:
                continue
            for day, regs in d["per_day_reg"].items():
                for reg_s, (n, s) in regs.items():
                    reg = int(reg_s)
                    if reg in excluded_regs or reg not in reg_to_store:
                        continue
                    a = days[day]; a[0] += n; a[1] += s
        return sorted(days.items(), key=lambda kv: -kv[1][1])[:k]

    top_days_now = top_days(ry, rm)
    top_days_prev = top_days(ry - 1, 12)

    # Høyeste enkeltsalg for the report month
    md = month_data(ry, rm)
    singles = [t for t in (md["top_sales"] if md else [])
               if t["reg"] not in excluded_regs and t["reg"] in reg_to_store][:10]

    # Store tables
    def rows_for(month_aggs, ytd_aggs):
        names = sorted(set(STORE_STOCKS) | set(STORE_REG_EXTRAS),
                       key=lambda s: -month_aggs[0].stores.get(s, [0, 0])[1])
        out_rows = []
        for name in names:
            cur = month_aggs[0].stores.get(name, [0, 0.0])
            p1 = month_aggs[1].stores.get(name, [0, 0.0])
            p2 = month_aggs[2].stores.get(name, [0, 0.0])
            ycur = ytd_aggs[0].stores.get(name, [0, 0.0])
            yp1 = ytd_aggs[1].stores.get(name, [0, 0.0])
            out_rows.append((name, p2, p1, cur, ycur, yp1))
        return out_rows

    store_rows = rows_for((this_m, prev_y, prev2_y), (ytd, ytd_prev, ytd_prev2))

    # ---- validation before rendering ----------------------------------------
    juni = A([(2026, 6)])
    delta = juni.revenue - 48_840_661  # deck grand total, June 2026
    print(f"  validation: June-2026 vs deck grand total: {delta:+,.0f} NOK "
          f"({'OK' if abs(delta) < 10_000 else 'CHECK CONVENTIONS'})", file=sys.stderr)
    if this_m.unmapped[1] > 50_000:
        print(f"  WARNING: {this_m.unmapped[1]:,.0f} NOK in unmapped registers "
              f"{dict(this_m.unmapped_regs)} for the report month — investigate "
              "before publishing", file=sys.stderr)

    page = render(
        ry=ry, rm=rm, this_m=this_m, prev_y=prev_y, prev2_y=prev2_y,
        ytd=ytd, ytd_prev=ytd_prev, r12=r12, r24_12=r24_12, matrix=matrix,
        store_rows=store_rows, top_days_now=top_days_now,
        top_days_prev=top_days_prev, singles=singles,
        reg_to_store=reg_to_store, juni_delta=delta, nxt=nxt, la=la,
    )
    if not page.isascii():
        raise SystemExit("output not pure ASCII — would mojibake on a non-UTF-8 surface")
    import line_reports
    page += "<script>" + line_reports.SORT_JS + "</script>"
    out.write_text(page, encoding="ascii")
    print(f"  {MND[rm-1]} {ry}: brutto {this_m.revenue:,.0f} "
          f"({pct(this_m.revenue, prev_y.revenue)}) trans {this_m.trans:,} "
          f"snitt {this_m.revenue/this_m.trans:,.0f}", file=sys.stderr)
    print(f"  wrote {out}", file=sys.stderr)


def _logo_css_for_template() -> str:
    # bare CSS: the placeholder sits INSIDE the template's <style> block
    import line_reports
    return line_reports.LOGO_CSS + line_reports.SORT_CSS


def render(**k):
    tpl = (pathlib.Path(__file__).parent / "monthly_report_template.html"
           ).read_text(encoding="ascii")
    ry, rm = k["ry"], k["rm"]
    this_m, prev_y = k["this_m"], k["prev_y"]
    ytd, ytd_prev, r12, r24 = k["ytd"], k["ytd_prev"], k["r12"], k["r24_12"]

    def snitt(a):
        return a.revenue / a.trans if a.trans else 0

    kpi_rows = ""
    for label, f in [
        ("Brutto omsetning", lambda a: nf(a.revenue)),
        ("Netto omsetning (eks. mva)", lambda a: nf(a.revenue / VAT)),
        ("Transaksjoner", lambda a: nf(a.trans)),
        ("Snittkjøp", lambda a: nf(snitt(a))),
    ]:
        kpi_rows += (
            f"<tr><td>{esc(label)}</td>"
            f"<td class='num r'>{f(this_m)}</td><td class='num r'>{f(prev_y)}</td>"
            f"<td class='num r'>{pct_metric(label, this_m, prev_y)}</td>"
            f"<td class='num r'>{f(ytd)}</td><td class='num r'>{f(ytd_prev)}</td>"
            f"<td class='num r'>{pct_metric(label, ytd, ytd_prev)}</td>"
            f"<td class='num r'>{f(r12)}</td><td class='num r'>{f(r24)}</td>"
            f"<td class='num r'>{pct_metric(label, r12, r24)}</td></tr>")
    la = k["la"]
    bf_block = ""
    if all(la.values()):
        def bfr(a): return nf(a["bf"])
        def bfp(a): return (f"{a['bf']/a['netto']*100:.1f} %".replace(".", ",")
                            ).encode("ascii", "xmlcharrefreplace").decode()
        def pstp(a, b):
            d = (a["bf"]/a["netto"] - b["bf"]/b["netto"]) * 100
            return (f"{d:+.1f} pstp.".replace(".", ",", 1)
                    ).encode("ascii", "xmlcharrefreplace").decode()
        trip = lambda f_, a, b, chg: (f"<td class='num r'>{f_(a)}</td>"
                                      f"<td class='num r'>{f_(b)}</td>"
                                      f"<td class='num r'>{chg}</td>")
        kpi_rows += ("<tr><td>BF i kroner</td>"
            + trip(bfr, la["m"], la["pm"], pct(la["m"]["bf"], la["pm"]["bf"]))
            + trip(bfr, la["ytd"], la["pytd"], pct(la["ytd"]["bf"], la["pytd"]["bf"]))
            + trip(bfr, la["r12"], la["r24"], pct(la["r12"]["bf"], la["r24"]["bf"]))
            + "</tr>")
        kpi_rows += ("<tr><td>BF %</td>"
            + trip(bfp, la["m"], la["pm"], pstp(la["m"], la["pm"]))
            + trip(bfp, la["ytd"], la["pytd"], pstp(la["ytd"], la["pytd"]))
            + trip(bfp, la["r12"], la["r24"], pstp(la["r12"], la["r24"]))
            + "</tr>")
        rab = lambda a: nf(a["rab"])
        kpi_rows += ("<tr><td>Rabatt i kroner</td>"
            + trip(rab, la["m"], la["pm"], pct(la["m"]["rab"], la["pm"]["rab"]))
            + trip(rab, la["ytd"], la["pytd"], pct(la["ytd"]["rab"], la["pytd"]["rab"]))
            + trip(rab, la["r12"], la["r24"], pct(la["r12"]["rab"], la["r24"]["rab"]))
            + "</tr>")
        kpi_rows += ("<tr class='na'><td></td><td class='r' colspan='9'>"
            + "BF = netto omsetning (eks. mva) minus varekost; BF % av netto. "
            + "Linjedata hentet med from/to-parametrene."
            + "</td></tr>")
    else:
        for label in ("BF i kroner", "BF %", "Rabatt i kroner"):
            kpi_rows += (f"<tr class='na'><td>{esc(label)}</td>"
                         + "<td class='r' colspan='9'>ikke tilgjengelig &mdash; "
                           "API-et har ingen varelinjedata for perioden</td></tr>")

    max_rev = max(r[3][1] for r in k["store_rows"]) or 1
    store_rows = ""
    for name, p2, p1, cur, ycur, yp1 in k["store_rows"]:
        store_rows += (
            f"<tr><td>{esc(name)}</td>"
            f"<td><div class='track'><div class='fill' style='width:{cur[1]/max_rev*100:.1f}%'>"
            f"</div></div></td>"
            f"<td class='num r'>{nf(p2[1])}</td><td class='num r'>{nf(p1[1])}</td>"
            f"<td class='num r'>{nf(cur[1])}</td><td class='num r'>{pct(cur[1], p1[1])}</td>"
            f"<td class='num r'>{nf(cur[0])}</td>"
            f"<td class='num r'>{nf(cur[1]/cur[0]) if cur[0] else '&ndash;'}</td>"
            f"<td class='num r'>{nf(ycur[1])}</td><td class='num r'>{pct(ycur[1], yp1[1])}</td>"
            f"</tr>")
    tot = ("<tr class='total'><td>Sum</td><td></td>"
           f"<td class='num r'>{nf(k['prev2_y'].revenue)}</td>"
           f"<td class='num r'>{nf(prev_y.revenue)}</td>"
           f"<td class='num r'>{nf(this_m.revenue)}</td>"
           f"<td class='num r'>{pct(this_m.revenue, prev_y.revenue)}</td>"
           f"<td class='num r'>{nf(this_m.trans)}</td>"
           f"<td class='num r'>{nf(snitt(this_m))}</td>"
           f"<td class='num r'>{nf(ytd.revenue)}</td>"
           f"<td class='num r'>{pct(ytd.revenue, ytd_prev.revenue)}</td></tr>")
    store_rows += tot

    mrows = ""
    for metric, f in [("Brutto omsetning", lambda a: nf(a.revenue)),
                      ("Transaksjoner", lambda a: nf(a.trans)),
                      ("Snittkjøp", lambda a: nf(snitt(a)))]:
        for y in (ry - 2, ry - 1, ry):
            cells = "".join(
                f"<td class='num r'>{f(k['matrix'][(y, m)]) if (y, m) in k['matrix'] else ''}</td>"
                for m in range(1, 13))
            first = (f"<td rowspan='3' class='mlabel'>{esc(metric)}</td>"
                     if y == ry - 2 else "")
            mrows += f"<tr>{first}<td class='num'>{y}</td>{cells}</tr>"

    chart = json.dumps({
        "y1": ry - 1, "y2": ry,
        "prev": [round(k["matrix"][(ry - 1, m)].revenue) if (ry - 1, m) in k["matrix"] else None
                 for m in range(1, 13)],
        "cur": [round(k["matrix"][(ry, m)].revenue) if (ry, m) in k["matrix"] else None
                for m in range(1, 13)],
    }, ensure_ascii=True)

    days_rows = "".join(
        f"<tr><td class='num'>{i+1}</td><td>{esc(day)}</td>"
        f"<td>{esc(dt.date.fromisoformat(day).strftime('%A')).lower()}</td>"
        f"<td class='num r'>{nf(v[1])}</td><td class='num r'>{nf(v[0])}</td></tr>"
        for i, (day, v) in enumerate(k["top_days_now"]))
    days_prev_rows = "".join(
        f"<tr><td class='num'>{i+1}</td><td>{esc(day)}</td>"
        f"<td>{esc(dt.date.fromisoformat(day).strftime('%A')).lower()}</td>"
        f"<td class='num r'>{nf(v[1])}</td><td class='num r'>{nf(v[0])}</td></tr>"
        for i, (day, v) in enumerate(k["top_days_prev"]))
    singles_rows = "".join(
        f"<tr><td class='num'>{i+1}</td>"
        f"<td>{esc(k['reg_to_store'].get(s['reg'], '?'))}</td>"
        f"<td class='num'>{esc(s['date'])}</td>"
        f"<td class='num r'>{nf(s['total'])}</td></tr>"
        for i, s in enumerate(k["singles"]))

    excl_note = (f"BMB og BMB Shopify ({nf(this_m.excluded[1])} kr denne måneden), "
                 f"Outlet Nydalen/register 3207, samt nedlagte butikker "
                 f"({nf(this_m.unmapped[1])} kr denne måneden)")

    return (tpl
            .replace("__MND__", esc(MND[rm - 1]))
            .replace("__AAR__", str(ry))
            .replace("__PREVAAR__", str(ry - 1))
            .replace("__PREV2AAR__", str(ry - 2))
            .replace("__KPI_ROWS__", kpi_rows)
            .replace("__BF_BLOCK__", bf_block)
            .replace("__LOGO_CSS__", _logo_css_for_template())
            .replace("__STORE_ROWS__", store_rows)
            .replace("__MATRIX_ROWS__", mrows)
            .replace("__CHART__", chart)
            .replace("__DAYS_ROWS__", days_rows)
            .replace("__DAYS_PREV_ROWS__", days_prev_rows)
            .replace("__SINGLES_ROWS__", singles_rows)
            .replace("__EXCL__", excl_note.encode("ascii", "xmlcharrefreplace").decode())
            .replace("__JUNI_DELTA__", nf(abs(k["juni_delta"])))
            .replace("__GENERATED__", dt.date.today().isoformat())
            .replace("__NSTORES__", str(len(k["store_rows"]))))


def pct_metric(label, a, b):
    if "Snitt" in label:
        va = a.revenue / a.trans if a.trans else 0
        vb = b.revenue / b.trans if b.trans else 0
        return pct(va, vb)
    if "Trans" in label:
        return pct(a.trans, b.trans)
    return pct(a.revenue, b.revenue)


if __name__ == "__main__":
    main()
