#!/bin/bash
set -e

# ============================================
#  Polymarket Bot — Quick Update
#  Syncs code, rebuilds, and restarts existing
#  instances without touching system config.
#  Run as root: ./update.sh [instance-name]
# ============================================

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}  $1${NC}"; }
warn()  { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
ok()    { echo -e "${GREEN}  ✓ $1${NC}"; }
err()   { echo -e "${RED}  ✗ $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  ${CYAN}Polymarket Bot — Quick Update${NC}${BOLD}                ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

if [ "$EUID" -ne 0 ]; then
    err "Please run as root: sudo ./update.sh"
    exit 1
fi

# ── Scan existing instances ──
INSTANCES=()
INST_DIRS=()
INST_SVCS=()
INST_PORTS=()
INST_STATUSES=()

for inst_dir in /opt/pmbot-*/; do
    [ -d "$inst_dir/backend" ] || continue
    name=$(basename "$inst_dir" | sed 's/^pmbot-//')
    svc="pmbot-${name}-backend"
    svc_file="/etc/systemd/system/${svc}.service"

    port="-"
    [ -f "$svc_file" ] && port=$(grep -oP 'Environment=PORT=\K[0-9]+' "$svc_file" 2>/dev/null || echo "-")

    if systemctl is-active "$svc" &>/dev/null; then
        st="${GREEN}running${NC}"
    elif systemctl is-enabled "$svc" &>/dev/null; then
        st="${YELLOW}stopped${NC}"
    else
        st="${RED}unknown${NC}"
    fi

    has_fe=""
    [ -d "$inst_dir/frontend" ] && has_fe=" + frontend"

    INSTANCES+=("$name")
    INST_DIRS+=("$inst_dir")
    INST_SVCS+=("$svc")
    INST_PORTS+=("$port")
    INST_STATUSES+=("$st")
done

if [ ${#INSTANCES[@]} -eq 0 ]; then
    err "No existing instances found in /opt/pmbot-*/"
    echo -e "  Run ${CYAN}./deploy-instance.sh${NC} first to create an instance."
    exit 1
fi

# ── If instance name passed as argument, use it directly ──
TARGET_NAME="$1"

if [ -z "$TARGET_NAME" ]; then
    echo -e "${BOLD}Existing instances:${NC}"
    echo ""
    for i in "${!INSTANCES[@]}"; do
        has_fe=""
        [ -d "${INST_DIRS[$i]}/frontend" ] && has_fe=" ${GREEN}+fe${NC}"
        echo -e "  ${CYAN}$((i+1)))${NC} ${BOLD}${INSTANCES[$i]}${NC}  [port ${INST_PORTS[$i]}]  ${INST_STATUSES[$i]}${has_fe}"
    done
    echo -e "  ${CYAN}a)${NC} ${BOLD}All instances${NC}"
    echo ""

    read -p "  Select instance to update [1-${#INSTANCES[@]}/a]: " choice

    if [[ "$choice" =~ ^[Aa]$ ]]; then
        TARGET_NAME="__all__"
    elif [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le ${#INSTANCES[@]} ]; then
        TARGET_NAME="${INSTANCES[$((choice-1))]}"
    else
        err "Invalid selection"
        exit 1
    fi
fi

# ── Build the list of instances to update ──
UPDATE_LIST=()
if [ "$TARGET_NAME" = "__all__" ]; then
    UPDATE_LIST=("${INSTANCES[@]}")
else
    # Validate the name exists
    found=false
    for inst in "${INSTANCES[@]}"; do
        if [ "$inst" = "$TARGET_NAME" ]; then
            found=true
            break
        fi
    done
    if [ "$found" = false ]; then
        err "Instance '$TARGET_NAME' not found"
        exit 1
    fi
    UPDATE_LIST=("$TARGET_NAME")
fi

echo ""

# ── Detect source directories ──
detect_source() {
    local inst_name="$1"
    local be_src="" fe_src=""

    # Try common naming patterns
    for candidate in "${inst_name}-backend" "${inst_name}_backend" "backend"; do
        if [ -d "$SCRIPT_DIR/$candidate" ] && { [ -f "$SCRIPT_DIR/$candidate/main.py" ] || [ -f "$SCRIPT_DIR/$candidate/requirements.txt" ]; }; then
            be_src="$candidate"
            break
        fi
    done

    if [ -z "$be_src" ]; then
        # Fallback: look for any backend dir that matches
        for dir in "$SCRIPT_DIR"/*/; do
            dirname=$(basename "$dir")
            if [[ "$dirname" == *"$inst_name"* ]] && { [ -f "$dir/main.py" ] || [ -f "$dir/requirements.txt" ]; }; then
                be_src="$dirname"
                break
            fi
        done
    fi

    if [ -n "$be_src" ]; then
        fe_src="${be_src/backend/frontend}"
        [ ! -d "$SCRIPT_DIR/$fe_src" ] && fe_src=""
    fi

    echo "$be_src|$fe_src"
}

# ── Update each instance ──
UPDATED=0
FAILED=0

for inst_name in "${UPDATE_LIST[@]}"; do
    APP_DIR="/opt/pmbot-${inst_name}"
    SERVICE_NAME="pmbot-${inst_name}-backend"
    PM2_NAME="pmbot-${inst_name}-frontend"
    APP_USER="pmbot"

    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  Updating: ${CYAN}${inst_name}${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # Detect sources
    IFS='|' read -r BACKEND_SRC FRONTEND_SRC <<< "$(detect_source "$inst_name")"

    if [ -z "$BACKEND_SRC" ]; then
        err "Cannot find source directory for '${inst_name}'"
        echo -e "  Looked for: ${inst_name}-backend, ${inst_name}_backend, backend"
        ((FAILED++))
        echo ""
        continue
    fi

    info "Backend source: ${BACKEND_SRC}/"
    [ -n "$FRONTEND_SRC" ] && info "Frontend source: ${FRONTEND_SRC}/"

    # ── 1. Stop services ──
    info "Stopping services..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    if [ -n "$FRONTEND_SRC" ]; then
        PM2_BIN=$(which pm2 2>/dev/null || true)
        [ -n "$PM2_BIN" ] && $PM2_BIN stop "$PM2_NAME" 2>/dev/null || true
    fi

    # ── 2. Sync code ──
    info "Syncing backend code..."
    rsync -a --delete \
        --exclude '.env' \
        --exclude '.auth.json' \
        --exclude '*.db' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        "$SCRIPT_DIR/$BACKEND_SRC/" "$APP_DIR/backend/"

    if [ -n "$FRONTEND_SRC" ] && [ -d "$APP_DIR/frontend" ]; then
        info "Syncing frontend code..."
        rsync -a --delete \
            --exclude 'node_modules' \
            --exclude 'dist' \
            "$SCRIPT_DIR/$FRONTEND_SRC/" "$APP_DIR/frontend/"
    fi

    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    ok "Code synced"

    # ── 3. Update pip dependencies ──
    if [ -f "$APP_DIR/backend/requirements.txt" ] && [ -d "$APP_DIR/venv" ]; then
        info "Updating Python dependencies..."
        sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
        ok "Dependencies updated"
    fi

    # ── 4. Rebuild frontend ──
    if [ -n "$FRONTEND_SRC" ] && [ -d "$APP_DIR/frontend" ]; then
        info "Rebuilding frontend..."
        cd "$APP_DIR/frontend"
        # Clear build cache to ensure fresh build
        rm -rf dist node_modules/.vite
        sudo -u "$APP_USER" npm install --silent
        sudo -u "$APP_USER" npm run build
        ok "Frontend rebuilt"
    fi

    # ── 5. Restart services ──
    info "Restarting services..."
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
    ok "Backend restarted"

    if [ -n "$FRONTEND_SRC" ] && [ -n "$PM2_BIN" ]; then
        $PM2_BIN restart "$PM2_NAME" 2>/dev/null || $PM2_BIN start "npx vite preview --host 0.0.0.0 --port 5174" --name "$PM2_NAME" --cwd "$APP_DIR/frontend" --uid "$APP_USER" 2>/dev/null || true
        $PM2_BIN save 2>/dev/null || true
        ok "Frontend restarted"
    fi

    # ── 6. Verify ──
    sleep 2
    if systemctl is-active "$SERVICE_NAME" &>/dev/null; then
        ok "${inst_name} is running"
    else
        warn "${inst_name} may not have started — check: journalctl -u ${SERVICE_NAME} -n 20"
    fi

    if [ -f "$APP_DIR/backend/.auth.json" ]; then
        ok "Auth credentials intact"
    fi

    ((UPDATED++))
    echo ""
done

# ── Summary ──
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
if [ $FAILED -eq 0 ]; then
    echo -e "${BOLD}  ${GREEN}✓ Updated ${UPDATED} instance(s) successfully${NC}"
else
    echo -e "${BOLD}  ${GREEN}✓ Updated: ${UPDATED}${NC}  ${RED}✗ Failed: ${FAILED}${NC}"
fi
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo ""
