# Front Systems reporting — arbeidsregler for Claude Code

Les skillen `.claude/skills/front-systems-reporting/` FØR API-kall eller
rapportarbeid — den bærer API-fellene (silent failures), deck-konvensjonene,
valideringene og errata-registeret. Denne filen er kun det aller viktigste.

## Periode-renhetsregelen (absolutt)

En måneds utgave inneholder INGENTING nyere enn måneden den dekker — ikke
som tall, ikke i forklaringstekst, ikke som tidsstempel. Historiske
sammenligninger (fjorår, YTD, rullerende) er unntaket. `make_pdf.py` har en
lekkasjeport som håndhever dette — behold den. Alle rapporter nummerert inn
i suiten dekker SAMME måned.

## De to hovedrapporttypene

1. **Månedsrapport** (9 deler) — tas ut på forespørsel den 1. i måneden for
   forrige måned: `bash scripts/monthly_run.sh` (evt. med `YYYY-MM`).
   **PDF er frosset — jobb kun med HTML** (`make_pdf.py --html-only`).
   Republiser deretter til artifact-lenken (samme URL hver utgave):
   https://claude.ai/code/artifact/ec2171c1-8f3c-4e8e-baf8-3ce03592a715
2. **Høyer Nye KPI** (K1 kapitalavkastning, K2 returer) —
   `python3 scripts/hoyer_nye_kpi.py`; egen artifact:
   https://claude.ai/code/artifact/8f681efc-794c-45ae-9a2d-5b6e3afad0bb

Kjørerekkefølge i suiten: line_reports FØR monthly_suite (begge skriver 03
og 09; suite-versjonene skal vinne). Lokal deling: `scripts/serve_reports.sh`
(port 8811). Interim-utgaver (delmåned på eksplisitt forespørsel) bygges i
egen katalog med `make_pdf.py --dir` og egen artifact — og delmåneds-cacher
SLETTES etterpå.

## Tall-konvensjoner (deck-validert)

Omsetning = SUM(Qty × Price) inkl. mva; netto = brutto/1,25;
BF = netto − SUM(Qty × Cost); rabatt-% = rabatt/fullpris. Dagens
butikksammensetning i alle år (brukerbeslutning 2026-08-13) inkl. de syv
beviste forgjengerenhetene — se skillens manedsrapport.md. Avvik mot
PPT-decket skal sjekkes mot errata-listen der FØR feilsøking: flere av
deckets egne sider er bevist feil (KPK-juli viser junitall m.m.).

## Stil og hygiene

Generert HTML er ren ASCII (æøå som entiteter) — ikke skriv rå ø i
f-strenger. Alle tabellkolonner og grafkolonner skal kunne sorteres
(SORT_JS/CHART_SORT_JS i line_reports). Beløp i 1000 kr merkes med
dempet «k». Kundedata hentes ALDRI fra API-et.

## Praktisk

`.env` har nøklene (aldri i git; mal i `.env.example`). Cacher under
`reports/cache/` — fortidsmåneder endres aldri; from/to-vinduet mot
Saleslines tar maks 31 dager. Repoet er PRIVAT og skal forbli det:
commit-meldinger og skill inneholder forretningstall.
