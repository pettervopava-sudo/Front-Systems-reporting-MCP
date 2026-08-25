#!/usr/bin/env python3
"""Build a shareable KPI dashboard from Front Systems line-level sales.

Encodes the presentation decisions in references/reporting.md so they are
structural rather than remembered:

  * weekday is revenue PER TRADING DAY, never a total, because a window with
    two Saturdays and one Thursday makes Saturday look twice as good as it is
  * non-trading days render as "closed", not as a zero bar that reads as a
    catastrophe, and are excluded from per-day averages
  * coverage and caveats sit above the fold, not in a footnote
  * discounting is computed and surfaced -- Price is already net of it, so it
    is invisible otherwise, and it is usually the largest lever
  * the palette is the CVD-validated copper/blue pair; the prettier muted
    brass/petrol fails the chroma floor and reads as grey
  * output is pure ASCII, so Norwegian names survive a surface that does not
    declare UTF-8

Revenue is SUM(Qty * Price) throughout -- Price is a unit price and returns
carry Qty = -1. See SKILL.md.

Usage
  python3 build_dashboard.py --stock 3229 --from 2026-08-01 --to 2026-08-11 \
      --name "Hoyer Paleet" --out report.html

Dates are inclusive-from, exclusive-to. Requires the front_systems_mcp package
(pip install -e /path/to/project) or fs_query.py alongside this script.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import html
import json
import sys
from pathlib import Path

LINES_START = dt.date(2026, 8, 1)

PALETTE = {  # validated: CVD dE 24.5, chroma floor passed, contrast >= 3:1
    "copper_light": "#C2701A", "blue_light": "#1D6FBF",
    "copper_dark": "#D07E22", "blue_dark": "#3B82D9",
}


def esc(s) -> str:
    """HTML-escape and force ASCII, so Norwegian names cannot mojibake."""
    return html.escape(str(s), quote=True).encode("ascii", "xmlcharrefreplace").decode()


def js(s) -> str:
    """JSON with every non-ASCII char escaped, safe inside <script>."""
    return json.dumps(s, ensure_ascii=True).replace("</", "<\\/")


def nf(n) -> str:
    return f"{round(n):,}".replace(",", " ")


async def fetch_lines(stock_id: int, date_from: dt.date, date_to: dt.date):
    from front_systems_mcp.client import FrontSystemsClient
    from front_systems_mcp.config import load_config
    from front_systems_mcp.odata import date_range, eq
    from front_systems_mcp.reports.sales import LINE_SELECT

    client = FrontSystemsClient(load_config())
    try:
        return await client.fetch(
            "Saleslines",
            [*date_range("SaleDate", date_from, date_to), eq("STOCKID_FK", stock_id)],
            LINE_SELECT,
        )
    finally:
        await client.aclose()


def analyse(rows, date_from: dt.date, date_to: dt.date) -> dict:
    import pandas as pd
    from front_systems_mcp.reports.sales import lines_to_frame

    f = lines_to_frame(rows)
    if f.empty:
        raise SystemExit(
            "0 rows. On this API that is more often a broken query than no trade. "
            f"Check whether {date_from} predates {LINES_START} (line-level data "
            "starts there), and that only numeric FK columns were filtered."
        )
    # lines_to_frame leaves the source columns as strings; coerce before summing,
    # or .sum() concatenates instead of adding.
    for col in ("Qty", "Price", "Cost", "Discount", "FullPrice"):
        if col in f:
            f[col] = pd.to_numeric(f[col], errors="coerce").fillna(0.0)
    f["hour"] = f["SaleDateTime"].astype(str).str[11:13].astype(int)
    f["wd"] = f["day"].map(lambda d: dt.date.fromisoformat(d).strftime("%a"))

    currencies = sorted({c for c in f["Currency"].dropna().unique() if c})
    traded = sorted(f["day"].unique())
    span = [(date_from + dt.timedelta(days=i)).isoformat()
            for i in range((date_to - date_from).days)]

    daily = []
    for d in span:
        g = f[f["day"] == d]
        if g.empty:
            daily.append({"d": d, "closed": 1})
        else:
            daily.append({"d": d, "r": round(g["LineTotal"].sum(), 2),
                          "m": round(g["LineMargin"].sum(), 2),
                          "u": int(g["Qty"].sum()), "t": int(g["SALEID"].nunique())})

    # Per trading day, never a total -- uneven bucket sizes otherwise mislead.
    week = []
    for w in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        g = f[f["wd"] == w]
        n = int(g["day"].nunique())
        if n:
            week.append({"k": w, "v": round(g["LineTotal"].sum() / n, 2), "n": n})

    ret = f[f["Qty"] < 0]
    disc = f[f["Discount"] > 0]
    list_value = float((f["Qty"] * f["FullPrice"]).sum()) if "FullPrice" in f else 0.0
    disc_value = float((disc["Qty"] * disc["Discount"]).sum()) if len(disc) else 0.0
    revenue = float(f["LineTotal"].sum())
    units = int(f["Qty"].sum())
    txns = int(f["SALEID"].nunique())

    top = lambda col, k: [
        {"k": str(r[col]), "v": round(r.revenue, 2), "u": int(r.units),
         "mp": round(r.margin / r.revenue * 100, 1) if r.revenue else 0.0}
        for _, r in f.groupby(col).agg(revenue=("LineTotal", "sum"), units=("Qty", "sum"),
                                       margin=("LineMargin", "sum"))
                     .reset_index().nlargest(k, "revenue").iterrows()]

    return {
        "daily": daily,
        "hourly": [{"k": int(h), "v": round(g["LineTotal"].sum(), 2),
                    "t": int(g["SALEID"].nunique())} for h, g in f.groupby("hour")],
        "week": week,
        "brands": top("Brand", 12),
        "cats": top("Group", 8),
        "regs": [{"k": int(r.STOREID_FK), "v": round(r.revenue, 2), "t": int(r.txns)}
                 for _, r in f.groupby("STOREID_FK")
                              .agg(revenue=("LineTotal", "sum"), txns=("SALEID", "nunique"))
                              .reset_index().sort_values("revenue", ascending=False).iterrows()],
        "kpi": {
            "revenue": revenue, "margin": float(f["LineMargin"].sum()), "units": units,
            "txns": txns, "basket": revenue / txns if txns else 0.0,
            "unit_price": revenue / units if units else 0.0,
            "per_basket": units / txns if txns else 0.0,
            "brands": int(f["Brand"].nunique()),
            "ret_lines": int(len(ret)), "ret_value": round(float(ret["LineTotal"].sum()), 2),
            "gross": round(float(f[f["Qty"] > 0]["LineTotal"].sum()), 2),
            "disc_lines": int(len(disc)), "disc_value": round(disc_value, 2),
            "list_value": round(list_value, 2),
            "disc_pct": round(disc_value / list_value * 100, 1) if list_value else 0.0,
            "lines": int(len(f)),
        },
        "coverage": {
            "requested": f"{date_from} to {date_to - dt.timedelta(days=1)}",
            "traded": len(traded), "span": len(span),
            "closed": [d["d"] for d in daily if d.get("closed")],
            "currencies": currencies,
            "pre_history": date_from < LINES_START,
        },
    }


def render(a: dict, store: str, generated: str) -> str:
    k, cov = a["kpi"], a["coverage"]
    max_daily = max([d.get("r", 0) for d in a["daily"]] or [1]) * 1.12
    max_hour = max([h["v"] for h in a["hourly"]] or [1]) * 1.12
    max_week = max([w["v"] for w in a["week"]] or [1]) * 1.12

    warn = []
    if cov["pre_history"]:
        warn.append(f"Requested period starts before {LINES_START}, where line-level "
                    "data begins. Only the later part is covered here.")
    if len(cov["currencies"]) > 1:
        warn.append("More than one currency appears; totals are per currency in the "
                    "tables and are not blended.")
    warn.append("No comparison period: line-level history begins "
                f"{LINES_START}, so week-over-week is not yet computable.")

    rows_brand = "".join(
        f'<tr><td>{esc(b["k"])}</td><td><div class="track"><div class="fill" '
        f'style="width:{b["v"]/a["brands"][0]["v"]*100:.1f}%"></div></div></td>'
        f'<td class="num r">{nf(b["v"])}</td><td class="num r">{b["u"]}</td>'
        f'<td class="num r">{b["mp"]:.1f}%</td></tr>' for b in a["brands"])
    rows_cat = "".join(
        f'<tr><td>{esc(c["k"])}</td><td class="num r">{nf(c["v"])}</td>'
        f'<td class="num r">{c["u"]}</td></tr>' for c in a["cats"])
    rows_reg = "".join(
        f'<tr><td class="num">{r["k"]}</td><td class="num r">{nf(r["v"])}</td>'
        f'<td class="num r">{r["t"]}</td>'
        f'<td class="num r">{nf(r["v"]/r["t"]) if r["t"] else 0}</td></tr>'
        for r in a["regs"])
    warn_html = "".join(f"<li>{esc(w)}</li>" for w in warn)

    tpl = (Path(__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    return (tpl
            .replace("__STORE__", esc(store))
            .replace("__PERIOD__", esc(cov["requested"]))
            .replace("__TRADED__", f'{cov["traded"]} of {cov["span"]}')
            .replace("__CLOSED__", esc(", ".join(cov["closed"]) or "none"))
            .replace("__LINES__", nf(k["lines"]))
            .replace("__CURRENCY__", esc("/".join(cov["currencies"]) or "n/a"))
            .replace("__REVENUE__", nf(k["revenue"]))
            .replace("__MARGIN__", nf(k["margin"]))
            .replace("__MARGINPCT__", f'{k["margin"]/k["revenue"]*100:.1f}' if k["revenue"] else "0")
            .replace("__TXNS__", nf(k["txns"]))
            .replace("__PERDAY__", nf(k["txns"]/cov["traded"]) if cov["traded"] else "0")
            .replace("__BASKET__", nf(k["basket"]))
            .replace("__PERBASKET__", f'{k["per_basket"]:.2f}')
            .replace("__UNITS__", nf(k["units"]))
            .replace("__UNITPRICE__", nf(k["unit_price"]))
            .replace("__BRANDS__", str(k["brands"]))
            .replace("__RETVALUE__", nf(k["ret_value"]))
            .replace("__RETLINES__", str(k["ret_lines"]))
            .replace("__RETPCT__", f'{abs(k["ret_value"])/k["gross"]*100:.1f}' if k["gross"] else "0")
            .replace("__GROSS__", nf(k["gross"]))
            .replace("__DISCVALUE__", nf(k["disc_value"]))
            .replace("__DISCLINES__", str(k["disc_lines"]))
            .replace("__DISCPCT__", f'{k["disc_pct"]:.1f}')
            .replace("__LISTVALUE__", nf(k["list_value"]))
            .replace("__WARNINGS__", warn_html)
            .replace("__ROWS_BRAND__", rows_brand)
            .replace("__ROWS_CAT__", rows_cat)
            .replace("__ROWS_REG__", rows_reg)
            .replace("__GENERATED__", esc(generated))
            .replace("__DAILY__", js(a["daily"]))
            .replace("__HOURLY__", js(a["hourly"]))
            .replace("__WEEK__", js(a["week"]))
            .replace("__MAXDAILY__", str(int(max_daily)))
            .replace("__MAXHOUR__", str(int(max_hour)))
            .replace("__MAXWEEK__", str(int(max_week))))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stock", type=int, required=True, help="STOCKID_FK (see fs_query.py stores)")
    p.add_argument("--from", dest="dfrom", required=True, help="YYYY-MM-DD, inclusive")
    p.add_argument("--to", dest="dto", required=True, help="YYYY-MM-DD, EXCLUSIVE")
    p.add_argument("--name", default=None, help="store name for the heading")
    p.add_argument("--out", default="dashboard.html")
    args = p.parse_args()

    date_from = dt.date.fromisoformat(args.dfrom)
    date_to = dt.date.fromisoformat(args.dto)
    if date_to <= date_from:
        raise SystemExit("--to is exclusive and must be after --from.")

    rows = asyncio.run(fetch_lines(args.stock, date_from, date_to))
    print(f"  fetched {len(rows)} lines", file=sys.stderr)
    a = analyse(rows, date_from, date_to)
    out = Path(args.out)
    page = render(a, args.name or f"Stock {args.stock}", dt.date.today().isoformat())
    if not page.isascii():
        raise SystemExit("output is not pure ASCII -- a non-UTF-8 surface would mojibake it")
    out.write_text(page, encoding="ascii")

    k = a["kpi"]
    print(f"  revenue {k['revenue']:,.2f}  margin {k['margin']:,.2f} "
          f"({k['margin']/k['revenue']*100:.1f}%)  txns {k['txns']}", file=sys.stderr)
    print(f"  returns {k['ret_lines']} lines {k['ret_value']:,.2f} | "
          f"discount {k['disc_value']:,.2f} ({k['disc_pct']}% off list)", file=sys.stderr)
    print(f"  wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
