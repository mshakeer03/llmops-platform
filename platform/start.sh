#!/usr/bin/env bash
# ==============================================================================
# LLMOps Platform — One-Shot Startup Script
# ==============================================================================
# Ensures the host-launcher is running, then brings up all containers.
# Safe to run multiple times (idempotent).
#
# Usage:
#   ./platform/start.sh            # start everything
#   ./platform/start.sh --status   # show status only
# ==============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM_DIR="$REPO_ROOT/platform"
LAUNCHER_PY="$PLATFORM_DIR/host-launcher/launcher.py"
VLLM_PYTHON="$REPO_ROOT/vllm_env/bin/python"
LAUNCHER_PORT="${LAUNCHER_PORT:-9001}"
LAUNCHER_LOG="/tmp/host-launcher.log"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/com.llmops.host-launcher.plist"

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✔${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
info() { echo -e "${CYAN}▸${NC}  $*"; }
fail() { echo -e "${RED}✘${NC}  $*"; }

# ── Helpers ───────────────────────────────────────────────────────────────────

launcher_running() {
    curl -sf "http://localhost:$LAUNCHER_PORT/health" > /dev/null 2>&1
}

start_launcher() {
    info "Starting host-launcher (port $LAUNCHER_PORT)…"
    mkdir -p /tmp/vllm_logs

    # Prefer launchd (persists across reboots); fall back to nohup
    if [[ -f "$LAUNCHD_PLIST" ]]; then
        launchctl load "$LAUNCHD_PLIST" 2>/dev/null || true
        sleep 1
    fi

    if ! launcher_running; then
        # Direct nohup fallback
        nohup "$VLLM_PYTHON" "$LAUNCHER_PY" \
            >> "$LAUNCHER_LOG" 2>&1 &
        disown
        for i in $(seq 1 10); do
            sleep 0.5
            if launcher_running; then break; fi
        done
    fi

    if launcher_running; then
        ok "Host-launcher is up on :$LAUNCHER_PORT"
    else
        fail "Host-launcher failed to start — check $LAUNCHER_LOG"
        exit 1
    fi
}

# ── Status mode ───────────────────────────────────────────────────────────────

if [[ "${1:-}" == "--status" ]]; then
    echo ""
    echo "════════════════════════════════════════"
    echo " LLMOps Platform Status"
    echo "════════════════════════════════════════"

    if launcher_running; then
        HEALTH=$(curl -sf "http://localhost:$LAUNCHER_PORT/health")
        ok "Host-launcher   :$LAUNCHER_PORT  |  $HEALTH"
    else
        fail "Host-launcher   NOT running"
    fi

    echo ""
    cd "$PLATFORM_DIR"
    podman-compose ps 2>/dev/null | grep -v "^Name" | grep -v "^---" || \
        podman ps --format "table {{.Names}}\t{{.Status}}" 2>/dev/null
    echo ""
    exit 0
fi

# ── Main startup ──────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════"
echo " LLMOps Platform — Starting Up"
echo "════════════════════════════════════════"
echo ""

# 1. Validate prerequisites
if [[ ! -x "$VLLM_PYTHON" ]]; then
    fail "vLLM Python not found at: $VLLM_PYTHON"
    fail "Run: cd $REPO_ROOT && python3 -m venv vllm_env && vllm_env/bin/pip install vllm"
    exit 1
fi
ok "vLLM Python found: $VLLM_PYTHON"

# 2. Ensure host-launcher is running
if launcher_running; then
    ok "Host-launcher already running on :$LAUNCHER_PORT"
else
    start_launcher
fi

# 3. Install launchd service if not already done (auto-start on login)
if [[ ! -f "$LAUNCHD_PLIST" ]]; then
    warn "launchd plist not installed — host-launcher won't auto-start on reboot"
    warn "To install: launchctl load $LAUNCHD_PLIST"
else
    # Ensure it's loaded (idempotent — launchctl ignores if already loaded)
    launchctl load "$LAUNCHD_PLIST" 2>/dev/null || true
    ok "launchd service registered (auto-start on login)"
fi

# 4. Start containers
echo ""
info "Starting containers via podman-compose…"
cd "$PLATFORM_DIR"
podman-compose up -d

# 5. Wait briefly then show health
echo ""
info "Waiting for API to be ready…"
for i in $(seq 1 20); do
    sleep 1
    if curl -sf "http://localhost:8001/health" > /dev/null 2>&1; then
        ok "API is healthy  →  http://localhost:8001"
        break
    fi
    if [[ $i -eq 20 ]]; then
        warn "API not yet responding — check: podman logs llmops-api"
    fi
done

# 6. Status summary
echo ""
echo "════════════════════════════════════════"
echo " Services"
echo "════════════════════════════════════════"
ok "Host-launcher   →  http://localhost:$LAUNCHER_PORT/health"
ok "API             →  http://localhost:8001/health"
ok "UI / Admin      →  http://localhost:3000"
ok "Open WebUI      →  http://localhost:8080"
ok "MLflow          →  http://localhost:5001"
ok "Grafana         →  http://localhost:3001"
ok "LiteLLM Admin  →  http://localhost:4000/ui"
echo ""
ok "Platform is ready."
echo ""
