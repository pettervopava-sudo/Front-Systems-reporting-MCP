#!/usr/bin/env python3
"""Bind the månedsrapport suite into one PDF via headless Chrome.

Wraps each report file in a print shell (forced light theme, A4 landscape,
suite nav hidden), prints each to PDF with Chrome so the SVG charts render,
and merges the parts with pypdf. For the July 2026 edition the line-data
sections (04–07) are represented by a single explanatory page, since their
on-disk versions cover a later window and may not appear in a July document.

Usage:
  python3 scripts/make_pdf.py --month 2026-07
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import line_reports as LR  # noqa: E402  (shared CSS for the insert page)

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

PRINT_CSS = """
@page{size:A4 landscape;margin:9mm 10mm;}
html{background:#fff;}
.suite,#tip{display:none!important;}
.wrap{padding:0;gap:26px;max-width:none;zoom:.88;}
table{font-size:11px;}
th{padding:0 9px 4px 0;}
th.r,td.r{padding-left:10px;}
td{padding:4px 9px 4px 0;}
tr{break-inside:avoid;}
section,.note,.kpis{break-inside:avoid-page;}
.shead{break-after:avoid;}
.mast{padding-bottom:14px;}
.mast .no{font-size:56px;}
.mast h1{font-size:30px;}
.brand{width:96px;}
.plot{break-inside:avoid;}
/* the 02 chart pair is taller than a landscape page together with its mast;
   cap its height so masthead + chart share the first page of the part */
svg.sumsvg{max-height:420px;margin:0 auto;}
/* the noekkeltall table (17 numeric columns) and the 14-column month matrix
   are wider than landscape A4 even zoomed; shrink
   just that section's table so okt-des are not clipped off the page edge */
section:has(svg#monthly) table{font-size:9px;}
section:has(svg#monthly) th.r,section:has(svg#monthly) td.r{padding-left:6px;}
section:has(svg#monthly) th{padding-right:6px;}
section.nkl table{font-size:9.5px;}
section.nkl th.r,section.nkl td.r{padding-left:5px;}
section.nkl th,section.nkl td{padding-right:4px;}
section.nkl .gs{padding-left:9px;}
section.mnd table{font-size:9px;}
section.mnd th.r,section.mnd td.r{padding-left:4px;}
section.mnd th,section.mnd td{padding-right:3px;}
section.mnd .sum{padding-left:8px;}
"""


def print_shell(content: str) -> str:
    return ('<!doctype html><html data-theme="light"><head>'
            '<meta charset="utf-8"><style>' + PRINT_CSS + "</style></head><body>"
            + content + "</body></html>")


def to_pdf(html_path: pathlib.Path, pdf_path: pathlib.Path) -> None:
    cmd = [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           "--virtual-time-budget=4000",
           f"--print-to-pdf={pdf_path}", html_path.as_uri()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not pdf_path.exists():
        raise SystemExit(f"Chrome failed for {html_path.name}: {res.stderr[-300:]}")


def unavailable_page(mnd: str, ry: int) -> str:
    body = f"""{LR.suite_nav("none")}
<header class="mast">
  <div class="no">4&ndash;7</div>
  <div>
    <div class="kicker">H&Oslash;YER-kjeden &middot; m&aring;nedsrapport</div>
    <h1>Sesonger &middot; Rabatter &middot; Merker &middot; Selgere</h1>
    <div class="win">Ikke tilgjengelig for {mnd} {ry}.</div>
  </div>
  <div class="brand" role="img" aria-label="H&Oslash;yer"></div>
</header>
<div class="note"><strong>Hvorfor.</strong> Disse delene krever varelinjedata,
  som ikke finnes i API-et for {mnd} {ry} eller tidligere perioder. Tallene
  finnes kun i BI-verkt&oslash;yet bak PPT-rapporten. Delene omfatter: salg og
  BF fordelt p&aring; sesong; n&oslash;kkeltall rabatter; merker med
  h&oslash;yest omsetning og n&oslash;kkeltall pr merke; plagg pr kunde (PPK)
  pr butikk og beste selger (PPK/KPK). St&oslash;rste kunder utelates bevisst
  &mdash; rapportserien henter ikke kundedata.</div>"""
    return f"<title>Ikke tilgjengelig</title><style>{LR.CSS}</style>\n" \
           f'<div class="wrap">{body}</div>'


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", default="2026-07")
    ap.add_argument("--out", default=None)
    ap.add_argument("--html", action="store_true",
                    help="also write the bound suite as one HTML file")
    ap.add_argument("--html-only", action="store_true",
                    help="only the combined HTML; no Chrome, no PDF")
    args = ap.parse_args()
    ry, rm = int(args.month[:4]), int(args.month[5:7])
    import monthly_report as MR
    mnd = MR.MND[rm - 1]
    LR.set_suite_month(ry, rm)
    reports = ROOT / "reports"
    out = pathlib.Path(args.out or reports / f"Manedsrapport_{mnd}_{ry}.pdf")

    line_parts = [reports / f"{nn}.html" for nn in
                  ("04_Sesonger", "05_Rabatter", "06_Merker", "07_Selgere")]
    middle = line_parts if all(f.exists() for f in line_parts) else [None]
    parts = [reports / f"01_Manedsrapport_{mnd}_{ry}.html",
             reports / "02_Oppsummering.html",
             reports / "03_Omsetning_og_BF.html",
             *middle,
             reports / "08_Diverse.html"]

    def combine_html(contents, out_html, anchors):
        """One self-contained page from the parts, same order as the PDF.

        Each part carries an identical <style> and its own <script> with the
        same const names; keep one stylesheet, scope every script in a bare
        block so const declarations cannot collide, and keep one #tip div.
        The suite nav is KEPT and its file links become in-page anchors, so
        the menu works on hosts that only serve this single file.
        """
        pieces = []
        for i, (c, aid) in enumerate(zip(contents, anchors)):
            if i > 0:
                c = re.sub(r"<title>.*?</title>", "", c, count=1, flags=re.S)
                c = re.sub(r"<style>.*?</style>", "", c, count=1, flags=re.S)
            c = c.replace('<div id="tip" role="status" aria-live="polite"></div>', "")
            c = c.replace("<script>", "<script>{").replace("</script>", "}</script>")
            pieces.append(f'<div class="part" id="{aid}">' + c + "</div>")
        page = ("\n".join(pieces)
                + '<div id="tip" role="status" aria-live="polite"></div>')
        # nav file links -> anchors (every suite filename, wherever it appears)
        seen = {a for a in anchors}
        for fname, _title in LR.SUITE:
            aid = f"del-{fname[:2]}"
            target = aid if aid in seen else "del-0407"
            page = page.replace(f'href="{fname}"', f'href="#{target}"')
        # Paged mode: one part visible at a time, the sticky menu switches
        # pages via location.hash. Progressive enhancement -- without JS the
        # parts simply flow after one another as before.
        page += ("<style>"
                 "body.paged .part{display:none}"
                 "body.paged .part.active{display:block}"
                 "</style>"
                 "<script>{"
                 "const parts=[...document.querySelectorAll('.part')];"
                 "const ids=new Set(parts.map(p=>p.id));"
                 "function show(){"
                 "  const want=location.hash.slice(1);"
                 "  const id=ids.has(want)?want:parts[0].id;"
                 "  parts.forEach(p=>p.classList.toggle('active',p.id===id));"
                 "  window.scrollTo({top:0,behavior:'auto'});"
                 "}"
                 "document.body.classList.add('paged');"
                 "show();"
                 "addEventListener('hashchange',show);"
                 "}</script>")
        if not page.isascii():
            raise SystemExit("combined HTML not pure ASCII")
        out_html.write_text(page, encoding="ascii")
        print(f"  html: {out_html} ({out_html.stat().st_size:,} bytes)",
              file=sys.stderr)

    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        pdfs, contents = [], []
        for i, part in enumerate(parts):
            if part is None:
                content = unavailable_page(mnd, ry)
            else:
                if not part.exists():
                    raise SystemExit(f"missing {part}")
                content = part.read_text(encoding="ascii")
            # Period-purity gate: nothing newer than the report month may
            # appear in any bound part. Built from (ry, rm): later-month
            # dates this year, any date next year, and later-month names in
            # lowercase running text ("August" alone stays legal -- it is a
            # common given name; "August 2026"-style with the year is not).
            forbidden = ([rf"{ry}-{mm:02d}" for mm in range(rm + 1, 13)]
                         + [rf"{ry + 1}-\d\d"]
                         + [m for m in MR.MND[rm:]]
                         + [rf"{m.capitalize()} {ry}" for m in MR.MND[rm:]])
            leaks = re.findall("|".join(forbidden), content)
            if leaks:
                raise SystemExit(f"period leakage in part {i+1}: {set(leaks)}")
            contents.append(content)
            if args.html_only:
                continue
            html = tdp / f"part{i}.html"
            html.write_text(print_shell(content), encoding="ascii",
                            errors="strict")
            pdf = tdp / f"part{i}.pdf"
            to_pdf(html, pdf)
            pdfs.append(pdf)
            print(f"  part {i+1}: {pdf.stat().st_size:,} bytes", file=sys.stderr)

        if not args.html_only:
            from pypdf import PdfWriter
            writer = PdfWriter()
            for pdf in pdfs:
                writer.append(str(pdf))
            with open(out, "wb") as fh:
                writer.write(fh)
    if args.html or args.html_only:
        anchors = [(f"del-{p.name[:2]}" if p is not None else "del-0407")
                   for p in parts]
        combine_html(contents, out.with_suffix(".html"), anchors)
    if not args.html_only:
        from pypdf import PdfReader
        pages = len(PdfReader(str(out)).pages)
        print(f"  merged: {out} ({pages} pages, {out.stat().st_size:,} bytes)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
