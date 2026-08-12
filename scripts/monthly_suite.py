#!/usr/bin/env python3
"""Reports 02–07 of the månedsrapport suite, strictly month-and-earlier data.

The report month's edition may not contain anything newer than the month it
covers. Sections that need line-level data (BF, sesonger, rabatter, merker,
selgere) cannot be computed for months before 2026-08-01, when Saleslines
begins — those pages say so explicitly instead of borrowing later data.

What IS computable from Sales headers for any month back to 2024:
per-store revenue/transactions/snittkjøp (02), and høyeste enkeltsalg and
salgsdager (07).

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
        closed_note += (f" {esc(store)} ble avviklet {esc(when)} og inng&aring;r "
                        f"i alle perioder butikken var i drift.")

    unavailable = (f"Krever varelinjedata, som API-et f&oslash;rst inneholder fra "
                   f"<code>2026-08-01</code>. For {mnd} {ry} kan denne delen derfor "
                   f"ikke lages fra API-et &mdash; tallene finnes kun i BI-verkt&oslash;yet "
                   f"bak PPT-rapporten. Fra og med august-utgaven produseres delen "
                   f"automatisk.")

    files = {}

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
    body = f"""<section><div class="shead"><h2>Omsetning pr butikk &mdash; {esc(mnd)}</h2>
  <p>Shopify-salg er sl&aring;tt sammen med moderbutikken.{closed_note}</p></div>
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
<div class="note"><strong>BF pr butikk</strong> ({esc(mnd)}-kolonnene i
  PPT-rapporten) krever varelinjedata og kan ikke beregnes for {esc(mnd)} {ry}
  fra API-et &mdash; linjedata finnes fra 01.08.2026. Salgsheadere har ingen
  kostpris.</div>
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
    files["02_Omsetning_og_BF.html"] = page(
        "02_Omsetning_og_BF.html", "02", "Omsetning og bruttofortjeneste",
        window, body)

    # ---- 03-06 honest unavailability pages ---------------------------------
    def stub(fname, no, title, contents):
        items = "".join(f"<li>{c}</li>" for c in contents)
        body = f"""<div class="note"><strong>Ikke tilgjengelig for {esc(mnd)} {ry}.</strong>
  {unavailable}</div>
<section><div class="shead"><h2>Innhold fra august-utgaven</h2></div>
<ul style="margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px;
  font-size:13.5px;color:var(--ink2);">{items}</ul></section>"""
        files[fname] = page(fname, no, title, window, body)

    stub("03_Sesonger.html", "03", "Sesonger",
         ["Salg og BF fordelt p&aring; sesong &mdash; m&aring;ned og hittil i &aring;r"])
    stub("04_Rabatter.html", "04", "Rabatter",
         ["N&oslash;kkeltall rabatter &mdash; m&aring;ned og hittil i &aring;r",
          "Rabatt gitt i % &mdash; utvikling etter hvert som linjehistorikk bygges opp",
          "Rabatter p&aring; sesongvarer og basisvarer"])
    stub("05_Merker.html", "05", "Merker",
         ["Merker med h&oslash;yest omsetning",
          "Generelle n&oslash;kkeltall pr merke",
          "Ralph Lauren, By Malene Birger og NN.07 pr butikk"])
    stub("06_Selgere.html", "06", "Selgere &mdash; PPK og KPK",
         ["Plagg pr kunde (PPK) pr butikk",
          "Beste selger PPK og KPK, med terskler for antall transaksjoner",
          "Beste selger dame og herre"])

    # ---- 07 Diverse ---------------------------------------------------------
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
    files["07_Diverse.html"] = page("07_Diverse.html", "07", "Diverse", window, body)

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
