#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# ParcelVision — one-command startup
#   • Resolves a public SERVER_URL (Tailscale → cloudflared → localhost.run → local IP)
#   • Starts Flask (app2.py)
#   • Injects the 1Valet listener script into Chrome automatically
#
# Usage (Git Bash on Windows):  ./start.sh
#
# One-time Chrome setup — run this once, then always launch Chrome the same way:
#   Windows: Add --remote-debugging-port=9222 to your Chrome shortcut target, or run:
#     "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
ENV_FILE="$BACKEND/.env"

# ── Colours ───────────────────────────────────────────────────────────
C_CYAN='\033[0;36m'; C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
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
    set -a; source "$ENV_FILE"; set +a
fi

# ── Detect Python command (python3 on Mac/Linux, python on Windows) ───
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

# ── Globals ───────────────────────────────────────────────────────────
SERVER_URL=""
TUNNEL_PID=""
FLASK_PID=""
TUNNEL_LOG=""

# ── Helper: write SERVER_URL into .env ────────────────────────────────
save_server_url() {
    local url="$1"
    "$PY" - "$ENV_FILE" "$url" <<'PYEOF'
import sys, re
path, url = sys.argv[1], sys.argv[2]
try:
    content = open(path, newline='').read().replace('\r\n', '\n').replace('\r', '\n')
except FileNotFoundError:
    content = ""
if re.search(r'^SERVER_URL=', content, re.MULTILINE):
    content = re.sub(r'^SERVER_URL=.*', f'SERVER_URL={url}', content, flags=re.MULTILINE)
else:
    content = content.rstrip('\n') + f'\nSERVER_URL={url}\n'
open(path, 'w', newline='\n').write(content)
PYEOF
}

# ── Helper: local IP (Windows / Mac / Linux) ──────────────────────────
local_ip() {
    local ip="" os
    os="$(uname -s)"

    # Windows (Git Bash / MINGW / CYGWIN)
    if [[ "$os" == MINGW* || "$os" == CYGWIN* ]]; then
        ip=$(ipconfig 2>/dev/null \
            | grep -A1 "Wireless\|Wi-Fi\|Ethernet" \
            | grep "IPv4" \
            | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
            | grep -v "^127\." \
            | head -1 || true)
        echo "${ip:-127.0.0.1}"
        return
    fi

    # macOS
    if [[ "$os" == Darwin* ]]; then
        ip=$(ipconfig getifaddr en0 2>/dev/null || true)
        [ -n "$ip" ] && echo "$ip" && return
        ip=$(ipconfig getifaddr en1 2>/dev/null || true)
        echo "${ip:-127.0.0.1}"
        return
    fi

    # Linux / WSL
    ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    echo "${ip:-127.0.0.1}"
}

# ── 1. Resolve SERVER_URL ─────────────────────────────────────────────
info "Resolving public URL..."

# Priority 1: Tailscale — permanent, set TAILSCALE_URL in .env once
if [ -n "${TAILSCALE_URL:-}" ]; then
    SERVER_URL="$TAILSCALE_URL"
    ok "Tailscale (permanent): $SERVER_URL"

# Priority 2: cloudflared quick tunnel
elif command -v cloudflared &>/dev/null; then
    info "Starting Cloudflare Quick Tunnel..."
    TUNNEL_LOG="$(mktemp)"
    cloudflared tunnel --url "http://localhost:5002" --no-autoupdate \
        >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!

    for i in $(seq 1 30); do
        SERVER_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' \
            "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
        [ -n "$SERVER_URL" ] && break
        printf "  waiting... %ds\r" "$i"
        sleep 1
    done
    echo ""

    if [ -n "$SERVER_URL" ]; then
        ok "Cloudflare tunnel: $SERVER_URL"
    else
        warn "Cloudflare URL not captured — trying SSH tunnel."
        kill "$TUNNEL_PID" 2>/dev/null || true
        TUNNEL_PID=""
    fi
fi

# Priority 3: localhost.run SSH tunnel — no install, no admin needed
# Works with Windows built-in SSH or Git Bash SSH
if [ -z "$SERVER_URL" ] && command -v ssh &>/dev/null; then
    info "Starting SSH tunnel via localhost.run (no admin required)..."
    TUNNEL_LOG="$(mktemp)"
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -o ConnectTimeout=15 \
        -R "80:localhost:5002" \
        nokey@localhost.run \
        >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!

    for i in $(seq 1 25); do
        SERVER_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.lhr\.life' \
            "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
        [ -n "$SERVER_URL" ] && break
        printf "  waiting for tunnel... %ds\r" "$i"
        sleep 1
    done
    echo ""

    if [ -n "$SERVER_URL" ]; then
        ok "SSH tunnel (localhost.run): $SERVER_URL"
    else
        warn "SSH tunnel failed — falling back to local IP."
        kill "$TUNNEL_PID" 2>/dev/null || true
        TUNNEL_PID=""
        SERVER_URL="http://$(local_ip):5002"
        warn "Phone must be on the same Wi-Fi as this machine."
        info "Local IP: $SERVER_URL"
    fi
fi

# Priority 4: local IP fallback
if [ -z "$SERVER_URL" ]; then
    SERVER_URL="http://$(local_ip):5002"
    warn "No tunnel available — using local IP: $SERVER_URL"
    warn "Phone must be on the same Wi-Fi as this machine."
fi

save_server_url "$SERVER_URL"
export SERVER_URL

# ── 2. Start Flask ────────────────────────────────────────────────────
echo ""
info "Starting Flask server (app2.py)..."
cd "$BACKEND"
"$PY" app2.py &
FLASK_PID=$!

info "Waiting for Flask to be ready..."
for i in $(seq 1 20); do
    curl -sf "http://localhost:5002/" >/dev/null 2>&1 && break || true
    sleep 1
done
ok "Flask is ready on port 5002."

# ── 3. Inject into Chrome ─────────────────────────────────────────────
echo ""
info "Injecting 1Valet script into Chrome..."
"$PY" "$BACKEND/inject_tab.py" "$SERVER_URL" \
    && ok "Script injected into Chrome." \
    || warn "Chrome injection failed. Start Chrome with --remote-debugging-port=9222 and retry."

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Phone upload URL : $SERVER_URL"
echo "  Admin panel      : $SERVER_URL"
echo "  Press Ctrl+C to stop all services"
echo "============================================================"
echo ""

# ── Cleanup on exit ───────────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down..."
    [ -n "$FLASK_PID"   ] && kill "$FLASK_PID"   2>/dev/null || true
    [ -n "$TUNNEL_PID"  ] && kill "$TUNNEL_PID"  2>/dev/null || true
    [ -n "$TUNNEL_LOG"  ] && rm -f "$TUNNEL_LOG" 2>/dev/null || true
    ok "Stopped."
}
trap cleanup EXIT INT TERM

wait "$FLASK_PID" 2>/dev/null || true
