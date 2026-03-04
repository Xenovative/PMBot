#!/usr/bin/env bash
# ============================================
#  PMBot Instance Manager — TUI
#  Requires: whiptail (pre-installed on Ubuntu/Debian)
#  Run as root: sudo bash manage.sh
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="pmbot"
NPM_CACHE_DIR="/opt/pmbot/.npm-cache"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root: sudo bash manage.sh${NC}"
    exit 1
fi

if ! command -v whiptail &>/dev/null; then
    echo "Installing whiptail..."
    apt-get install -y whiptail
fi

if ! command -v whiptail &>/dev/null; then
    echo -e "${RED}whiptail not found and could not be installed. Try: apt-get install whiptail${NC}"
    exit 1
fi


PYTHON_BIN=$(command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || echo python3)
PM2_BIN=$(command -v pm2 2>/dev/null || echo pm2)

# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

scan_instances() {
    INSTANCES=()
    for d in /opt/pmbot-*/; do
        [ -d "${d}backend" ] && INSTANCES+=("$(basename "$d" | sed 's/^pmbot-//')")
    done
}

inst_backend_port() {
    grep -oP 'Environment=PORT=\K[0-9]+' \
        "/etc/systemd/system/pmbot-${1}-backend.service" 2>/dev/null || echo "?"
}

inst_nginx_port() {
    grep -oP 'listen\s+\K[0-9]+' \
        "/etc/nginx/sites-available/pmbot-${1}" 2>/dev/null | head -1 || echo "?"
}

inst_status() {
    systemctl is-active "pmbot-${1}-backend" 2>/dev/null || echo "inactive"
}

inst_mode() {
    local env="/opt/pmbot-${1}/backend/.env"
    if grep -q "^DRY_RUN=true" "$env" 2>/dev/null; then echo "DRY"
    elif grep -qP "^PRIVATE_KEY=.+" "$env" 2>/dev/null && \
         ! grep -q "your_private_key" "$env" 2>/dev/null; then echo "LIVE"
    else echo "DRY"; fi
}

set_env_val() {   # key value file
    if grep -q "^${1}=" "${3}" 2>/dev/null; then
        sed -i "s|^${1}=.*|${1}=${2}|" "${3}"
    else
        echo "${1}=${2}" >> "${3}"
    fi
}

# ─────────────────────────────────────────────
#  Main menu (checklist)
# ─────────────────────────────────────────────

main_menu() {
    while true; do
        scan_instances

        if [ ${#INSTANCES[@]} -eq 0 ]; then
            echo -e "${YELLOW}No deployed instances found in /opt/pmbot-*/${NC}"
            echo -e "Run ${CYAN}sudo bash onboard.sh${NC} to deploy your first instance."
            echo ""
            read -rp "Press Enter to exit..." _
            exit 0
        fi

        local items=()
        for n in "${INSTANCES[@]}"; do
            local st mode bp np icon
            st=$(inst_status "$n"); mode=$(inst_mode "$n")
            bp=$(inst_backend_port "$n"); np=$(inst_nginx_port "$n")
            [ "$st" = "active" ] && icon="▶" || icon="■"
            items+=("$n" "$(printf '%-9s [%-4s] api:%-5s nginx:%-5s' "$icon $st" "$mode" "$bp" "$np")" "OFF")
        done
        items+=("---"    "─────────────────────────────────" "OFF")
        items+=("[new]"  "Deploy a new instance"             "OFF")
        items+=("[quit]" "Exit"                              "OFF")

        local sel
        sel=$(whiptail --title "PMBot Manager  ($(date '+%H:%M:%S')  •  ${#INSTANCES[@]} instance(s))" \
            --checklist \
            "SPACE=select  ENTER=confirm  •  1 instance → manage, 2+→ bulk" \
            24 72 16 \
            "${items[@]}" 3>&1 1>&2 2>&3) || exit 0

        # strip quotes, build array
        local chosen=()
        for w in $sel; do chosen+=("${w//\"/}"); done
        [ ${#chosen[@]} -eq 0 ] && continue

        # filter specials
        local real=()
        for s in "${chosen[@]}"; do
            [[ "$s" == "---" || "$s" == "[new]" || "$s" == "[quit]" ]] && continue
            real+=("$s")
        done

        # handle specials if only specials chosen
        if [ ${#real[@]} -eq 0 ]; then
            for s in "${chosen[@]}"; do
                case "$s" in
                    "[quit]"|"---") exit 0 ;;
                    "[new]") deploy_new ;;
                esac
            done
            continue
        fi

        if [ ${#real[@]} -eq 1 ]; then
            instance_menu "${real[0]}"
        else
            bulk_menu "${real[@]}"
        fi
    done
}

# ─────────────────────────────────────────────
#  Single instance menu
# ─────────────────────────────────────────────

instance_menu() {
    local name="$1"
    while true; do
        local st bp np mode sline
        st=$(inst_status "$name"); mode=$(inst_mode "$name")
        bp=$(inst_backend_port "$name"); np=$(inst_nginx_port "$name")
        [ "$st" = "active" ] && sline="▶ $st" || sline="■ $st"
        sline="$sline  |  API :$bp  |  Nginx :$np  |  [$mode]"

        local action
        action=$(whiptail --title "Instance: $name" \
            --menu "$sline" 22 66 12 \
            "status"   "Service status + recent logs" \
            "start"    "Start backend" \
            "stop"     "Stop backend" \
            "restart"  "Restart backend" \
            "update"   "Update code from source" \
            "env"      "Edit .env in nano" \
            "wallet"   "Change wallet / private key" \
            "logs"     "Tail live logs" \
            "pentest"  "Run security pentest" \
            "remove"   "Remove this instance" \
            "back"     "← Back" \
            3>&1 1>&2 2>&3) || return

        case "$action" in
            status)  do_status  "$name" ;;
            start)   do_start   "$name" ;;
            stop)    do_stop    "$name" ;;
            restart) do_restart "$name" ;;
            update)  do_update  "$name" ;;
            env)     do_env     "$name" ;;
            wallet)  do_wallet  "$name" ;;
            logs)    do_logs    "$name" ;;
            pentest) clear; do_pentest "$name" ;;
            remove)  do_remove  "$name" && return ;;
            back|*)  return ;;
        esac
    done
}

# ─────────────────────────────────────────────
#  Bulk menu
# ─────────────────────────────────────────────

bulk_menu() {
    local sel=("$@")
    local display
    display=$(printf '%s  ' "${sel[@]}")

    while true; do
        local action
        action=$(whiptail --title "Bulk Actions — ${#sel[@]} instances" \
            --menu "Selected: $display\n\nAction to apply to ALL:" \
            20 72 9 \
            "start"   "Start all" \
            "stop"    "Stop all" \
            "restart" "Restart all" \
            "status"  "Status summary + recent logs" \
            "update"  "Update code on all" \
            "pentest" "Pentest all" \
            "logs"    "Tail logs (tmux split or sequential)" \
            "back"    "← Back" \
            3>&1 1>&2 2>&3) || return

        case "$action" in
            start)   bulk_start   "${sel[@]}" ;;
            stop)    bulk_stop    "${sel[@]}" ;;
            restart) bulk_restart "${sel[@]}" ;;
            status)  bulk_status  "${sel[@]}" ;;
            update)  bulk_update  "${sel[@]}" ;;
            pentest) bulk_pentest "${sel[@]}" ;;
            logs)    bulk_logs    "${sel[@]}" ;;
            back|*)  return ;;
        esac
    done
}

# ─────────────────────────────────────────────
#  Single-instance actions
# ─────────────────────────────────────────────

do_status() {
    local out
    out=$(systemctl status "pmbot-${1}-backend" --no-pager -l 2>&1 | head -30)
    out+=$'\n\n─── Last 10 log lines ───\n'
    out+=$(journalctl -u "pmbot-${1}-backend" --no-pager -n 10 --output=cat 2>&1)
    whiptail --title "Status: $1" --scrolltext --msgbox "$out" 28 80
}

do_start() {
    systemctl start "pmbot-${1}-backend" 2>/dev/null; sleep 1
    whiptail --title "Start: $1" --msgbox "Status: $(inst_status "$1")" 7 44
}

do_stop() {
    whiptail --title "Stop: $1" --yesno "Stop pmbot-${1}-backend?" 7 44 || return
    systemctl stop "pmbot-${1}-backend" 2>/dev/null
    whiptail --title "Stop: $1" --msgbox "Stopped." 7 36
}

do_restart() {
    systemctl restart "pmbot-${1}-backend" 2>/dev/null; sleep 1
    whiptail --title "Restart: $1" --msgbox "Status: $(inst_status "$1")" 7 44
}

do_update() {
    local name="$1"
    local app_dir="/opt/pmbot-${name}"
    local svc="pmbot-${name}-backend"

    # Build source list
    local src_items=()
    for c in backend m5-backend hourly-backend 4h-backend daily-backend; do
        [ -d "$SCRIPT_DIR/$c" ] && src_items+=("$c" "")
    done
    [ ${#src_items[@]} -eq 0 ] && {
        whiptail --title "Update" --msgbox "No source directories found in $SCRIPT_DIR" 8 55
        return
    }

    local src
    src=$(whiptail --title "Update: $name" \
        --menu "Select source directory:" 14 52 6 \
        "${src_items[@]}" 3>&1 1>&2 2>&3) || return

    whiptail --title "Update: $name" --yesno \
        "Sync $name from '$src'?\n\n• Preserve .env, .auth.json, databases\n• Reinstall deps\n• Rebuild frontend\n• Restart service" \
        13 56 || return

    _run_update "$name" "$src"

    read -rp "Run pentest now? [Y/n]: " pt; pt=${pt:-Y}
    [[ "$pt" =~ ^[Yy]$ ]] && do_pentest "$name" || read -rp "Press Enter to continue..." _
}

_run_update() {      # name src
    local name="$1" src="$2"
    local app_dir="/opt/pmbot-${name}"
    local svc="pmbot-${name}-backend"

    clear
    echo -e "${BOLD}${CYAN}━━━ Updating: $name from $src ━━━${NC}\n"

    echo -e "${CYAN}[1/5] Stopping services...${NC}"
    systemctl stop "$svc" 2>/dev/null || true
    $PM2_BIN stop "pmbot-${name}-frontend" 2>/dev/null || true
    echo "      done"

    echo -e "${CYAN}[2/5] Syncing code...${NC}"
    rsync -a --delete \
        --exclude '.env' --exclude '.auth.json' \
        --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
        --exclude '__pycache__' --exclude '*.pyc' \
        "$SCRIPT_DIR/${src}/" "$app_dir/backend/"
    local fsrc="${src/backend/frontend}"
    if [ -d "$SCRIPT_DIR/$fsrc" ]; then
        rsync -a --delete --exclude 'node_modules' --exclude 'dist' \
            "$SCRIPT_DIR/${fsrc}/" "$app_dir/frontend/"
    fi
    chown -R "$APP_USER:$APP_USER" "$app_dir"
    echo "      done"

    echo -e "${CYAN}[3/5] Reinstalling Python deps...${NC}"
    runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q --upgrade pip
    runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q \
        -r "$app_dir/backend/requirements.txt"
    echo "      done"

    if [ -d "$app_dir/frontend" ]; then
        echo -e "${CYAN}[4/5] Rebuilding frontend...${NC}"
        mkdir -p "$NPM_CACHE_DIR"
        chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"
        rm -rf "$app_dir/frontend/dist" "$app_dir/frontend/node_modules/.vite"
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" \
            npm --prefix "$app_dir/frontend" install --no-audit --no-fund
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" \
            npm --prefix "$app_dir/frontend" run build
        echo "      done"
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
    echo "      done"
    echo ""
    echo -e "${GREEN}✓ $name updated  ($(inst_status "$name"))${NC}"
    echo ""
}

do_env() {
    local env="/opt/pmbot-${1}/backend/.env"
    [ -f "$env" ] || { whiptail --title "Edit .env" --msgbox "No .env at $env" 7 50; return; }
    whiptail --title "Edit .env: $1" --yesno \
        "Open $env in nano?\n\nCtrl+O save  •  Ctrl+X exit\nService will be restarted after." \
        9 56 || return
    nano "$env"
    whiptail --title "Edit .env: $1" --yesno "Restart pmbot-${1}-backend?" 7 48 && \
        systemctl restart "pmbot-${1}-backend" && \
        whiptail --title "Edit .env: $1" --msgbox "Restarted." 7 36
}

do_wallet() {
    local name="$1"
    local env="/opt/pmbot-${name}/backend/.env"
    local cur
    cur=$(grep -oP '^PRIVATE_KEY=\K.*' "$env" 2>/dev/null || echo "")

    local pk
    pk=$(whiptail --title "Wallet: $name" \
        --inputbox "New private key (blank = keep ${cur:0:8}...):" \
        9 62 "" 3>&1 1>&2 2>&3) || return
    [ -z "$pk" ] && { whiptail --title "Wallet" --msgbox "No change." 7 36; return; }

    local fa
    fa=$(whiptail --title "Wallet: $name" \
        --inputbox "Funder address (blank for EOA):" \
        9 62 "" 3>&1 1>&2 2>&3) || return

    local st
    st=$(whiptail --title "Wallet: $name" \
        --menu "Signature type:" 12 44 3 \
        "0" "EOA (plain wallet)" \
        "1" "Magic Link" \
        "2" "Gnosis Safe" \
        3>&1 1>&2 2>&3) || return

    set_env_val "PRIVATE_KEY"    "$pk"    "$env"
    set_env_val "FUNDER_ADDRESS" "$fa"    "$env"
    set_env_val "SIGNATURE_TYPE" "$st"    "$env"
    set_env_val "DRY_RUN"        "false"  "$env"
    chmod 600 "$env"

    whiptail --title "Wallet: $name" --yesno "Restart to apply?" 7 40 && \
        systemctl restart "pmbot-${name}-backend" && \
        whiptail --title "Wallet: $name" --msgbox "Restarted." 7 36
}

do_logs() {
    local svc="pmbot-${1}-backend"
    clear
    echo -e "${BOLD}${CYAN}Live logs: $svc  (Ctrl+C to exit)${NC}\n"
    journalctl -u "$svc" -f --no-pager -n 50
}

do_pentest() {
    local name="$1"
    local np bp target
    np=$(inst_nginx_port "$name"); bp=$(inst_backend_port "$name")
    [ "$np" = "?" ] && target="http://127.0.0.1:${bp}" || target="http://127.0.0.1:${np}"

    local pdir="$SCRIPT_DIR/pentest"
    local pvenv="$pdir/venv"
    local pscript="$pdir/pentest_bot.py"

    if [ ! -f "$pscript" ]; then
        echo -e "${RED}pentest/pentest_bot.py not found${NC}"
        read -rp "Press Enter..." _; return
    fi

    if [ ! -f "$pvenv/bin/python" ]; then
        echo -e "${CYAN}Setting up pentest venv...${NC}"
        $PYTHON_BIN -m venv "$pvenv"
        "$pvenv/bin/pip" install -q --upgrade pip
        "$pvenv/bin/pip" install -q -r "$pdir/requirements.txt"
    fi

    echo -e "${CYAN}Waiting for $target ...${NC}"
    local r=12
    while [ $r -gt 0 ]; do
        curl -sf "${target}/api/auth/status" >/dev/null 2>&1 && break
        sleep 2; ((r--))
    done
    [ $r -eq 0 ] && echo -e "${YELLOW}⚠️  Service not responding — results may be incomplete${NC}"

    local logfile="/opt/pmbot-${name}/pentest-$(date +%Y%m%d-%H%M%S).log"
    echo ""
    echo -e "${BOLD}${CYAN}━━━ Pentest: $name @ $target ━━━${NC}\n"

    "$pvenv/bin/python" "$pscript" --target "$target" --logfile "$logfile"
    local rc=$?

    echo ""
    echo -e "  Log: $logfile"
    echo ""
    case $rc in
        0) echo -e "${GREEN}✅ Clean — no critical/high/medium issues.${NC}" ;;
        1) echo -e "${YELLOW}⚠️  Medium issues found — review above.${NC}" ;;
        2) echo -e "${RED}🔴 Critical/High issues — action required!${NC}" ;;
    esac
    echo ""
    read -rp "Press Enter to continue..." _
}

do_remove() {
    local name="$1"
    whiptail --title "Remove: $name" --yesno \
        "⚠️  REMOVE: $name\n\nThis deletes:\n• systemd service\n• nginx config\n• /opt/pmbot-$name/\n\nIrreversible. Continue?" \
        13 56 || return 1

    local confirm
    confirm=$(whiptail --title "Remove: $name" \
        --inputbox "Type instance name to confirm:" 8 50 "" \
        3>&1 1>&2 2>&3) || return 1
    [ "$confirm" != "$name" ] && \
        { whiptail --title "Remove" --msgbox "Name mismatch — cancelled." 7 44; return 1; }

    systemctl stop    "pmbot-${name}-backend" 2>/dev/null || true
    systemctl disable "pmbot-${name}-backend" 2>/dev/null || true
    rm -f "/etc/systemd/system/pmbot-${name}-backend.service"
    systemctl daemon-reload
    $PM2_BIN delete "pmbot-${name}-frontend" 2>/dev/null || true
    $PM2_BIN save 2>/dev/null || true
    rm -f "/etc/nginx/sites-enabled/pmbot-${name}"
    rm -f "/etc/nginx/sites-available/pmbot-${name}"
    nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
    rm -rf "/opt/pmbot-${name}"

    whiptail --title "Remove" --msgbox "'$name' removed." 7 40
    return 0
}

# ─────────────────────────────────────────────
#  Bulk actions
# ─────────────────────────────────────────────

bulk_start() {
    clear; echo -e "${BOLD}${CYAN}━━━ Start ━━━${NC}\n"
    for n in "$@"; do
        printf "  %-24s " "pmbot-${n}-backend..."
        systemctl start "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done
    echo ""; read -rp "Press Enter..." _
}

bulk_stop() {
    whiptail --title "Bulk Stop" --yesno "Stop backends for:\n$(printf '  %s\n' "$@")" \
        $((${#@}+7)) 52 || return
    clear; echo -e "${BOLD}${CYAN}━━━ Stop ━━━${NC}\n"
    for n in "$@"; do
        printf "  %-24s " "pmbot-${n}-backend..."
        systemctl stop "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done
    echo ""; read -rp "Press Enter..." _
}

bulk_restart() {
    clear; echo -e "${BOLD}${CYAN}━━━ Restart ━━━${NC}\n"
    for n in "$@"; do
        printf "  %-24s " "pmbot-${n}-backend..."
        systemctl restart "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${GREEN}$(inst_status "$n")${NC}" || echo -e "${RED}failed${NC}"
    done
    echo ""; read -rp "Press Enter..." _
}

bulk_status() {
    clear; echo -e "${BOLD}${CYAN}━━━ Status Summary ━━━${NC}\n"
    printf "  ${BOLD}%-22s %-10s %-6s :%-5s :%-5s${NC}\n" \
        "Instance" "Status" "Mode" "API" "Nginx"
    printf "  %-22s %-10s %-6s %-6s %-6s\n" \
        "────────" "──────" "────" "───" "─────"
    for n in "$@"; do
        local st mode bp np
        st=$(inst_status "$n"); mode=$(inst_mode "$n")
        bp=$(inst_backend_port "$n"); np=$(inst_nginx_port "$n")
        if [ "$st" = "active" ]; then
            printf "  ${GREEN}%-22s${NC} %-10s %-6s :%-5s :%-5s\n" "$n" "▶ $st" "$mode" "$bp" "$np"
        else
            printf "  ${RED}%-22s${NC} %-10s %-6s :%-5s :%-5s\n"   "$n" "■ $st" "$mode" "$bp" "$np"
        fi
    done
    echo ""
    for n in "$@"; do
        echo -e "${CYAN}  ── $n last 3 lines:${NC}"
        journalctl -u "pmbot-${n}-backend" --no-pager -n 3 --output=cat 2>/dev/null \
            | sed 's/^/    /'
        echo ""
    done
    read -rp "Press Enter..." _
}

bulk_update() {
    local sel=("$@")
    local src_items=()
    for c in backend m5-backend hourly-backend 4h-backend daily-backend; do
        [ -d "$SCRIPT_DIR/$c" ] && src_items+=("$c" "")
    done
    [ ${#src_items[@]} -eq 0 ] && {
        whiptail --title "Bulk Update" --msgbox "No source dirs found." 7 44; return
    }

    local src
    src=$(whiptail --title "Bulk Update" \
        --menu "Source to apply to ALL ${#sel[@]} instances:" \
        14 52 6 "${src_items[@]}" 3>&1 1>&2 2>&3) || return

    whiptail --title "Bulk Update" --yesno \
        "Update ALL ${#sel[@]} instances from '$src'?\n\n$(printf '  • %s\n' "${sel[@]}")" \
        $((${#sel[@]}+8)) 56 || return

    for n in "${sel[@]}"; do
        _run_update "$n" "$src"
    done

    read -rp "Run pentest on all? [Y/n]: " pt; pt=${pt:-Y}
    [[ "$pt" =~ ^[Yy]$ ]] && bulk_pentest "${sel[@]}" || read -rp "Press Enter..." _
}

bulk_pentest() {
    for n in "$@"; do
        clear
        echo -e "${BOLD}${CYAN}━━━ Pentest: $n ━━━${NC}\n"
        do_pentest "$n"
    done
}

bulk_logs() {
    if command -v tmux &>/dev/null; then
        local sess="pmbot-logs-$$"
        tmux new-session -d -s "$sess" -x 220 -y 50
        local first=true
        for n in "$@"; do
            local svc="pmbot-${n}-backend"
            if [ "$first" = true ]; then
                tmux send-keys -t "$sess" "journalctl -u $svc -f --no-pager -n 30" Enter
                first=false
            else
                tmux split-window -t "$sess" "journalctl -u $svc -f --no-pager -n 30"
                tmux select-layout -t "$sess" tiled
            fi
        done
        echo -e "${CYAN}tmux session '$sess' — Ctrl+B D to detach${NC}"
        sleep 1
        tmux attach-session -t "$sess"
    else
        clear
        for n in "$@"; do
            echo -e "${CYAN}━━━ $n (Ctrl+C for next) ━━━${NC}"
            journalctl -u "pmbot-${n}-backend" -f --no-pager -n 20 || true
        done
        read -rp "Press Enter..." _
    fi
}

# ─────────────────────────────────────────────
#  Deploy new
# ─────────────────────────────────────────────

deploy_new() {
    [ -f "$SCRIPT_DIR/onboard.sh" ] || {
        whiptail --title "Deploy" --msgbox "onboard.sh not found in $SCRIPT_DIR" 7 52
        return
    }
    whiptail --title "Deploy New" --yesno \
        "Launch onboard.sh?\n\nThe manager will resume after." 8 50 || return
    clear
    bash "$SCRIPT_DIR/onboard.sh"
    echo -e "\n${GREEN}Returning to manager...${NC}"; sleep 2
}

# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────
echo -e "${CYAN}PMBot Manager starting...${NC}"
echo -e "whiptail: $(command -v whiptail)"
echo -e "Instances: $(ls -d /opt/pmbot-*/backend 2>/dev/null | wc -l) found"
echo ""
main_menu
