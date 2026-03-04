#!/usr/bin/env bash
# PMBot Instance Manager — TUI
# Usage: sudo bash manage.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="pmbot"
NPM_CACHE_DIR="/opt/pmbot/.npm-cache"

R='\033[0;31m' G='\033[0;32m' C='\033[0;36m'
Y='\033[1;33m' B='\033[1m'   N='\033[0m'

[ "$EUID" -ne 0 ] && { echo -e "${R}Run as root: sudo bash manage.sh${N}"; exit 1; }
command -v whiptail &>/dev/null || apt-get install -y -qq whiptail
PYTHON_BIN=$(command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || echo python3)
PM2_BIN=$(command -v pm2 2>/dev/null || echo pm2)

# ─── whiptail helpers (tempfile + /dev/tty so fd redirects can't interfere) ───

# wt_menu   VAR  title  h w  "item" "desc" ...
# wt_check  VAR  title  h w  "item" "desc" ON/OFF ...
# wt_input  VAR  title  prompt  h w  [default]
# wt_yesno  title  question  h w         → returns 0=yes 1=no
# wt_msg    title  text  h w

_WT() {
    local _out _rc _tmp
    _tmp=$(mktemp)
    whiptail "$@" 2>"$_tmp" </dev/tty >/dev/tty
    _rc=$?
    _WT_OUT=$(cat "$_tmp")
    rm -f "$_tmp"
    return $_rc
}

wt_menu()  {  # VAR title h w items...
    local _var=$1 _title=$2 _h=$3 _w=$4; shift 4
    _WT --title "$_title" --menu "" "$_h" "$_w" $(( _h - 6 )) "$@" && \
        printf -v "$_var" '%s' "${_WT_OUT//\"/}"
}

wt_check() {  # VAR title h w items...
    local _var=$1 _title=$2 _h=$3 _w=$4; shift 4
    _WT --title "$_title" --checklist "" "$_h" "$_w" $(( _h - 7 )) "$@"
    local _rc=$?
    printf -v "$_var" '%s' "$_WT_OUT"
    return $_rc
}

wt_input() {  # VAR title prompt h w [default]
    local _var=$1 _title=$2 _prompt=$3 _h=$4 _w=$5 _def=${6:-}
    _WT --title "$_title" --inputbox "$_prompt" "$_h" "$_w" "$_def" && \
        printf -v "$_var" '%s' "$_WT_OUT"
}

wt_yesno() { _WT --title "$1" --yesno "$2" "$3" "$4"; }

wt_msg()   { _WT --title "$1" --msgbox "$2" "$3" "$4"; }

# ─── instance info ───

scan_instances() {
    INSTANCES=()
    for d in /opt/pmbot-*/; do
        [ -d "${d}backend" ] && INSTANCES+=("$(basename "$d" | sed 's/^pmbot-//')")
    done
}

iport()  { grep -oP 'Environment=PORT=\K[0-9]+' \
               "/etc/systemd/system/pmbot-${1}-backend.service" 2>/dev/null || echo "?"; }
nport()  { grep -oP 'listen\s+\K[0-9]+' \
               "/etc/nginx/sites-available/pmbot-${1}" 2>/dev/null | head -1 || echo "?"; }
istatus(){ systemctl is-active "pmbot-${1}-backend" 2>/dev/null || echo "inactive"; }
imode()  {
    local e="/opt/pmbot-${1}/backend/.env"
    if grep -q "^DRY_RUN=true" "$e" 2>/dev/null; then echo "DRY"
    elif grep -qP "^PRIVATE_KEY=.+" "$e" 2>/dev/null && \
         ! grep -q "your_private_key" "$e" 2>/dev/null; then echo "LIVE"
    else echo "DRY"; fi
}

setenv() {   # key val file
    grep -q "^${1}=" "${3}" 2>/dev/null \
        && sed -i "s|^${1}=.*|${1}=${2}|" "${3}" \
        || echo "${1}=${2}" >> "${3}"
}

# ─────────────────────────────────────────────
#  Main menu
# ─────────────────────────────────────────────

main_menu() {
    while true; do
        scan_instances

        if [ ${#INSTANCES[@]} -eq 0 ]; then
            echo -e "${Y}No instances found in /opt/pmbot-*/${N}"
            echo -e "Run: ${C}sudo bash onboard.sh${N}"
            read -rp "Press Enter to exit..." _; exit 0
        fi

        local items=()
        for n in "${INSTANCES[@]}"; do
            local st md bp np ic
            st=$(istatus "$n"); md=$(imode "$n")
            bp=$(iport "$n");   np=$(nport "$n")
            [ "$st" = "active" ] && ic="▶" || ic="■"
            items+=("$n"
                "$(printf '%-9s [%-4s] api:%-5s nginx:%-5s' "$ic $st" "$md" "$bp" "$np")"
                "OFF")
        done
        items+=("---"    "─────────────────────────────────────" "OFF")
        items+=("[new]"  "Deploy a new instance"                 "OFF")
        items+=("[quit]" "Exit"                                  "OFF")

        local sel
        wt_check sel \
            "PMBot Manager  $(date '+%H:%M:%S')  •  ${#INSTANCES[@]} instance(s)" \
            24 74 "${items[@]}" || exit 0

        # parse quoted tokens
        local chosen=()
        for w in $sel; do chosen+=("${w//\"/}"); done
        [ ${#chosen[@]} -eq 0 ] && continue

        # filter specials
        local real=()
        for s in "${chosen[@]}"; do
            case "$s" in
                "---")    ;;
                "[quit]") exit 0 ;;
                "[new]")  deploy_new ;;
                *)        real+=("$s") ;;
            esac
        done

        [ ${#real[@]} -eq 0 ] && continue
        [ ${#real[@]} -eq 1 ] && instance_menu "${real[0]}" || bulk_menu "${real[@]}"
    done
}

# ─────────────────────────────────────────────
#  Single instance menu
# ─────────────────────────────────────────────

instance_menu() {
    local name="$1"
    while true; do
        local st bp np md line
        st=$(istatus "$name"); md=$(imode "$name")
        bp=$(iport "$name");   np=$(nport "$name")
        [ "$st" = "active" ] && line="▶ $st" || line="■ $st"
        line="$line  |  API :$bp  |  Nginx :$np  |  [$md]"

        local action
        wt_menu action "Instance: $name" 22 66 \
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
            "back"     "← Back" || return

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
    local disp; disp=$(printf '%s  ' "${sel[@]}")
    while true; do
        local action
        wt_menu action "Bulk — ${#sel[@]} instances selected" 20 72 \
            "start"   "Start all" \
            "stop"    "Stop all" \
            "restart" "Restart all" \
            "status"  "Status summary + recent logs" \
            "update"  "Update all from source" \
            "pentest" "Pentest all" \
            "logs"    "Tail logs (tmux panes or sequential)" \
            "back"    "← Back" || return

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
    out=$(systemctl status "pmbot-${1}-backend" --no-pager -l 2>&1 | head -25)
    out+=$'\n\n─── Last 10 log lines ───\n'
    out+=$(journalctl -u "pmbot-${1}-backend" --no-pager -n 10 --output=cat 2>&1)
    wt_msg "Status: $1" "$out" 28 80
}

do_start() {
    systemctl start "pmbot-${1}-backend" 2>/dev/null; sleep 1
    wt_msg "Start: $1" "Status: $(istatus "$1")" 7 44
}

do_stop() {
    wt_yesno "Stop: $1" "Stop pmbot-${1}-backend?" 7 46 || return
    systemctl stop "pmbot-${1}-backend" 2>/dev/null
    wt_msg "Stop: $1" "Stopped." 7 36
}

do_restart() {
    systemctl restart "pmbot-${1}-backend" 2>/dev/null; sleep 1
    wt_msg "Restart: $1" "Status: $(istatus "$1")" 7 46
}

do_update() {
    local name="$1"
    local src_items=()
    for c in backend m5-backend hourly-backend 4h-backend daily-backend; do
        [ -d "$SCRIPT_DIR/$c" ] && src_items+=("$c" "")
    done
    [ ${#src_items[@]} -eq 0 ] && { wt_msg "Update" "No source dirs in $SCRIPT_DIR" 7 52; return; }

    local src
    wt_menu src "Update: $name" 14 52 "${src_items[@]}" || return

    wt_yesno "Update: $name" \
        "Sync '$name' from '$src'?\n\n• .env / .auth.json / databases preserved\n• Deps reinstalled\n• Frontend rebuilt\n• Service restarted" \
        13 58 || return

    _run_update "$name" "$src"

    read -rp "Run pentest now? [Y/n]: " pt; pt=${pt:-Y}
    [[ "$pt" =~ ^[Yy]$ ]] && do_pentest "$name" || read -rp "Press Enter..." _
}

_run_update() {
    local name="$1" src="$2"
    local app_dir="/opt/pmbot-${name}"
    local svc="pmbot-${name}-backend"
    clear
    echo -e "${B}${C}━━━ Updating: $name ← $src ━━━${N}\n"

    echo -e "${C}[1/5] Stopping...${N}"
    systemctl stop "$svc" 2>/dev/null || true
    $PM2_BIN stop "pmbot-${name}-frontend" 2>/dev/null || true; echo "      done"

    echo -e "${C}[2/5] Syncing code...${N}"
    rsync -a --delete \
        --exclude '.env' --exclude '.auth.json' \
        --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
        --exclude '__pycache__' --exclude '*.pyc' \
        "$SCRIPT_DIR/${src}/" "$app_dir/backend/"
    local fsrc="${src/backend/frontend}"
    [ -d "$SCRIPT_DIR/$fsrc" ] && rsync -a --delete \
        --exclude 'node_modules' --exclude 'dist' \
        "$SCRIPT_DIR/${fsrc}/" "$app_dir/frontend/"
    chown -R "$APP_USER:$APP_USER" "$app_dir"; echo "      done"

    echo -e "${C}[3/5] Reinstalling Python deps...${N}"
    runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q --upgrade pip
    runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q \
        -r "$app_dir/backend/requirements.txt"; echo "      done"

    if [ -d "$app_dir/frontend" ]; then
        echo -e "${C}[4/5] Rebuilding frontend...${N}"
        mkdir -p "$NPM_CACHE_DIR"; chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"
        rm -rf "$app_dir/frontend/dist" "$app_dir/frontend/node_modules/.vite"
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" \
            npm --prefix "$app_dir/frontend" install --no-audit --no-fund
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" \
            npm --prefix "$app_dir/frontend" run build; echo "      done"
    else echo -e "${C}[4/5] No frontend — skip${N}"; fi

    echo -e "${C}[5/5] Restarting...${N}"
    systemctl start "$svc"
    if [ -d "$app_dir/frontend" ]; then
        $PM2_BIN delete "pmbot-${name}-frontend" 2>/dev/null || true
        $PM2_BIN start "npx vite preview --host 0.0.0.0 --port 3000" \
            --name "pmbot-${name}-frontend" --cwd "$app_dir/frontend" --uid "$APP_USER"
        $PM2_BIN save
    fi
    echo "      done"
    echo -e "\n${G}✓ $name updated  ($(istatus "$name"))${N}\n"
}

do_env() {
    local env="/opt/pmbot-${1}/backend/.env"
    [ -f "$env" ] || { wt_msg "Edit .env" "No .env at $env" 7 52; return; }
    wt_yesno "Edit .env: $1" \
        "Open $env in nano?\n\nCtrl+O save  •  Ctrl+X exit\nService restarted after save." \
        9 58 || return
    nano "$env" </dev/tty >/dev/tty
    wt_yesno "Edit .env: $1" "Restart pmbot-${1}-backend now?" 7 50 || return
    systemctl restart "pmbot-${1}-backend"
    wt_msg "Edit .env: $1" "Restarted." 7 36
}

do_wallet() {
    local name="$1"
    local env="/opt/pmbot-${name}/backend/.env"
    local cur; cur=$(grep -oP '^PRIVATE_KEY=\K.*' "$env" 2>/dev/null || echo "")

    local pk
    wt_input pk "Wallet: $name" \
        "New private key (blank = keep ${cur:0:8}...):" 9 66 || return
    [ -z "$pk" ] && { wt_msg "Wallet" "No change." 7 36; return; }

    local fa
    wt_input fa "Wallet: $name" "Funder address (blank for EOA):" 9 66 || return

    local st
    wt_menu st "Wallet: $name" 12 46 \
        "0" "EOA (plain wallet)" \
        "1" "Magic Link" \
        "2" "Gnosis Safe" || return

    setenv "PRIVATE_KEY"    "$pk"    "$env"
    setenv "FUNDER_ADDRESS" "$fa"    "$env"
    setenv "SIGNATURE_TYPE" "$st"    "$env"
    setenv "DRY_RUN"        "false"  "$env"
    chmod 600 "$env"

    wt_yesno "Wallet: $name" "Restart to apply?" 7 42 || return
    systemctl restart "pmbot-${name}-backend"
    wt_msg "Wallet: $name" "Restarted." 7 36
}

do_logs() {
    clear
    echo -e "${B}${C}Live logs: pmbot-${1}-backend  (Ctrl+C to exit)${N}\n"
    journalctl -u "pmbot-${1}-backend" -f --no-pager -n 50
}

do_pentest() {
    local name="$1"
    local np bp target
    np=$(nport "$name"); bp=$(iport "$name")
    [ "$np" = "?" ] && target="http://127.0.0.1:${bp}" || target="http://127.0.0.1:${np}"

    local pdir="$SCRIPT_DIR/pentest"
    local pvenv="$pdir/venv"
    local pscript="$pdir/pentest_bot.py"

    if [ ! -f "$pscript" ]; then
        echo -e "${R}pentest/pentest_bot.py not found${N}"
        read -rp "Press Enter..." _; return
    fi
    if [ ! -f "$pvenv/bin/python" ]; then
        echo -e "${C}Setting up pentest venv...${N}"
        $PYTHON_BIN -m venv "$pvenv"
        "$pvenv/bin/pip" install -q --upgrade pip
        "$pvenv/bin/pip" install -q -r "$pdir/requirements.txt"
    fi

    echo -e "${C}Waiting for $target ...${N}"
    local r=12
    while [ $r -gt 0 ]; do
        curl -sf "${target}/api/auth/status" >/dev/null 2>&1 && break
        sleep 2; ((r--))
    done
    [ $r -eq 0 ] && echo -e "${Y}⚠ Service not responding — results may be incomplete${N}"

    local logfile="/opt/pmbot-${name}/pentest-$(date +%Y%m%d-%H%M%S).log"
    echo -e "\n${B}${C}━━━ Pentest: $name @ $target ━━━${N}\n"
    "$pvenv/bin/python" "$pscript" --target "$target" --logfile "$logfile"
    local rc=$?
    echo -e "\n  Log: $logfile\n"
    case $rc in
        0) echo -e "${G}✅ Clean — no critical/high/medium issues.${N}" ;;
        1) echo -e "${Y}⚠  Medium issues found — review above.${N}" ;;
        2) echo -e "${R}🔴 Critical/High issues — action required!${N}" ;;
    esac
    echo ""; read -rp "Press Enter..." _
}

do_remove() {
    local name="$1"
    wt_yesno "Remove: $name" \
        "⚠  REMOVE: $name\n\nDeletes:\n• systemd service\n• nginx config\n• /opt/pmbot-$name/\n\nIrreversible. Continue?" \
        13 56 || return 1

    local confirm
    wt_input confirm "Remove: $name" "Type instance name to confirm:" 8 52 || return 1
    [ "$confirm" != "$name" ] && { wt_msg "Remove" "Name mismatch — cancelled." 7 46; return 1; }

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
    wt_msg "Remove" "'$name' removed." 7 40
    return 0
}

# ─────────────────────────────────────────────
#  Bulk actions
# ─────────────────────────────────────────────

bulk_start() {
    clear; echo -e "${B}${C}━━━ Start ━━━${N}\n"
    for n in "$@"; do
        printf "  %-28s" "pmbot-${n}-backend..."
        systemctl start "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${G}ok${N}" || echo -e "${R}failed${N}"
    done; echo ""; read -rp "Press Enter..." _
}

bulk_stop() {
    wt_yesno "Bulk Stop" "Stop all ${#@} selected backends?" 7 50 || return
    clear; echo -e "${B}${C}━━━ Stop ━━━${N}\n"
    for n in "$@"; do
        printf "  %-28s" "pmbot-${n}-backend..."
        systemctl stop "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${G}ok${N}" || echo -e "${R}failed${N}"
    done; echo ""; read -rp "Press Enter..." _
}

bulk_restart() {
    clear; echo -e "${B}${C}━━━ Restart ━━━${N}\n"
    for n in "$@"; do
        printf "  %-28s" "pmbot-${n}-backend..."
        systemctl restart "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${G}$(istatus "$n")${N}" || echo -e "${R}failed${N}"
    done; echo ""; read -rp "Press Enter..." _
}

bulk_status() {
    clear; echo -e "${B}${C}━━━ Status Summary ━━━${N}\n"
    printf "  ${B}%-22s %-10s %-6s :%-5s :%-5s${N}\n" \
        "Instance" "Status" "Mode" "API" "Nginx"
    printf "  %-22s %-10s %-6s %-6s %-6s\n" \
        "────────" "──────" "────" "───" "─────"
    for n in "$@"; do
        local st md bp np
        st=$(istatus "$n"); md=$(imode "$n")
        bp=$(iport "$n");   np=$(nport "$n")
        local col="${R}"
        [ "$st" = "active" ] && col="${G}"
        printf "  ${col}%-22s${N} %-10s %-6s :%-5s :%-5s\n" \
            "$n" "$([ "$st"=active ]&&echo "▶ $st"||echo "■ $st")" "$md" "$bp" "$np"
    done
    echo ""
    for n in "$@"; do
        echo -e "${C}  ── $n last 3 lines:${N}"
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
    [ ${#src_items[@]} -eq 0 ] && { wt_msg "Bulk Update" "No source dirs found." 7 44; return; }

    local src
    wt_menu src "Bulk Update — source" 14 54 "${src_items[@]}" || return

    wt_yesno "Bulk Update" \
        "Update ALL ${#sel[@]} instances from '$src'?\n\n$(printf '  • %s\n' "${sel[@]}")" \
        $((${#sel[@]}+8)) 58 || return

    for n in "${sel[@]}"; do _run_update "$n" "$src"; done

    read -rp "Pentest all? [Y/n]: " pt; pt=${pt:-Y}
    [[ "$pt" =~ ^[Yy]$ ]] && bulk_pentest "${sel[@]}" || read -rp "Press Enter..." _
}

bulk_pentest() {
    for n in "$@"; do clear; do_pentest "$n"; done
}

bulk_logs() {
    if command -v tmux &>/dev/null; then
        local sess="pmbot-$$"
        tmux new-session -d -s "$sess" -x 220 -y 50
        local first=true
        for n in "$@"; do
            if [ "$first" = true ]; then
                tmux send-keys -t "$sess" \
                    "journalctl -u pmbot-${n}-backend -f --no-pager -n 30" Enter
                first=false
            else
                tmux split-window -t "$sess" \
                    "journalctl -u pmbot-${n}-backend -f --no-pager -n 30"
                tmux select-layout -t "$sess" tiled
            fi
        done
        echo -e "${C}tmux '$sess' — Ctrl+B D to detach${N}"; sleep 1
        tmux attach-session -t "$sess"
    else
        clear
        for n in "$@"; do
            echo -e "${C}━━━ $n (Ctrl+C for next) ━━━${N}"
            journalctl -u "pmbot-${n}-backend" -f --no-pager -n 20 || true
        done
        read -rp "Press Enter..." _
    fi
}

# ─────────────────────────────────────────────
#  Deploy new
# ─────────────────────────────────────────────

deploy_new() {
    [ -f "$SCRIPT_DIR/onboard.sh" ] || \
        { wt_msg "Deploy" "onboard.sh not found in $SCRIPT_DIR" 7 54; return; }
    wt_yesno "Deploy New" "Launch onboard.sh?\nManager resumes after." 8 50 || return
    clear; bash "$SCRIPT_DIR/onboard.sh"
    echo -e "\n${G}Returning to manager...${N}"; sleep 2
}

# ─────────────────────────────────────────────
main_menu
