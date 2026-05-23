#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# ParcelVision — one-command startup
#   • Fixed URL via serveo.net subdomain (never changes between restarts)
#   • Falls back to localhost.run if serveo is unavailable
#   • Starts Flask on port 5002
#
# Usage (Git Bash on Windows):  ./start.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
ENV_FILE="$BACKEND/.env"

# ── Fixed tunnel subdomain — URL will always be https://SUBDOMAIN.serveo.net
TUNNEL_SUBDOMAIN="parcelvision"

# ── Colours ───────────────────────────────────────────────────────────
C_CYAN='\033[0;36m'; C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'; C_RESET='\033[0m'
info()  { echo -e "${C_CYAN}  $*${C_RESET}"; }
ok()    { echo -e "${C_GREEN}  [OK] $*${C_RESET}"; }
warn()  { echo -e "${C_YELLOW}  [!]  $*${C_RESET}"; }

echo ""
echo "============================================================"
echo "  ParcelVision -- Starting up"
echo "============================================================"
echo ""

# ── Load .env ─────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key rest; do
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        export "$key=$rest"
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" 2>/dev/null)
fi

# ── Detect Python ─────────────────────────────────────────────────────
VENV_PY="$SCRIPT_DIR/backend/venv/Scripts/python"
if [ -f "$VENV_PY" ]; then
    PY="$VENV_PY"
elif command -v python &>/dev/null; then
    PY="python"
elif command -v python3 &>/dev/null; then
    PY="python3"
else
    echo "  [!] Python not found. Activate your venv first."
    exit 1
fi

FLASK_PID=""
TUNNEL_PID=""
TUNNEL_LOG="$SCRIPT_DIR/.tunnel.log"

# ── Open Windows Firewall for port 5002 ───────────────────────────────
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == CYGWIN* ]]; then
    netsh advfirewall firewall add rule \
        name="ParcelVision Flask 5002" dir=in action=allow \
        protocol=TCP localport=5002 \
        >/dev/null 2>&1 || true
fi

# ── Start Flask ───────────────────────────────────────────────────────
echo ""
info "Starting Flask server (app2.py) on port 5002..."
cd "$BACKEND"
"$PY" app2.py &
FLASK_PID=$!

info "Waiting for Flask to be ready..."
for i in $(seq 1 20); do
    curl -sf "http://localhost:5002/" >/dev/null 2>&1 && break || true
    sleep 1
done
ok "Flask is ready on port 5002."

# ── Fixed serveo.net tunnel ───────────────────────────────────────────
SERVER_URL="https://${TUNNEL_SUBDOMAIN}.serveo.net"
info "Starting tunnel → ${SERVER_URL} ..."
> "$TUNNEL_LOG"
ssh -o StrictHostKeyChecking=no \
    -o ConnectTimeout=15 \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -R "${TUNNEL_SUBDOMAIN}:80:localhost:5002" \
    serveo.net >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# Give serveo 5s to connect; if the process dies the subdomain is taken
sleep 5
if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    warn "serveo.net failed — subdomain '${TUNNEL_SUBDOMAIN}' may be taken."
    warn "Change TUNNEL_SUBDOMAIN in start.sh and try again, or falling back to localhost.run..."
    TUNNEL_PID=""
    SERVER_URL=""
    > "$TUNNEL_LOG"
    ssh -o StrictHostKeyChecking=no \
        -o ConnectTimeout=10 \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -R 80:localhost:5002 \
        nokey@localhost.run >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 15); do
        SERVER_URL=$(grep -oE 'https://[^[:space:]]+' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
        [ -n "$SERVER_URL" ] && break || true
        sleep 1
    done
    if [ -n "$SERVER_URL" ]; then
        ok "Fallback tunnel active: $SERVER_URL"
        warn "URL is random this session — update TUNNEL_SUBDOMAIN to fix it."
    else
        kill "$TUNNEL_PID" 2>/dev/null || true
        TUNNEL_PID=""
        SERVER_URL=""
    fi
else
    ok "Tunnel active: ${SERVER_URL}"
fi

# ── Local IP fallback if both tunnels failed ──────────────────────────
if [ -z "$SERVER_URL" ]; then
    LOCAL_IP=""
    if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == CYGWIN* ]]; then
        LOCAL_IP=$(ipconfig 2>/dev/null \
            | grep "IPv4" \
            | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
            | grep -v "^127\." \
            | head -1 || true)
    fi
    if [ -n "${LOCAL_IP:-}" ]; then
        SERVER_URL="http://$LOCAL_IP:5002"
        warn "Using LAN IP: $SERVER_URL (phone must be on same network)"
    else
        SERVER_URL="http://localhost:5002"
        warn "Using localhost only — phone upload won't work."
    fi
fi

# ── Update SERVER_URL in SmartLocker scripts ──────────────────────────
URL_PATTERN='s|const SERVER_URL = "[^"]*";|const SERVER_URL = "'"${SERVER_URL}"'";|'

for SCRIPT in "$BACKEND/smartlockerscript.txt" "$BACKEND/smartlockerscript_g1.txt"; do
    if [ -f "$SCRIPT" ]; then
        sed -i "$URL_PATTERN" "$SCRIPT"
        ok "$(basename "$SCRIPT") updated with: $SERVER_URL"
    fi
done

# ── Banner ────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Flask     : http://localhost:5002"
echo ""
echo "  >>> PUBLIC URL (same every restart): <<<"
echo "      $SERVER_URL"
echo ""
echo "  Paste smartlockerscript.txt     → G2 1Valet tab"
echo "  Paste smartlockerscript_g1.txt  → G1 1Valet tab"
echo ""
echo "  Press Ctrl+C to stop"
echo "============================================================"
echo ""

# ── Cleanup on exit ───────────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down..."
    [ -n "$FLASK_PID"  ] && kill "$FLASK_PID"  2>/dev/null || true
    [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
    rm -f "$TUNNEL_LOG"
    ok "Stopped."
}
trap cleanup EXIT INT TERM

wait "$FLASK_PID" 2>/dev/null || true
