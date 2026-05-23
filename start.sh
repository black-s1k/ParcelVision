#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# ParcelVision — one-command startup
#   • Creates SSH tunnel (serveo.net → localhost.run → local IP fallback)
#   • Updates smartlockerscript.txt and smartlockerscript_g1.txt with URL
#   • Starts Flask on port 5002
#
# Usage (Git Bash on Windows):  ./start.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
ENV_FILE="$BACKEND/.env"

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

# ── SSH Tunnel ────────────────────────────────────────────────────────
SERVER_URL=""

try_tunnel() {
    local host="$1"
    local user_arg="$2"
    local label="$3"
    info "Trying SSH tunnel via $label..."
    > "$TUNNEL_LOG"
    ssh -o StrictHostKeyChecking=no \
        -o ConnectTimeout=10 \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -R 80:localhost:5002 \
        "$user_arg@$host" \
        >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    local url=""
    for i in $(seq 1 15); do
        url=$(grep -oE 'https://[^[:space:]]+' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
        if [ -n "$url" ]; then
            SERVER_URL="$url"
            ok "Tunnel active: $SERVER_URL"
            return 0
        fi
        sleep 1
    done
    kill "$TUNNEL_PID" 2>/dev/null || true
    TUNNEL_PID=""
    warn "$label failed — no URL received within 15s."
    return 1
}

try_tunnel "serveo.net" "serveo.net" "serveo.net" \
    || try_tunnel "localhost.run" "nokey" "localhost.run" \
    || true

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
        warn "No tunnel — using LAN IP: $SERVER_URL"
        warn "Phone must be on the same network as this machine."
    else
        SERVER_URL="http://localhost:5002"
        warn "No tunnel — using localhost only (phone upload won't work)."
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
echo "  >>> PUBLIC URL (phone + SmartLocker scripts): <<<"
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
