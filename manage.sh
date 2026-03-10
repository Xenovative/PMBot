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

if ! command -v sqlite3 &>/dev/null; then
    apt-get install -y -qq sqlite3
fi

PYTHON_BIN=$(command -v python3.12 || command -v python3)
PM2_BIN=$(command -v pm2 || echo "pm2")

analytics_db_path() {
    local name="$1"
    local backend_dir="/opt/pmbot-${name}/backend"
    local env_file="$backend_dir/.env"
    local db_prefix="pmbot"
    local db_mode_suffix="live"

    if [[ "$name" == 4h* ]]; then
        db_prefix="pmbot_4h"
    elif [[ "$name" == hourly* ]]; then
        db_prefix="pmbot_hourly"
    elif [[ "$name" == daily* ]]; then
        db_prefix="pmbot_daily"
    elif [[ "$name" == m5* ]]; then
        db_prefix="pmbot_m5"
    fi

    if [ -f "$env_file" ] && grep -q '^DRY_RUN=true' "$env_file" 2>/dev/null; then
        db_mode_suffix="paper"
    fi

    local db_candidates=(
        "$backend_dir/${db_prefix}_${db_mode_suffix}.db"
        "$backend_dir/${db_prefix}_live.db"
        "$backend_dir/${db_prefix}_paper.db"
        "$backend_dir/pmbot_m5_live.db"
        "$backend_dir/pmbot_m5_paper.db"
        "$backend_dir/pmbot_hourly_live.db"
        "$backend_dir/pmbot_hourly_paper.db"
        "$backend_dir/pmbot_daily_live.db"
        "$backend_dir/pmbot_daily_paper.db"
        "$backend_dir/pmbot_4h_live.db"
        "$backend_dir/pmbot_4h_paper.db"
        "$backend_dir/pmbot.db"
    )
    local db_path
    for db_path in "${db_candidates[@]}"; do
        if [ -f "$db_path" ]; then
            echo "$db_path"
            return 0
        fi
    done
    return 1
}

analytics_run_sql() {
    local name="$1"
    local sql_query="$2"
    local db_path
    db_path=$(analytics_db_path "$name") || return 1
    sqlite3 -readonly -json "$db_path" "$sql_query"
}

 analytics_table_exists() {
     local name="$1"
     local table_name="$2"
     local db_path
     db_path=$(analytics_db_path "$name") || return 1
     local table_count
     table_count=$(sqlite3 -readonly "$db_path" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='${table_name}';" 2>/dev/null)
     [ "$table_count" = "1" ]
 }

 analytics_require_tables() {
     local name="$1"
     shift
     local required_table_name
     for required_table_name in "$@"; do
         if ! analytics_table_exists "$name" "$required_table_name"; then
             whiptail --title "Analytics: $name" --msgbox "Analytics is not available for this backend variant yet. Missing table: ${required_table_name}" 10 70
             return 1
         fi
     done
     return 0
 }

analytics_overview_json() {
    local name="$1"
    local db_path
    db_path=$(analytics_db_path "$name") || return 1
    "$PYTHON_BIN" - "$db_path" <<'PY'
import json
import sqlite3
import sys

database_path = sys.argv[1]
connection = sqlite3.connect(database_path)
connection.row_factory = sqlite3.Row

trade_row = connection.execute(
    """
    SELECT
        COUNT(*) AS total_trades,
        SUM(CASE WHEN status IN ('executed','simulated') THEN 1 ELSE 0 END) AS successful,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
        COALESCE(SUM(profit), 0) AS total_profit,
        COALESCE(AVG(profit), 0) AS avg_profit,
        COALESCE(MAX(profit), 0) AS best_trade_profit,
        COALESCE(MIN(profit), 0) AS worst_trade_profit,
        COALESCE(SUM(order_size * total_cost), 0) AS total_volume
    FROM trades
    """
).fetchone()

today_row = connection.execute(
    """
    SELECT
        COUNT(*) AS trades,
        COALESCE(SUM(profit), 0) AS profit
    FROM trades
    WHERE date(timestamp) = date('now')
    """
).fetchone()

merge_row = connection.execute(
    """
    SELECT
        COUNT(*) AS total_merges,
        COALESCE(SUM(usdc_received), 0) AS total_usdc
    FROM merges
    WHERE status IN ('success','simulated')
    """
).fetchone()

successful_count = trade_row["successful"] or 0
failed_count = trade_row["failed"] or 0
total_valid = successful_count or 1

payload = {
    "total_trades": trade_row["total_trades"] or 0,
    "successful": successful_count,
    "failed": failed_count,
    "total_profit": round(trade_row["total_profit"] or 0, 4),
    "avg_profit": round(trade_row["avg_profit"] or 0, 4),
    "best_trade_profit": round(trade_row["best_trade_profit"] or 0, 4),
    "worst_trade_profit": round(trade_row["worst_trade_profit"] or 0, 4),
    "total_volume": round(trade_row["total_volume"] or 0, 4),
    "success_rate": round(successful_count / total_valid * 100, 1),
    "today_trades": today_row["trades"] or 0,
    "today_profit": round(today_row["profit"] or 0, 4),
    "total_merges": merge_row["total_merges"] or 0,
    "total_merge_usdc": round(merge_row["total_usdc"] or 0, 2),
}

print(json.dumps(payload, ensure_ascii=False))
PY
}

analytics_format_overview() {
    local analytics_payload
    analytics_payload=$(cat)
    ANALYTICS_PAYLOAD="$analytics_payload" "$PYTHON_BIN" - <<'PY'
import json
import os

payload_text = os.environ.get("ANALYTICS_PAYLOAD", "").strip()
if not payload_text:
    payload = {}
else:
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = {}

total_trades = payload.get('total_trades', 0)
successful_trades = payload.get('successful', 0)
success_rate = 0
try:
    success_rate = round((float(successful_trades) / float(total_trades) * 100), 1) if float(total_trades) > 0 else 0
except Exception:
    success_rate = 0

lines = [
    f"Total trades      : {total_trades}",
    f"Successful        : {successful_trades}",
    f"Failed            : {payload.get('failed', 0)}",
    f"Success rate      : {success_rate}%",
    f"Net PnL           : {payload.get('total_profit', 0)}",
    f"Gross Profit      : {payload.get('gross_profit', 0)}",
    f"Gross Loss        : {payload.get('gross_loss', 0)}",
    f"Average profit    : {payload.get('avg_profit', 0)}",
    f"Best trade        : {payload.get('best_trade', 0)}",
    f"Worst trade       : {payload.get('worst_trade', 0)}",
    f"Average cost      : {payload.get('avg_cost', 0)}",
    f"Unique markets    : {payload.get('unique_markets', 0)}",
    f"Today's trades    : {payload.get('today_trades', 0)}",
    f"Today's Net PnL   : {payload.get('today_profit', 0)}",
    f"Today's Profit    : {payload.get('today_gross_profit', 0)}",
    f"Today's Loss      : {payload.get('today_gross_loss', 0)}",
    f"Merge count       : {payload.get('total_merges', 0)}",
    f"Merge USDC        : {payload.get('total_merge_usdc', 0)}",
]
print("\n".join(lines))
PY
}

analytics_format_trades() {
    local analytics_payload
    analytics_payload=$(cat)
    ANALYTICS_PAYLOAD="$analytics_payload" "$PYTHON_BIN" - <<'PY'
import json
import os

payload_text = os.environ.get("ANALYTICS_PAYLOAD", "").strip()
if not payload_text:
    payload = []
else:
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = []
if not payload or not isinstance(payload, list):
    print("No trades found.")
    raise SystemExit(0)

formatted_lines = []
for trade_row in payload[:20]:
    formatted_lines.append(
        " | ".join([
            str(trade_row.get("timestamp", "")),
            str(trade_row.get("market_slug", "")),
            str(trade_row.get("side", "")),
            f"status={trade_row.get('status', '')}",
            f"profit={trade_row.get('profit', 0)}",
        ])
    )

print("\n".join(formatted_lines))
PY
}

analytics_format_merges() {
    local analytics_payload
    analytics_payload=$(cat)
    ANALYTICS_PAYLOAD="$analytics_payload" "$PYTHON_BIN" - <<'PY'
import json
import os

payload_text = os.environ.get("ANALYTICS_PAYLOAD", "").strip()
if not payload_text:
    payload = []
else:
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = []
if not payload or not isinstance(payload, list):
    print("No merges found.")
    raise SystemExit(0)

formatted_lines = []
for merge_row in payload[:20]:
    formatted_lines.append(
        " | ".join([
            str(merge_row.get("timestamp", "")),
            str(merge_row.get("condition_id", ""))[:24],
            f"status={merge_row.get('status', '')}",
            f"usdc={merge_row.get('usdc_received', 0)}",
            f"gas={merge_row.get('gas_cost', 0)}",
        ])
    )

print("\n".join(formatted_lines))
PY
}

analytics_menu() {
    local name="$1"
    while true; do
        local analytics_choice
        analytics_choice=$(whiptail --title "Analytics: $name" \
            --menu "Select analytics view" \
            18 65 8 \
            "overview" "Summary metrics from the local instance DB" \
            "trades"   "Recent trades from the local instance DB" \
            "merges"   "Recent merges from the local instance DB" \
            "back"     "← Back to instance menu" \
            3>&1 1>&2 2>&3) || return

        case "$analytics_choice" in
            "overview")
                analytics_require_tables "$name" "trades" "merges" || continue
                local overview_json
                overview_json=$(analytics_overview_json "$name") || {
                    whiptail --title "Analytics Overview: $name" --msgbox "Failed to load analytics overview." 8 55
                    continue
                }
                local overview_text
                overview_text=$(printf '%s' "$overview_json" | analytics_format_overview)
                whiptail --title "Analytics Overview: $name" --scrolltext --msgbox "$overview_text" 20 70
                ;;
            "trades")
                analytics_require_tables "$name" "trades" || continue
                local trades_json
                trades_json=$(analytics_run_sql "$name" "SELECT timestamp, market_slug, side, status, profit FROM trades ORDER BY id DESC LIMIT 20;") || {
                    whiptail --title "Recent Trades: $name" --msgbox "Failed to load recent trades." 8 50
                    continue
                }
                local trades_text
                trades_text=$(printf '%s' "$trades_json" | analytics_format_trades)
                whiptail --title "Recent Trades: $name" --scrolltext --msgbox "$trades_text" 24 100
                ;;
            "merges")
                analytics_require_tables "$name" "merges" || continue
                local merges_json
                merges_json=$(analytics_run_sql "$name" "SELECT timestamp, condition_id, status, usdc_received, gas_cost FROM merges ORDER BY id DESC LIMIT 20;") || {
                    whiptail --title "Recent Merges: $name" --msgbox "Failed to load recent merges." 8 50
                    continue
                }
                local merges_text
                merges_text=$(printf '%s' "$merges_json" | analytics_format_merges)
                whiptail --title "Recent Merges: $name" --scrolltext --msgbox "$merges_text" 24 100
                ;;
            "back"|*)
                return
                ;;
        esac
    done
}

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
#  Migration: ensure all service units load .env
# ============================================
migrate_environment_files() {
    local patched=0
    for inst_dir in /opt/pmbot-*/; do
        [ -d "$inst_dir/backend" ] || continue
        local inst_name env_file svc_file
        inst_name=$(basename "$inst_dir" | sed 's/^pmbot-//')
        env_file="${inst_dir}backend/.env"
        svc_file="/etc/systemd/system/pmbot-${inst_name}-backend.service"

        [ -f "$svc_file" ] || continue
        grep -q "^EnvironmentFile=" "$svc_file" && continue

        # Patch missing EnvironmentFile line in after Environment=PORT=
        if grep -q "^Environment=PORT=" "$svc_file"; then
            sed -i "/^Environment=PORT=/a EnvironmentFile=${env_file}" "$svc_file"
        else
            sed -i "/^\[Service\]/a EnvironmentFile=${env_file}" "$svc_file"
        fi
        ((patched++))
    done

    if [ "$patched" -gt 0 ]; then
        systemctl daemon-reload
        echo -e "${YELLOW}  ⚠️  Migrated ${patched} service unit(s) to load .env via EnvironmentFile.${NC}"
        echo -e "${YELLOW}     Restart affected instances for changes to take effect.${NC}"
        sleep 2
    fi
}

# ============================================
#  Main menu
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

        # Build dashboard table for main menu
        local menu_text=""
        menu_text+="$(printf '  %-20s %-8s %-8s %-6s %-6s\n' 'Instance' 'Status' 'Mode' 'API' 'Nginx')\n"
        menu_text+="$(printf '  %-20s %-8s %-8s %-6s %-6s\n' '────────' '──────' '────' '───' '─────')\n"
        for name in "${INSTANCES[@]}"; do
            status=$(inst_status "$name")
            mode=$(inst_dry_run "$name")
            bport=$(inst_backend_port "$name")
            nport=$(inst_nginx_port "$name")
            status_icon="●"
            [ "$status" = "running" ] && status_icon="▶"
            [ "$status" = "stopped" ] && status_icon="■"
            menu_text+="$(printf '  %-20s %-8s %-8s %-6s %-6s\n' "$name" "$status_icon $status" "$mode" ":$bport" ":$nport")\n"
        done

        # Build whiptail menu items
        local menu_items=()
        for name in "${INSTANCES[@]}"; do
            status=$(inst_status "$name")
            mode=$(inst_dry_run "$name")
            bport=$(inst_backend_port "$name")
            nport=$(inst_nginx_port "$name")
            label="$(printf '%-8s %-5s  API:%-6s Nginx:%-6s' "$status" "[$mode]" ":$bport" ":$nport")"
            menu_items+=("$name" "$label")
        done
        menu_items+=("─────"  "──────────────────────────────")
        menu_items+=("[bulk]" "Select multiple instances for bulk action")
        menu_items+=("[new]"  "Deploy a new instance")
        menu_items+=("[quit]" "Exit")

        local choice
        choice=$(whiptail --title "PMBot Instance Manager" \
            --menu "Select an instance to manage:\n($(date '+%H:%M:%S')  •  ${#INSTANCES[@]} instance(s))" \
            22 70 14 \
            "${menu_items[@]}" \
            3>&1 1>&2 2>&3) || exit 0

        case "$choice" in
            "[quit]"|"─────") exit 0 ;;
            "[new]")  deploy_new_instance ;;
            "[bulk]") bulk_select_menu ;;
            *)        instance_menu "$choice" ;;
        esac
    done
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
            21 65 13 \
            "status"    "Show service status & recent logs" \
            "start"     "Start backend service" \
            "stop"      "Stop backend service" \
            "restart"   "Restart backend service" \
            "analytics" "View backend analytics" \
            "update"    "Update code from source" \
            "relayer"   "Install/update relayer helper deps" \
            "env"       "Edit .env configuration" \
            "wallet"    "Change wallet / private key" \
            "logs"      "Tail live logs (last 50 lines)" \
            "pentest"   "Run security pentest" \
            "remove"    "Remove this instance entirely" \
            "back"      "← Back to instance list" \
            3>&1 1>&2 2>&3) || return

        case "$action" in
            "status")   action_status "$name" ;;
            "start")    action_start "$name" ;;
            "stop")     action_stop "$name" ;;
            "restart")  action_restart "$name" ;;
            "analytics") analytics_menu "$name" ;;
            "update")   action_update "$name" ;;
            "relayer")  action_relayer "$name" ;;
            "env")      action_edit_env "$name" ;;
            "wallet")   action_change_wallet "$name" ;;
            "logs")     action_logs "$name" ;;
            "pentest")  clear; run_pentest "$name" ;;
            "remove")   action_remove "$name" && return ;;
            "back"|*)   return ;;
        esac
    done
}

# Install/update relayer helper dependencies (scripts/package.json)
action_relayer() {
    local name="$1"
    local app_dir="/opt/pmbot-${name}"
    local scripts_dir="$app_dir/scripts"

    if [ ! -d "$scripts_dir" ]; then
        whiptail --title "Relayer" --msgbox "Scripts directory not found at $scripts_dir. Run 'update' first to sync scripts." 10 70
        return
    fi

    whiptail --title "Relayer" --yesno \
        "Install/update relayer helper dependencies?\n\nThis will run 'npm install' in $scripts_dir" 12 70 || return

    clear
    echo -e "${CYAN}[Relayer] Installing/updating dependencies...${NC}"
    mkdir -p "$NPM_CACHE_DIR"
    chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"
    runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$scripts_dir" install --no-audit --no-fund
    echo -e "${GREEN}✓ Relayer helper dependencies installed/updated${NC}"
    echo ""
    read -p "Press Enter to return..." _
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

    if [ -d "$SCRIPT_DIR/scripts" ]; then
        echo -e "${CYAN}    Syncing scripts (relayer helper)...${NC}"
        rsync -a --delete "$SCRIPT_DIR/scripts/" "$app_dir/scripts/"
        chown -R "$APP_USER:$APP_USER" "$app_dir/scripts"
    fi

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

    # Validate private key: strip optional 0x, must be exactly 64 hex chars
    local raw_key="${new_key#0x}"; raw_key="${raw_key#0X}"
    if ! echo "$raw_key" | grep -qP '^[0-9a-fA-F]{64}$'; then
        whiptail --title "Change Wallet: $name" --msgbox \
            "❌ Invalid private key.\n\nMust be a 64-character hex string (32 bytes), with or without 0x prefix.\nGot ${#raw_key} hex chars.\n\nDid you paste a wallet address instead of the private key?" \
            13 65
        return
    fi

    local new_funder
    new_funder=$(whiptail --title "Change Wallet: $name" \
        --inputbox "Funder address (Gnosis Safe proxy, or leave blank for EOA):" \
        9 65 "" \
        3>&1 1>&2 2>&3) || return

    # Validate funder address: must be empty or 0x + 40 hex chars
    if [ -n "$new_funder" ] && ! echo "$new_funder" | grep -qP '^0x[0-9a-fA-F]{40}$'; then
        whiptail --title "Change Wallet: $name" --msgbox \
            "❌ Invalid funder address.\n\nMust be a 0x-prefixed 20-byte EVM address (42 chars total).\nGot: $new_funder" \
            11 65
        return
    fi

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

    # Ensure systemd unit has EnvironmentFile= (may be missing on older installs)
    local svc_file="/etc/systemd/system/${svc}.service"
    if [ -f "$svc_file" ] && ! grep -q "^EnvironmentFile=" "$svc_file"; then
        sed -i "/^Environment=PORT=/a EnvironmentFile=${env_file}" "$svc_file"
        systemctl daemon-reload
    fi

    whiptail --title "Change Wallet: $name" --yesno \
        "Wallet updated.\nRestart service to apply?" 8 50 && \
        systemctl restart "$svc" && \
        whiptail --title "Change Wallet: $name" --msgbox "Service restarted." 8 40
}

action_logs() {
    local name="$1"
    local svc="pmbot-${name}-backend"

    local choice
    choice=$(whiptail --title "Logs: $name" \
        --menu "運行日誌選項" \
        14 70 6 \
        "snapshot" "查看最近 200 行運行日誌" \
        "follow"   "即時追蹤日誌 (Ctrl+C 退出)" \
        3>&1 1>&2 2>&3) || return

    case "$choice" in
        "snapshot")
            local logs
            logs=$(journalctl -u "$svc" --no-pager --output=cat 2>&1)
            [ -z "$logs" ] && logs="(no logs yet)"
            whiptail --title "運行日誌: $name" --scrolltext --msgbox "$logs" 28 90
            ;;
        "follow"|*)
            clear
            echo -e "${BOLD}${CYAN}Live logs: $svc  (Ctrl+C to exit)${NC}"
            echo ""
            journalctl -u "$svc" -f --no-pager -n 50
            ;;
    esac
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
#  Pentest runner
# ============================================
inst_public_url() {
    local name="$1"
    local conf="/etc/nginx/sites-available/pmbot-${name}"
    # Prefer server_name with a real domain (not _ or localhost)
    local domain
    domain=$(grep -oP 'server_name\s+\K[^;]+' "$conf" 2>/dev/null \
        | tr ' ' '\n' | grep -v '^_$' | grep -v '^localhost$' | grep '\.' | head -1)
    if [ -n "$domain" ]; then
        # Check if SSL cert exists for this domain
        if [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]; then
            echo "https://${domain}"
        else
            echo "http://${domain}"
        fi
    else
        echo ""
    fi
}

run_pentest() {
    local name="$1"
    local nport bp target pub
    nport=$(inst_nginx_port "$name")
    bp=$(inst_backend_port "$name")
    pub=$(inst_public_url "$name")
    if [ -n "$pub" ]; then
        target="$pub"
    elif [ "$nport" != "?" ]; then
        target="http://127.0.0.1:${nport}"
    else
        target="http://127.0.0.1:${bp}"
    fi

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

    echo -e "${CYAN}Waiting for $target to be ready...${NC}"
    local r=12
    while [ $r -gt 0 ]; do
        curl -sf "${target}/api/auth/status" >/dev/null 2>&1 && break
        sleep 2; ((r--))
    done
    [ $r -eq 0 ] && echo -e "${YELLOW}⚠️  Not responding — results may be incomplete${NC}"

    local logfile="/opt/pmbot-${name}/pentest-$(date +%Y%m%d-%H%M%S).log"
    echo -e "\n${BOLD}${CYAN}━━━ Pentest: $name @ $target ━━━${NC}\n"
    "$pvenv/bin/python" "$pscript" --target "$target" --logfile "$logfile"
    local rc=$?
    echo -e "\n  Log: $logfile\n"
    case $rc in
        0) echo -e "${GREEN}✅ Clean — no critical/high/medium issues.${NC}" ;;
        1) echo -e "${YELLOW}⚠️  Medium issues found — review above.${NC}" ;;
        2) echo -e "${RED}🔴 Critical/High issues — action required!${NC}" ;;
    esac
    echo ""; read -rp "Press Enter to continue..." _
}

# ============================================
#  Bulk select + actions
# ============================================
bulk_select_menu() {
    scan_instances
    [ ${#INSTANCES[@]} -eq 0 ] && return

    local check_items=()
    for name in "${INSTANCES[@]}"; do
        local status mode bport nport
        status=$(inst_status "$name"); mode=$(inst_dry_run "$name")
        bport=$(inst_backend_port "$name"); nport=$(inst_nginx_port "$name")
        local label; label="$(printf '%-8s [%-4s]  API:%-6s Nginx:%-6s' "$status" "$mode" ":$bport" ":$nport")"
        check_items+=("$name" "$label" "OFF")
    done

    local raw
    raw=$(whiptail --title "Bulk Select" \
        --checklist "SPACE to select instances, then ENTER:" \
        20 72 12 \
        "${check_items[@]}" \
        3>&1 1>&2 2>&3) || return

    # Parse selection
    local selected=()
    for w in $raw; do selected+=("${w//\"/}"); done
    [ ${#selected[@]} -eq 0 ] && return

    bulk_action_menu "${selected[@]}"
}

bulk_action_menu() {
    local sel=("$@")
    local names_display; names_display=$(printf '%s  ' "${sel[@]}")

    while true; do
        local action
        action=$(whiptail --title "Bulk Actions — ${#sel[@]} selected" \
            --menu "Instances: $names_display\n\nApply to ALL:" \
            20 72 8 \
            "start"   "Start all backends" \
            "stop"    "Stop all backends" \
            "restart" "Restart all backends" \
            "status"  "Status summary + recent logs" \
            "update"  "Update code on all" \
            "pentest" "Pentest all" \
            "logs"    "Tail logs (tmux panes or sequential)" \
            "back"    "← Back" \
            3>&1 1>&2 2>&3) || return

        case "$action" in
            "start")   bulk_start   "${sel[@]}" ;;
            "stop")    bulk_stop    "${sel[@]}" ;;
            "restart") bulk_restart "${sel[@]}" ;;
            "status")  bulk_status  "${sel[@]}" ;;
            "update")  bulk_update  "${sel[@]}" ;;
            "pentest") bulk_pentest "${sel[@]}" ;;
            "logs")    bulk_logs    "${sel[@]}" ;;
            "back"|*)  return ;;
        esac
    done
}

bulk_start() {
    clear; echo -e "${BOLD}${CYAN}━━━ Start ━━━${NC}\n"
    for n in "$@"; do
        printf "  %-30s" "pmbot-${n}-backend..."
        systemctl start "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done; echo ""; read -rp "Press Enter..." _
}

bulk_stop() {
    whiptail --title "Bulk Stop" --yesno "Stop all ${#@} backends?" 7 48 || return
    clear; echo -e "${BOLD}${CYAN}━━━ Stop ━━━${NC}\n"
    for n in "$@"; do
        printf "  %-30s" "pmbot-${n}-backend..."
        systemctl stop "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${GREEN}ok${NC}" || echo -e "${RED}failed${NC}"
    done; echo ""; read -rp "Press Enter..." _
}

bulk_restart() {
    clear; echo -e "${BOLD}${CYAN}━━━ Restart ━━━${NC}\n"
    for n in "$@"; do
        printf "  %-30s" "pmbot-${n}-backend..."
        systemctl restart "pmbot-${n}-backend" 2>/dev/null \
            && echo -e "${GREEN}$(inst_status "$n")${NC}" || echo -e "${RED}failed${NC}"
    done; echo ""; read -rp "Press Enter..." _
}

bulk_status() {
    clear; echo -e "${BOLD}${CYAN}━━━ Status Summary ━━━${NC}\n"
    printf "  ${BOLD}%-22s %-10s %-6s :%-5s :%-5s${NC}\n" \
        "Instance" "Status" "Mode" "API" "Nginx"
    printf "  %-22s %-10s %-6s %-6s %-6s\n" \
        "────────" "──────" "────" "───" "─────"
    for n in "$@"; do
        local st mode bp np
        st=$(inst_status "$n"); mode=$(inst_dry_run "$n")
        bp=$(inst_backend_port "$n"); np=$(inst_nginx_port "$n")
        if [ "$st" = "running" ]; then
            printf "  ${GREEN}▶ %-20s${NC} %-10s %-6s :%-5s :%-5s\n" "$n" "$st" "$mode" "$bp" "$np"
        else
            printf "  ${RED}■ %-20s${NC} %-10s %-6s :%-5s :%-5s\n" "$n" "$st" "$mode" "$bp" "$np"
        fi
    done
    echo ""
    for n in "$@"; do
        echo -e "${CYAN}  ── $n last 3 lines:${NC}"
        journalctl -u "pmbot-${n}-backend" --no-pager -n 3 --output=cat 2>/dev/null | sed 's/^/    /'
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
    [ ${#src_items[@]} -eq 0 ] && { whiptail --title "Bulk Update" --msgbox "No source dirs found." 7 44; return; }

    local selected_src
    selected_src=$(whiptail --title "Bulk Update" \
        --menu "Source for ALL ${#sel[@]} instances:" \
        14 54 6 "${src_items[@]}" \
        3>&1 1>&2 2>&3) || return

    whiptail --title "Bulk Update" --yesno \
        "Update ALL ${#sel[@]} instances from '${selected_src}'?" \
        8 58 || return

    for n in "${sel[@]}"; do
        clear
        echo -e "${BOLD}${CYAN}━━━ Updating $n ━━━${NC}\n"
        local app_dir="/opt/pmbot-${n}"
        local svc="pmbot-${n}-backend"

        echo -e "${CYAN}[1/5] Stopping...${NC}"
        systemctl stop "$svc" 2>/dev/null || true
        $PM2_BIN stop "pmbot-${n}-frontend" 2>/dev/null || true; echo "      done"

        echo -e "${CYAN}[2/5] Syncing code...${NC}"
        rsync -a --delete \
            --exclude '.env' --exclude '.auth.json' \
            --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' \
            --exclude '__pycache__' --exclude '*.pyc' \
            "$SCRIPT_DIR/$selected_src/" "$app_dir/backend/"
        local fsrc="${selected_src/backend/frontend}"
        [ -d "$SCRIPT_DIR/$fsrc" ] && rsync -a --delete \
            --exclude 'node_modules' --exclude 'dist' \
            "$SCRIPT_DIR/$fsrc/" "$app_dir/frontend/"
        chown -R "$APP_USER:$APP_USER" "$app_dir"; echo "      done"

        echo -e "${CYAN}[3/5] Reinstalling deps...${NC}"
        runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q --upgrade pip
        runuser -u "$APP_USER" -- "$app_dir/venv/bin/pip" install -q -r "$app_dir/backend/requirements.txt"
        echo "      done"

        if [ -d "$app_dir/frontend" ]; then
            echo -e "${CYAN}[4/5] Rebuilding frontend...${NC}"
            mkdir -p "$NPM_CACHE_DIR"; chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"
            rm -rf "$app_dir/frontend/dist" "$app_dir/frontend/node_modules/.vite"
            runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$app_dir/frontend" install --no-audit --no-fund
            runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$app_dir/frontend" run build
            echo "      done"
        else echo -e "${CYAN}[4/5] No frontend — skip${NC}"; fi

        echo -e "${CYAN}[5/5] Restarting...${NC}"
        systemctl start "$svc"
        if [ -d "$app_dir/frontend" ]; then
            $PM2_BIN delete "pmbot-${n}-frontend" 2>/dev/null || true
            $PM2_BIN start "npx vite preview --host 0.0.0.0 --port 3000" \
                --name "pmbot-${n}-frontend" --cwd "$app_dir/frontend" --uid "$APP_USER"
            $PM2_BIN save
        fi
        echo "      done"
        echo -e "${GREEN}✓ $n done ($(inst_status "$n"))${NC}\n"
    done

    read -rp "Pentest all? [Y/n]: " pt; pt=${pt:-Y}
    [[ "$pt" =~ ^[Yy]$ ]] && bulk_pentest "${sel[@]}" || read -rp "Press Enter..." _
}

bulk_pentest() {
    for n in "$@"; do clear; run_pentest "$n"; done
}

bulk_logs() {
    if command -v tmux &>/dev/null; then
        local sess="pmbot-logs-$$"
        tmux new-session -d -s "$sess" -x 220 -y 50
        local first=true
        for n in "$@"; do
            if [ "$first" = true ]; then
                tmux send-keys -t "$sess" "journalctl -u pmbot-${n}-backend -f --no-pager -n 30" Enter
                first=false
            else
                tmux split-window -t "$sess" "journalctl -u pmbot-${n}-backend -f --no-pager -n 30"
                tmux select-layout -t "$sess" tiled
            fi
        done
        echo -e "${CYAN}tmux '$sess' opened — Ctrl+B D to detach${NC}"; sleep 1
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
migrate_environment_files
main_menu
