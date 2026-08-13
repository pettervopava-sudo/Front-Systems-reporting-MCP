#!/usr/bin/env python3
"""Reports 02, 03 and 08 of the månedsrapport suite.

02 Oppsummering: the deck's chain-level chart pair — brutto omsetning and
BF % for the report month, year for year back to 2017, from the linjeagg
caches (built on demand via the Saleslines from/to window parameters).
03 Omsetning pr butikk and 08 Diverse come from the Sales-header caches.
Reports 04–07 come from line_reports.py, which must run BEFORE this script
so the suite versions of 03 and 08 win.

The report month's edition may not contain anything newer than the month it
covers (period-purity rule); earlier years are historical comparison and
always allowed.

The register→store mapping is dimensional metadata (validated to the krone
against the June 2026 deck); no post-month figures are used anywhere.

Usage:
  python3 scripts/monthly_suite.py --month 2026-07 [--outdir reports]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import line_reports as LR  # noqa: E402  (shared identity: CSS, page, nav)
import monthly_report as MR  # noqa: E402  (cache, mapping, aggregation)

esc, nf, p1, page = LR.esc, LR.nf, LR.p1, LR.page


def month_days(y, upto, reg_to_store, excluded):
    """Per-day chain totals for months 1..upto of year y, deck conventions."""
    days = collections.defaultdict(lambda: [0, 0.0])
    for mm in range(1, upto + 1):
        d = MR.month_data(y, mm)
        if not d:
            continue
        for day, regs in d["per_day_reg"].items():
            for reg_s, (n, s) in regs.items():
                reg = int(reg_s)
                if reg in excluded or reg not in reg_to_store:
                    continue
                a = days[day]; a[0] += n; a[1] += s
    return days


def _panel(vals, labels, top, bot, W, gl, gstep, unit, aria):
    """One line-chart panel: grid, ox line with labelled points, dashed trend."""
    L, R = 64, 28
    lo = (min(vals) // gstep - (1 if min(vals) % gstep < gstep * .15 else 0)) * gstep
    hi = (max(vals) // gstep + 1) * gstep
    if (max(vals) - lo) / (hi - lo) > .92:
        hi += gstep
    n = len(vals)
    xs = [L + (W - L - R) * (i + .5) / n for i in range(n)]
    y = lambda v: bot - (v - lo) / (hi - lo) * (bot - top)
    s = f'<g class="grid">'
    g = lo
    while g <= hi:
        s += f'<line x1="{L}" x2="{W - R}" y1="{y(g):.1f}" y2="{y(g):.1f}"/>'
        g += gstep
    s += '</g><g class="axis">'
    g = lo
    while g <= hi:
        s += (f'<text x="{L - 8}" y="{y(g) + 3.5:.1f}" text-anchor="end">'
              f'{gl(g)}</text>')
        g += gstep
    for x, lab in zip(xs, labels):
        s += f'<text x="{x:.1f}" y="{bot + 18}" text-anchor="middle">{lab}</text>'
    s += '</g>'
    # least-squares trend over the series, dashed, understated
    mx, my = (n - 1) / 2, sum(vals) / n
    beta = (sum((i - mx) * (v - my) for i, v in enumerate(vals))
            / (sum((i - mx) ** 2 for i in range(n)) or 1))
    s += (f'<line x1="{xs[0]:.1f}" y1="{y(my + beta * (0 - mx)):.1f}" '
          f'x2="{xs[-1]:.1f}" y2="{y(my + beta * ((n - 1) - mx)):.1f}" '
          f'stroke="var(--stone)" stroke-width="1.5" stroke-dasharray="7 6"/>')
    pts = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, vals))
    s += (f'<polyline points="{pts}" fill="none" stroke="var(--ox)" '
          f'stroke-width="2"/>')
    for i, (x, v) in enumerate(zip(xs, vals)):
        below = (0 < i < n - 1 and vals[i - 1] > v and vals[i + 1] > v)
        ly = y(v) + 22 if below else y(v) - 11
        s += (f'<g class="pt" tabindex="0" role="img" aria-label="{aria(i)}">'
              f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="3.5" fill="var(--ox)"/>'
              f'<text class="vlab" x="{x:.1f}" y="{ly:.1f}" '
              f'text-anchor="middle">{unit(v)}</text></g>')
    return s


def _pair_svg(series, label, rev_step):
    """One 'Omsetning og BF kjeden' chart pair as a standalone SVG."""
    years = [y for y, _ in series]
    rev = [d["rev"] for _, d in series]
    bfp = [d["bf"] / d["netto"] * 100 for _, d in series]
    W = 1080
    m_lab = lambda v: (f"{v / 1e6:.0f}M" if v >= 100e6
                       else f"{v / 1e6:.1f}".replace(".", ",") + "M")
    p_lab = lambda v: (f"{v:.1f}".replace(".", ",") + "%")
    aria_r = lambda i: (f"{label} {years[i]}: brutto {nf(rev[i])} kr")
    aria_b = lambda i: (f"{label} {years[i]}: BF {p_lab(bfp[i])}")
    return (f'<svg class="sumsvg" viewBox="0 0 {W} 660" role="img" '
            f'aria-label="Brutto omsetning og BF-prosent, {label} '
            f'{years[0]}&ndash;{years[-1]}">'
            f'<text class="ptitle" x="64" y="18">BRUTTO OMSETNING</text>'
            + _panel(rev, years, 40, 280, W, lambda g: f"{g / 1e6:.0f}M",
                     rev_step, m_lab, aria_r)
            + f'<text class="ptitle" x="64" y="388">BF %</text>'
            + _panel(bfp, years, 410, 620, W, lambda g: f"{g:.0f}%",
                     5, p_lab, aria_b)
            + "</svg>")


def _pair_table(series):
    rows = ""
    prev = None
    for (y, d) in series:
        chg = MR.pct(d["rev"], prev["rev"]) if prev else "&ndash;"
        rows += (f"<tr><td class='num'>{y}</td>"
                 f"<td class='num r'>{nf(d['rev'])}</td>"
                 f"<td class='num r'>{chg}</td>"
                 f"<td class='num r'>{nf(d['bf'])}</td>"
                 f"<td class='num r'>{p1(d['bf'] / d['netto'] * 100)}</td>"
                 f"<td class='num r'>{nf(d['rab'])}</td></tr>")
        prev = d
    return f"""<div class="tw"><table>
  <thead><tr><th>&Aring;r</th><th class="r">Brutto omsetning</th>
    <th class="r">Endring</th><th class="r">BF i kroner</th>
    <th class="r">BF %</th><th class="r">Rabatt</th></tr></thead>
  <tbody>{rows}</tbody></table></div>"""


def summary_page(series_m, series_y, mnd, ry, window):
    """Report 02: the deck's 'Oppsummering' chart pairs -- month and YTD."""
    ytd_label = ("hittil i &aring;r (januar)" if mnd == "januar"
                 else f"hittil i &aring;r (januar&ndash;{esc(mnd)})")
    legend = (f'<div class="legend"><span><i class="sw" '
              f'style="background:var(--ox)"></i>Pr &aring;r</span>'
              f'<span><i class="sw" style="background:var(--stone)"></i>'
              f'Trend</span></div>')
    body = f"""<section><div class="shead"><h2>Omsetning og BF % &mdash; {esc(mnd)}, kjeden</h2>
  <p>Bruttoomsetning og bruttofortjeneste i prosent av netto for {esc(mnd)}
     m&aring;ned, &aring;r for &aring;r. Stiplet linje er line&aelig;r trend.</p></div>
{legend}
<div class="plot">{_pair_svg(series_m, esc(mnd), 10e6)}</div></section>
<section><div class="shead"><h2>Tallene bak grafen &mdash; {esc(mnd)}</h2></div>
{_pair_table(series_m)}</section>
<section><div class="shead"><h2>Omsetning og BF % &mdash; {ytd_label}, kjeden</h2>
  <p>Samme fremstilling for hittil i &aring;r: januar til og med {esc(mnd)},
     &aring;r for &aring;r.</p></div>
{legend}
<div class="plot">{_pair_svg(series_y, "hittil i aar", 20e6)}</div></section>
<section><div class="shead"><h2>Tallene bak grafen &mdash; hittil i &aring;r</h2></div>
{_pair_table(series_y)}</section>
<div class="note"><strong>Sammensetning (brukervalg).</strong> Alle &aring;r
  viser dagens butikksammensetning &mdash; nedlagte butikker pr {esc(mnd)}
  {ry} er utelatt fra alle &aring;r, konsistent med resten av rapportserien.
  PPT-utgavens grafer teller med noen &mdash; men ikke alle &mdash; senere
  nedlagte butikker i eldre &aring;r, etter en BI-klassifisering som ikke
  finnes i kassasystemets API: juli 2021 var hele kjeden (alle butikker i
  drift da) 82,0M og dagens sammensetning 55,4M, mens PPT-grafen viser 61M.
  Fra 2025 er definisjonene sammenfallende. BF = netto omsetning (eks. mva)
  minus varekost.</div>
<script>
{LR.TIP_JS}
document.querySelectorAll(".sumsvg g.pt").forEach(g=>bind(g,g.getAttribute("aria-label")));
</script>
<style>.sumsvg .ptitle{{font-size:11px;letter-spacing:.12em;fill:var(--stone);
  font-weight:600;font-family:var(--sans);}}
.sumsvg .vlab{{font-family:var(--mono);font-size:11px;fill:var(--ink);}}
.sumsvg g.pt:focus-visible circle{{stroke:var(--ink);stroke-width:2;outline:none;}}</style>"""
    return page("02_Oppsummering.html", "02", "Omsetning og BF %", window, body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--outdir", default=str(MR.ROOT / "reports"))
    args = ap.parse_args()
    ry, rm = int(args.month[:4]), int(args.month[5:7])
    outdir = pathlib.Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    nxt = (ry + 1, 1) if rm == 12 else (ry, rm + 1)
    mnd = MR.MND[rm - 1]

    entries = asyncio.run(MR.ensure_cache(nxt))
    reg_to_store, excluded = MR.build_reg_to_store(entries)
    A = lambda ms: MR.aggregate(ms, reg_to_store, excluded)

    this_m, prev_y, prev2_y = A([(ry, rm)]), A([(ry - 1, rm)]), A([(ry - 2, rm)])
    ytd, ytd_prev = A([(ry, m) for m in range(1, rm + 1)]), \
                    A([(ry - 1, m) for m in range(1, rm + 1)])

    window = (f"Data til og med {dt.date(ry, rm, 1).replace(day=28).strftime('%B') and ''}"
              )
    window = (f"Kun data til og med utgangen av {mnd} {ry} &mdash; "
              f"ingen senere data er brukt.")

    closed_note = ""
    for store, when in getattr(MR, "STORE_CLOSED", {}).items():
        closed_note += (f" {esc(store)} er avviklet og inng&aring;r i alle "
                        f"perioder butikken var i drift.")

    files = {}

    # ---- 02 Oppsummering: omsetning og BF %, rapportmaaneden aar for aar ----
    sum_years = list(range(2017, ry + 1))
    asyncio.run(MR.ensure_linjeagg(
        [(y, m) for y in sum_years for m in range(1, rm + 1)]))
    series_m, series_y = [], []
    for y in sum_years:
        d = MR.linjeagg([(y, rm)])
        if d and d["rev"] > 0:
            series_m.append((y, d))
        dy = MR.linjeagg([(y, m) for m in range(1, rm + 1)])
        if dy and dy["rev"] > 0:
            series_y.append((y, dy))
    files["02_Oppsummering.html"] = summary_page(series_m, series_y,
                                                 mnd, ry, window)

    # Web units, derived from the live stock map (user-confirmed taxonomy):
    # 183 = the chain webstore; Shopify stocks are store-owned webstores that
    # the chain's reporting merges into the eponymous physical store.
    stock_regs = {e.stock_id: list(e.register_ids) for e in entries}
    web_units = [
        ("H&oslash;yer Webshop", "kjedens nettbutikk (egen linje over)",
         stock_regs.get(183, [])),
        ("H&oslash;yer Trondheim &mdash; nettbutikk", "Shopify; inng&aring;r i "
         "H&oslash;yer Trondheim over", stock_regs.get(2014, [])),
        ("H&oslash;yer Harstad &mdash; nettbutikk", "Shopify; inng&aring;r i "
         "H&oslash;yer Harstad over", stock_regs.get(2794, [])),
    ]

    def reg_sum(months, regs):
        n_t, s_t = 0, 0.0
        for y, m in months:
            d = MR.month_data(y, m)
            if not d:
                continue
            for r in regs:
                v = d["per_reg"].get(str(r))
                if v:
                    n_t += v[0]; s_t += v[1]
        return n_t, s_t

    # ---- 02 Omsetning (og BF) pr butikk ------------------------------------
    names = sorted(set(MR.STORE_STOCKS) | set(MR.STORE_REG_EXTRAS),
                   key=lambda s: -this_m.stores.get(s, [0, 0])[1])
    maxrev = max(this_m.stores.get(n, [0, 1])[1] for n in names) or 1
    rows = ""
    for n in names:
        cur = this_m.stores.get(n, [0, 0.0]); p1y = prev_y.stores.get(n, [0, 0.0])
        p2y = prev2_y.stores.get(n, [0, 0.0])
        yc = ytd.stores.get(n, [0, 0.0]); yp = ytd_prev.stores.get(n, [0, 0.0])
        rows += (f"<tr><td>{esc(n)}</td>"
                 f"<td><div class='track'><div class='fill' "
                 f"style='width:{cur[1]/maxrev*100:.1f}%'></div></div></td>"
                 f"<td class='num r'>{nf(p2y[1])}</td><td class='num r'>{nf(p1y[1])}</td>"
                 f"<td class='num r'>{nf(cur[1])}</td>"
                 f"<td class='num r'>{MR.pct(cur[1], p1y[1])}</td>"
                 f"<td class='num r'>{nf(cur[0])}</td>"
                 f"<td class='num r'>{nf(cur[1]/cur[0]) if cur[0] else '&ndash;'}</td>"
                 f"<td class='num r'>{nf(yc[1])}</td>"
                 f"<td class='num r'>{MR.pct(yc[1], yp[1])}</td></tr>")
    rows += (f"<tr class='total'><td>Sum</td><td></td>"
             f"<td class='num r'>{nf(prev2_y.revenue)}</td>"
             f"<td class='num r'>{nf(prev_y.revenue)}</td>"
             f"<td class='num r'>{nf(this_m.revenue)}</td>"
             f"<td class='num r'>{MR.pct(this_m.revenue, prev_y.revenue)}</td>"
             f"<td class='num r'>{nf(this_m.trans)}</td>"
             f"<td class='num r'>{nf(this_m.revenue/this_m.trans)}</td>"
             f"<td class='num r'>{nf(ytd.revenue)}</td>"
             f"<td class='num r'>{MR.pct(ytd.revenue, ytd_prev.revenue)}</td></tr>")
    chart = json.dumps([{"k": n.replace("Høyer ", ""),
                         "c": round(this_m.stores.get(n, [0, 0])[1]),
                         "p": round(prev_y.stores.get(n, [0, 0])[1])}
                        for n in names], ensure_ascii=True)
    web_rows = ""
    for label, how, regs in web_units:
        if not regs:
            continue
        n_c, s_c = reg_sum([(ry, rm)], regs)
        n_p, s_p = reg_sum([(ry - 1, rm)], regs)
        n_y, s_y = reg_sum([(ry, m) for m in range(1, rm + 1)], regs)
        web_rows += (f"<tr><td>{label}</td><td>{how}</td>"
                     f"<td class='num r'>{nf(s_c)}</td>"
                     f"<td class='num r'>{nf(s_p)}</td>"
                     f"<td class='num r'>{MR.pct(s_c, s_p)}</td>"
                     f"<td class='num r'>{nf(n_c)}</td>"
                     f"<td class='num r'>{nf(s_y)}</td></tr>")

    body = f"""<section><div class="shead"><h2>Omsetning pr butikk &mdash; {esc(mnd)}</h2>
  <p>H&oslash;yer Webshop er kjedens nettbutikk. Butikkenes egne nettbutikker (Shopify) inng&aring;r i moderbutikkens tall &mdash; se egen tabell under.{closed_note}</p></div>
<div class="legend"><span><i class="sw" style="background:var(--slate)"></i>{esc(mnd)} {ry-1}</span>
  <span><i class="sw" style="background:var(--ox)"></i>{esc(mnd)} {ry}</span></div>
<div class="plot"><svg id="c" viewBox="0 0 1080 320" role="img"
  aria-label="Omsetning pr butikk, {esc(mnd)} {ry-1} mot {ry}"></svg></div>
<div class="tw"><table>
  <thead><tr><th>Butikk</th><th style="width:11%">Andel</th>
    <th class="r">{ry-2}</th><th class="r">{ry-1}</th><th class="r">{ry}</th>
    <th class="r">Endring</th><th class="r">Trans</th><th class="r">Snitt</th>
    <th class="r">Hittil i &aring;r</th><th class="r">Endr. HIA</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>
<section><div class="shead"><h2>Nettbutikker</h2>
  <p>Kjedens nettbutikk rapporteres som egen butikk; butikkenes egne
     Shopify-nettbutikker inng&aring;r i moderbutikkens tall over og vises her
     separat for synlighet.</p></div>
<div class="tw"><table>
  <thead><tr><th>Nettbutikk</th><th>Rapporteres som</th>
    <th class="r">{esc(mnd)} {ry}</th><th class="r">{esc(mnd)} {ry-1}</th>
    <th class="r">Endring</th><th class="r">Trans</th>
    <th class="r">Hittil i &aring;r</th></tr></thead>
  <tbody>{web_rows}</tbody></table></div></section>
<div class="note"><strong>BF pr butikk</strong> ({esc(mnd)}-kolonnene i
  PPT-rapporten) krever varelinjedata og kan ikke beregnes for {esc(mnd)} {ry}
  fra API-et. Salgsheadere har ingen kostpris.</div>
<script>
const D={chart};{LR.TIP_JS}
const NS="http://www.w3.org/2000/svg";
const el=(t,a={{}})=>{{const e=document.createElementNS(NS,t);
  for(const k in a)e.setAttribute(k,a[k]);return e;}};
const nfj=n=>Math.round(n).toLocaleString("en-US").replace(/,/g," ");
(function(){{
  const svg=document.getElementById("c"),W=1080,H=320,P={{l:56,r:10,t:12,b:74}};
  const max=Math.max(...D.map(d=>Math.max(d.c,d.p)))*1.08,iw=W-P.l-P.r,
        bw=iw/D.length,w=Math.min(15,bw/2-4),base=H-P.b,ih=H-P.t-P.b;
  const g=el("g",{{class:"grid"}}),ax=el("g",{{class:"axis"}});
  for(let i=0;i<=4;i++){{const y=base-ih*i/4;
    g.appendChild(el("line",{{x1:P.l,x2:W-P.r,y1:y,y2:y}}));
    const tx=el("text",{{x:P.l-8,y:y+3.5,"text-anchor":"end"}});
    tx.textContent=(max*i/4/1e6).toFixed(1)+"M";ax.appendChild(tx);}}
  svg.appendChild(g);
  D.forEach((d,i)=>{{const cx=P.l+bw*i+bw/2;
    const lab=el("text",{{x:cx,y:base+12,"text-anchor":"end",
      transform:`rotate(-38 ${{cx}} ${{base+12}})`}});
    lab.textContent=d.k;ax.appendChild(lab);
    [[d.p,"var(--slate)","{ry-1}",-w-1],[d.c,"var(--ox)","{ry}",1]].forEach(([v,f,l,off])=>{{
      if(!v)return;
      const h=ih*v/max,grp=el("g",{{class:"bar"}});
      grp.appendChild(el("rect",{{x:cx+off,y:base-h,width:w,height:Math.max(h,1),fill:f}}));
      svg.appendChild(grp);bind(grp,`${{d.k}} ${{l}}\\n${{nfj(v)}} kr`);}});
  }});
  svg.appendChild(ax);
}})();
</script>"""
    files["03_Omsetning_og_BF.html"] = page(
        "03_Omsetning_og_BF.html", "03", "Omsetning og bruttofortjeneste",
        window, body)

    # Reports 04-07 (sesonger, rabatter, merker, selgere) come from
    # line_reports.py, run for the report month's window BEFORE this script
    # so the suite versions of 03 and 08 win. Line history is deep via the
    # from/to parameters, so those sections exist for any month.

    # ---- 08 Diverse ---------------------------------------------------------
    md = MR.month_data(ry, rm) or {"top_sales": []}
    singles = [s for s in md["top_sales"]
               if s["reg"] not in excluded and s["reg"] in reg_to_store][:10]
    s_rows = "".join(
        f"<tr><td class='num'>{i+1}</td><td>{esc(reg_to_store[s['reg']])}</td>"
        f"<td class='num'>{esc(s['date'])}</td><td class='num r'>{nf(s['total'])}</td></tr>"
        for i, s in enumerate(singles))
    d_now = sorted(month_days(ry, rm, reg_to_store, excluded).items(),
                   key=lambda kv: -kv[1][1])[:10]
    d_prev = sorted(month_days(ry - 1, 12, reg_to_store, excluded).items(),
                    key=lambda kv: -kv[1][1])[:10]
    def drows(dd):
        return "".join(
            f"<tr><td class='num'>{i+1}</td><td class='num'>{esc(day)}</td>"
            f"<td>{esc(dt.date.fromisoformat(day).strftime('%A').lower())}</td>"
            f"<td class='num r'>{nf(v[1])}</td><td class='num r'>{nf(v[0])}</td></tr>"
            for i, (day, v) in enumerate(dd))
    body = f"""<div class="two">
<section><div class="shead"><h2>H&oslash;yeste enkeltsalg &mdash; {esc(mnd)} {ry}</h2>
  <p>Kundedata hentes ikke.</p></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Butikk</th><th>Dato</th><th class="r">Bel&oslash;p</th></tr></thead>
  <tbody>{s_rows}</tbody></table></div></section>
<section><div class="shead"><h2>H&oslash;yeste salgsdager hittil i {ry}</h2></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Dato</th><th>Dag</th><th class="r">Omsetning</th>
    <th class="r">Trans</th></tr></thead>
  <tbody>{drows(d_now)}</tbody></table></div></section>
</div>
<section><div class="shead"><h2>H&oslash;yeste salgsdager {ry-1} (hele &aring;ret)</h2></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Dato</th><th>Dag</th><th class="r">Omsetning</th>
    <th class="r">Trans</th></tr></thead>
  <tbody>{drows(d_prev)}</tbody></table></div></section>
<div class="note"><strong>St&oslash;rste kunder</strong> er bevisst utelatt:
  det krever kundeidentifikatorer, og rapportserien henter ikke kundedata.</div>"""
    files["08_Diverse.html"] = page("08_Diverse.html", "08", "Diverse", window, body)

    for fname, content in files.items():
        if not content.isascii():
            bad = next(ch for ch in content if ord(ch) > 127)
            raise SystemExit(f"{fname}: non-ASCII {bad!r}")
        (outdir / fname).write_text(content, encoding="ascii")
        print(f"  wrote {outdir / fname}", file=sys.stderr)
    print(f"  {mnd} {ry}: butikker {len(names)}, chain {this_m.revenue:,.0f}",
          file=sys.stderr)



if __name__ == "__main__":
    main()
