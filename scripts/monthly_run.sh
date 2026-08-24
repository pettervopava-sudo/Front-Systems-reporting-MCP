#!/bin/bash
# Maanedsrutinen: bygg hele maanedsrapport-serien for en maaned.
#
#   scripts/monthly_run.sh [YYYY-MM]     (uten argument: forrige maaned)
#
# Kjoerer delene i riktig rekkefoelge (line_reports FOER monthly_suite, saa
# suite-versjonene av 03/08 vinner), binder PDF + enkeltfil-HTML og synker
# 8811-serveren. Cachene gjoer at bare nye maaneder hentes fra API-et.
# Artifact-lenken republiseres fra samtalen etterpaa (samme URL).
set -euo pipefail
cd "$(dirname "$0")/.."
MONTH="${1:-$(python3 -c "import datetime as dt; p = dt.date.today().replace(day=1) - dt.timedelta(days=1); print(f'{p.year:04d}-{p.month:02d}')")}"
FROM="$MONTH-01"
TO="$(python3 -c "import datetime as dt; y, m = map(int, '$MONTH'.split('-')); print(dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1))")"
echo "== Maanedsrapport $MONTH (linjevindu $FROM -> $TO ekskl.) ==" >&2
python3 scripts/monthly_report.py --month "$MONTH"
python3 scripts/line_reports.py --from "$FROM" --to "$TO" --seller-min 100
python3 scripts/monthly_suite.py --month "$MONTH"
python3 scripts/make_pdf.py --month "$MONTH" --html
bash scripts/serve_reports.sh
echo "== Ferdig: reports/01-08 + Manedsrapport-PDF/HTML; 8811 synket. ==" >&2
