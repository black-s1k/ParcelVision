#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# ParcelVision — one-command startup
#   • Resolves a public SERVER_URL (Tailscale → cloudflared → local IP)
#   • Starts Flask (app2.py)
#   • Injects the 1Valet listener script into Chrome automatically
#
# Usage:  ./start.sh
#
# One-time Chrome setup (do this once, not every time):
#   macOS:  open -a "Google Chrome" --args --remote-debugging-port=9222
#   Linux:  google-chrome --remote-debugging-port=9222 &
#   Or add --remote-debugging-port=9222 to your Chrome shortcut permanently.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"
ENV_FILE="$BACKEND/.env"

# ── Colours ───────────────────────────────────────────────────────────
C_CYAN='\033[0;36m'; C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
info()  { echo -e "${C_CYAN}  $*${C_RESET}"; }
ok()    { echo -e "${C_GREEN}  ✅ $*${C_RESET}"; }
warn()  { echo -e "${C_YELLOW}  ⚠️  $*${C_RESET}"; }
err()   { echo -e "${C_RED}  ❌ $*${C_RESET}"; }

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║            ParcelVision — Starting up                    ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Load .env ─────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi

# ── Globals ───────────────────────────────────────────────────────────
SERVER_URL=""
CF_PID=""
FLASK_PID=""
TUNNEL_LOG=""

# ── Helper: write SERVER_URL into .env ────────────────────────────────
save_server_url() {
    local url="$1"
    python3 - "$ENV_FILE" "$url" <<'PYEOF'
import sys, re
path, url = sys.argv[1], sys.argv[2]
try:
    content = open(path).read()
except FileNotFoundError:
    content = ""
if re.search(r'^SERVER_URL=', content, re.MULTILINE):
    content = re.sub(r'^SERVER_URL=.*', f'SERVER_URL={url}', content, flags=re.MULTILINE)
else:
    content = content.rstrip('\n') + f'\nSERVER_URL={url}\n'
open(path, 'w').write(content)
PYEOF
}

# ── Helper: local IP ──────────────────────────────────────────────────
local_ip() {
    # macOS
    ipconfig getifaddr en0 2>/dev/null && return
    ipconfig getifaddr en1 2>/dev/null && return
    # Linux
    hostname -I 2>/dev/null | awk '{print $1}' && return
    echo "127.0.0.1"
}

# ── 1. Resolve SERVER_URL ─────────────────────────────────────────────
info "Resolving public URL..."

# Priority 1: Tailscale — truly permanent, no domain needed
if [ -n "${TAILSCALE_URL:-}" ]; then
    SERVER_URL="$TAILSCALE_URL"
    ok "Tailscale (permanent): $SERVER_URL"

# Priority 2: cloudflared quick tunnel (no admin needed, just needs cloudflared)
elif command -v cloudflared &>/dev/null; then
    info "Starting Cloudflare Quick Tunnel..."
    TUNNEL_LOG="$(mktemp)"
    cloudflared tunnel --url "http://localhost:5002" --no-autoupdate \
        >"$TUNNEL_LOG" 2>&1 &
    CF_PID=$!

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
        warn "Cloudflare URL not captured — falling back to SSH tunnel."
        kill "$CF_PID" 2>/dev/null || true
        CF_PID=""
    fi

# Priority 3: localhost.run SSH tunnel (no install, no admin — uses built-in SSH)
elif command -v ssh &>/dev/null; then
    info "Starting SSH tunnel via localhost.run (no admin required)..."
    TUNNEL_LOG="$(mktemp)"
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -R "80:localhost:5002" \
        nokey@localhost.run \
        >"$TUNNEL_LOG" 2>&1 &
    CF_PID=$!

    for i in $(seq 1 20); do
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
        warn "SSH tunnel URL not captured — falling back to local IP."
        kill "$CF_PID" 2>/dev/null || true
        CF_PID=""
        SERVER_URL="http://$(local_ip):5002"
        info "Local IP fallback: $SERVER_URL"
    fi

# Priority 4: local network IP (phone and PC must be on same Wi-Fi)
else
    warn "No tunnel available — using local IP."
    warn "Phone must be on the same Wi-Fi as this machine."
    warn "Find your IP with: ipconfig (Windows) or ifconfig (Mac/Linux)"
    SERVER_URL="http://$(local_ip):5002"
    info "Local: $SERVER_URL"
fi

save_server_url "$SERVER_URL"
export SERVER_URL

# ── 2. Start Flask ────────────────────────────────────────────────────
echo ""
info "Starting Flask server (app2.py)..."
cd "$BACKEND"
python3 app2.py &
FLASK_PID=$!

info "Waiting for Flask..."
for i in $(seq 1 20); do
    curl -sf "http://localhost:5002/" >/dev/null 2>&1 && break || true
    sleep 1
done
ok "Flask is ready."

# ── 3. Inject into Chrome ─────────────────────────────────────────────
echo ""
info "Injecting 1Valet script into Chrome (tab 3 / 1Valet tab)..."
python3 "$BACKEND/inject_tab.py" "$SERVER_URL" \
    && ok "Script injected into Chrome automatically." \
    || warn "Chrome injection failed — see above for the one-time setup command."

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
printf "  📱  Phone upload URL : ${C_CYAN}%s${C_RESET}\n" "$SERVER_URL"
printf "  🖥️   Admin panel      : ${C_CYAN}%s${C_RESET}\n" "$SERVER_URL"
echo "  Press Ctrl+C to stop all services"
echo "══════════════════════════════════════════════════════════"
echo ""

# ── Cleanup on exit ───────────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down..."
    [ -n "$FLASK_PID" ] && kill "$FLASK_PID" 2>/dev/null || true
    [ -n "$CF_PID"    ] && kill "$CF_PID"    2>/dev/null || true
    [ -n "$TUNNEL_LOG" ] && rm -f "$TUNNEL_LOG" || true
    ok "Stopped."
}
trap cleanup EXIT INT TERM

wait "$FLASK_PID" 2>/dev/null || true
