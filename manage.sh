#!/bin/bash
# ============================================
#  PMBot Instance Manager — TUI
#  Requires: whiptail (pre-installed on Ubuntu/Debian)
#  Run as root: sudo ./manage.sh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_USER="pmbot"
NPM_CACHE_DIR="/opt/pmbot/.npm-cache"

# ── Colors for non-whiptail output ──
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root: sudo ./manage.sh${NC}"
    exit 1
fi

if ! command -v whiptail &>/dev/null; then
    apt-get install -y -qq whiptail
fi

PYTHON_BIN=$(command -v python3.12 || command -v python3)
PM2_BIN=$(command -v pm2 || echo "pm2")

# ============================================
#  Helper: scan all deployed instances
# ============================================
scan_instances() {
    INSTANCES=()
    INSTANCE_DIRS=()
    for inst_dir in /opt/pmbot-*/; do
        [ -d "$inst_dir/backend" ] || continue
        inst_name=$(basename "$inst_dir" | sed 's/^pmbot-//')
        INSTANCES+=("$inst_name")
        INSTANCE_DIRS+=("$inst_dir")
    done
}

# ── Get instance info ──
inst_backend_port() {
    local name="$1"
    local svc="/etc/systemd/system/pmbot-${name}-backend.service"
    grep -oP 'Environment=PORT=\K[0-9]+' "$svc" 2>/dev/null || echo "?"
}

inst_nginx_port() {
    local name="$1"
    local conf="/etc/nginx/sites-available/pmbot-${name}"
    grep -oP 'listen\s+\K[0-9]+' "$conf" 2>/dev/null | head -1 || echo "?"
}

inst_status() {
    local name="$1"
    if systemctl is-active "pmbot-${name}-backend" &>/dev/null; then
        echo "running"
    elif systemctl is-enabled "pmbot-${name}-backend" &>/dev/null; then
        echo "stopped"
    else
        echo "unknown"
    fi
}

inst_dry_run() {
    local name="$1"
    local env="/opt/pmbot-${name}/backend/.env"
    if grep -q "^DRY_RUN=true" "$env" 2>/dev/null; then
        echo "DRY"
    elif grep -q "^PRIVATE_KEY=" "$env" 2>/dev/null && ! grep -q "^PRIVATE_KEY=$" "$env" 2>/dev/null && ! grep -q "your_private_key" "$env" 2>/dev/null; then
        echo "LIVE"
    else
        echo "DRY"
    fi
}

inst_source() {
    # Try to detect the source backend dir from the service WorkingDirectory
    local name="$1"
    local svc="/etc/systemd/system/pmbot-${name}-backend.service"
    local wdir
    wdir=$(grep -oP 'WorkingDirectory=\K.*' "$svc" 2>/dev/null || echo "")
    # Extract source dir hint from name (strip suffix after last known stack name)
    for src in backend m5-backend hourly-backend 4h-backend daily-backend; do
        if echo "$name" | grep -q "$(echo $src | sed 's/-backend//')"; then
            echo "$src"; return
        fi
    done
    echo "backend"
}

# ============================================
#  Main menu — checklist multi-select
# ============================================
main_menu() {
    while true; do
        scan_instances

        if [ ${#INSTANCES[@]} -eq 0 ]; then
            whiptail --title "PMBot Manager" --msgbox \
                "No deployed instances found.\n\nRun ./onboard.sh to deploy your first instance." \
                10 55
            exit 0
        fi

        # Build checklist items: name | label | ON/OFF
        local check_items=()
        for name in "${INSTANCES[@]}"; do
            local status mode bport nport icon
            status=$(inst_status "$name")
            mode=$(inst_dry_run "$name")
            bport=$(inst_backend_port "$name")
            nport=$(inst_nginx_port "$name")
            [ "$status" = "running" ] && icon="▶" || icon="■"
            label="$(printf '%-8s [%-4s]  :%-5s :%-5s' "$icon $status" "$mode" "$bport" "$nport")"
            check_items+=("$name" "$label" "OFF")
        done
        # Extra actions at bottom
        check_items+=("---"   "────────────────────────────────────" "OFF")
        check_items+=("[new]" "Deploy a new instance"                "OFF")
        check_items+=("[quit]" "Exit"                                "OFF")

        local raw_selection
        raw_selection=$(whiptail --title "PMBot Instance Manager  ($(date '+%H:%M:%S'))" \
            --checklist "SPACE to select  •  TAB to OK/Cancel  •  Select 1 for single manage, 2+ for bulk actions\n${#INSTANCES[@]} instance(s) deployed" \
            24 72 15 \
            "${check_items[@]}" \
            3>&1 1>&2 2>&3) || exit 0

        # Parse selection — whiptail returns quoted words
        local selected=()
        for item in $raw_selection; do
            item="${item//\"/}"
            selected+=("$item")
        done

        [ ${#selected[@]} -eq 0 ] && continue

        # Handle special single selections
        if [ ${#selected[@]} -eq 1 ]; then
            case "${selected[0]}" in
                "[quit]"|"---") exit 0 ;;
                "[new]") deploy_new_instance; continue ;;
                *) instance_menu "${selected[0]}"; continue ;;
            esac
        fi

        # Filter out special tokens from multi-select
        local real_selected=()
        for s in "${selected[@]}"; do
            [[ "$s" == "---" || "$s" == "[new]" || "$s" == "[quit]" ]] && continue
            real_selected+=("$s")
        done

        [ ${#real_selected[@]} -eq 0 ] && continue

        if [ ${#real_selected[@]} -eq 1 ]; then
            instance_menu "${real_selected[0]}"
        else
            bulk_action_menu "${real_selected[@]}"
        fi
    done
}

# ============================================
#  Bulk action menu
# ============================================
bulk_action_menu() {
    local selected=("$@")
    local count=${#selected[@]}
    local names_display
    names_display=$(printf '%s  ' "${selected[@]}")

    while true; do
        local action
        action=$(whiptail --title "Bulk Actions  ($count instances selected)" \
            --menu "Selected: $names_display\n\nChoose an action to apply to ALL selected instances:" \
            20 72 10 \
            "start"    "Start all selected backends" \
            "stop"     "Stop all selected backends" \
            "restart"  "Restart all selected backends" \
            "update"   "Update code on all selected" \
            "pentest"  "Pentest all selected instances" \
            "status"   "Show status summary of all selected" \
            "logs"     "Tail logs for all selected (tmux)" \
            "back"     "← Back to instance list" \
            3>&1 1>&2 2>&3) || return

        case "$action" in
            "start")   bulk_start   "${selected[@]}" ;;
            "stop")    bulk_stop    "${selected[@]}" ;;
            "restart") bulk_restart "${selected[@]}" ;;
            "update")  bulk_update  "${selected[@]}" ;;
            "pentest") bulk_pentest "${selected[@]}" ;;
            "status")  bulk_status  "${selected[@]}" ;;
            "logs")    bulk_logs    "${selected[@]}" ;;
            "back"|*)  return ;;
        esac
    done
}

# ── Bulk helpers ──

bulk_start() {
    clear
    echo -e "${BOLD}${CYAN}━━━ Start: $* ━━━${NC}\n"
    for name in "$@"; do
        echo -ne "  Starting pmbot-${name}-backend... "
        systemctl start "pmbot-${name}-backend" 2>/dev/null && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done
    echo ""; read -p "Press Enter to continue..." _
}

bulk_stop() {
    clear
    whiptail --title "Bulk Stop" --yesno "Stop backends for: $*?" 8 60 || return
    clear
    echo -e "${BOLD}${CYAN}━━━ Stop: $* ━━━${NC}\n"
    for name in "$@"; do
        echo -ne "  Stopping pmbot-${name}-backend... "
        systemctl stop "pmbot-${name}-backend" 2>/dev/null && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done
    echo ""; read -p "Press Enter to continue..." _
}

bulk_restart() {
    clear
    echo -e "${BOLD}${CYAN}━━━ Restart: $* ━━━${NC}\n"
    for name in "$@"; do
        echo -ne "  Restarting pmbot-${name}-backend... "
        systemctl restart "pmbot-${name}-backend" 2>/dev/null && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done
    echo ""; read -p "Press Enter to continue..." _
}

bulk_update() {
    local selected=("$@")

    # Pick a single source dir to apply to all
    local src_items=()
    for candidate in backend m5-backend hourly-backend 4h-backend daily-backend; do
        [ -d "$SCRIPT_DIR/$candidate" ] && src_items+=("$candidate" "")
    done
    local selected_src
    selected_src=$(whiptail --title "Bulk Update — Source" \
        --menu "Select source to sync to ALL selected instances:" \
        14 55 6 "${src_items[@]}" \
        3>&1 1>&2 2>&3) || return

    whiptail --title "Bulk Update" --yesno \
        "Update ALL ${#selected[@]} instances from '$selected_src'?\n\n$(printf '  • %s\n' "${selected[@]}")" \
        $((${#selected[@]} + 8)) 60 || return

    for name in "${selected[@]}"; do
        clear
        echo -e "${BOLD}${CYAN}━━━ Updating $name ━━━${NC}\n"
        # Re-use single update logic inline
        local app_dir="/opt/pmbot-${name}"
        local svc="pmbot-${name}-backend"

        echo -e "${CYAN}[1/5] Stopping...${NC}"
        systemctl stop "$svc" 2>/dev/null || true
        $PM2_BIN stop "pmbot-${name}-frontend" 2>/dev/null || true

        echo -e "${CYAN}[2/5] Syncing code...${NC}"
        rsync -a --delete \
            --exclude '.env' --exclude '.auth.json' \
            --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
            --exclude '__pycache__' --exclude '*.pyc' \
            "$SCRIPT_DIR/$selected_src/" "$app_dir/backend/"
        local fsrc="${selected_src/backend/frontend}"
        if [ -d "$SCRIPT_DIR/$fsrc" ]; then
            rsync -a --delete --exclude 'node_modules' --exclude 'dist' \
                "$SCRIPT_DIR/$fsrc/" "$app_dir/frontend/"
        fi
        chown -R "$APP_USER:$APP_USER" "$app_dir"

        echo -e "${CYAN}[3/5] Reinstalling deps...${NC}"
        runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q --upgrade pip
        runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q -r "$app_dir/backend/requirements.txt"

        if [ -d "$app_dir/frontend" ]; then
            echo -e "${CYAN}[4/5] Rebuilding frontend...${NC}"
            mkdir -p "$NPM_CACHE_DIR"; chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"
            rm -rf "$app_dir/frontend/dist" "$app_dir/frontend/node_modules/.vite"
            runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$app_dir/frontend" install --no-audit --no-fund
            runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$app_dir/frontend" run build
        fi

        echo -e "${CYAN}[5/5] Restarting...${NC}"
        systemctl start "$svc"
        if [ -d "$app_dir/frontend" ]; then
            $PM2_BIN delete "pmbot-${name}-frontend" 2>/dev/null || true
            $PM2_BIN start "npx vite preview --host 0.0.0.0 --port 3000" \
                --name "pmbot-${name}-frontend" --cwd "$app_dir/frontend" --uid "$APP_USER"
            $PM2_BIN save
        fi
        echo -e "${GREEN}✓ $name done  ($(inst_status "$name"))${NC}\n"
    done

    read -p "All updates complete. Run pentest on all? [Y/n]: " do_pt
    do_pt=${do_pt:-Y}
    if [[ "$do_pt" =~ ^[Yy]$ ]]; then
        bulk_pentest "${selected[@]}"
    else
        read -p "Press Enter to continue..." _
    fi
}

bulk_pentest() {
    clear
    echo -e "${BOLD}${CYAN}━━━ Pentest: $* ━━━${NC}\n"
    local results=()
    for name in "$@"; do
        echo -e "${CYAN}── $name ──${NC}"
        run_pentest "$name"   # reuses existing function, waits for Enter per instance
        results+=("$name:$?")
    done
}

bulk_status() {
    clear
    echo -e "${BOLD}${CYAN}━━━ Status Summary ━━━${NC}\n"
    printf "  ${BOLD}%-20s %-10s %-6s %-6s %-6s${NC}\n" "Instance" "Status" "Mode" "API" "Nginx"
    printf "  %-20s %-10s %-6s %-6s %-6s\n"             "────────" "──────" "────" "───" "─────"
    for name in "$@"; do
        local status mode bport nport icon
        status=$(inst_status "$name")
        mode=$(inst_dry_run "$name")
        bport=$(inst_backend_port "$name")
        nport=$(inst_nginx_port "$name")
        [ "$status" = "running" ] && icon="${GREEN}▶${NC}" || icon="${RED}■${NC}"
        printf "  %-20s " "$name"
        echo -ne "${icon} "
        printf "%-8s %-6s :%-5s :%-5s\n" "$status" "$mode" "$bport" "$nport"
    done
    echo ""
    # Last 3 log lines per instance
    for name in "$@"; do
        echo -e "${CYAN}  ── $name recent logs:${NC}"
        journalctl -u "pmbot-${name}-backend" --no-pager -n 3 --output=cat 2>/dev/null | sed 's/^/    /'
        echo ""
    done
    read -p "Press Enter to continue..." _
}

bulk_logs() {
    # Use tmux split panes if available, otherwise sequential tail
    if command -v tmux &>/dev/null; then
        local session="pmbot-logs-$$"
        tmux new-session -d -s "$session" -x 220 -y 50
        local first=true
        for name in "$@"; do
            local svc="pmbot-${name}-backend"
            if [ "$first" = true ]; then
                tmux send-keys -t "$session" "journalctl -u $svc -f --no-pager -n 30" Enter
                first=false
            else
                tmux split-window -t "$session" -h "journalctl -u $svc -f --no-pager -n 30"
                tmux select-layout -t "$session" tiled
            fi
        done
        echo -e "${CYAN}Opening tmux session '$session' with split log panes...${NC}"
        echo -e "${YELLOW}Detach with Ctrl+B then D. Kill with: tmux kill-session -t $session${NC}"
        sleep 1
        tmux attach-session -t "$session"
    else
        # Fallback: sequential per-instance tail
        clear
        echo -e "${BOLD}${CYAN}━━━ Live Logs (Ctrl+C to skip to next) ━━━${NC}\n"
        for name in "$@"; do
            echo -e "${CYAN}── $name (Ctrl+C for next) ──${NC}"
            journalctl -u "pmbot-${name}-backend" -f --no-pager -n 20 || true
            echo ""
        done
        read -p "Press Enter to continue..." _
    fi
}

# ============================================
#  Instance action menu
# ============================================
instance_menu() {
    local name="$1"
    local app_dir="/opt/pmbot-${name}"
    local svc="pmbot-${name}-backend"
    local pm2="pmbot-${name}-frontend"

    while true; do
        local status bport nport mode
        status=$(inst_status "$name")
        bport=$(inst_backend_port "$name")
        nport=$(inst_nginx_port "$name")
        mode=$(inst_dry_run "$name")

        local status_line
        if [ "$status" = "running" ]; then
            status_line="▶ RUNNING  |  API :${bport}  |  Nginx :${nport}  |  Mode: ${mode}"
        else
            status_line="■ STOPPED  |  API :${bport}  |  Nginx :${nport}  |  Mode: ${mode}"
        fi

        local action
        action=$(whiptail --title "Instance: $name" \
            --menu "$status_line" \
            20 65 12 \
            "status"    "Show service status & recent logs" \
            "start"     "Start backend service" \
            "stop"      "Stop backend service" \
            "restart"   "Restart backend service" \
            "update"    "Update code from source" \
            "env"       "Edit .env configuration" \
            "wallet"    "Change wallet / private key" \
            "logs"      "Tail live logs (last 50 lines)" \
            "pentest"   "Run security pentest against this instance" \
            "remove"    "Remove this instance entirely" \
            "back"      "← Back to instance list" \
            3>&1 1>&2 2>&3) || return

        case "$action" in
            "status")   action_status "$name" ;;
            "start")    action_start "$name" ;;
            "stop")     action_stop "$name" ;;
            "restart")  action_restart "$name" ;;
            "update")   action_update "$name" ;;
            "env")      action_edit_env "$name" ;;
            "wallet")   action_change_wallet "$name" ;;
            "logs")     action_logs "$name" ;;
            "pentest")  clear; run_pentest "$name" ;;
            "remove")   action_remove "$name" && return ;;
            "back"|*)   return ;;
        esac
    done
}

# ============================================
#  Actions
# ============================================

action_status() {
    local name="$1"
    local svc="pmbot-${name}-backend"
    local out
    out=$(systemctl status "$svc" --no-pager -l 2>&1 | head -30)
    out+=$'\n\n─── Last 10 log lines ───\n'
    out+=$(journalctl -u "$svc" --no-pager -n 10 --output=cat 2>&1)
    whiptail --title "Status: $name" --scrolltext --msgbox "$out" 28 78
}

action_start() {
    local name="$1"
    local svc="pmbot-${name}-backend"
    systemctl start "$svc" 2>&1
    sleep 1
    local status
    status=$(inst_status "$name")
    whiptail --title "Start: $name" --msgbox "Service $svc: $status" 8 50
}

action_stop() {
    local name="$1"
    local svc="pmbot-${name}-backend"
    whiptail --title "Stop: $name" --yesno "Stop $svc?" 8 45 || return
    systemctl stop "$svc" 2>&1
    whiptail --title "Stop: $name" --msgbox "Service stopped." 8 40
}

action_restart() {
    local name="$1"
    local svc="pmbot-${name}-backend"
    systemctl restart "$svc" 2>&1
    sleep 1
    local status
    status=$(inst_status "$name")
    whiptail --title "Restart: $name" --msgbox "Service $svc: $status" 8 50
}

action_update() {
    local name="$1"
    local app_dir="/opt/pmbot-${name}"
    local svc="pmbot-${name}-backend"

    # Detect source dir
    local src_name
    # Strip suffix: pmbot names are like "m5", "m5-alice", "1h-bob" etc.
    # Match against known stack dirs present in SCRIPT_DIR
    src_name=""
    for candidate in backend m5-backend hourly-backend 4h-backend daily-backend; do
        stack_short=$(echo "$candidate" | sed 's/-backend//' | sed 's/backend/15m/')
        if [ -d "$SCRIPT_DIR/$candidate" ]; then
            if echo "$name" | grep -qE "^(${stack_short}|$(echo $candidate | sed 's/-backend//'))"; then
                src_name="$candidate"
                break
            fi
        fi
    done

    # Ask user to confirm / select source
    local src_items=()
    for candidate in backend m5-backend hourly-backend 4h-backend daily-backend; do
        [ -d "$SCRIPT_DIR/$candidate" ] && src_items+=("$candidate" "")
    done

    local selected_src
    selected_src=$(whiptail --title "Update: $name" \
        --menu "Select source directory to sync from:" \
        16 55 8 \
        "${src_items[@]}" \
        3>&1 1>&2 2>&3) || return

    whiptail --title "Update: $name" --yesno \
        "Update $name from $selected_src?\n\nThis will:\n• Sync code (preserving .env, .auth.json, databases)\n• Reinstall Python deps\n• Rebuild frontend\n• Restart service" \
        14 60 || return

    clear
    echo -e "${BOLD}${CYAN}━━━ Updating: $name ━━━${NC}"
    echo ""

    echo -e "${CYAN}[1/5] Stopping service...${NC}"
    systemctl stop "$svc" 2>/dev/null || true
    $PM2_BIN stop "pmbot-${name}-frontend" 2>/dev/null || true
    echo "    done"

    echo -e "${CYAN}[2/5] Syncing backend code...${NC}"
    rsync -a --delete \
        --exclude '.env' --exclude '.auth.json' \
        --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
        --exclude '__pycache__' --exclude '*.pyc' \
        "$SCRIPT_DIR/$selected_src/" "$app_dir/backend/"

    local fsrc="${selected_src/backend/frontend}"
    if [ -d "$SCRIPT_DIR/$fsrc" ]; then
        echo -e "${CYAN}    Syncing frontend code...${NC}"
        rsync -a --delete \
            --exclude 'node_modules' --exclude 'dist' \
            "$SCRIPT_DIR/$fsrc/" "$app_dir/frontend/"
    fi
    chown -R "$APP_USER:$APP_USER" "$app_dir"
    echo "    done"

    echo -e "${CYAN}[3/5] Reinstalling Python deps...${NC}"
    runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q --upgrade pip
    runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q -r "$app_dir/backend/requirements.txt"
    echo "    done"

    if [ -d "$app_dir/frontend" ]; then
        echo -e "${CYAN}[4/5] Rebuilding frontend...${NC}"
        mkdir -p "$NPM_CACHE_DIR"
        chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"
        rm -rf "$app_dir/frontend/dist" "$app_dir/frontend/node_modules/.vite"
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$app_dir/frontend" install --no-audit --no-fund
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$app_dir/frontend" run build
        echo "    done"
    else
        echo -e "${CYAN}[4/5] No frontend — skipping${NC}"
    fi

    echo -e "${CYAN}[5/5] Restarting services...${NC}"
    systemctl start "$svc"
    if [ -d "$app_dir/frontend" ]; then
        $PM2_BIN delete "pmbot-${name}-frontend" 2>/dev/null || true
        $PM2_BIN start "npx vite preview --host 0.0.0.0 --port 3000" \
            --name "pmbot-${name}-frontend" \
            --cwd "$app_dir/frontend" \
            --uid "$APP_USER"
        $PM2_BIN save
    fi
    echo "    done"

    echo ""
    local status
    status=$(inst_status "$name")
    echo -e "${GREEN}✓ Update complete! Service status: ${status}${NC}"
    echo ""

    read -p "Run pentest against $name now? [Y/n]: " do_pentest
    do_pentest=${do_pentest:-Y}
    if [[ "$do_pentest" =~ ^[Yy]$ ]]; then
        run_pentest "$name"
    else
        read -p "Press Enter to return to manager..." _
    fi
}

# ============================================
#  Pentest runner
# ============================================
run_pentest() {
    local name="$1"
    local nport
    nport=$(inst_nginx_port "$name")

    # Fall back to backend port if nginx isn't configured
    local target_port="$nport"
    [ "$target_port" = "?" ] && target_port=$(inst_backend_port "$name")

    local target="http://127.0.0.1:${target_port}"

    local pentest_dir="$SCRIPT_DIR/pentest"
    local pentest_venv="$pentest_dir/venv"
    local pentest_script="$pentest_dir/pentest_bot.py"

    if [ ! -f "$pentest_script" ]; then
        echo -e "${RED}pentest/pentest_bot.py not found — skipping${NC}"
        read -p "Press Enter to return to manager..." _
        return
    fi

    # Set up pentest venv once
    if [ ! -f "$pentest_venv/bin/python" ]; then
        echo -e "${CYAN}Setting up pentest environment...${NC}"
        $PYTHON_BIN -m venv "$pentest_venv"
        "$pentest_venv/bin/pip" install -q --upgrade pip
        "$pentest_venv/bin/pip" install -q -r "$pentest_dir/requirements.txt"
    fi

    # Wait briefly for service to be ready
    echo -e "${CYAN}Waiting for service to be ready...${NC}"
    local retries=10
    while [ $retries -gt 0 ]; do
        if curl -sf "${target}/api/auth/status" >/dev/null 2>&1; then
            break
        fi
        sleep 2
        ((retries--))
    done
    if [ $retries -eq 0 ]; then
        echo -e "${YELLOW}⚠️  Service not responding at ${target} — pentest may produce incomplete results${NC}"
    fi

    local logfile="/opt/pmbot-${name}/pentest-$(date +%Y%m%d-%H%M%S).log"
    echo ""
    echo -e "${BOLD}${CYAN}━━━ Pentest: $name @ ${target} ━━━${NC}"
    echo ""

    "$pentest_venv/bin/python" "$pentest_script" \
        --target "$target" \
        --logfile "$logfile"
    local exit_code=$?

    echo ""
    echo -e "  Log saved: ${logfile}"
    echo ""
    case $exit_code in
        0) echo -e "${GREEN}✅ Pentest passed — no critical/high/medium issues.${NC}" ;;
        1) echo -e "${YELLOW}⚠️  Pentest: MEDIUM severity issues found — review above.${NC}" ;;
        2) echo -e "${RED}🔴 Pentest: CRITICAL or HIGH issues found — action required!${NC}" ;;
    esac

    echo ""
    read -p "Press Enter to return to manager..." _
}

action_edit_env() {
    local name="$1"
    local env_file="/opt/pmbot-${name}/backend/.env"

    if [ ! -f "$env_file" ]; then
        whiptail --title "Edit .env: $name" --msgbox "No .env found at $env_file" 8 55
        return
    fi

    # Read current content, edit in a temp file via whiptail inputbox is too small
    # Use nano/vi in a sub-shell instead, then offer restart
    whiptail --title "Edit .env: $name" --yesno \
        "Opening $env_file in nano.\n\nSave with Ctrl+O, exit with Ctrl+X.\nThe service will be restarted after editing." \
        10 60 || return

    nano "$env_file"

    whiptail --title "Edit .env: $name" --yesno \
        "Restart pmbot-${name}-backend to apply changes?" \
        8 55 && systemctl restart "pmbot-${name}-backend" && \
        whiptail --title "Edit .env: $name" --msgbox "Service restarted." 8 40
}

action_change_wallet() {
    local name="$1"
    local env_file="/opt/pmbot-${name}/backend/.env"
    local svc="pmbot-${name}-backend"

    local current_key=""
    current_key=$(grep -oP '^PRIVATE_KEY=\K.*' "$env_file" 2>/dev/null || echo "")

    local new_key
    new_key=$(whiptail --title "Change Wallet: $name" \
        --inputbox "Enter new private key (leave blank to keep current):\nCurrent: ${current_key:0:8}..." \
        10 65 "" \
        3>&1 1>&2 2>&3) || return

    if [ -z "$new_key" ]; then
        whiptail --title "Change Wallet: $name" --msgbox "No changes made." 8 40
        return
    fi

    local new_funder
    new_funder=$(whiptail --title "Change Wallet: $name" \
        --inputbox "Funder address (Gnosis Safe proxy, or leave blank for EOA):" \
        9 65 "" \
        3>&1 1>&2 2>&3) || return

    local sig_type
    sig_type=$(whiptail --title "Change Wallet: $name" \
        --menu "Signature type:" 12 50 3 \
        "0" "EOA (plain wallet)" \
        "1" "Magic Link (Polymarket web)" \
        "2" "Gnosis Safe proxy" \
        3>&1 1>&2 2>&3) || return

    # Apply to .env
    set_env() {
        local key="$1" val="$2" file="$3"
        if grep -q "^${key}=" "$file" 2>/dev/null; then
            sed -i "s|^${key}=.*|${key}=${val}|" "$file"
        else
            echo "${key}=${val}" >> "$file"
        fi
    }

    set_env "PRIVATE_KEY"    "$new_key"    "$env_file"
    set_env "FUNDER_ADDRESS" "$new_funder" "$env_file"
    set_env "SIGNATURE_TYPE" "$sig_type"   "$env_file"
    set_env "DRY_RUN"        "false"       "$env_file"
    chmod 600 "$env_file"

    whiptail --title "Change Wallet: $name" --yesno \
        "Wallet updated.\nRestart service to apply?" 8 50 && \
        systemctl restart "$svc" && \
        whiptail --title "Change Wallet: $name" --msgbox "Service restarted." 8 40
}

action_logs() {
    local name="$1"
    local svc="pmbot-${name}-backend"
    # Show in less — exit with q
    clear
    echo -e "${BOLD}${CYAN}Live logs: $svc  (Ctrl+C to exit)${NC}"
    echo ""
    journalctl -u "$svc" -f --no-pager -n 50
}

action_remove() {
    local name="$1"
    local app_dir="/opt/pmbot-${name}"
    local svc="pmbot-${name}-backend"
    local pm2="pmbot-${name}-frontend"

    whiptail --title "Remove: $name" --yesno \
        "⚠️  REMOVE INSTANCE: $name\n\nThis will:\n• Stop and disable systemd service\n• Remove nginx config\n• Delete $app_dir (code, venv, DB)\n\nThis CANNOT be undone. Continue?" \
        14 60 || return 1

    # Double-confirm
    local confirm
    confirm=$(whiptail --title "Remove: $name" \
        --inputbox "Type the instance name to confirm deletion:" \
        9 55 "" \
        3>&1 1>&2 2>&3) || return 1

    if [ "$confirm" != "$name" ]; then
        whiptail --title "Remove: $name" --msgbox "Name mismatch — removal cancelled." 8 50
        return 1
    fi

    systemctl stop "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/${svc}.service"
    systemctl daemon-reload

    $PM2_BIN delete "$pm2" 2>/dev/null || true
    $PM2_BIN save 2>/dev/null || true

    rm -f "/etc/nginx/sites-enabled/pmbot-${name}"
    rm -f "/etc/nginx/sites-available/pmbot-${name}"
    nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true

    rm -rf "$app_dir"

    whiptail --title "Remove: $name" --msgbox "Instance '$name' removed." 8 50
    return 0
}

# ============================================
#  Deploy new instance (calls onboard.sh)
# ============================================
deploy_new_instance() {
    if [ ! -f "$SCRIPT_DIR/onboard.sh" ]; then
        whiptail --title "Deploy New" --msgbox "onboard.sh not found in $SCRIPT_DIR" 8 55
        return
    fi

    whiptail --title "Deploy New Instance" --yesno \
        "This will launch the onboard.sh script to deploy a new instance.\n\nThe TUI will resume after deployment completes." \
        10 60 || return

    clear
    bash "$SCRIPT_DIR/onboard.sh"
    echo ""
    echo -e "${GREEN}Returning to manager...${NC}"
    sleep 2
}

# ============================================
#  Entry point
# ============================================
main_menu
