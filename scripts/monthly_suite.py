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
import re
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


def _no_sort_key(name):
    """Mirror the deck's collation: oe/aa sort with o/a, not after z."""
    s = name.replace("Høyer ", "").lower()
    return (s.replace("ø", "o").replace("å", "a")
             .replace("æ", "ae"))


def linjestore_sum(y, months):
    """Per-store aggregates summed over several months of one year."""
    out = {}
    for m in months:
        d = MR.linjestore(y, m)
        if not d:
            continue
        for n, a in d["stores"].items():
            t = out.setdefault(n, {"rev": 0.0, "cost": 0.0, "rab": 0.0,
                                   "trans": 0})
            for k in ("rev", "cost", "rab"):
                t[k] += a[k]
            t["trans"] += a["trans"]
    return out


def _nkl_section(per_year, years, title, sub):
    """A 'Noekkeltall' table: per-store, three years side by side --
    brutto, endring, BF %, BF kr, trans, rabatt.

    Money is in TNOK and the 'Hoeyer ' prefix is dropped, so all 17 numeric
    columns fit the page without horizontal scrolling and no store name
    wraps. Group boundaries are hairlines drawn by the 'gs' (group start)
    class, from the group header down through every row."""
    names = sorted({n for d in per_year.values() for n in d},
                   key=_no_sort_key)
    y0, y1, y2 = years

    def td(txt, gs=False):
        return f"<td class='num r{' gs' if gs else ''}'>{txt}</td>"

    def money(v):
        return (nf(v / 1000) + "<span class='k'>k</span>") if v is not None \
            else "&ndash;"

    rows = ""
    for n in names:
        c = {y: per_year.get(y, {}).get(n) for y in years}
        bf = {y: (a["rev"] / 1.25 - a["cost"]) if a else None
              for y, a in c.items()}
        bfp_ = {y: (bf[y] / (a["rev"] / 1.25) * 100 if a and a["rev"] else None)
                for y, a in c.items()}
        chg = {y: (MR.pct(c[y]["rev"], c[p]["rev"])
                   if c[y] and c[p] and c[p]["rev"] else "&ndash;")
               for y, p in ((y1, y0), (y2, y1))}
        rows += ("<tr><td>" + esc(n.replace("Høyer ", "")) + "</td>"
                 + "".join(td(money(c[y] and c[y]["rev"]), gs=(y == y0))
                           for y in years)
                 + td(chg[y1], gs=True) + td(chg[y2])
                 + "".join(td(p1(bfp_[y]) if bfp_[y] is not None else "&ndash;",
                              gs=(y == y0)) for y in years)
                 + "".join(td(money(bf[y]), gs=(y == y0)) for y in years)
                 + "".join(td(nf(c[y]["trans"]) if c[y] else "&ndash;",
                              gs=(y == y0)) for y in years)
                 + "".join(td(money(c[y] and c[y]["rab"]), gs=(y == y0))
                           for y in years)
                 + "</tr>")
    tot = {y: {k: sum(a[k] for a in per_year.get(y, {}).values())
               for k in ("rev", "cost", "rab", "trans")} for y in years}
    tbf = {y: tot[y]["rev"] / 1.25 - tot[y]["cost"] for y in years}
    rows += ("<tr class='total'><td>Sum kjeden</td>"
             + "".join(td(money(tot[y]["rev"]), gs=(y == y0)) for y in years)
             + td(MR.pct(tot[y1]["rev"], tot[y0]["rev"]), gs=True)
             + td(MR.pct(tot[y2]["rev"], tot[y1]["rev"]))
             + "".join(td(p1(tbf[y] / (tot[y]["rev"] / 1.25) * 100),
                          gs=(y == y0)) for y in years)
             + "".join(td(money(tbf[y]), gs=(y == y0)) for y in years)
             + "".join(td(nf(tot[y]["trans"]), gs=(y == y0)) for y in years)
             + "".join(td(money(tot[y]["rab"]), gs=(y == y0)) for y in years)
             + "</tr>")

    def yhead(ys):
        return "".join(f"<th class='r{' gs' if i == 0 else ''}'>{y}</th>"
                       for i, y in enumerate(ys))

    yh3 = yhead(years)
    return f"""<section class="nkl"><div class="shead"><h2>{title}</h2>
  <p>{sub}</p></div>
<div class="tw"><table>
  <thead>
    <tr><th></th><th class="grp gs" colspan="3">Brutto omsetning &middot; 1000 kr</th>
      <th class="grp gs" colspan="2">Endring</th>
      <th class="grp gs" colspan="3">BF %</th>
      <th class="grp gs" colspan="3">BF &middot; 1000 kr</th>
      <th class="grp gs" colspan="3">Trans &middot; antall</th>
      <th class="grp gs" colspan="3">Rabatt &middot; 1000 kr</th></tr>
    <tr><th>Butikk</th>{yh3}{yhead((y1, y2))}{yh3}{yh3}{yh3}{yh3}</tr>
  </thead>
  <tbody>{rows}</tbody></table></div></section>"""



MND_KORT = ["jan", "feb", "mar", "apr", "mai", "jun", "jul",
            "aug", "sep", "okt", "nov", "des"]


def _mnd_section(ry, rm):
    """The deck's 'Generelle noekkeltall - maanedsvis' matrix: eight chain
    figures x three years against the twelve months plus a year total.
    Money in TNOK; the report year stops at the report month (period
    purity), its Sum column is hittil i aar and its change is measured
    against the same months of the year before."""
    years = (ry - 2, ry - 1, ry)
    data = {}
    for y in years:
        for m in range(1, 13):
            if y == ry and m > rm:
                continue
            la = MR.linjeagg([(y, m)])
            ls = MR.linjestore(y, m)
            if la is None or ls is None:
                continue
            data[(y, m)] = {"rev": la["rev"], "bf": la["bf"],
                            "netto": la["netto"],
                            "trans": sum(a["trans"] for a in ls["stores"].values())}

    def tot(y, upto=12):
        ms = [data[(y, m)] for m in range(1, upto + 1) if (y, m) in data]
        if not ms:
            return None
        return {k: sum(d[k] for d in ms) for k in ("rev", "bf", "netto", "trans")}

    money = lambda v: nf(v / 1000) + "<span class='k'>k</span>"
    dash = "&ndash;"

    def cells(fn, y, sumv):
        out = ""
        for m in range(1, 13):
            d = data.get((y, m))
            out += f"<td class='num r'>{fn(d) if d else ''}</td>"
        return out + f"<td class='num r sum'>{sumv}</td>"

    def chg_cells(key, y):
        if y == years[0]:
            return cells(lambda d: "", y, "")
        out = ""
        for m in range(1, 13):
            d, p = data.get((y, m)), data.get((y - 1, m))
            out += ("<td class='num r'>"
                    + (MR.pct(d[key], p[key]) if d and p and p[key] else "")
                    + "</td>")
        upto = rm if y == ry else 12
        t, tp = tot(y, upto), tot(y - 1, upto)
        return out + ("<td class='num r sum'>"
                      + (MR.pct(t[key], tp[key]) if t and tp and tp[key] else "")
                      + "</td>")

    blocks = [
        ("Brutto omsetning &middot; 1000 kr", lambda y: cells(
            lambda d: money(d["rev"]), y, money(tot(y)["rev"]) if tot(y) else dash)),
        ("Endring brutto", lambda y: chg_cells("rev", y)),
        ("BF &middot; 1000 kr", lambda y: cells(
            lambda d: money(d["bf"]), y, money(tot(y)["bf"]) if tot(y) else dash)),
        ("Endring BF", lambda y: chg_cells("bf", y)),
        ("BF %", lambda y: cells(
            lambda d: p1(d["bf"] / d["netto"] * 100) if d["netto"] else dash, y,
            p1(tot(y)["bf"] / tot(y)["netto"] * 100) if tot(y) and tot(y)["netto"] else dash)),
        ("Trans &middot; antall", lambda y: cells(
            lambda d: nf(d["trans"]), y, nf(tot(y)["trans"]) if tot(y) else dash)),
        ("Omsetning pr transaksjon &middot; kr", lambda y: cells(
            lambda d: nf(d["rev"] / d["trans"]) if d["trans"] else dash, y,
            nf(tot(y)["rev"] / tot(y)["trans"]) if tot(y) and tot(y)["trans"] else dash)),
        ("Netto omsetning &middot; 1000 kr", lambda y: cells(
            lambda d: money(d["netto"]), y, money(tot(y)["netto"]) if tot(y) else dash)),
    ]
    rows = ""
    for label, fn in blocks:
        for i, y in enumerate(years):
            cls = " class='blk'" if i == 0 else ""
            lab = (f"<td class='mlabel' rowspan='3'>{label}</td>" if i == 0 else "")
            rows += f"<tr{cls}>{lab}<td class='num yr'>{y}</td>{fn(y)}</tr>"
    mh = "".join(f"<th class='r'>{m}</th>" for m in MND_KORT)
    mnd = MR.MND[rm - 1]
    return f"""<section class="mnd"><div class="shead"><h2>Generelle n&oslash;kkeltall &mdash; m&aring;nedsvis</h2>
  <p>Kjeden, {years[0]}&ndash;{ry}, fra varelinjene. Bel&oslash;p merket <i>k</i> er i 1000 kr;
     trans er antall og omsetning pr transaksjon er i kr.
     {ry} til og med {esc(mnd)}; Sum-kolonnen for {ry} er hittil i &aring;r,
     og endringen m&aring;les mot samme m&aring;neder &aring;ret f&oslash;r.
     Netto = brutto / 1,25; BF = netto minus varekost; trans er linjebaserte.</p></div>
<div class="tw"><table>
  <thead><tr><th></th><th>&Aring;r</th>{mh}<th class="r sum">Sum</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>"""


NONCHAIN = re.compile(r"BMB|Outlet|test|Pop.?up|IKKE BRUK", re.I)


def linjestore_closed(y, months):
    """The deck's 'Nedlagte butikker' bucket: every unmapped stock except the
    deck's explicit exclusions (BMB, outlets, pop-ups, test and decommissioned
    internal stocks), summed over the months."""
    tot = {"rev": 0.0, "cost": 0.0, "trans": 0}
    for m in months:
        d = MR.linjestore(y, m)
        if not d:
            continue
        for a in d["unmapped"].values():
            if NONCHAIN.search(a["name"]):
                continue
            tot["rev"] += a["rev"]; tot["cost"] += a["cost"]; tot["trans"] += a["trans"]
    return tot


def _store_bf_chart(per_year, years, mnd, title, sub, closed=None):
    """Report 03's opening chart: per store, three year panels, each with a
    brutto bar and a BF % bar whose ink density encodes the BF level (the
    deck's gradient, in the suite's palette). Stores sort by the report
    year's brutto; the last row is the per-store average with the chain's
    BF %."""
    ry = years[-1]
    names = sorted({n for d in per_year.values() for n in d},
                   key=lambda n: -per_year[ry].get(n, {"rev": 0})["rev"])
    def bfp(a):
        netto = a["rev"] / 1.25
        return (netto - a["cost"]) / netto * 100 if netto else 0.0
    vals = {(y, n): (a["rev"], bfp(a))
            for y, d in per_year.items() for n, a in d.items()}
    closed_vals = {}
    if closed:
        for y, a in closed.items():
            if a and a["rev"] > 0:
                closed_vals[y] = (a["rev"], bfp(a))
    rev_max = max([v[0] for v in vals.values()] + [v[0] for v in closed_vals.values()])
    bf_pos = [v[1] for v in vals.values() if v[1] > 0]
    bf_lo, bf_hi = min(bf_pos), max(bf_pos)
    bf_axis = max(45.0, (bf_hi // 5 + 1) * 5)
    # geometry
    W, L, RH, TOP = 1080, 150, 21, 46
    GW = (W - L - 8) / len(years)           # one year group
    LG, GG, LAB = 30, 30, 44                 # lane gap, group gap, label room
    BW = (GW - LG - GG) / 2                  # one bar lane inside a group
    LW = BW - LAB                            # bar length at axis max
    n_rows = len(names) + 1 + (1 if closed_vals else 0)
    H = TOP + n_rows * RH + 44
    s = [f'<svg class="sbf" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="Omsetning og BF pr butikk, {esc(mnd)} {years[0]}&ndash;{ry}">']
    s.append('<g class="axis">')
    for gi, y in enumerate(years):
        gx = L + gi * GW
        s.append(f'<text x="{gx + GW / 2 - 12:.1f}" y="16" text-anchor="middle" '
                 f'class="yh">{y}</text>')
        s.append(f'<text x="{gx + BW / 2:.1f}" y="{TOP - 12}" text-anchor="middle">'
                 f'Brutto omsetning</text>')
        s.append(f'<text x="{gx + BW + LG + BW / 2:.1f}" y="{TOP - 12}" '
                 f'text-anchor="middle">BF %</text>')
        # axis ticks at bottom
        by = TOP + n_rows * RH + 14
        step = 5 if rev_max <= 15e6 else 10 if rev_max <= 30e6 else 20
        for t in range(0, int(rev_max / 1e6) + 1, step):
            tx = gx + LW * t * 1e6 / rev_max
            s.append(f'<text x="{tx:.1f}" y="{by}" '
                     f'text-anchor="{"start" if t == 0 else "middle"}">{t}M</text>')
        for t in (0, 20, 40):
            tx = gx + BW + LG + LW * t / bf_axis
            s.append(f'<text x="{tx:.1f}" y="{by}" '
                     f'text-anchor="{"start" if t == 0 else "middle"}">{t}%</text>')
    s.append('</g>')
    def op(b):
        if bf_hi <= bf_lo:
            return 1.0
        return 0.28 + 0.72 * (b - bf_lo) / (bf_hi - bf_lo)
    def row(i, label, cells, gray=False):
        yy = TOP + i * RH
        s.append(f'<line class="rl" x1="{L - 6}" x2="{W - 8}" y1="{yy + RH:.1f}" '
                 f'y2="{yy + RH:.1f}"/>')
        s.append(f'<text class="sl{" tot" if gray else ""}" x="{L - 10}" '
                 f'y="{yy + RH - 6:.1f}" text-anchor="end">{label}</text>')
        for gi, (rv, bf, aria) in enumerate(cells):
            gx = L + gi * GW
            if rv is None:
                s.append(f'<text class="na" x="{gx + 4}" y="{yy + RH - 6:.1f}">&ndash;</text>')
                continue
            w1 = LW * rv / rev_max
            w2 = LW * bf / bf_axis if bf > 0 else 0
            fill1 = "var(--stone)" if gray else "var(--slate)"
            fill2 = "var(--stone)" if gray else "var(--ox)"
            o2 = 1.0 if gray else op(bf)
            m_lab = f"{rv / 1e6:.1f}".replace(".", ",") + "M"
            p_lab = f"{bf:.1f}".replace(".", ",").replace("-", "&minus;") + "%"
            neg = " neg" if bf < 0 else ""
            s.append(f'<g class="pt" tabindex="0" role="img" aria-label="{aria}">'
                     f'<rect x="{gx:.1f}" y="{yy + 4:.1f}" width="{max(w1, 1):.1f}" '
                     f'height="{RH - 8}" fill="{fill1}"/>'
                     f'<text class="vl" x="{gx + max(w1, 1) + 4:.1f}" '
                     f'y="{yy + RH - 6:.1f}">{m_lab}</text>'
                     f'<rect x="{gx + BW + LG:.1f}" y="{yy + 4:.1f}" '
                     f'width="{max(w2, 1):.1f}" height="{RH - 8}" fill="{fill2}" '
                     f'fill-opacity="{o2:.2f}"/>'
                     f'<text class="vl{neg}" x="{gx + BW + LG + max(w2, 1) + 4:.1f}" '
                     f'y="{yy + RH - 6:.1f}">{p_lab}</text></g>')
    for i, n in enumerate(names):
        cells = []
        for y in years:
            v = vals.get((y, n))
            if v is None:
                cells.append((None, None, ""))
            else:
                cells.append((v[0], v[1],
                              f"{esc(n)} {esc(mnd)} {y}: brutto {nf(v[0])} kr, "
                              f"BF {v[1]:.1f}%".replace(".", ",")))
        row(i, esc(n.replace("Høyer ", "")), cells)
    r = len(names)
    if closed_vals:
        cells = []
        for y in years:
            v = closed_vals.get(y)
            cells.append((None, None, "") if v is None else
                         (v[0], v[1], f"Nedlagte butikker {esc(mnd)} {y}: brutto "
                                      f"{nf(v[0])} kr, BF {v[1]:.1f}%".replace(".", ",")))
        row(r, "Nedlagte butikker", cells)
        r += 1
    cells = []
    for y in years:
        d = per_year[y]
        if not d:
            cells.append((None, None, "")); continue
        rev = sum(a["rev"] for a in d.values()); cost = sum(a["cost"] for a in d.values())
        netto = rev / 1.25
        cells.append((rev / len(d), (netto - cost) / netto * 100,
                      f"Kjeden {esc(mnd)} {y}: snitt pr butikk {nf(rev / len(d))} kr, "
                      f"BF {(netto - cost) / netto * 100:.1f}%".replace(".", ",")))
    row(r, "Snitt butikk / kjeden BF", cells, gray=True)
    s.append("</svg>")
    svg = "".join(s)
    lo = f"{bf_lo:.1f}".replace(".", ","); hi = f"{bf_hi:.1f}".replace(".", ",")
    return f"""<section><div class="shead"><h2>{title}</h2>
  <p>{sub} M&oslash;rkere BF-s&oslash;yle = h&oslash;yere BF %
     (skala {lo}&nbsp;%&ndash;{hi}&nbsp;%). Siste rad: snitt pr butikk og
     kjedens BF %.</p></div>
<div class="legend"><span><i class="sw" style="background:var(--slate)"></i>Brutto omsetning</span>
  <span><i class="sw" style="background:var(--ox)"></i>BF %</span></div>
<div class="plot">{svg}</div></section>
<style>.sbf .sl{{font-family:var(--sans);font-size:11px;fill:var(--ink);}}
.sbf .sl.tot{{font-weight:700;}}
.sbf .yh{{font-family:var(--sans);font-size:12px;font-weight:700;fill:var(--ink);letter-spacing:.04em;}}
.sbf .vl{{font-family:var(--mono);font-size:10px;fill:var(--ink2);}}
.sbf .na{{font-family:var(--mono);font-size:10px;fill:var(--stone);}}
.sbf .vl.neg{{fill:var(--ox);font-weight:700;}}
.sbf .rl{{stroke:var(--hair2);stroke-width:1;}}
.sbf g.pt:focus-visible rect{{stroke:var(--ink);stroke-width:1.5;outline:none;}}</style>"""

HARSTAD_NOTE = ("Harstad inkluderer Shopify-kanalen, hvis kostpriser i "
                "kassasystemet gir lavere BF enn i BI-verkt&oslash;yet bak "
                "PPT-rapporten.")


def _year_fill(i, n):
    """Fill + opacity for year index i of n (oldest first): the report year
    in oxblood, the year before in slate, older years in stone, fading."""
    k = n - 1 - i
    if k == 0:
        return "var(--ox)", 1.0
    if k == 1:
        return "var(--slate)", 1.0
    return "var(--stone)", 1.0 if k == 2 else 0.55


def _store_metric_chart(per_year, years, mnd, title, sub, metric, closed=None):
    """Report 03's single-metric store charts: one panel per year, one bar
    per store coloured by year, stores sorted by the report year's value,
    then the deck's 'Nedlagte butikker' bucket and a chain row.

    metric 'bfp': BF % of netto, chain row = the chain's BF %.
    metric 'bfkr': BF in kroner, chain row = average per store."""
    ry = years[-1]
    n_y = len(years)
    def netto_bf(a):
        netto = a["rev"] / 1.25
        return netto, netto - a["cost"]
    def val(a):
        netto, bf = netto_bf(a)
        if metric == "bfp":
            return bf / netto * 100 if netto else None
        return bf
    vals = {(y, n): val(a) for y, d in per_year.items() for n, a in d.items()}
    names = sorted({n for d in per_year.values() for n in d},
                   key=lambda n: -(vals.get((ry, n)) if vals.get((ry, n)) is not None else -1e12))
    extra = []
    if closed:
        cv = {y: val(a) for y, a in closed.items() if a and a["rev"] > 0}
        if cv:
            extra.append(("Nedlagte butikker", cv, False))
    chain = {}
    for y in years:
        d = per_year[y]
        if d:
            rev = sum(a["rev"] for a in d.values()); cost = sum(a["cost"] for a in d.values())
            if metric == "bfp":
                chain[y] = val({"rev": rev, "cost": cost})
            else:
                chain[y] = (rev / 1.25 - cost) / len(d)
    extra.append(("Kjeden" if metric == "bfp" else "Snitt pr butikk", chain, True))
    allv = [v for v in vals.values() if v is not None] + \
           [v for _, cv, _ in extra for v in cv.values() if v is not None]
    vmax = max(allv)
    if metric == "bfp":
        axis, step, unit = max(50.0, (vmax // 10 + 1) * 10), 10, lambda t: f"{t}%"
    else:
        step = 1e6 if vmax <= 3.5e6 else 5e6 if vmax <= 20e6 else 10e6
        axis, unit = (vmax // step + 1) * step, lambda t: f"{t / 1e6:.0f}M"
    def lab(v):
        if metric == "bfp":
            return f"{v:.1f}".replace(".", ",").replace("-", "&minus;") + "%"
        return f"{v / 1e6:.1f}".replace(".", ",").replace("-", "&minus;") + "M"
    W, L, RH, TOP = 1080, 150, 21, 46
    GW = (W - L - 8) / n_y
    GG, LAB = 26, 44
    LW = GW - GG - LAB
    n_rows = len(names) + len(extra)
    H = TOP + n_rows * RH + 44
    s = [f'<svg class="sbf" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="{title} {years[0]}&ndash;{ry}">', '<g class="axis">']
    by = TOP + n_rows * RH + 14
    head = "BF %" if metric == "bfp" else "BF i kroner"
    for gi, y in enumerate(years):
        gx = L + gi * GW
        s.append(f'<text x="{gx + LW / 2:.1f}" y="16" text-anchor="middle" class="yh">{y}</text>')
        s.append(f'<text x="{gx + LW / 2:.1f}" y="{TOP - 12}" text-anchor="middle">{head}</text>')
        t = 0
        while t <= axis:
            tx = gx + LW * t / axis
            s.append(f'<text x="{tx:.1f}" y="{by}" '
                     f'text-anchor="{"start" if t == 0 else "middle"}">{unit(int(t))}</text>')
            t += step
    s.append('</g>')
    def row(i, label, cells, bold=False):
        yy = TOP + i * RH
        s.append(f'<line class="rl" x1="{L - 6}" x2="{W - 8}" y1="{yy + RH:.1f}" y2="{yy + RH:.1f}"/>')
        s.append(f'<text class="sl{" tot" if bold else ""}" x="{L - 10}" '
                 f'y="{yy + RH - 6:.1f}" text-anchor="end">{label}</text>')
        for gi, (y, v, aria) in enumerate(cells):
            gx = L + gi * GW
            if v is None:
                s.append(f'<text class="na" x="{gx + 4}" y="{yy + RH - 6:.1f}">&ndash;</text>')
                continue
            w = LW * v / axis if v > 0 else 0
            fill, op = ("var(--ink2)", 1.0) if bold else _year_fill(gi, n_y)
            neg = " neg" if v < 0 else ""
            s.append(f'<g class="pt" tabindex="0" role="img" aria-label="{aria}">'
                     f'<rect x="{gx:.1f}" y="{yy + 4:.1f}" width="{max(w, 1):.1f}" '
                     f'height="{RH - 8}" fill="{fill}" fill-opacity="{op}"/>'
                     f'<text class="vl{neg}" x="{gx + max(w, 1) + 4:.1f}" '
                     f'y="{yy + RH - 6:.1f}">{lab(v)}</text></g>')
    def aria(label, y, v):
        if v is None:
            return ""
        return (f"{label} {esc(mnd)} {y}: " + ("BF " + lab(v) if metric == "bfp"
                else "BF " + nf(v) + " kr"))
    for i, n in enumerate(names):
        row(i, esc(n.replace("Høyer ", "")),
            [(y, vals.get((y, n)), aria(esc(n), y, vals.get((y, n)))) for y in years])
    for j, (label, cv, bold) in enumerate(extra):
        row(len(names) + j, label,
            [(y, cv.get(y), aria(label, y, cv.get(y))) for y in years], bold=bold)
    s.append("</svg>")
    legend = "".join(
        f'<span><i class="sw" style="background:{_year_fill(i, n_y)[0]};'
        f'opacity:{_year_fill(i, n_y)[1]}"></i>{y}</span>' for i, y in enumerate(years))
    return f"""<section><div class="shead"><h2>{title}</h2><p>{sub}</p></div>
<div class="legend">{legend}</div>
<div class="plot">{"".join(s)}</div></section>"""


def _store_bfp_chart(per_year, years, mnd, title, sub, closed=None):
    return _store_metric_chart(per_year, years, mnd, title, sub, "bfp", closed)

def summary_page(series_m, series_y, nkl, mnd, ry, window):
    """Report 02: the deck's 'Oppsummering' chart pairs -- month and YTD --
    plus the per-store Noekkeltall table at the bottom."""
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
{nkl}
<div class="note"><strong>Sammensetning (brukervalg).</strong> Alle &aring;r
  viser dagens butikksammensetning &mdash; nedlagte butikker pr {esc(mnd)}
  {ry} er utelatt fra alle &aring;r, konsistent med resten av rapportserien.
  Sj&oslash;lyst herre inng&aring;r historisk i H&oslash;yer Sj&oslash;lyst
  og Kvadrats forgjengerenhet i H&oslash;yer Kvadrat, som i PPT-rapporten.
  PPT-utgavens grafer teller med noen &mdash; men ikke alle &mdash; senere
  nedlagte butikker i eldre &aring;r, etter en BI-klassifisering som ikke
  finnes i kassasystemets API: juli 2021 var hele kjeden (alle butikker i
  drift da) 82,0M og dagens sammensetning 57,6M, mens PPT-grafen viser 61M.
  Fra 2025 er definisjonene sammenfallende.
  BF = netto omsetning (eks. mva) minus varekost.</div>
<script>
{LR.TIP_JS}
document.querySelectorAll(".sumsvg g.pt").forEach(g=>bind(g,g.getAttribute("aria-label")));
</script>
<style>.sumsvg .ptitle{{font-size:11px;letter-spacing:.12em;fill:var(--stone);
  font-weight:600;font-family:var(--sans);}}
.sumsvg .vlab{{font-family:var(--mono);font-size:11px;fill:var(--ink);}}
.sumsvg g.pt:focus-visible circle{{stroke:var(--ink);stroke-width:2;outline:none;}}
.k{{color:var(--stone);font-size:.82em;margin-left:1px;}}
.nkl table{{font-size:11.5px;}}
.nkl th.r,.nkl td.r{{padding-left:6px;}}
.nkl th{{padding-right:4px;}}
.nkl td{{padding-right:4px;}}
.nkl td:first-child{{white-space:nowrap;}}
.nkl td.gs,.nkl th.gs{{border-left:1px solid var(--hair);padding-left:7px;}}
.nkl th.grp{{text-align:center;letter-spacing:.06em;}}
.mnd table{{font-size:12px;}}
.mnd th.r,.mnd td.r{{padding-left:6px;}}
.mnd th,.mnd td{{padding-right:4px;}}
.mnd td.mlabel{{font-weight:700;vertical-align:top;white-space:normal;
  max-width:11ch;font-family:var(--sans);padding-top:8px;}}
.mnd td.yr{{color:var(--stone);}}
.mnd tr.blk td{{border-top:1px solid var(--ink2);}}
.mnd .sum{{border-left:1px solid var(--hair);padding-left:12px;font-weight:600;}}</style>"""
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

    LR.set_suite_month(ry, rm)
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
    nkl_years = (ry - 2, ry - 1, ry)
    asyncio.run(MR.ensure_linjestore(
        [(y, m) for y in nkl_years for m in range(1, rm + 1)]))
    sub = ("Pr butikk, {per}, fra varelinjene. Bel&oslash;p merket <i>k</i> er i 1000 kr; "
           "transaksjoner er antall. "
           "Butikkenes nettbutikker og Sj&oslash;lyst herre inng&aring;r i "
           "moderbutikken; transaksjoner er linjebaserte og avviker derfor "
           "marginalt fra kassetellingen i del 01.")
    per_m = {y: (MR.linjestore(y, rm) or {}).get("stores", {})
             for y in nkl_years}
    nkl = _nkl_section(
        per_m, nkl_years,
        f"N&oslash;kkeltall m&aring;ned &mdash; {esc(mnd)}",
        sub.format(per=f"{esc(mnd)} {nkl_years[0]}&ndash;{ry}"))
    if rm > 1:
        per_ytd = {y: linjestore_sum(y, range(1, rm + 1)) for y in nkl_years}
        nkl += _nkl_section(
            per_ytd, nkl_years,
            f"N&oslash;kkeltall hittil i &aring;r &mdash; januar&ndash;{esc(mnd)}",
            sub.format(per=f"januar&ndash;{esc(mnd)}, {nkl_years[0]}&ndash;{ry}"))
    # Generelle noekkeltall maanedsvis: full prior years, report year to rm
    mnd_months = [(y, m) for y in nkl_years
                  for m in range(1, (rm if y == ry else 12) + 1)]
    asyncio.run(MR.ensure_linjeagg(mnd_months))
    asyncio.run(MR.ensure_linjestore(mnd_months))
    nkl += _mnd_section(ry, rm)
    files["02_Oppsummering.html"] = summary_page(series_m, series_y, nkl,
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

    sbf = _store_bf_chart(
        per_m, nkl_years, mnd,
        f"Omsetning og BF pr butikk &mdash; {esc(mnd)}",
        f"Bruttoomsetning og BF % pr butikk, {esc(mnd)} {nkl_years[0]}&ndash;{ry}, "
        f"fra varelinjene. Sortert etter {esc(mnd)} {ry}.")
    if rm > 1:
        closed = {y: linjestore_closed(y, range(1, rm + 1)) for y in nkl_years}
        sbf += _store_bf_chart(
            per_ytd, nkl_years, f"hittil i {esc(mnd)}",
            "Omsetning og BF pr butikk &mdash; YTD",
            f"Hittil i &aring;r, januar&ndash;{esc(mnd)} {nkl_years[0]}&ndash;{ry}, "
            f"fra varelinjene. Sortert etter {ry}. Nedlagte butikker vises samlet "
            f"for sammenligning og inng&aring;r ikke i snitt/kjede-raden.",
            closed=closed)
    closed_m = {y: linjestore_closed(y, [rm]) for y in nkl_years}
    sbf += _store_bfp_chart(
        per_m, nkl_years, mnd,
        f"BF % pr butikk &mdash; {esc(mnd)}",
        f"Bruttofortjeneste i prosent av netto pr butikk, {esc(mnd)} "
        f"{nkl_years[0]}&ndash;{ry}, fra varelinjene. Sortert etter {esc(mnd)} {ry}. "
        f"Nedlagte butikker vises samlet og inng&aring;r ikke i kjeden. "
        f"{HARSTAD_NOTE}",
        closed=closed_m)
    if rm > 1:
        sbf += _store_bfp_chart(
            per_ytd, nkl_years, f"hittil i {esc(mnd)}",
            "BF % pr butikk &mdash; YTD",
            f"Bruttofortjeneste i prosent av netto pr butikk, hittil i &aring;r "
            f"januar&ndash;{esc(mnd)} {nkl_years[0]}&ndash;{ry}, fra varelinjene. "
            f"Sortert etter {ry}. Nedlagte butikker vises samlet og inng&aring;r "
            f"ikke i kjeden. {HARSTAD_NOTE}",
            closed=closed)
    # BF i kroner goes four years back, as the deck does
    y4 = (ry - 3, ry - 2, ry - 1, ry)
    asyncio.run(MR.ensure_linjestore([(y4[0], m) for m in range(1, rm + 1)]))
    per_m4 = {y: (MR.linjestore(y, rm) or {}).get("stores", {}) for y in y4}
    sbf += _store_metric_chart(
        per_m4, y4, mnd,
        f"BF i kroner pr butikk &mdash; {esc(mnd)}",
        f"Bruttofortjeneste i kroner pr butikk, {esc(mnd)} {y4[0]}&ndash;{ry}, fra "
        f"varelinjene. Sortert etter {esc(mnd)} {ry}. Nedlagte butikker vises "
        f"samlet; siste rad er snitt pr butikk.",
        "bfkr", closed={y: linjestore_closed(y, [rm]) for y in y4})
    if rm > 1:
        per_ytd4 = {y: linjestore_sum(y, range(1, rm + 1)) for y in y4}
        sbf += _store_metric_chart(
            per_ytd4, y4, f"hittil i {esc(mnd)}",
            "BF i kroner pr butikk &mdash; YTD",
            f"Bruttofortjeneste i kroner pr butikk, hittil i &aring;r "
            f"januar&ndash;{esc(mnd)} {y4[0]}&ndash;{ry}, fra varelinjene. Sortert "
            f"etter {ry}. Nedlagte butikker vises samlet; siste rad er snitt pr butikk.",
            "bfkr", closed={y: linjestore_closed(y, range(1, rm + 1)) for y in y4})
    body = f"""{sbf}
<section><div class="shead"><h2>Omsetning pr butikk &mdash; {esc(mnd)}</h2>
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
<div class="note"><strong>BF pr butikk</strong> vises i grafen &oslash;verst
  (fra varelinjene); tabellen under den bygger p&aring; kassetellingen og har
  derfor hittil-i-&aring;r-kolonner for alle &aring;r. N&oslash;kkeltall pr butikk
  med BF i kroner og rabatt st&aring;r i del 02.</div>
<script>
const D={chart};{LR.TIP_JS}
document.querySelectorAll(".sbf g.pt").forEach(g=>bind(g,g.getAttribute("aria-label")));
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
