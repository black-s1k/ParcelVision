#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# ParcelVision — one-command startup
#   • Starts Flask on port 5002
#   • VS Code auto-forwards port 5002 — check the PORTS tab for the URL
#   • Chrome injection is manual: run inject_tab.py <URL> or paste
#     smartlockerscript.js into DevTools console on tab 3 (index 2)
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

# ── Load .env (only valid KEY=VALUE lines — skip corrupt/garbage lines) ──
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

# ── Open Windows Firewall for port 5002 (silent no-op if rule exists) ─
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

# ── Local IP (same-WiFi fallback) ─────────────────────────────────────
LOCAL_IP=""
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == CYGWIN* ]]; then
    LOCAL_IP=$(ipconfig 2>/dev/null \
        | grep "IPv4" \
        | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
        | grep -v "^127\." \
        | head -1 || true)
fi

echo ""
echo "============================================================"
echo "  Flask         : http://localhost:5002"
if [ -n "$LOCAL_IP" ]; then
echo "  Local network : http://$LOCAL_IP:5002"
fi
echo ""
echo "  Phone URL ──> VS Code PORTS tab, port 5002"
echo "                (VS Code auto-forwards to a public https URL)"
echo ""
echo "  Inject SmartLocker script manually:"
echo "    Option A)  python backend/inject_tab.py <PORTS-URL>"
echo "               targets tab 3 (index 2) or 1valetbas.com tab"
echo "    Option B)  paste smartlockerscript.js into Chrome DevTools"
echo "               console (replace __SERVER_URL__ with PORTS URL)"
echo ""
echo "  Press Ctrl+C to stop"
echo "============================================================"
echo ""

# ── Cleanup on exit ───────────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down..."
    [ -n "$FLASK_PID" ] && kill "$FLASK_PID" 2>/dev/null || true
    ok "Stopped."
}
trap cleanup EXIT INT TERM

wait "$FLASK_PID" 2>/dev/null || true
