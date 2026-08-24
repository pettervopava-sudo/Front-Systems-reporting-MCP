#!/usr/bin/env python3
"""Høyer Nye KPI — egen hovedrapporttype ved siden av månedsrapporten.

To rapporter (definert av Petter 2026-08-24, bygget mot probede felter):

  K1 Kapitalavkastning varer — omsetning, BF %, BF-kr, varekost solgt,
     BF-kr pr solgt innkjøpskrone, sell-through og GMROI pr butikk, merke,
     varegruppe og sesong. Lagersiden kommer fra Stockstatus-snapshots
     (Cost pr vare, probet 2026-08-24).
  K2 Returer — returoversikt pr utførende (bytte vs ren retur via SID,
     fellesbruker-flagg), returgrad pr opprinnelig selger via
     EAN+butikk-heuristikken, og unntaksliste.

Én selvstendig HTML-side (paginert: Om / K1 / K2), suitens design. Ingen
PDF. Beregningsregler som månedsrapporten: brutto = SUM(Qty*Price) inkl.
mva, netto = brutto/1,25, BF = netto - varekost. Kundedata hentes ikke.

Usage:
  python3 scripts/hoyer_nye_kpi.py [--month YYYY-MM]   # default forrige mnd
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from front_systems_mcp.client import FrontSystemsClient  # noqa: E402
from front_systems_mcp.config import load_config  # noqa: E402
import line_reports as LR  # noqa: E402

esc, nf, p1 = LR.esc, LR.nf, LR.p1
CACHE = ROOT / "reports" / "cache"
VAT = 1.25
KSEL = ["SALEID", "SID", "STOCKID_FK", "Qty", "Price", "Cost", "Brand",
        "Group", "Season", "Name", "EAN", "Employee", "SaleDate",
        "SaleDateTime", "OrderLineReasons", "Stock"]
#: kjente felles-/systembrukere; i tillegg flagges brukernavn som inneholder
#: et butikknavn (Arendal, Storo, "Paleet Man" ...) som fellesbrukere.
FELLES = {"webshop", "shopify integrasjon", "shopify", "integrasjon",
          "høyer", "hoyer", "storo", "limon media", "teststore"}
STORE_WORDS = {n.replace("Høyer ", "").lower() for n in set(LR.STOCK_STORE.values())}
GMROI_MIN_LAGER = 20000.0  # under dette vises GMROI som strek


def is_felles(emp: str) -> bool:
    e = (emp or "").strip().lower()
    if not e:
        return True
    if e in FELLES:
        return True
    return any(w in e for w in STORE_WORDS)


async def ensure_klines(months):
    """Slimmed raw lines per month for the K reports (klines_YYYY-MM.json)."""
    missing = [(y, m) for y, m in months
               if not (CACHE / f"klines_{y:04d}-{m:02d}.json").exists()]
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
                "$select": ",".join(KSEL), "$top": "2000000"})
        # chain stores, plus closed Hoeyer stores (for the Nedlagte rows in
        # the sesong tables); BMB/outlets/pop-ups/test stay out.
        import monthly_suite as MS
        def keeps(r):
            if r["STOCKID_FK"] in LR.STOCK_STORE:
                return True
            if r["STOCKID_FK"] in LR.EXCLUDED_STOCKS:
                return False
            return not MS.NONCHAIN.search(r.get("Stock") or "")
        keep = [r for r in rows if keeps(r)]
        json.dump(keep, open(CACHE / f"klines_{y:04d}-{m:02d}.json", "w"),
                  ensure_ascii=False)
        print(f"  klines {y:04d}-{m:02d}: {len(keep):,} linjer", file=sys.stderr)

    try:
        await asyncio.gather(*(one(y, m) for y, m in missing))
    finally:
        await client.aclose()


async def ensure_stockagg(day: dt.date):
    """Inventory snapshot aggregated to (store, brand, group, season)."""
    f = CACHE / f"stockagg_{day}.json"
    if f.exists():
        return json.load(open(f))
    client = FrontSystemsClient(load_config())
    try:
        rows = await client.fetch_raw("Stockstatus", {
            "snapshotDateTime": f"'{day} 23:00:00'", "$top": "2000000"})
    finally:
        await client.aclose()
    agg = {}
    kept = 0
    for r in rows:
        store = LR.STOCK_STORE.get(r.get("Stockid"))
        if store is None:
            continue
        kept += 1
        q = float(r.get("Qty") or 0)
        c = float(r.get("Cost") or 0)
        season = str(r.get("Season") or "").strip() or "Basis / uspesifisert"
        if season in ("0", "1", "None"):
            season = "Basis / uspesifisert"
        key = "\t".join([store, r.get("Brand") or "(ukjent)",
                         r.get("Group") or "(ukjent)", season])
        a = agg.setdefault(key, [0.0, 0.0])   # enheter, kostverdi
        a[0] += q
        a[1] += q * c
    out = {"day": str(day), "rows": len(rows), "kept": kept, "agg": agg}
    json.dump(out, open(f, "w"), ensure_ascii=False)
    print(f"  stockagg {day}: {kept:,} varelinjer paa lager", file=sys.stderr)
    return out


class KL:
    __slots__ = ("store", "qty", "rev", "cost", "brand", "group", "season",
                 "name", "ean", "emp", "sid", "saleid", "date", "time", "reasons")

    def __init__(self, r):
        self.store = LR.STOCK_STORE.get(r["STOCKID_FK"])
        q = float(r["Qty"] or 0)
        self.qty = q
        self.rev = q * float(r["Price"] or 0)
        self.cost = q * float(r["Cost"] or 0)
        self.brand = r.get("Brand") or "(ukjent)"
        self.group = r.get("Group") or "(ukjent)"
        s = str(r.get("Season") or "").strip()
        self.season = s if s not in ("", "0", "1", "None") else "Basis / uspesifisert"
        self.name = r.get("Name") or ""
        self.ean = r.get("EAN") or ""
        self.emp = (r.get("Employee") or "").strip()
        self.sid = r.get("SID")
        self.saleid = r.get("SALEID")
        self.date = (r.get("SaleDate") or "")[:10]
        self.time = (r.get("SaleDateTime") or "")[11:16]
        self.reasons = r.get("OrderLineReasons") or []


def money(v):
    return nf(v / 1000) + "<span class='k'>k</span>"


def ratio(v):
    return f"{v:.2f}".replace(".", ",")


# ---------------------------------------------------------------- K1 ----
def k1_dim_table(lines, start_agg, end_agg, keyfn, aggkey_idx, title, sub,
                 top_n=None):
    """One K1 table: dimension -> omsetning, BF%, BF-kr, varekost,
    BF/innkjopskrone, sell-through, GMROI."""
    sold = collections.defaultdict(lambda: {"rev": 0.0, "cost": 0.0, "qty": 0.0})
    for ln in lines:
        k = keyfn(ln)
        a = sold[k]
        a["rev"] += ln.rev; a["cost"] += ln.cost; a["qty"] += ln.qty
    inv = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # u0, v0, u1, v1
    for src, off in ((start_agg, 0), (end_agg, 2)):
        for key, (units, value) in src["agg"].items():
            parts = key.split("\t")
            k = parts[aggkey_idx]
            inv[k][off] += units
            inv[k][off + 1] += value
    order = sorted(sold, key=lambda k: -sold[k]["rev"])
    if top_n:
        order = order[:top_n]
    rows = ""
    for k in order:
        a = sold[k]
        netto = a["rev"] / VAT
        bf = netto - a["cost"]
        u0, v0, u1, v1 = inv.get(k, [0, 0, 0, 0])
        avg_v = (v0 + v1) / 2
        st = a["qty"] / (a["qty"] + u1) * 100 if (a["qty"] + u1) > 0 and a["qty"] > 0 else None
        gm = bf / avg_v if avg_v >= GMROI_MIN_LAGER else None
        rows += ("<tr><td>" + esc(str(k).replace("Høyer ", "")) + "</td>"
                 f"<td class='num r'>{money(a['rev'])}</td>"
                 f"<td class='num r'>{p1(bf / netto * 100) if netto else '&ndash;'}</td>"
                 f"<td class='num r'>{money(bf)}</td>"
                 f"<td class='num r'>{money(a['cost'])}</td>"
                 f"<td class='num r'>{ratio(bf / a['cost']) if a['cost'] > 0 else '&ndash;'}</td>"
                 f"<td class='num r'>{p1(st) if st is not None else '&ndash;'}</td>"
                 f"<td class='num r'>{ratio(gm) if gm is not None else '&ndash;'}</td>"
                 f"<td class='num r'>{money(avg_v) if avg_v > 0 else '&ndash;'}</td></tr>")
    return f"""<section><div class="shead"><h2>{title}</h2><p>{sub}</p></div>
<div class="tw"><table>
  <thead><tr><th></th><th class="r">Omsetning</th><th class="r">BF %</th>
    <th class="r">BF</th><th class="r">Varekost solgt</th>
    <th class="r">BF pr innkj.kr</th><th class="r">Sell-through</th>
    <th class="r">GMROI</th><th class="r">Snittlager kost</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>"""


def build_k1(lines, start_agg, end_agg, mnd, ry):
    chain = {"rev": sum(l.rev for l in lines), "cost": sum(l.cost for l in lines),
             "qty": sum(l.qty for l in lines)}
    netto = chain["rev"] / VAT
    bf = netto - chain["cost"]
    inv0 = sum(v[1] for v in start_agg["agg"].values())
    inv1 = sum(v[1] for v in end_agg["agg"].values())
    u1 = sum(v[0] for v in end_agg["agg"].values())
    avg_v = (inv0 + inv1) / 2
    st = chain["qty"] / (chain["qty"] + u1) * 100
    kpis = f"""<div class="kpis">
  <div class="kpi lead"><span class="l">BF pr innkj&oslash;pskrone (solgt)</span>
    <span class="v">{ratio(bf / chain['cost'])}</span>
    <span class="f">BF {nf(bf)} / varekost {nf(chain['cost'])}</span></div>
  <div class="kpi"><span class="l">GMROI</span>
    <span class="v">{ratio(bf / avg_v)}</span>
    <span class="f">BF pr krone bundet i snittlager ({money(avg_v)})</span></div>
  <div class="kpi"><span class="l">Sell-through</span>
    <span class="v">{p1(st)}</span>
    <span class="f">solgte enheter / (solgte + sluttlager)</span></div>
  <div class="kpi"><span class="l">BF %</span>
    <span class="v">{p1(bf / netto * 100)}</span>
    <span class="f">av netto omsetning</span></div>
  <div class="kpi"><span class="l">Brutto omsetning</span>
    <span class="v">{money(chain['rev'])}</span><span class="f">inkl. mva</span></div>
</div>"""
    note = f"""<div class="note"><strong>Lesing.</strong> BF pr innkj&oslash;pskrone
  (BF / varekost for det som ble solgt) er en ren omregning av BF % &mdash;
  den viser marginstyrke, ikke kapitalbinding. GMROI (BF / gjennomsnittlig
  lagerverdi til kost, snitt av inng&aring;ende og utg&aring;ende snapshot) og
  sell-through viser avkastningen p&aring; kapitalen som faktisk st&aring;r
  p&aring; lager. GMROI vises ikke der snittlageret er under {nf(GMROI_MIN_LAGER)} kr.
  Lager-snapshots: {esc(start_agg['day'])} og {esc(end_agg['day'])} kl. 23.</div>"""
    sub = f"{esc(mnd)} {ry}. Bel&oslash;p merket <i>k</i> er i 1000 kr."
    body = (kpis + note
            + k1_dim_table(lines, start_agg, end_agg, lambda l: l.store, 0,
                           "Pr butikk", sub)
            + k1_dim_table(lines, start_agg, end_agg, lambda l: l.brand, 1,
                           f"Pr merke (topp 30 etter omsetning)", sub, top_n=30)
            + k1_dim_table(lines, start_agg, end_agg, lambda l: l.group, 2,
                           "Pr varegruppe (topp 25)", sub, top_n=25)
            + k1_dim_table(lines, start_agg, end_agg, lambda l: l.season, 3,
                           "Pr sesong", sub))
    return body


# ---------------------------------------------------------------- K2 ----
def build_k2(month_lines, hist_lines, mnd, ry):
    ret = [l for l in month_lines if l.qty < 0]
    pos_sid_dates = collections.defaultdict(set)
    for l in hist_lines:
        if l.qty > 0:
            pos_sid_dates[l.sid].add(l.date)
    # heuristikk: siste foregaaende salg av samme EAN i samme butikk
    cand = collections.defaultdict(list)
    for l in hist_lines:
        if l.qty > 0 and l.ean:
            cand[(l.store, l.ean)].append((l.date, l.emp))
    for v in cand.values():
        v.sort()
    def original_seller(l):
        cs = [x for x in cand.get((l.store, l.ean), []) if x[0] <= l.date]
        if not cs:
            return None
        last = cs[-1][0]
        sellers = {e for d, e in cs if d == last}
        return sellers.pop() if len(sellers) == 1 else "(flertydig)"

    # -- del 1: pr utfoerende ------------------------------------------------
    per_emp = collections.defaultdict(lambda: {"n": 0, "v": 0.0, "bytte": 0})
    for l in ret:
        a = per_emp[(l.emp or "(tom)",
                     (l.store or "?").replace("Høyer ", ""))]
        a["n"] += 1
        a["v"] += -l.rev
        if l.date in pos_sid_dates.get(l.sid, set()):
            a["bytte"] += 1
    tot_n = sum(a["n"] for a in per_emp.values())
    tot_v = sum(a["v"] for a in per_emp.values())
    felles_n = sum(a["n"] for (e, _st), a in per_emp.items() if is_felles(e))
    rows = ""
    for (e, st), a in sorted(per_emp.items(), key=lambda kv: -kv[1]["v"])[:30]:
        rows += (f"<tr><td>{esc(e)}</td>"
                 f"<td>{esc(st)}</td>"
                 f"<td>{'Felles/system' if is_felles(e) else 'Personlig'}</td>"
                 f"<td class='num r'>{nf(a['n'])}</td>"
                 f"<td class='num r'>{money(a['v'])}</td>"
                 f"<td class='num r'>{nf(a['v'] / a['n'])}</td>"
                 f"<td class='num r'>{p1(a['bytte'] / a['n'] * 100)}</td></tr>")
    d1 = f"""<div class="kpis">
  <div class="kpi lead"><span class="l">Returlinjer i {esc(mnd)}</span>
    <span class="v">{nf(tot_n)}</span><span class="f">{money(tot_v)} returverdi</span></div>
  <div class="kpi"><span class="l">Bytter (samme bes&oslash;k)</span>
    <span class="v">{p1(sum(a['bytte'] for a in per_emp.values()) / tot_n * 100)}</span>
    <span class="f">retur + nykj&oslash;p deler kvitteringskjede (SID)</span></div>
  <div class="kpi"><span class="l">P&aring; felles-/systembruker</span>
    <span class="v">{p1(felles_n / tot_n * 100)}</span>
    <span class="f">etterlevelses-KPI: b&oslash;r mot 0 for personlig sporbarhet</span></div>
</div>
<section><div class="shead"><h2>Returer pr utf&oslash;rende bruker</h2>
  <p>{esc(mnd)} {ry}, &eacute;n rad pr bruker og butikk, topp 30 etter returverdi. Bel&oslash;p merket <i>k</i> er i 1000 kr.</p></div>
<div class="tw"><table>
  <thead><tr><th>Bruker</th><th>Butikk</th><th>Type</th><th class="r">Antall</th>
    <th class="r">Returverdi</th><th class="r">Snitt kr</th>
    <th class="r">Andel bytte</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>"""

    # -- del 2: returgrad pr opprinnelig selger ------------------------------
    attributed = collections.defaultdict(lambda: {"n": 0, "v": 0.0})
    matched = ambiguous = nocand = 0
    for l in ret:
        o = original_seller(l) if l.ean else None
        if o is None:
            nocand += 1
        elif o == "(flertydig)":
            ambiguous += 1
        else:
            matched += 1
            a = attributed[(o, (l.store or "?").replace("Høyer ", ""))]
            a["n"] += 1
            a["v"] += -l.rev
    sold = collections.defaultdict(float)
    for l in month_lines:
        if l.qty > 0:
            sold[(l.emp, (l.store or "?").replace("Høyer ", ""))] += l.rev
    rows = ""
    sellers = [k for k, v in sold.items()
               if v >= 100000 and not is_felles(k[0])]
    for k in sorted(sellers, key=lambda k: -(attributed[k]["v"] / sold[k])):
        e, st = k
        a = attributed[k]
        rows += (f"<tr><td>{esc(e)}</td>"
                 f"<td>{esc(st)}</td>"
                 f"<td class='num r'>{money(sold[k])}</td>"
                 f"<td class='num r'>{nf(a['n'])}</td>"
                 f"<td class='num r'>{money(a['v'])}</td>"
                 f"<td class='num r'>{p1(a['v'] / sold[k] * 100)}</td></tr>")
    d2 = f"""<section><div class="shead"><h2>Returgrad pr opprinnelig selger</h2>
  <p>Returer i {esc(mnd)} tilskrevet selgeren av det siste foreg&aring;ende
     salget av samme vare (EAN) i samme butikk. &Eacute;n rad pr selger og
     butikk; personlige selgere med minst 100k i salg i butikken i
     {esc(mnd)}.</p></div>
<div class="tw"><table>
  <thead><tr><th>Selger</th><th>Butikk</th><th class="r">Salg {esc(mnd)}</th>
    <th class="r">Returer tilskrevet</th><th class="r">Returverdi</th>
    <th class="r">Returgrad</th></tr></thead>
  <tbody>{rows}</tbody></table></div>
<div class="note"><strong>Dekning.</strong> {nf(matched)} av {nf(tot_n)}
  returlinjer ({p1(matched / tot_n * 100)}) fikk entydig opprinnelig selger;
  {nf(ambiguous)} flertydige og {nf(nocand)} uten kandidat i historikken
  (kj&oslash;pt f&oslash;r januar, i annen butikk, eller uten EAN).
  Returgraden sammenligner {esc(mnd)}-returer med {esc(mnd)}-salg og er
  dermed en tiln&aelig;rming; utf&oslash;rende bruker belastes ikke.</div>"""

    # -- del 3: unntak -------------------------------------------------------
    rows = ""
    exceptions = []
    for l in ret:
        flags = []
        if -l.rev >= 5000:
            flags.append("stort bel&oslash;p")
        if l.ean and original_seller(l) is None:
            flags.append("uten sporbart originalsalg")
        if not l.ean:
            flags.append("uten EAN")
        if l.date not in pos_sid_dates.get(l.sid, set()) and is_felles(l.emp):
            flags.append("ren retur p&aring; fellesbruker")
        if flags:
            exceptions.append((l, flags))
    exceptions.sort(key=lambda x: x[0].rev)
    for l, flags in exceptions[:30]:
        rows += (f"<tr><td class='num'>{esc(l.date)} {esc(l.time)}</td>"
                 f"<td>{esc((l.store or '').replace('Høyer ', ''))}</td>"
                 f"<td>{esc(l.name[:32]) or '(uten navn)'}</td>"
                 f"<td class='num r'>{nf(-l.rev)}</td>"
                 f"<td>{esc(l.emp) or '(tom)'}</td>"
                 f"<td>{', '.join(flags)}</td></tr>")
    d3 = f"""<section><div class="shead"><h2>Unntaksliste</h2>
  <p>{nf(len(exceptions))} returlinjer med avvik i {esc(mnd)}; de 30
     st&oslash;rste vises. Kriterier: bel&oslash;p &ge; 5&nbsp;000 kr, uten
     sporbart originalsalg, uten EAN, eller ren retur utf&oslash;rt av
     felles-/systembruker.</p></div>
<div class="tw"><table>
  <thead><tr><th>Tidspunkt</th><th>Butikk</th><th>Vare</th>
    <th class="r">Bel&oslash;p</th><th>Utf&oslash;rt av</th><th>Avvik</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>
<div class="note"><strong>Prosess og KPI-skjerming.</strong> Personlig
  innlogging ved retur kan ikke h&aring;ndheves av en rapport &mdash; det er
  kasserutine/oppsett i Front Systems. Andelen p&aring; felles-/systembruker
  over m&aring;ler etterlevelsen. Kobling til opprinnelig kvittering finnes
  ikke i API-et (SID er bes&oslash;ket, ikke originalkj&oslash;pet) &mdash;
  be Front Systems eksponere originalkvittering p&aring; returlinjer.
  Selger-KPI-ene i m&aring;nedsrapportens del 07 kan p&aring; foresp&oslash;rsel
  legges om til kun salgslinjer, med returer rapportert her i stedet.</div>"""
    return d1 + d2 + d3


# ---------------------------------------------------------------- page --
NAV = ('<nav class="suite"><a href="#om">H&oslash;yer Nye KPI</a>'
       '<a href="#k1">K1 Kapitalavkastning</a>'
       '<a href="#k2">K2 Returer</a></nav>')


def part(pid, no, title, win, body):
    return f"""<div class="part" id="{pid}"><div class="wrap">
{NAV}
<header class="mast">
  <div class="no">{no}</div>
  <div>
    <div class="kicker">H&Oslash;YER-kjeden &middot; H&oslash;yer Nye KPI</div>
    <h1>{title}</h1>
    <div class="win">{win}</div>
  </div>
  <div class="brand" role="img" aria-label="H&Oslash;yer"></div>
</header>
{body}
<footer>
  <div>Omsetning er <code>SUM(Qty &times; Price)</code> (inkl. mva); BF er
    nettobasert. Dagens butikksammensetning; BMB, outlets og teststore er
    utelatt. Kundedata hentes ikke.</div>
  <div>H&oslash;yer Nye KPI &middot; <code>scripts/hoyer_nye_kpi.py</code></div>
</footer>
</div></div>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    prev = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    ap.add_argument("--month", default=f"{prev.year:04d}-{prev.month:02d}")
    ap.add_argument("--out", default=str(ROOT / "reports" / "Hoyer_Nye_KPI.html"))
    args = ap.parse_args()
    ry, rm = int(args.month[:4]), int(args.month[5:7])
    mnd = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august",
           "september", "oktober", "november", "desember"][rm - 1]
    hist_months = []
    y, m = ry, rm
    for _ in range(7):
        hist_months.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    asyncio.run(ensure_klines(hist_months))
    d1 = dt.date(ry + 1, 1, 1) if rm == 12 else dt.date(ry, rm + 1, 1)
    end_day = d1 - dt.timedelta(days=1)
    start_day = dt.date(ry, rm, 1) - dt.timedelta(days=1)
    start_agg = asyncio.run(ensure_stockagg(start_day))
    end_agg = asyncio.run(ensure_stockagg(end_day))

    month_lines = [KL(r) for r in json.load(open(CACHE / f"klines_{ry:04d}-{rm:02d}.json"))]
    hist_lines = []
    for yy, mm in hist_months:
        hist_lines += [KL(r) for r in json.load(open(CACHE / f"klines_{yy:04d}-{mm:02d}.json"))]

    win = (f"Kun data til og med utgangen av {esc(mnd)} {ry}.")
    om_body = f"""<div class="note"><strong>To rapporter.</strong> K1 m&aring;ler
  avkastningen p&aring; varekapitalen (BF pr innkj&oslash;pskrone, sell-through,
  GMROI); K2 gir sporbarhet p&aring; returer uten &aring; belaste den som
  utf&oslash;rer dem. Definert av Petter, bygget p&aring; kassasystemets
  varelinjer og lager-snapshots. Bruk menyen &oslash;verst.</div>"""
    page = ("<title>H&oslash;yer Nye KPI &mdash; H&Oslash;YER-kjeden</title>\n"
            f"<style>{LR.CSS}\n"
            ".k{color:var(--stone);font-size:.82em;margin-left:1px;}\n"
            "body.paged .part{display:none}body.paged .part.active{display:block}\n"
            "</style>\n"
            + part("om", "K", "H&oslash;yer Nye KPI", win, om_body)
            + part("k1", "K1", "Kapitalavkastning varer", win,
                   build_k1(month_lines, start_agg, end_agg, mnd, ry))
            + part("k2", "K2", "Returer og returgrad", win,
                   build_k2(month_lines, hist_lines, mnd, ry))
            + f"<script>{LR.SORT_JS}</script>"
            + """<script>{
const parts=[...document.querySelectorAll('.part')];
const ids=new Set(parts.map(p=>p.id));
function show(){
  const want=location.hash.slice(1);
  const id=ids.has(want)?want:parts[0].id;
  parts.forEach(p=>p.classList.toggle('active',p.id===id));
  window.scrollTo({top:0,behavior:'auto'});
}
document.body.classList.add('paged');show();
addEventListener('hashchange',show);
}</script>""")
    if not page.isascii():
        bad = next(ch for ch in page if ord(ch) > 127)
        raise SystemExit(f"non-ASCII output: {bad!r}")
    out = pathlib.Path(args.out)
    out.write_text(page, encoding="ascii")
    print(f"  wrote {out} ({out.stat().st_size:,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
