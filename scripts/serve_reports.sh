#!/bin/bash
# Serverer månedsrapport-serien fra denne maskinen på port 8811.
# Kun selve rapportfilene kopieres til webrapport/ — aldri reports/cache/
# (rådata, selgernavn) eller CSV/Excel-eksportene.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/webrapport"
mkdir -p "$WEB"
cp "$ROOT"/reports/0[1-7]_*.html "$WEB"/
cp "$ROOT"/reports/Manedsrapport_*.html "$WEB"/ 2>/dev/null || true
# indeks -> rapport 01, så en lenke uten filnavn også virker
FIRST=$(ls "$WEB" | grep '^01_' | head -1)
printf '<meta http-equiv="refresh" content="0; url=%s">' "$FIRST" > "$WEB/index.html"
# restart serveren
pkill -f "http.server 8811" 2>/dev/null || true
sleep 0.5
nohup python3 -m http.server 8811 --directory "$WEB" --bind 0.0.0.0 \
  > /dev/null 2>&1 & disown
echo "serverer $WEB på port 8811 (pid $!)"
