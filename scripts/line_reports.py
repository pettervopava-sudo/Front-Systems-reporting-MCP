#!/usr/bin/env python3
"""HØYER report suite — the line-data sections of the monthly report deck.

Generates reports 03–08 of the suite (Omsetning og BF, Sesonger, Rabatter,
Merker, Selgere, Diverse) from Saleslines, fetched via the endpoint's
own from/to window parameters (full history back to 2017).
Report 01 (månedsrapporten) comes from monthly_report.py.

All revenue is SUM(Qty * Price); Price is a unit price and returns carry
Qty = -1. BF er nettobasert: SUM(Qty * Price)/1.25 - SUM(Qty * Cost).
Rabatt = SUM(Qty * Discount).
Customer fields are never fetched. Output is pure ASCII.

Usage:
  python3 scripts/line_reports.py --from 2026-08-01 --to 2026-08-12 \
      [--outdir reports]
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

from front_systems_mcp.client import FrontSystemsClient  # noqa: E402
from front_systems_mcp.config import load_config  # noqa: E402
from front_systems_mcp.odata import date_range  # noqa: E402

SELECT = ["SALESLINEID", "SALEID", "STOREID_FK", "STOCKID_FK", "SaleDate",
          "Qty", "Price", "FullPrice", "Discount", "Cost", "Currency",
          "Brand", "Group", "Name", "SizeLabel", "Employee", "Season",
          "Gender", "Store", "Stock"]

#: Stock -> deck store name. Taxonomy (user-confirmed): Online Frontend (183)
#: is the CHAIN webstore ('Hoyer Webshop'); the Shopify stocks are separate
#: webstores OWNED BY the physical store of the same name and are merged into
#: it, as the chain's own report does. Strommen Treasure is a Strommen unit,
#: not a webstore.
STOCK_STORE = {
    146: "Høyer Arendal", 444: "Høyer Bodø", 157: "Høyer Byporten",
    145: "Høyer Grimstad", 2175: "Høyer Gulskogen",
    2239: "Høyer Harstad", 2794: "Høyer Harstad",
    213: "Høyer Haugesund", 3856: "Høyer Kvadrat", 3229: "Høyer Paleet",
    1584: "Høyer Kvadrat",  # forgjengerenheten paa Kvadrat (dimensjonsnavn i
                            # dag "Fashion Outlet"; deckets rad "tidl Collabs").
                            # Bevist av deckets YTD-tabell 2024: 3856+1584
                            # treffer Kvadrat-raden og kjedetotalen paa kronen.
                            # Collabs LAGUNEN (1642) er en annen, nedlagt butikk.

    2100: "Høyer Sandefjord", 148: "Høyer Sjølyst", 153: "Høyer Solsiden",
    144: "Høyer Sjølyst",  # tidl. "Sjoelyst herre", slaatt sammen; handlet t.o.m. 2024

    203: "Høyer Sørlandssenteret", 193: "Høyer Stadionparken",
    150: "Høyer Storo", 151: "Høyer Strømmen", 5368: "Høyer Strømmen",
    181: "Høyer Trondheim", 2014: "Høyer Trondheim", 183: "Høyer Webshop",
    279: "Høyer Bergen",  # linjedata-stock; avviklet 2026-08-01, med t.o.m. juli
    # Kanal-enheter som deckets BI teller under moderbutikken -- bevist mot
    # BF-i-kroner-siden 2023 (alle fire paa 0,1M): "CR"-enhetene (senere
    # IKKE BRUK) og Paleet Zalando. Bodoe 2 (3456) og Byporten Herre (2559)
    # er bevist UTENFOR (decket matcher uten dem).
    2841: "Høyer Bergen",    # IKKE BRUK Hoeyer CR Bergen
    3589: "Høyer Paleet",    # Hoeyer Paleet Zalando
    2888: "Høyer Strømmen",  # IKKE BRUK Hoeyer CR Stroemmen
    3002: "Høyer Sjølyst",   # IKKE BRUK Hoeyer CR Sjoelyst dame
    3003: "Høyer Sjølyst",   # IKKE BRUK Hoeyer CR Sjoelyst herre
}
EXCLUDED_STOCKS = {1333, 1901, 2957, 5442}  # BMB + Outlet Nydalen + Teststore
SYSTEM_SELLERS = {"webshop", "shopify integrasjon", "shopify", "integrasjon"}
SPECIFIC_BRANDS = ["Polo Ralph Lauren", "By Malene Birger", "NN.07"]
SELLER_MIN_TRANS = 40  # default for short windows; use --seller-min 100 for full months

MND = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
       "august", "september", "oktober", "november", "desember"]

SUITE = [
    ("01_Manedsrapport_juli_2026.html", "M&aring;nedsrapport juli"),
    ("02_Oppsummering.html", "Oppsummering"),
    ("03_Omsetning_og_BF.html", "Omsetning og BF"),
    ("04_Sesonger.html", "Sesonger"),
    ("05_Rabatter.html", "Rabatter"),
    ("06_Merker.html", "Merker"),
    ("07_Selgere.html", "Selgere"),
    ("08_Diverse.html", "Diverse"),
]


def esc(s) -> str:
    return html.escape(str(s), quote=True).encode("ascii", "xmlcharrefreplace").decode()


def nf(n) -> str:
    return f"{round(n):,}".replace(",", " ")


def p1(x) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


CSS = """
:root{--paper:#F7F6F3;--ink:#1C1B1E;--ink2:#4C4A46;--stone:#8B8880;--hair:#E3E1DC;
  --hair2:#EDEBE6;--ox:#A03A3F;--ox-soft:rgba(160,58,63,.10);--slate:#3A6EA5;
  --serif:"Didot","Bodoni 72","Playfair Display",Georgia,serif;
  --sans:"Helvetica Neue",Helvetica,"Segoe UI",Arial,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;}
@media (prefers-color-scheme:dark){:root{--paper:#151417;--ink:#EBE9E4;--ink2:#C6C3BC;
  --stone:#8F8C86;--hair:#312F33;--hair2:#232226;--ox:#C15055;--ox-soft:rgba(193,80,85,.16);
  --slate:#5B8FC9;}}
:root[data-theme="dark"]{--paper:#151417;--ink:#EBE9E4;--ink2:#C6C3BC;--stone:#8F8C86;
  --hair:#312F33;--hair2:#232226;--ox:#C15055;--ox-soft:rgba(193,80,85,.16);--slate:#5B8FC9;}
:root[data-theme="light"]{--paper:#F7F6F3;--ink:#1C1B1E;--ink2:#4C4A46;--stone:#8B8880;
  --hair:#E3E1DC;--hair2:#EDEBE6;--ox:#A03A3F;--ox-soft:rgba(160,58,63,.10);--slate:#3A6EA5;}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5;
  -webkit-font-smoothing:antialiased;}
.wrap{max-width:1120px;margin:0 auto;padding:44px 26px 88px;display:flex;
  flex-direction:column;gap:44px;}
@media (max-width:640px){.wrap{padding:26px 14px 60px;gap:32px;}}
.suite{display:flex;flex-wrap:wrap;gap:2px 18px;font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--stone);border-bottom:1px solid var(--hair);
  padding:10px 0 12px;position:sticky;top:0;z-index:40;background:var(--paper);}
.suite a{color:var(--stone);text-decoration:none;}
.suite a:hover,.suite a:focus-visible{color:var(--ox);outline:none;}
.suite .cur{color:var(--ink);font-weight:600;border-bottom:2px solid var(--ox);
  padding-bottom:2px;}
.mast{display:flex;align-items:baseline;gap:26px;border-bottom:2px solid var(--ink);
  padding-bottom:24px;flex-wrap:wrap;}
.no{font-family:var(--serif);font-size:clamp(64px,9vw,104px);line-height:.8;
  color:var(--ox);font-weight:400;}
.mast h1{font-family:var(--serif);font-weight:400;font-size:clamp(30px,4.6vw,52px);
  line-height:1.02;letter-spacing:.01em;margin:0;text-wrap:balance;}
.mast .kicker{font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--stone);font-weight:600;margin-bottom:8px;}
.mast .win{font-size:13.5px;color:var(--ink2);margin-top:10px;max-width:64ch;}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;}
section{display:flex;flex-direction:column;gap:12px;}
.shead{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
  flex-wrap:wrap;border-bottom:1px solid var(--ink);padding-bottom:7px;}
.shead h2{font-size:14px;font-weight:700;letter-spacing:.02em;margin:0;
  text-transform:uppercase;}
.shead p{margin:0;font-size:12.5px;color:var(--stone);max-width:56ch;}
table{width:100%;border-collapse:collapse;font-size:13.5px;}
th{text-align:left;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--stone);font-weight:600;padding:0 12px 6px 0;white-space:nowrap;
  border-bottom:1px solid var(--hair);}
th.r,td.r{text-align:right;padding-left:16px;}
th:last-child,td:last-child{padding-right:0;}
td{padding:6px 10px 6px 0;border-bottom:1px solid var(--hair2);}
td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap;}
tr.total td{font-weight:700;border-top:1px solid var(--ink);border-bottom:none;}
tr.na td{color:var(--stone);font-size:12.5px;font-style:italic;}
td.mlabel{font-weight:700;vertical-align:top;}
th.grp{border-bottom:none;padding-bottom:2px;color:var(--ink2);}
.tw{overflow-x:auto;}
.track{position:relative;height:6px;background:var(--hair2);border-radius:1px;
  min-width:54px;}
.fill{position:absolute;inset:0 auto 0 0;background:var(--ox);border-radius:1px;}
.fill.alt{background:var(--slate);}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  border-top:1px solid var(--ink);border-bottom:1px solid var(--hair);}
.kpi{padding:16px 18px 16px 0;display:flex;flex-direction:column;gap:4px;}
.kpi .l{font-size:10px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--stone);font-weight:600;}
.kpi .v{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:23px;
  font-weight:600;letter-spacing:-.02em;}
.kpi .f{font-size:12px;color:var(--stone);}
.kpi.lead .v{color:var(--ox);}
.note{border-left:2px solid var(--ox);padding:4px 0 4px 18px;font-size:13.5px;
  color:var(--ink2);max-width:78ch;}
.note strong{color:var(--ink);}
.legend{display:flex;gap:16px;font-size:12px;color:var(--ink2);}
.legend span{display:inline-flex;align-items:center;gap:6px;}
.sw{width:10px;height:10px;border-radius:1px;display:inline-block;}
.plot{border:1px solid var(--hair);padding:16px 14px 8px;overflow-x:auto;}
svg{display:block;max-width:100%;height:auto;}
.grid line{stroke:var(--hair2);stroke-width:1;}
.axis text{font-family:var(--mono);font-size:10.5px;fill:var(--stone);}
.bar{cursor:pointer;}
.bar:focus-visible{outline:2px solid var(--ox);outline-offset:2px;}
.two{display:grid;grid-template-columns:1fr 1fr;gap:30px;}
@media (max-width:840px){.two{grid-template-columns:1fr;}}
footer{border-top:1px solid var(--hair);padding-top:16px;font-size:12.5px;
  color:var(--stone);display:flex;flex-direction:column;gap:6px;}
code{font-family:var(--mono);font-size:.92em;background:var(--ox-soft);
  padding:1px 5px;border-radius:2px;color:var(--ink);}
#tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;z-index:50;
  background:var(--ink);color:var(--paper);padding:8px 11px;border-radius:2px;
  font-size:12px;font-family:var(--mono);font-variant-numeric:tabular-nums;
  white-space:pre;box-shadow:0 4px 14px rgba(0,0,0,.25);}
@media (prefers-reduced-motion:reduce){*{transition:none!important;}}
"""


def _logo_css() -> str:
    """Brand mark as theme tokens: ink variant on paper, paper variant on dark.

    Built from the original mark (never redrawn); both variants share the
    exact same alpha shape. Empty string if the assets are missing, so the
    reports still build without them.
    """
    import base64
    a = pathlib.Path(__file__).parent / "assets"
    try:
        ink = base64.b64encode((a / "hoyer_logo_ink.png").read_bytes()).decode()
        pap = base64.b64encode((a / "hoyer_logo_paper.png").read_bytes()).decode()
    except FileNotFoundError:
        return ""
    u = "url(data:image/png;base64,"
    return (
        f":root{{--logo:{u}{ink})}}"
        f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"])'
        f"{{--logo:{u}{pap})}}}}"
        f':root[data-theme="dark"]{{--logo:{u}{pap})}}'
        f':root[data-theme="light"]{{--logo:{u}{ink})}}'
        ".brand{width:132px;aspect-ratio:300/210;flex-shrink:0;"
        "background:var(--logo) center/contain no-repeat;margin-left:auto;"
        "align-self:center;}"
        "@media (max-width:640px){.brand{width:92px}}"
    )



SORT_CSS = """
th[data-srt]{cursor:pointer;user-select:none;}
th[data-srt]:hover,th[data-srt]:focus-visible{color:var(--ox);outline:none;}
th.s-asc:after{content:" \\2191";color:var(--ox);}
th.s-desc:after{content:" \\2193";color:var(--ox);}
text.chead{cursor:pointer;}
text.chead:hover{fill:var(--ox);}
text.chead.s-on{fill:var(--ox);font-weight:700;}
"""

#: Klikk paa kolonneoverskrift sorterer tabellen (norsk tallformat, k/M/%-
#: suffiks, minustegn og datoer forstaas; strek sorterer sist; rader med
#: class 'total' ligger fast nederst). Tabeller med rowspan (matriser)
#: hoppes over -- radene der er grupper, ikke enkeltlinjer.
SORT_JS = """
(function(){
function parseVal(t){
  t=(t||"").replace(/\\u00a0/g," ").trim();
  if(!t||t==="\\u2013"||t==="-"||t==="\\u2014")return{n:null,t:""};
  if(/^\\d{4}-\\d{2}/.test(t))return{n:null,t:t};
  var x=t.replace(/\\u2212/g,"-").replace(/[%kM]/g,"").replace(/ /g,"")
         .replace(/,/g,".").replace(/\\.(?=.*\\.)/g,"");
  var n=parseFloat(x);
  if(isFinite(n)&&/[0-9]/.test(x))return{n:n,t:t.toLowerCase()};
  return{n:null,t:t.toLowerCase()};
}
function sortTable(tb,th,ci){
  var body=tb.tBodies[0];if(!body)return;
  var rows=[].slice.call(body.rows);
  var pin=rows.filter(function(r){return r.className.indexOf("total")>=0;});
  var sortable=rows.filter(function(r){return r.className.indexOf("total")<0;});
  var dir=th.classList.contains("s-asc")?-1:1;
  var hdr=th.parentNode.cells;
  for(var i=0;i<hdr.length;i++){hdr[i].classList.remove("s-asc","s-desc");
    hdr[i].removeAttribute("aria-sort");}
  th.classList.add(dir===1?"s-asc":"s-desc");
  th.setAttribute("aria-sort",dir===1?"ascending":"descending");
  var vals=sortable.map(function(r,i){
    var c=r.cells[ci];return{i:i,r:r,v:parseVal(c?c.textContent:"")};});
  var nums=vals.filter(function(x){return x.v.n!==null;}).length;
  var numeric=nums>=vals.length/2&&nums>0;
  vals.sort(function(a,b){
    var x=a.v,y=b.v;
    if(numeric){
      if(x.n===null&&y.n===null)return a.i-b.i;
      if(x.n===null)return 1;
      if(y.n===null)return -1;
      return dir*(x.n-y.n)||a.i-b.i;
    }
    if(!x.t&&!y.t)return a.i-b.i;
    if(!x.t)return 1;
    if(!y.t)return -1;
    return dir*x.t.localeCompare(y.t,"no")||a.i-b.i;
  });
  vals.forEach(function(x){body.appendChild(x.r);});
  pin.forEach(function(r){body.appendChild(r);});
}
[].slice.call(document.querySelectorAll("table")).forEach(function(tb){
  if(tb.querySelector("[rowspan]"))return;
  if(!tb.tHead||!tb.tHead.rows.length)return;
  var leaf=tb.tHead.rows[tb.tHead.rows.length-1];
  [].slice.call(leaf.cells).forEach(function(th,ci){
    if(th.dataset.srt)return;
    th.dataset.srt="1";
    th.setAttribute("tabindex","0");
    th.setAttribute("title","Sorter");
    th.addEventListener("click",function(){sortTable(tb,th,ci);});
    th.addEventListener("keydown",function(ev){
      if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();sortTable(tb,th,ci);}
    });
  });
});
})();
"""


#: Klikk paa kolonnetitlene i butikkgrafene (Brutto omsetning / BF % /
#: BF i kroner under hvert aar) omsorterer radgruppene (g.srow) vertikalt;
#: rader med class 'pin' (Nedlagte / Snitt / Kjeden) ligger fast nederst.
CHART_SORT_JS = """
(function(){
[].slice.call(document.querySelectorAll("svg")).forEach(function(svg){
  var rows=[].slice.call(svg.querySelectorAll("g.srow"));
  if(!rows.length||svg.dataset.cs)return;
  svg.dataset.cs="1";
  var ys=rows.map(function(g){
    return parseFloat(/translate\\(0,([0-9.]+)\\)/.exec(g.getAttribute("transform"))[1]);
  }).sort(function(a,b){return a-b;});
  function val(g,ci){
    var v=(g.getAttribute("data-vals")||"").split("|")[ci];
    return v===""||v===undefined?null:parseFloat(v);
  }
  var heads=[].slice.call(svg.querySelectorAll("text.chead"));
  heads.forEach(function(h){
    h.addEventListener("click",function(){
      var ci=+h.getAttribute("data-ci");
      var desc=h.getAttribute("data-dir")!=="desc";
      heads.forEach(function(x){x.removeAttribute("data-dir");
        x.classList.remove("s-on");});
      h.setAttribute("data-dir",desc?"desc":"asc");h.classList.add("s-on");
      var mov=rows.filter(function(g){return !g.classList.contains("pin");});
      var pin=rows.filter(function(g){return g.classList.contains("pin");});
      mov.sort(function(a,b){
        var x=val(a,ci),y=val(b,ci);
        if(x===null&&y===null)return 0;
        if(x===null)return 1;
        if(y===null)return -1;
        return desc?y-x:x-y;
      });
      mov.concat(pin).forEach(function(g,i){
        g.setAttribute("transform","translate(0,"+ys[i]+")");});
    });
  });
});
})();
"""

LOGO_CSS = _logo_css()
CSS = CSS + LOGO_CSS + SORT_CSS

TIP_JS = """
const tip=document.getElementById("tip");
function bind(node,text){
  const show=ev=>{tip.textContent=text;tip.style.opacity="1";
    tip.style.left=Math.min(ev.clientX+14,innerWidth-tip.offsetWidth-10)+"px";
    tip.style.top=Math.max(ev.clientY-tip.offsetHeight-12,8)+"px";};
  node.addEventListener("mousemove",show);node.addEventListener("mouseenter",show);
  node.addEventListener("mouseleave",()=>tip.style.opacity="0");
  node.setAttribute("tabindex","0");node.setAttribute("role","img");
  node.setAttribute("aria-label",text.replace(/\\n/g,". "));
}
"""


def set_suite_month(ry, rm):
    """Point the suite nav's first entry at the edition's report 01."""
    mnd = MND[rm - 1]
    SUITE[0] = (f"01_Manedsrapport_{mnd}_{ry}.html",
                f"M&aring;nedsrapport {mnd}")


NAV_SUBSET = None  # set by build_all(only=...) so subset builds self-link only


def suite_nav(current: str) -> str:
    parts = []
    entries = ([(f, t) for f, t in SUITE if f[:2] in NAV_SUBSET]
               if NAV_SUBSET else SUITE)
    for fname, title in entries:
        if fname == current:
            parts.append(f'<span class="cur">{title}</span>')
        else:
            parts.append(f'<a href="{fname}">{title}</a>')
    return f'<nav class="suite">{"".join(parts)}</nav>'


def page(current, no, title, window_note, body, foot_extra="") -> str:
    return f"""<title>{title} &mdash; H&Oslash;YER-kjeden</title>
<style>{CSS}</style>
<div class="wrap">
{suite_nav(current)}
<header class="mast">
  <div class="no">{no}</div>
  <div>
    <div class="kicker">H&Oslash;YER-kjeden &middot; rapportserie</div>
    <h1>{title}</h1>
    <div class="win">{window_note}</div>
  </div>
  <div class="brand" role="img" aria-label="H&Oslash;yer"></div>
</header>
{body}
<footer>
  <div>Omsetning er <code>SUM(Qty &times; Price)</code> (inkl. mva); BF er
    nettobasert: <code>SUM(Qty &times; Price)/1,25 &minus; SUM(Qty &times; Cost)</code>; returer (Qty = &minus;1)
    trekkes fra. BMB er utelatt, som i m&aring;nedsrapporten. Kundedata hentes ikke.</div>
  {foot_extra}
  <div>Rapportserie H&Oslash;YER-kjeden &middot; <code>scripts/line_reports.py</code></div>
</footer>
</div>
<script>{SORT_JS}</script>
<script>{CHART_SORT_JS}</script>
<div id="tip" role="status" aria-live="polite"></div>
"""


class L:
    """One sale line with derived figures."""
    __slots__ = ("store", "qty", "rev", "cogs", "bf", "rab", "full", "brand",
                 "group", "name", "season", "gender", "emp", "sale", "date")

    def __init__(self, r):
        self.store = STOCK_STORE.get(r["STOCKID_FK"])
        q = float(r["Qty"] or 0)
        self.qty = q
        self.rev = round(q * float(r["Price"] or 0), 2)
        self.cogs = round(q * float(r["Cost"] or 0), 2)
        # BF er nettobasert: Price er inkl. mva, Cost eks. mva -- validert
        # mot juni-deckets BF (avvik 0,2 %). BF% regnes av netto.
        self.bf = round(self.rev / 1.25 - self.cogs, 2)
        self.rab = round(q * float(r["Discount"] or 0), 2)
        self.full = round(q * float(r["FullPrice"] or 0), 2)
        self.brand = r.get("Brand") or "(ukjent)"
        self.group = r.get("Group") or "(ukjent)"
        self.name = r.get("Name") or ""
        s = str(r.get("Season") or "").strip()
        self.season = s if s not in ("", "0", "1", "None") else "Basis / uspesifisert"
        self.gender = (r.get("Gender") or "").strip().lower()
        self.emp = (r.get("Employee") or "").strip()
        self.sale = r["SALEID"]
        self.date = r["SaleDate"][:10]


def agg(lines, key):
    out = collections.defaultdict(lambda: {"rev": 0.0, "bf": 0.0, "rab": 0.0,
                                           "full": 0.0, "qty": 0.0, "sales": set()})
    for ln in lines:
        k = key(ln)
        if k is None:
            continue
        a = out[k]
        a["rev"] += ln.rev; a["bf"] += ln.bf; a["rab"] += ln.rab
        a["full"] += ln.full; a["qty"] += ln.qty; a["sales"].add(ln.sale)
    return out


def bfp(a) -> float:
    netto = a["rev"] / 1.25
    return a["bf"] / netto * 100 if netto else 0.0


def store_table(rows, maxrev, cols=("rev", "bf", "bfp", "trans", "snitt", "ppk")):
    body = ""
    for name, a in rows:
        t = len(a["sales"])
        body += (f"<tr><td>{esc(name)}</td>"
                 f"<td><div class='track'><div class='fill' "
                 f"style='width:{a['rev']/maxrev*100:.1f}%'></div></div></td>"
                 f"<td class='num r'>{nf(a['rev'])}</td>"
                 f"<td class='num r'>{nf(a['bf'])}</td>"
                 f"<td class='num r'>{esc(p1(bfp(a)))}</td>"
                 f"<td class='num r'>{nf(t)}</td>"
                 f"<td class='num r'>{nf(a['rev']/t) if t else '&ndash;'}</td>"
                 f"<td class='num r'>{a['qty']/t:.2f}".replace(".", ",") + "</td></tr>")
    return body


def build_all(lines, window_note, outdir, only=None):
    global NAV_SUBSET
    NAV_SUBSET = only
    chain = agg(lines, lambda l: "kjede")["kjede"]
    per_store = sorted(agg(lines, lambda l: l.store).items(),
                       key=lambda kv: -kv[1]["rev"])
    maxrev = per_store[0][1]["rev"]
    files = {}

    # ---- 02 Omsetning og BF ------------------------------------------------
    t = len(chain["sales"])
    kpis = f"""<div class="kpis">
      <div class="kpi lead"><span class="l">Brutto omsetning</span>
        <span class="v">{nf(chain['rev'])}</span><span class="f">NOK inkl. mva</span></div>
      <div class="kpi"><span class="l">BF i kroner</span>
        <span class="v">{nf(chain['bf'])}</span><span class="f">{esc(p1(bfp(chain)))} av omsetning</span></div>
      <div class="kpi"><span class="l">Transaksjoner</span>
        <span class="v">{nf(t)}</span><span class="f">{nf(chain['rev']/t)} snittkj&oslash;p</span></div>
      <div class="kpi"><span class="l">Solgte plagg</span>
        <span class="v">{nf(chain['qty'])}</span><span class="f">{chain['qty']/t:.2f} plagg pr kunde</span></div>
    </div>""".replace(f"{chain['qty']/t:.2f}", f"{chain['qty']/t:.2f}".replace(".", ","))
    rows = store_table(per_store, maxrev)
    chart_data = json.dumps([{"k": n.replace("Høyer ", ""), "r": round(a["rev"]),
                              "b": round(a["bf"])} for n, a in per_store],
                            ensure_ascii=True)
    body = f"""{kpis}
<section><div class="shead"><h2>Omsetning og BF pr butikk</h2>
  <p>Shopify-salg er sl&aring;tt sammen med moderbutikken. Bergen ble avviklet
     01.08.2026 og inng&aring;r derfor ikke.</p></div>
<div class="legend"><span><i class="sw" style="background:var(--ox)"></i>Omsetning</span>
  <span><i class="sw" style="background:var(--slate)"></i>BF i kroner</span></div>
<div class="plot"><svg id="c" viewBox="0 0 1080 320" role="img"
  aria-label="Omsetning og BF pr butikk"></svg></div>
<div class="tw"><table>
  <thead><tr><th>Butikk</th><th style="width:12%">Andel</th>
    <th class="r">Omsetning</th><th class="r">BF kr</th><th class="r">BF %</th>
    <th class="r">Trans</th><th class="r">Snitt</th><th class="r">PPK</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>
<script>
const D={chart_data};{TIP_JS}
const NS="http://www.w3.org/2000/svg";
const el=(t,a={{}})=>{{const e=document.createElementNS(NS,t);
  for(const k in a)e.setAttribute(k,a[k]);return e;}};
const nfj=n=>Math.round(n).toLocaleString("en-US").replace(/,/g," ");
(function(){{
  const svg=document.getElementById("c"),W=1080,H=320,P={{l:56,r:10,t:12,b:74}};
  const max=Math.max(...D.map(d=>d.r))*1.08,iw=W-P.l-P.r,bw=iw/D.length,
        w=Math.min(15,bw/2-4),base=H-P.b,ih=H-P.t-P.b;
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
    [[d.r,"var(--ox)","omsetning",-w-1],[d.b,"var(--slate)","BF",1]].forEach(([v,f,l,off])=>{{
      const h=ih*v/max,grp=el("g",{{class:"bar"}});
      grp.appendChild(el("rect",{{x:cx+off,y:base-h,width:w,height:Math.max(h,1),fill:f}}));
      svg.appendChild(grp);bind(grp,`${{d.k}}\\n${{l}} ${{nfj(v)}} kr`);}});
  }});
  svg.appendChild(ax);
}})();
</script>"""
    files["03_Omsetning_og_BF.html"] = page(
        "03_Omsetning_og_BF.html", "03", "Omsetning og bruttofortjeneste",
        window_note, body)

    # ---- 03 Sesonger -------------------------------------------------------
    per_season = sorted(agg(lines, lambda l: l.season).items(),
                        key=lambda kv: -kv[1]["rev"])
    maxs = per_season[0][1]["rev"]
    rows = "".join(
        f"<tr><td>{esc(k)}</td>"
        f"<td><div class='track'><div class='fill' "
        f"style='width:{a['rev']/maxs*100:.1f}%'></div></div></td>"
        f"<td class='num r'>{nf(a['rev'])}</td><td class='num r'>{nf(a['bf'])}</td>"
        f"<td class='num r'>{esc(p1(bfp(a)))}</td>"
        f"<td class='num r'>{nf(a['qty'])}</td>"
        f"<td class='num r'>{esc(p1(a['rev']/chain['rev']*100))}</td></tr>"
        for k, a in per_season)
    body = f"""<div class="note"><strong>Lesing.</strong> Sesongkodene kommer fra
  varelinjene (f.eks. 2601&nbsp;Main = hovedkolleksjon v&aring;r/sommer 2026,
  Pre = pre-kolleksjon). Basisvarer og varer uten sesongmerking er samlet i
  &laquo;Basis / uspesifisert&raquo;.</div>
<section><div class="shead"><h2>Salg og BF fordelt p&aring; sesong</h2></div>
<div class="tw"><table>
  <thead><tr><th>Sesong</th><th style="width:14%">Andel</th>
    <th class="r">Omsetning</th><th class="r">BF kr</th><th class="r">BF %</th>
    <th class="r">Plagg</th><th class="r">Andel oms.</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>"""
    files["04_Sesonger.html"] = page(
        "04_Sesonger.html", "04", "Sesonger", window_note, body)

    # ---- 04 Rabatter -------------------------------------------------------
    disc_lines = [l for l in lines if l.rab > 0]
    seasonal = [l for l in lines if l.season != "Basis / uspesifisert"]
    base = [l for l in lines if l.season == "Basis / uspesifisert"]
    a_se = agg(seasonal, lambda l: "x").get("x", collections.defaultdict(float))
    a_ba = agg(base, lambda l: "x").get("x", collections.defaultdict(float))
    rabp = chain["rab"] / chain["full"] * 100 if chain["full"] else 0
    per_store_r = sorted(agg(lines, lambda l: l.store).items(),
                         key=lambda kv: -kv[1]["rab"])
    maxr = per_store_r[0][1]["rab"] or 1
    rows = "".join(
        f"<tr><td>{esc(n)}</td>"
        f"<td><div class='track'><div class='fill' "
        f"style='width:{a['rab']/maxr*100:.1f}%'></div></div></td>"
        f"<td class='num r'>{nf(a['rab'])}</td>"
        f"<td class='num r'>{esc(p1(a['rab']/a['full']*100 if a['full'] else 0))}</td>"
        f"<td class='num r'>{nf(a['rev'])}</td>"
        f"<td class='num r'>{esc(p1(bfp(a)))}</td></tr>"
        for n, a in per_store_r)
    sb_rows = ""
    for label, a in (("Sesongvarer", a_se), ("Basis / uspesifisert", a_ba)):
        if not a.get("rev"):
            continue
        sb_rows += (f"<tr><td>{esc(label)}</td>"
                    f"<td class='num r'>{nf(a['rev'])}</td>"
                    f"<td class='num r'>{nf(a['rab'])}</td>"
                    f"<td class='num r'>{esc(p1(a['rab']/a['full']*100 if a['full'] else 0))}</td>"
                    f"<td class='num r'>{esc(p1(bfp(a)))}</td></tr>")
    body = f"""<div class="kpis">
  <div class="kpi lead"><span class="l">Rabatt i kroner</span>
    <span class="v">{nf(chain['rab'])}</span><span class="f">av {nf(chain['full'])} i fullpris</span></div>
  <div class="kpi"><span class="l">Rabatt i %</span>
    <span class="v">{esc(p1(rabp))}</span><span class="f">av fullpris</span></div>
  <div class="kpi"><span class="l">Linjer med rabatt</span>
    <span class="v">{nf(len(disc_lines))}</span><span class="f">av {nf(len(lines))} linjer</span></div>
  <div class="kpi"><span class="l">BF % etter rabatt</span>
    <span class="v">{esc(p1(bfp(chain)))}</span><span class="f">kjeden samlet</span></div>
</div>
<section><div class="shead"><h2>Rabatt pr butikk</h2></div>
<div class="tw"><table>
  <thead><tr><th>Butikk</th><th style="width:13%">Andel</th>
    <th class="r">Rabatt kr</th><th class="r">Rabatt %</th>
    <th class="r">Omsetning</th><th class="r">BF %</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>
<section><div class="shead"><h2>Sesongvarer mot basisvarer</h2>
  <p>Skillet f&oslash;lger sesongkoden p&aring; varelinjen.</p></div>
<div class="tw"><table>
  <thead><tr><th>Varetype</th><th class="r">Omsetning</th>
    <th class="r">Rabatt kr</th><th class="r">Rabatt %</th><th class="r">BF %</th></tr></thead>
  <tbody>{sb_rows}</tbody></table></div></section>
<div class="note"><strong>Historikk.</strong> Rabatt gitt i % siste 3 &aring;r
  (som i m&aring;nedsrapporten) kan f&oslash;rst lages n&aring;r linjedata har
  bygget seg opp &mdash; API-et inneholder linjer fra 01.08.2026.</div>"""
    files["05_Rabatter.html"] = page(
        "05_Rabatter.html", "05", "Rabatter", window_note, body)

    # ---- 05 Merker ---------------------------------------------------------
    per_brand = sorted(agg(lines, lambda l: l.brand).items(),
                       key=lambda kv: -kv[1]["rev"])[:20]
    maxb = per_brand[0][1]["rev"]
    rows = "".join(
        f"<tr><td>{esc(b)}</td>"
        f"<td><div class='track'><div class='fill' "
        f"style='width:{a['rev']/maxb*100:.1f}%'></div></div></td>"
        f"<td class='num r'>{nf(a['rev'])}</td><td class='num r'>{nf(a['bf'])}</td>"
        f"<td class='num r'>{esc(p1(bfp(a)))}</td>"
        f"<td class='num r'>{nf(a['qty'])}</td>"
        f"<td class='num r'>{esc(p1(a['rab']/a['full']*100 if a['full'] else 0))}</td></tr>"
        for b, a in per_brand)
    spec = ""
    for brand in SPECIFIC_BRANDS:
        blines = [l for l in lines if l.brand == brand]
        if not blines:
            spec += (f"<section><div class='shead'><h2>{esc(brand)}</h2></div>"
                     f"<p class='note'>Ingen salg i perioden.</p></section>")
            continue
        pb = sorted(agg(blines, lambda l: l.store).items(),
                    key=lambda kv: -kv[1]["rev"])
        mx = pb[0][1]["rev"]
        btot = agg(blines, lambda l: "x")["x"]
        brows = "".join(
            f"<tr><td>{esc(n)}</td>"
            f"<td><div class='track'><div class='fill alt' "
            f"style='width:{a['rev']/mx*100:.1f}%'></div></div></td>"
            f"<td class='num r'>{nf(a['rev'])}</td><td class='num r'>{nf(a['bf'])}</td>"
            f"<td class='num r'>{esc(p1(bfp(a)))}</td>"
            f"<td class='num r'>{nf(a['qty'])}</td></tr>"
            for n, a in pb)
        spec += f"""<section><div class="shead"><h2>{esc(brand)} pr butikk</h2>
  <p>{nf(btot['rev'])} kr &middot; BF {esc(p1(bfp(btot)))} &middot; {nf(btot['qty'])} plagg</p></div>
<div class="tw"><table>
  <thead><tr><th>Butikk</th><th style="width:13%">Andel</th>
    <th class="r">Omsetning</th><th class="r">BF kr</th><th class="r">BF %</th>
    <th class="r">Plagg</th></tr></thead>
  <tbody>{brows}</tbody></table></div></section>"""
    body = f"""<section><div class="shead"><h2>Merker med h&oslash;yest omsetning</h2>
  <p>Topp 20 av {nf(len(set(l.brand for l in lines)))} merker i perioden.</p></div>
<div class="tw"><table>
  <thead><tr><th>Merke</th><th style="width:13%">Andel</th>
    <th class="r">Omsetning</th><th class="r">BF kr</th><th class="r">BF %</th>
    <th class="r">Plagg</th><th class="r">Rabatt %</th></tr></thead>
  <tbody>{rows}</tbody></table></div></section>
{spec}"""
    files["06_Merker.html"] = page(
        "06_Merker.html", "06", "Merker", window_note, body)

    # ---- 06 Selgere --------------------------------------------------------
    ppk_rows = store_table(per_store, maxrev)
    real = [l for l in lines
            if l.emp and l.emp.lower() not in SYSTEM_SELLERS
            and "shopify" not in l.emp.lower() and "integrasjon" not in l.emp.lower()
            and "webshop" not in l.emp.lower()]
    per_emp = agg(real, lambda l: l.emp)
    emp_store = {}
    for l in real:
        emp_store.setdefault(l.emp, collections.Counter())[l.store] += 1
    qualified = {e: a for e, a in per_emp.items()
                 if len(a["sales"]) >= SELLER_MIN_TRANS}

    def seller_rows(metric):
        rank = sorted(qualified.items(),
                      key=lambda kv: -(kv[1]["qty"] / len(kv[1]["sales"])
                                       if metric == "ppk"
                                       else kv[1]["rev"] / len(kv[1]["sales"])))[:10]
        out = ""
        for i, (e, a) in enumerate(rank):
            n = len(a["sales"])
            val = (f"{a['qty']/n:.2f}".replace(".", ",") if metric == "ppk"
                   else nf(a["rev"] / n))
            home = emp_store[e].most_common(1)[0][0] or "?"
            out += (f"<tr><td class='num'>{i+1}</td><td>{esc(e)}</td>"
                    f"<td>{esc(home)}</td><td class='num r'>{val}</td>"
                    f"<td class='num r'>{nf(n)}</td>"
                    f"<td class='num r'>{nf(a['rev'])}</td></tr>")
        return out

    def gender_rows(g):
        glines = [l for l in real if l.gender == g]
        pe = agg(glines, lambda l: l.emp)
        rank = sorted(((e, a) for e, a in pe.items() if len(a["sales"]) >= 15),
                      key=lambda kv: -(kv[1]["qty"] / len(kv[1]["sales"])))[:5]
        return "".join(
            f"<tr><td class='num'>{i+1}</td><td>{esc(e)}</td>"
            f"<td class='num r'>{f_ppk(a)}</td>"
            f"<td class='num r'>{nf(len(a['sales']))}</td></tr>"
            for i, (e, a) in enumerate(rank))

    def f_ppk(a):
        return f"{a['qty']/len(a['sales']):.2f}".replace(".", ",")

    body = f"""<section><div class="shead"><h2>Plagg pr kunde (PPK) pr butikk</h2></div>
<div class="tw"><table>
  <thead><tr><th>Butikk</th><th style="width:12%">Andel</th>
    <th class="r">Omsetning</th><th class="r">BF kr</th><th class="r">BF %</th>
    <th class="r">Trans</th><th class="r">Snitt</th><th class="r">PPK</th></tr></thead>
  <tbody>{ppk_rows}</tbody></table></div></section>
<div class="two">
<section><div class="shead"><h2>Beste selger &mdash; PPK</h2>
  <p>Minst {SELLER_MIN_TRANS} transaksjoner i perioden.</p></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Selger</th><th>Butikk</th><th class="r">PPK</th>
    <th class="r">Trans</th><th class="r">Omsetning</th></tr></thead>
  <tbody>{seller_rows("ppk")}</tbody></table></div></section>
<section><div class="shead"><h2>Beste selger &mdash; kj&oslash;p pr kunde</h2>
  <p>Snittkj&oslash;p i kroner. Samme terskel.</p></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Selger</th><th>Butikk</th><th class="r">KPK</th>
    <th class="r">Trans</th><th class="r">Omsetning</th></tr></thead>
  <tbody>{seller_rows("kpk")}</tbody></table></div></section>
</div>
<div class="two">
<section><div class="shead"><h2>Beste selger dame</h2>
  <p>Damevarer (varelinjer merket f), minst 15 transaksjoner.</p></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Selger</th><th class="r">PPK</th><th class="r">Trans</th></tr></thead>
  <tbody>{gender_rows("f")}</tbody></table></div></section>
<section><div class="shead"><h2>Beste selger herre</h2>
  <p>Herrevarer (varelinjer merket m), minst 15 transaksjoner.</p></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Selger</th><th class="r">PPK</th><th class="r">Trans</th></tr></thead>
  <tbody>{gender_rows("m")}</tbody></table></div></section>
</div>
<div class="note"><strong>Terskler.</strong> M&aring;nedsrapporten bruker minst
  100 transaksjoner pr m&aring;ned; for dette 11-dagers vinduet er terskelen
  skalert til {SELLER_MIN_TRANS}. Systembrukere (Webshop, Shopify-integrasjon)
  er holdt utenfor.</div>"""
    files["07_Selgere.html"] = page(
        "07_Selgere.html", "07", "Selgere &mdash; PPK og KPK", window_note, body)

    # ---- 07 Diverse --------------------------------------------------------
    per_sale = collections.defaultdict(lambda: {"rev": 0.0, "store": None, "date": ""})
    for l in lines:
        s = per_sale[l.sale]
        s["rev"] += l.rev; s["store"] = l.store or s["store"]; s["date"] = l.date
    singles = sorted(per_sale.values(), key=lambda s: -s["rev"])[:10]
    s_rows = "".join(
        f"<tr><td class='num'>{i+1}</td><td>{esc(s['store'] or '?')}</td>"
        f"<td class='num'>{esc(s['date'])}</td><td class='num r'>{nf(s['rev'])}</td></tr>"
        for i, s in enumerate(singles))
    per_day = sorted(agg(lines, lambda l: l.date).items(),
                     key=lambda kv: -kv[1]["rev"])[:10]
    d_rows = "".join(
        f"<tr><td class='num'>{i+1}</td><td class='num'>{esc(d)}</td>"
        f"<td>{esc(dt.date.fromisoformat(d).strftime('%A').lower())}</td>"
        f"<td class='num r'>{nf(a['rev'])}</td>"
        f"<td class='num r'>{nf(len(a['sales']))}</td></tr>"
        for i, (d, a) in enumerate(per_day))
    body = f"""<div class="two">
<section><div class="shead"><h2>H&oslash;yeste enkeltsalg</h2></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Butikk</th><th>Dato</th><th class="r">Bel&oslash;p</th></tr></thead>
  <tbody>{s_rows}</tbody></table></div></section>
<section><div class="shead"><h2>H&oslash;yeste salgsdager</h2></div>
<div class="tw"><table>
  <thead><tr><th>#</th><th>Dato</th><th>Dag</th><th class="r">Omsetning</th>
    <th class="r">Trans</th></tr></thead>
  <tbody>{d_rows}</tbody></table></div></section>
</div>
<div class="note"><strong>St&oslash;rste kunder</strong> er bevisst utelatt:
  det krever kundeidentifikatorer, og denne rapportserien henter ikke kundedata
  fra API-et. Ta det som en egen beslutning hvis behovet finnes.</div>"""
    files["08_Diverse.html"] = page(
        "08_Diverse.html", "08", "Diverse", window_note, body)

    for fname, content in files.items():
        if only and fname[:2] not in only:
            continue
        if not content.isascii():
            bad = next(ch for ch in content if ord(ch) > 127)
            raise SystemExit(f"{fname}: non-ASCII char {bad!r} — would mojibake")
        (outdir / fname).write_text(content, encoding="ascii")
        print(f"  wrote {outdir / fname}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="dfrom", required=True)
    ap.add_argument("--to", dest="dto", required=True, help="exclusive")
    ap.add_argument("--outdir", default=str(ROOT / "reports"))
    ap.add_argument("--seller-min", type=int, default=None,
                    help="min transaksjoner for beste selger (100 = deckets terskel)")
    ap.add_argument("--only", default=None,
                    help="comma-separated report numbers, e.g. 04,05,06,07")
    args = ap.parse_args()
    d0, d1 = dt.date.fromisoformat(args.dfrom), dt.date.fromisoformat(args.dto)
    last = d1 - dt.timedelta(days=1)
    if (d0.year, d0.month) == (last.year, last.month):
        set_suite_month(d0.year, d0.month)  # full-month window: suite edition
    outdir = pathlib.Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)

    async def fetch():
        c = FrontSystemsClient(load_config())
        try:
            # from/to are the endpoint's own (undocumented) window parameters;
            # without them Saleslines serves only a recent default window.
            # 'to' is inclusive, our --to is exclusive.
            last = d1 - dt.timedelta(days=1)
            return await c.fetch_raw("Saleslines", {
                "from": f"'{d0}'", "to": f"'{last}'",
                "$select": ",".join(SELECT), "$top": "2000000"})
        finally:
            await c.aclose()

    cache = ROOT / "reports" / "cache" / f"saleslines_{d0}_{d1}.json"
    if cache.exists():
        raw = json.load(open(cache))
        print(f"  loaded {len(raw)} lines from {cache.name}", file=sys.stderr)
    else:
        raw = asyncio.run(fetch())
        cache.parent.mkdir(parents=True, exist_ok=True)
        json.dump(raw, open(cache, "w"), ensure_ascii=False)
        print(f"  fetched {len(raw)} lines -> saved {cache.name}", file=sys.stderr)
    lines = [L(r) for r in raw if r["STOCKID_FK"] not in EXCLUDED_STOCKS]
    kept = [l for l in lines if l.store]
    dropped = sum(1 for l in lines if not l.store)
    if dropped:
        print(f"  note: {dropped} lines from unmapped stocks excluded", file=sys.stderr)
    last_day = (d1 - dt.timedelta(days=1)).strftime("%d.%m.%Y").lstrip("0")
    first_day = d0.strftime("%d.%m.%Y").lstrip("0")
    window_note = (f"Linjedata {first_day} &ndash; {last_day}. "
                   f"{len(kept):,} varelinjer."
                   ).replace(",", " ")
    global SELLER_MIN_TRANS
    if args.seller_min:
        SELLER_MIN_TRANS = args.seller_min
    only = set(args.only.split(",")) if args.only else None
    build_all(kept, window_note, outdir, only=only)
    ch = agg(kept, lambda l: "x")["x"]
    print(f"  chain: {ch['rev']:,.2f} rev, BF {ch['bf']:,.2f} "
          f"({bfp(ch):.1f}%), {len(ch['sales'])} trans", file=sys.stderr)


if __name__ == "__main__":
    main()
