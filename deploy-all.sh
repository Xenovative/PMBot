#!/bin/bash
set -e

# ============================================
#  Polymarket Bots — Deploy All Instances
#  Uses deploy-instance.sh for each bot
#  Tested on: Ubuntu 22.04 / Debian 12
#  Run as root from project dir: ./deploy-all.sh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Polymarket Bots — Full Deployment       ║"
echo "║  Uses deploy-instance.sh per instance    ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "This will deploy all bot instances sequentially."
echo "Each instance will prompt you for configuration."
echo ""

# ── Detect backend source directories ──
BACKENDS=()
for dir in "$SCRIPT_DIR"/*/; do
    dirname=$(basename "$dir")
    if [ -f "$dir/main.py" ] || [ -f "$dir/requirements.txt" ]; then
        BACKENDS+=("$dirname")
    fi
done

if [ ${#BACKENDS[@]} -eq 0 ]; then
    echo "  No bot source directories found."
    exit 1
fi

echo "Found ${#BACKENDS[@]} bot backend(s): ${BACKENDS[*]}"
echo ""

DEPLOYED=()

for i in "${!BACKENDS[@]}"; do
    BACKEND="${BACKENDS[$i]}"
    NUM=$((i + 1))
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  [${NUM}/${#BACKENDS[@]}] ${BACKEND}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    read -p "  Deploy ${BACKEND}? [Y/n]: " DEPLOY_THIS
    DEPLOY_THIS=${DEPLOY_THIS:-Y}
    if [[ ! "$DEPLOY_THIS" =~ ^[Yy]$ ]]; then
        echo "  Skipping ${BACKEND}."
        echo ""
        continue
    fi

    cd "$SCRIPT_DIR"
    bash deploy-instance.sh
    DEPLOYED+=("$BACKEND")
    echo ""
done

# ── Summary ──
IP=$(hostname -I | awk '{print $1}')

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Deployment Complete!                    ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Deployed ${#DEPLOYED[@]} instance(s): ${DEPLOYED[*]}"
echo ""
echo "  Useful commands:"
echo "    systemctl list-units 'pmbot-*'    # all bot services"
echo "    pm2 status                         # all frontends"
echo "    ls /opt/pmbot-*/backend/.env       # all configs"
echo ""

# Check for unconfigured .env files
NEEDS_SETUP=false
for ENV_FILE in /opt/pmbot-*/backend/.env; do
    if [ -f "$ENV_FILE" ]; then
        if [ ! -s "$ENV_FILE" ] || grep -q "your_private_key_here" "$ENV_FILE" 2>/dev/null; then
            NEEDS_SETUP=true
            echo "  ⚠️  Edit: nano $ENV_FILE"
        fi
    fi
done
if [ "$NEEDS_SETUP" = true ]; then
    echo ""
    echo "  Then restart the corresponding services."
    echo ""
fi
