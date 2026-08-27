#!/bin/bash
# Installer leseproxyen som launchd-tjeneste: den starter ved innlogging og
# kommer opp igjen av seg selv hvis den kræsjer, så sperren mot skriving og
# kundedata står før noen rekker å kjøre et rapportskript.
#
#   bash scripts/install_proxy_service.sh              # installer / oppdater
#   bash scripts/install_proxy_service.sh --port 8813  # annen port
#   bash scripts/install_proxy_service.sh --uninstall  # fjern tjenesten
#
# Plist-en genereres her framfor å ligge ferdig i repoet fordi den må bære
# absolutte stier til DENNE klonen og DENNE maskinens python3 — launchd
# kjører uten brukerens PATH og uten skallets oppstartsfiler, så ingenting
# kan løses opp ved kjøretid. Skriptet er idempotent: kjør det på nytt etter
# at du har flyttet repoet, byttet python eller endret proxy-koden.
set -euo pipefail

LABEL="no.hoyer.frontsystems.readproxy"
PORT=8812
ACTION="install"

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) ACTION="uninstall"; shift ;;
    --port) PORT="${2:?--port trenger et portnummer}"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "ukjent valg: $1 (se --help)" >&2; exit 2 ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*) echo "porten må være et tall, fikk: $PORT" >&2; exit 2 ;;
esac
# Uten områdesjekk ville f.eks. 99999 passert her og først feilet i bind().
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "porten må være mellom 1 og 65535, fikk: $PORT" >&2
  exit 2
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "launchd finnes bare på macOS." >&2
  echo "På andre systemer: kjør 'bash scripts/serve_proxy.sh' manuelt," >&2
  echo "eller sett opp tilsvarende med systemd --user." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="gui/$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# bootout er asynkron. Bootstrapper vi før jobben faktisk er borte, feiler
# den med «Input/output error» — så vi venter til launchctl ikke kjenner den.
stop_service() {
  launchctl print "$DOMAIN/$LABEL" > /dev/null 2>&1 || return 0
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  for _ in $(seq 1 40); do
    launchctl print "$DOMAIN/$LABEL" > /dev/null 2>&1 || return 0
    sleep 0.25
  done
  echo "klarte ikke å stoppe $LABEL." >&2
  echo "Prøv manuelt: launchctl bootout $DOMAIN/$LABEL" >&2
  exit 1
}

if [ "$ACTION" = "uninstall" ]; then
  stop_service
  rm -f "$PLIST"
  echo "tjenesten er fjernet."
  echo "Proxyen kan fortsatt startes for hånd: bash scripts/serve_proxy.sh"
  exit 0
fi

# Absolutt sti til en python3 som faktisk har avhengighetene. sys.executable
# framfor 'command -v' fordi sistnevnte gjerne peker på en shim eller et
# venv-skall som launchd ikke ville fått til å virke.
PY="${PYTHON:-$(command -v python3 || true)}"
if [ -z "$PY" ]; then
  echo "fant ingen python3 på PATH. Sett PYTHON=/sti/til/python3 og prøv igjen." >&2
  exit 1
fi
PY="$("$PY" -c 'import sys; print(sys.executable)')"

# Bevis at avhengighetene er der nå, framfor å la tjenesten feile i loop
# etterpå — launchd struper restart til hvert 10. sekund og sier ingenting.
if ! PYTHONPATH="$ROOT/src" "$PY" -c \
     'import httpx, truststore, dotenv, front_systems_mcp.proxy' 2>/dev/null; then
  echo "python3 på $PY mangler avhengigheter for proxyen." >&2
  echo "Kjør med samme python:  \"$PY\" -m pip install -e \".[dev]\"" >&2
  exit 1
fi

# Samme begrunnelse som avhengighetssjekken over: bevis at nøklene finnes NÅ.
# Proxyen kaller load_config() FØR den binder porten, så uten komplette
# nøkler dør prosessen — og da ville vi ha bootstrappet en KeepAlive-jobb som
# kræsjløper hvert 10. sekund, overlever innlogging, og får serve_proxy.sh til
# å nekte å starte reserven fordi tjenesten «finnes». Vi sjekker samme fil som
# tjenesten vil lese. Feilmeldingen navngir manglende nøkler, aldri verdier.
if ! ENV_ERR="$(PYTHONPATH="$ROOT/src" "$PY" -c '
import pathlib, sys
from front_systems_mcp.config import ConfigError, load_config
try:
    load_config(pathlib.Path(sys.argv[1]))
except ConfigError as exc:
    sys.exit(str(exc))
' "$ROOT/.env" 2>&1)"; then
  echo "kan ikke installere tjenesten - nøklene er ikke på plass:" >&2
  echo "  $ENV_ERR" >&2
  echo "Fyll ut $ROOT/.env (mal i .env.example) og kjør skriptet på nytt." >&2
  exit 1
fi

# XML-escaping av stier. & må først, ellers escaper vi våre egne ampersander.
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}
E_PY="$(xml_escape "$PY")"
E_ROOT="$(xml_escape "$ROOT")"
E_SRC="$(xml_escape "$ROOT/src")"
E_LOG="$(xml_escape "$ROOT/proxy.log")"

stop_service
# Nå som launchd-jobben er ute, treffer denne bare en eventuelt manuelt
# startet proxy (scripts/serve_proxy.sh). Den ville holdt porten og fått
# tjenesten til å feile i loop.
pkill -f "front_systems_mcp.proxy" 2>/dev/null || true
sleep 0.5

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <!-- Generert av scripts/install_proxy_service.sh - ikke rediger for haand,
       kjoer skriptet paa nytt. Leseproxyen foran Front Systems: kun GET,
       kundefelter strippet, lytter bare paa 127.0.0.1:$PORT. Noeklene leses
       fra .env i WorkingDirectory og forlater aldri maskinen. -->
  <key>ProgramArguments</key>
  <array>
    <string>$E_PY</string>
    <string>-m</string>
    <string>front_systems_mcp.proxy</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$E_ROOT</string>

  <key>EnvironmentVariables</key>
  <dict>
    <!-- Gjoer at tjenesten virker uten pip install -e . -->
    <key>PYTHONPATH</key>
    <string>$E_SRC</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>

  <!-- Start paa nytt hvis prosessen doer. macOS struper til hvert 10.
       sekund, saa en proxy som ikke klarer aa starte gaar ikke amok. -->
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$E_LOG</string>
  <key>StandardErrorPath</key>
  <string>$E_LOG</string>

  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST_EOF

if ! plutil -lint "$PLIST" > /dev/null; then
  echo "generert plist er ugyldig: $PLIST" >&2
  exit 1
fi

launchctl bootstrap "$DOMAIN" "$PLIST"

for _ in $(seq 1 40); do
  # Grep-en på svaret: en fremmed tjener som allerede holder porten ville
  # ellers fått installeren til å melde suksess mens sperren ikke kjører.
  if curl -sf "http://127.0.0.1:$PORT/healthz" 2>/dev/null | grep -q '"ok"'; then
    echo "leseproxyen kjører som tjeneste på http://127.0.0.1:$PORT"
    echo "  starter ved innlogging, og på nytt hvis den kræsjer"
    echo "  restart:  launchctl kickstart -k $DOMAIN/$LABEL"
    echo "  fjern:    bash scripts/install_proxy_service.sh --uninstall"
    echo "  logg:     $ROOT/proxy.log"
    # Sperren hjelper ikke hvis klientene går utenom den. Vi leser bare
    # etter portnummeret; .env-innhold skal aldri ut på skjermen.
    # Tolerer mellomrom rundt = (python-dotenv gjoer det), og krever at
    # portnummeret slutter der - ellers ville 8812 matchet en URL paa 88120.
    if [ -f "$ROOT/.env" ] && ! grep -Eq \
       "^[[:space:]]*FRONT_SYSTEMS_BASE_URL[[:space:]]*=.*(127\.0\.0\.1|localhost):$PORT([^0-9]|$)" \
       "$ROOT/.env"; then
      echo >&2
      echo "advarsel: FRONT_SYSTEMS_BASE_URL i .env peker ikke på port $PORT —" >&2
      echo "  klientene går da utenom sperren, rett på Front Systems." >&2
    fi
    exit 0
  fi
  sleep 0.5
done

echo "tjenesten svarte ikke på port $PORT innen 20 sekunder." >&2
# Uten dette ville en KeepAlive-jobb blitt igjen og kræsjløpt ved hver
# innlogging, mens serve_proxy.sh nektet å starte reserven fordi launchctl
# fortsatt kjenner jobben. Vi rydder heller opp og lar brukeren prøve igjen.
echo "rydder opp så maskinen ikke sitter igjen med en kræsjløkke..." >&2
stop_service
rm -f "$PLIST"
echo "tjenesten er fjernet igjen. Årsaken står i $ROOT/proxy.log —" >&2
echo "rett den og kjør skriptet på nytt." >&2
exit 1
