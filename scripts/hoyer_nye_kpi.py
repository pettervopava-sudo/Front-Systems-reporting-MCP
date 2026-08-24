#!/usr/bin/env python3
"""Høyer Nye KPI — egen hovedrapporttype ved siden av månedsrapporten.

Innholdet defineres av Petter i iterasjoner (som månedsrapporten ble til).
Dette skriptet er rammeverket: suitens design (CSS, logo, mørk/lys), egen
side utenfor månedsrapport-serien (egen nav, ingen del-nummerering), og
tilgang til de samme datakildene og cachene:

  * Sales-headere (reports/cache/YYYY-MM.json, 2024->)
  * kjede-linjeaggregater (linjeagg_YYYY-MM.json, 2017->)
  * per-butikk-linjeaggregater (linjestore_YYYY-MM.json, 2023->)
  * raa Saleslines via from/to-vinduet (maks 31 dager pr kall)

Usage:
  python3 scripts/hoyer_nye_kpi.py [--out reports/Hoyer_Nye_KPI.html]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import line_reports as LR  # noqa: E402  (delt identitet: CSS, logo, esc)

esc = LR.esc


def build() -> str:
    body = """<nav class="suite"><span class="cur">H&oslash;yer Nye KPI</span></nav>
<header class="mast">
  <div class="no">K</div>
  <div>
    <div class="kicker">H&Oslash;YER-kjeden &middot; rapport</div>
    <h1>H&oslash;yer Nye KPI</h1>
    <div class="win">Ny hovedrapporttype &mdash; innholdet defineres.
      Rammeverket (design, datakilder, historikk-cacher) er klart.</div>
  </div>
  <div class="brand" role="img" aria-label="H&Oslash;yer"></div>
</header>
<div class="note"><strong>Status.</strong> Rapporten er opprettet som egen
  type ved siden av m&aring;nedsrapporten og venter p&aring; sin f&oslash;rste
  innholdsdefinisjon. Byggeklossene som allerede er validert mot
  PPT-rapporten kan gjenbrukes direkte: n&oslash;kkeltall pr butikk og kjede,
  BF og rabatter fra varelinjene (historikk til 2017), transaksjoner,
  sesonger, merker og selgere.</div>
<section><div class="shead"><h2>Definer innholdet</h2>
  <p>Si hvilke KPI-er, perioder og visninger rapporten skal ha, s&aring;
     bygges de her &mdash; samme arbeidsm&aring;te som m&aring;nedsrapporten.</p></div>
<ul style="margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px;
  font-size:13.5px;color:var(--ink2);">
  <li>Hvilke n&oslash;kkeltall? (omsetning, BF, rabatt, trans, snittkj&oslash;p, PPK &hellip;)</li>
  <li>Hvilket tidsvindu og hvilken sammenligning? (m&aring;ned, uke, rullerende, &aring;r mot &aring;r)</li>
  <li>Hvilket niv&aring;? (kjede, butikk, merke, selger)</li>
  <li>Graf, tabell eller begge?</li>
</ul></section>
<footer>
  <div>Datakilde: Front Systems (kassasystemet). Samme beregningsregler som
    m&aring;nedsrapporten: omsetning er <code>SUM(Qty &times; Price)</code>
    (inkl. mva); BF er nettobasert. Kundedata hentes ikke.</div>
  <div>H&oslash;yer Nye KPI &middot; <code>scripts/hoyer_nye_kpi.py</code></div>
</footer>"""
    return (f"<title>H&oslash;yer Nye KPI &mdash; H&Oslash;YER-kjeden</title>\n"
            f"<style>{LR.CSS}</style>\n<div class=\"wrap\">\n{body}\n</div>\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "reports" / "Hoyer_Nye_KPI.html"))
    args = ap.parse_args()
    content = build()
    if not content.isascii():
        raise SystemExit("Hoyer Nye KPI: non-ASCII output")
    out = pathlib.Path(args.out)
    out.write_text(content, encoding="ascii")
    print(f"  wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
