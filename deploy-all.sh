#!/bin/bash
set -e

# ============================================
#  Polymarket Bots — Deploy All
#  Deploys both 15-min and Daily bots
#  Tested on: Ubuntu 22.04 / Debian 12
#  Run as root from project dir: ./deploy-all.sh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Polymarket Bots — Full Deployment       ║"
echo "║  15-min bot + Daily bot                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Shared system dependencies (only once) ──
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Installing shared system dependencies..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

NODE_VERSION=20

apt-get update -qq
apt-get install -y -qq software-properties-common nginx curl git rsync

# Python 3.12
if ! python3.12 --version &>/dev/null; then
    echo "  Installing Python 3.12..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.12 python3.12-venv python3.12-dev
fi

# Node.js
if ! command -v node &>/dev/null; then
    echo "  Installing Node.js ${NODE_VERSION}..."
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash -
    apt-get install -y -qq nodejs
fi

# PM2
if ! command -v pm2 &>/dev/null; then
    echo "  Installing PM2..."
    npm install -g pm2
fi

echo "  Python: $(python3.12 --version)"
echo "  Node:   $(node --version)"
echo ""

# ── Deploy 15-min bot ──
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [1/2] Deploying 15-min bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "$SCRIPT_DIR"
bash deploy.sh
echo ""

# ── Deploy Daily bot ──
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [2/2] Deploying Daily bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "$SCRIPT_DIR"
bash deploy-daily.sh
echo ""

# ── Summary ──
IP=$(hostname -I | awk '{print $1}')

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  All Bots Deployed Successfully!         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  ┌─────────────────────────────────────┐"
echo "  │  15-min Bot                         │"
echo "  │  Dashboard:  http://$IP             │"
echo "  │  Backend:    http://127.0.0.1:8888  │"
echo "  │  Config:     /opt/pmbot/backend/.env│"
echo "  └─────────────────────────────────────┘"
echo ""
echo "  ┌─────────────────────────────────────┐"
echo "  │  Daily Bot                          │"
echo "  │  Dashboard:  http://$IP:81          │"
echo "  │  Backend:    http://127.0.0.1:8889  │"
echo "  │  Config:     /opt/pmbot-daily/backend/.env│"
echo "  └─────────────────────────────────────┘"
echo ""
echo "  Services:"
echo "    systemctl status pmbot-backend         # 15-min backend"
echo "    systemctl status pmbot-daily-backend   # daily backend"
echo "    sudo -u pmbot pm2 status               # both frontends"
echo ""
echo "  Logs:"
echo "    journalctl -u pmbot-backend -f         # 15-min logs"
echo "    journalctl -u pmbot-daily-backend -f   # daily logs"
echo ""

# Check if .env files need editing
NEEDS_SETUP=false
for ENV_FILE in /opt/pmbot/backend/.env /opt/pmbot-daily/backend/.env; do
    if [ ! -s "$ENV_FILE" ] || grep -q "your_private_key_here" "$ENV_FILE" 2>/dev/null; then
        NEEDS_SETUP=true
        echo "  ⚠️  Edit: nano $ENV_FILE"
    fi
done
if [ "$NEEDS_SETUP" = true ]; then
    echo ""
    echo "  Then restart:"
    echo "    systemctl restart pmbot-backend pmbot-daily-backend"
    echo ""
fi
