#!/bin/bash
# Leseproxy foran Front Systems — kjøres lokalt hos hver bruker.
# Alt klientverktøy peker på http://127.0.0.1:8812 via FRONT_SYSTEMS_BASE_URL,
# så ingen kodevei går til en skrivemetode. Nøklene leses fra .env og
# forlater aldri denne maskinen.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8812}"
pkill -f "front_systems_mcp.proxy" 2>/dev/null || true
sleep 0.5
cd "$ROOT"
# PYTHONPATH gjør at en fersk klone virker uten pip install -e .
PYTHONPATH="$ROOT/src:${PYTHONPATH:-}" nohup python3 -m front_systems_mcp.proxy \
  --port "$PORT" >> "$ROOT/proxy.log" 2>&1 & disown
# Vent til proxyen svarer. Kald oppstart (import av httpx/truststore) kan ta
# et par sekunder foerste gang, saa vi proever i inntil 10 sekunder framfor
# aa gjette paa ett sekund.
for _ in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:$PORT/healthz" > /dev/null; then
    echo "leseproxy kjører på http://127.0.0.1:$PORT (logg: proxy.log)"
    exit 0
  fi
  sleep 0.5
done
echo "proxyen startet ikke — se $ROOT/proxy.log" >&2
exit 1
