#!/bin/bash
set -e

# ============================================
#  Polymarket Bot — Full Stack Onboarding
#  Deploys all bot stacks in one run:
#    • backend      (15m)
#    • m5-backend   (5m)
#    • hourly-backend (1h)
#    • 4h-backend   (4h)
#    • daily-backend (daily)
#  Tested on: Ubuntu 22.04 / Debian 12
#  Run as root: sudo ./onboard.sh
# ============================================

NODE_VERSION=20

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()   { echo -e "${CYAN}  $1${NC}"; }
warn()   { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
ok()     { echo -e "${GREEN}  ✓ $1${NC}"; }
err()    { echo -e "${RED}  ✗ $1${NC}"; }
header() { echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${NC} $2"; }
section(){ echo -e "\n${BOLD}${GREEN}▶ $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  ${CYAN}Polymarket Bot — Full Stack Onboarding${NC}${BOLD}          ║${NC}"
echo -e "${BOLD}║  Deploys: 5m · 15m · 1h · 4h · Daily            ║${NC}"
echo -e "${BOLD}║  Each stack gets its own wallet + URL             ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Run this script once per user/deployment."
echo -e "  For multiple users: run again with a different instance suffix."
echo ""

if [ "$EUID" -ne 0 ]; then
    err "Please run as root: sudo ./onboard.sh"
    exit 1
fi

# ============================================
#  Stack definitions
#  Format: "name|backend_dir|frontend_dir|default_backend_port|default_nginx_port"
# ============================================
declare -a STACKS=(
    "m5|m5-backend|m5-frontend|8889|8080"
    "15m|backend|frontend|8888|8081"
    "1h|hourly-backend|hourly-frontend|8890|8082"
    "4h|4h-backend|4h-frontend|8891|8083"
    "daily|daily-backend|daily-frontend|8887|8084"
)

# ============================================
#  Stack selection
# ============================================
section "Select stacks to deploy"
echo ""
echo -e "  Available stacks:"
i=1
for stack in "${STACKS[@]}"; do
    name=$(echo "$stack" | cut -d'|' -f1)
    bsrc=$(echo "$stack" | cut -d'|' -f2)
    fsrc=$(echo "$stack" | cut -d'|' -f3)
    bport=$(echo "$stack" | cut -d'|' -f4)
    nport=$(echo "$stack" | cut -d'|' -f5)
    fe_exists=""
    [ -d "$SCRIPT_DIR/$fsrc" ] && fe_exists=" + frontend"
    [ -d "$SCRIPT_DIR/$bsrc" ] && echo -e "    ${CYAN}${i})${NC} ${BOLD}${name}${NC}  (${bsrc}${fe_exists}, port ${bport})" || echo -e "    ${YELLOW}${i})${NC} ${name}  ${RED}[source not found: ${bsrc}]${NC}"
    ((i++))
done
echo ""
echo -e "  ${BOLD}a)${NC} Deploy ALL stacks"
echo ""
read -p "  Select stacks to deploy [1-${#STACKS[@]}, comma-separated, or 'a']: " stack_input
stack_input=${stack_input:-a}

SELECTED_STACKS=()
if [[ "$stack_input" == "a" || "$stack_input" == "A" ]]; then
    SELECTED_STACKS=("${STACKS[@]}")
else
    IFS=',' read -ra choices <<< "$stack_input"
    for c in "${choices[@]}"; do
        c=$(echo "$c" | tr -d ' ')
        if [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le "${#STACKS[@]}" ]; then
            SELECTED_STACKS+=("${STACKS[$((c-1))]}")
        else
            warn "Ignoring invalid selection: $c"
        fi
    done
fi

if [ ${#SELECTED_STACKS[@]} -eq 0 ]; then
    err "No valid stacks selected."; exit 1
fi

echo ""
info "Selected stacks: $(for s in "${SELECTED_STACKS[@]}"; do echo -n "$(echo $s|cut -d'|' -f1) "; done)"

# ============================================
#  Instance suffix (for multi-user deployments)
# ============================================
section "Instance suffix"
echo ""
echo -e "  To deploy multiple users on the same server, give each deployment"
echo -e "  a unique suffix (e.g. 'alice', 'bob'). Services will be named"
echo -e "  pmbot-<stack>-<suffix>. Leave blank for the default naming."
echo ""
read -p "  Instance suffix (leave blank for default): " INSTANCE_SUFFIX
INSTANCE_SUFFIX=$(echo "$INSTANCE_SUFFIX" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')
if [ -n "$INSTANCE_SUFFIX" ]; then
    info "Suffix: $INSTANCE_SUFFIX — stacks will be: pmbot-<stack>-${INSTANCE_SUFFIX}"
else
    info "No suffix — stacks will be: pmbot-<stack>"
fi

# ============================================
#  Domain / SSL
# ============================================
section "Domain & SSL (optional)"
echo ""
echo -e "  You can serve each stack on its own nginx port, or optionally"
echo -e "  use a single domain with path-based routing."
echo ""
read -p "  Domain name (leave blank to use IP only): " GLOBAL_DOMAIN
GLOBAL_SSL=false
GLOBAL_SSL_EMAIL=""

if [ -n "$GLOBAL_DOMAIN" ]; then
    info "Domain: $GLOBAL_DOMAIN — Let's Encrypt SSL will be configured"
    read -p "  Let's Encrypt email (required for SSL): " GLOBAL_SSL_EMAIL
    if [ -z "$GLOBAL_SSL_EMAIL" ]; then
        warn "No email provided — skipping SSL"
        GLOBAL_DOMAIN=""
    else
        GLOBAL_SSL=true
    fi
fi

# ============================================
#  Per-stack port configuration
# ============================================
section "Port configuration"
echo ""

declare -A STACK_BACKEND_PORT
declare -A STACK_NGINX_PORT
declare -A STACK_PRIVATE_KEY
declare -A STACK_FUNDER
declare -A STACK_SIG_TYPE
declare -A STACK_DRY_RUN
declare -A STACK_INSTANCE_NAME

# Collect already-used ports to avoid conflicts
ALL_USED_PORTS=()
while IFS= read -r p; do
    [[ -n "$p" ]] && ALL_USED_PORTS+=("$p")
done < <(ss -tlnp 2>/dev/null | grep -oP ':\K[0-9]+' | sort -un)
for inst_dir in /opt/pmbot-*/; do
    [ -d "$inst_dir/backend" ] || continue
    svc_file="/etc/systemd/system/pmbot-$(basename "$inst_dir" | sed 's/^pmbot-//')-backend.service"
    bp=$(grep -oP 'Environment=PORT=\K[0-9]+' "$svc_file" 2>/dev/null || echo "")
    [ -n "$bp" ] && ALL_USED_PORTS+=("$bp")
done

suggest_port() {
    local p=$1
    while printf '%s\n' "${ALL_USED_PORTS[@]}" | grep -qw "$p" 2>/dev/null; do ((p++)); done
    ALL_USED_PORTS+=("$p")
    echo $p
}

for stack in "${SELECTED_STACKS[@]}"; do
    name=$(echo "$stack" | cut -d'|' -f1)
    bsrc=$(echo "$stack" | cut -d'|' -f2)
    def_bport=$(echo "$stack" | cut -d'|' -f4)
    def_nport=$(echo "$stack" | cut -d'|' -f5)

    if [ ! -d "$SCRIPT_DIR/$bsrc" ]; then
        warn "Skipping $name: source directory $bsrc not found"
        continue
    fi

    # Compute instance name (with optional suffix)
    inst_name="${name}"
    [ -n "$INSTANCE_SUFFIX" ] && inst_name="${name}-${INSTANCE_SUFFIX}"
    STACK_INSTANCE_NAME[$name]="$inst_name"

    echo -e "  ${BOLD}${CYAN}[${inst_name}]${NC}"

    # Check if updating existing instance
    existing_svc="/etc/systemd/system/pmbot-${inst_name}-backend.service"
    if [ -f "$existing_svc" ]; then
        ex_bp=$(grep -oP 'Environment=PORT=\K[0-9]+' "$existing_svc" 2>/dev/null || echo "$def_bport")
        def_bport="$ex_bp"
        info "  Existing instance detected — defaults from current install"
    fi

    sugg_bport=$(suggest_port "$def_bport")
    read -p "    Backend port [$sugg_bport]: " bp
    STACK_BACKEND_PORT[$name]=${bp:-$sugg_bport}
    ALL_USED_PORTS+=("${STACK_BACKEND_PORT[$name]}")

    sugg_nport=$(suggest_port "$def_nport")
    read -p "    Nginx port   [$sugg_nport]: " np
    STACK_NGINX_PORT[$name]=${np:-$sugg_nport}
    ALL_USED_PORTS+=("${STACK_NGINX_PORT[$name]}")

    # Per-stack wallet
    echo ""
    echo -e "    ${BOLD}Wallet setup:${NC}"
    echo -e "    • ${CYAN}EOA wallet${NC}: enter the private key directly (sig type 0)"
    echo -e "    • ${CYAN}Custodial/Magic/email account${NC}: enter the ${BOLD}proxy signer${NC} private key"
    echo -e "      (a separate EOA you created — NOT your email account password)."
    echo -e "      Then provide your Polymarket funder address and select sig type 1."
    echo -e "      Note: you must manually approve contracts in the Polymarket UI first."
    echo ""
    pk="" fa="" st="" raw_pk=""
    while true; do
        read -p "    Private key (blank = dry-run): " pk
        if [ -z "$pk" ]; then break; fi
        raw_pk="${pk#0x}"; raw_pk="${raw_pk#0X}"
        if echo "$raw_pk" | grep -qP '^[0-9a-fA-F]{64}$'; then break; fi
        err "    Invalid private key: must be 64 hex chars (32 bytes). Got ${#raw_pk} chars. Did you paste a wallet address?"
    done
    STACK_PRIVATE_KEY[$name]="$pk"
    if [ -n "$pk" ]; then
        while true; do
            read -p "    Funder address (blank for EOA / same wallet): " fa
            if [ -z "$fa" ]; then break; fi
            if echo "$fa" | grep -qP '^0x[0-9a-fA-F]{40}$'; then break; fi
            err "    Invalid funder address: must be 0x + 40 hex chars (42 total). Got: $fa"
        done
        STACK_FUNDER[$name]="$fa"
        echo -e "    Signature type: ${CYAN}0${NC}=EOA (direct wallet)  ${CYAN}1${NC}=Magic/email (custodial)  ${CYAN}2${NC}=Gnosis Safe"
        while true; do
            read -p "    Signature type [0]: " st
            st=${st:-0}
            if [[ "$st" =~ ^[012]$ ]]; then break; fi
            err "    Invalid signature type: must be 0, 1, or 2."
        done
        STACK_SIG_TYPE[$name]="$st"
        STACK_DRY_RUN[$name]="false"
    else
        warn "No key — ${inst_name} will run in dry-run mode"
        STACK_SIG_TYPE[$name]="0"
        STACK_DRY_RUN[$name]="true"
    fi
    echo ""
done

# ============================================
#  Confirmation summary
# ============================================
section "Deployment summary"
echo ""
printf "  %-8s %-12s %-12s %-6s\n" "Stack" "Backend" "Nginx" "DryRun"
printf "  %-8s %-12s %-12s %-6s\n" "─────" "───────" "─────" "──────"
for stack in "${SELECTED_STACKS[@]}"; do
    name=$(echo "$stack" | cut -d'|' -f1)
    [ -z "${STACK_BACKEND_PORT[$name]}" ] && continue
    inst_name="${STACK_INSTANCE_NAME[$name]}"
    printf "  ${CYAN}%-14s${NC} %-12s %-12s %-10s\n" \
        "$inst_name" \
        ":${STACK_BACKEND_PORT[$name]}" \
        ":${STACK_NGINX_PORT[$name]}" \
        "${STACK_DRY_RUN[$name]}"
done
echo ""
[ -n "$GLOBAL_DOMAIN" ] && echo -e "  Domain:  ${GLOBAL_DOMAIN} (SSL: ${GLOBAL_SSL})"
echo -e "  Install: /opt/pmbot-<instance>/"
echo ""

read -p "  Proceed? [Y/n]: " CONFIRM
CONFIRM=${CONFIRM:-Y}
[[ ! "$CONFIRM" =~ ^[Yy]$ ]] && echo "  Aborted." && exit 0

# ============================================
#  System dependencies (once)
# ============================================
section "Installing system dependencies"
echo ""

apt-get update -qq
apt-get install -y -qq software-properties-common nginx curl git rsync certbot python3-certbot-nginx

if ! python3.12 --version &>/dev/null; then
    info "Installing Python 3.12..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.12 python3.12-venv python3.12-dev
fi
PYTHON_BIN=$(command -v python3.12)
ok "Python: $($PYTHON_BIN --version)"

if ! command -v node &>/dev/null; then
    info "Installing Node.js ${NODE_VERSION}..."
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash -
    apt-get install -y -qq nodejs
fi
ok "Node: $(node --version)"

if ! command -v pm2 &>/dev/null; then
    info "Installing PM2..."
    npm install -g pm2
fi
PM2_BIN=$(which pm2)
ok "PM2: $(pm2 --version)"

APP_USER="pmbot"
NPM_CACHE_DIR="/opt/pmbot/.npm-cache"
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -d /opt -s /bin/bash "$APP_USER"
    ok "Created user: $APP_USER"
else
    ok "User $APP_USER already exists"
fi
mkdir -p "$NPM_CACHE_DIR"
chown -R "$APP_USER:$APP_USER" "$NPM_CACHE_DIR"

# ============================================
#  Deploy each stack
# ============================================
DEPLOYED=()
FAILED=()

for stack in "${SELECTED_STACKS[@]}"; do
    name=$(echo "$stack" | cut -d'|' -f1)
    bsrc=$(echo "$stack" | cut -d'|' -f2)
    fsrc=$(echo "$stack" | cut -d'|' -f3)
    [ -z "${STACK_BACKEND_PORT[$name]}" ] && continue

    inst_name="${STACK_INSTANCE_NAME[$name]}"
    BACKEND_PORT="${STACK_BACKEND_PORT[$name]}"
    NGINX_PORT="${STACK_NGINX_PORT[$name]}"
    PRIVATE_KEY="${STACK_PRIVATE_KEY[$name]}"
    FUNDER="${STACK_FUNDER[$name]}"
    SIG_TYPE="${STACK_SIG_TYPE[$name]}"
    DRY_RUN="${STACK_DRY_RUN[$name]}"

    APP_DIR="/opt/pmbot-${inst_name}"
    SERVICE_NAME="pmbot-${inst_name}-backend"
    PM2_NAME="pmbot-${inst_name}-frontend"
    NGINX_SITE="pmbot-${inst_name}"
    HAS_FRONTEND=false
    [ -d "$SCRIPT_DIR/$fsrc" ] && HAS_FRONTEND=true

    echo ""
    echo -e "${BOLD}════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Deploying ${CYAN}${inst_name}${NC}${BOLD} stack...${NC}"
    echo -e "${BOLD}════════════════════════════════════════════════${NC}"

    # ── Deploy code ──
    header "code" "Syncing files..."
    mkdir -p "$APP_DIR"

    IS_UPDATE=false
    if [ -d "$APP_DIR/backend" ]; then
        IS_UPDATE=true
        info "Updating existing install — stopping services..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        $PM2_BIN stop "$PM2_NAME" 2>/dev/null || true
    fi

    rsync -a --delete \
        --exclude '.env' \
        --exclude '.auth.json' \
        --exclude '*.db' \
        --exclude '*.db-shm' \
        --exclude '*.db-wal' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        "$SCRIPT_DIR/$bsrc/" "$APP_DIR/backend/"

    if [ "$HAS_FRONTEND" = true ]; then
        rsync -a --delete \
            --exclude 'node_modules' \
            --exclude 'dist' \
            "$SCRIPT_DIR/$fsrc/" "$APP_DIR/frontend/"
    fi

    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
    ok "Code synced → $APP_DIR"

    # ── Backend venv ──
    header "venv" "Setting up Python environment..."
    CURRENT_PY=$("$APP_DIR/venv/bin/python" --version 2>/dev/null || echo "none")
    if [[ "$CURRENT_PY" != *"3.12"* ]]; then
        rm -rf "$APP_DIR/venv"
    fi
    runuser -u "$APP_USER" -- $PYTHON_BIN -m venv "$APP_DIR/venv"
    runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/pip" install -q --upgrade pip
    runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
    ok "Python venv ready"

    # ── .env setup ──
    header "env" "Configuring environment..."
    ENV_FILE="$APP_DIR/backend/.env"

    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "$APP_DIR/backend/.env.example" ]; then
            cp "$APP_DIR/backend/.env.example" "$ENV_FILE"
        else
            touch "$ENV_FILE"
        fi
        ok "Created .env"
    else
        ok ".env preserved from previous install"
    fi

    # Write wallet config to .env (only if key provided or creating fresh)
    set_env() {
        local key="$1" val="$2" file="$3"
        if grep -q "^${key}=" "$file" 2>/dev/null; then
            sed -i "s|^${key}=.*|${key}=${val}|" "$file"
        else
            echo "${key}=${val}" >> "$file"
        fi
    }

    if [ -n "$PRIVATE_KEY" ]; then
        set_env "PRIVATE_KEY"      "$PRIVATE_KEY"  "$ENV_FILE"
        set_env "FUNDER_ADDRESS"   "$FUNDER"       "$ENV_FILE"
        set_env "SIGNATURE_TYPE"   "$SIG_TYPE"     "$ENV_FILE"
        set_env "DRY_RUN"          "$DRY_RUN"      "$ENV_FILE"
        ok "Wallet credentials written to .env"
    else
        set_env "DRY_RUN" "true" "$ENV_FILE"
        warn "No private key — $name running in dry-run mode"
    fi

    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    # ── Frontend build ──
    if [ "$HAS_FRONTEND" = true ]; then
        header "frontend" "Building frontend..."
        rm -rf "$APP_DIR/frontend/dist" "$APP_DIR/frontend/node_modules/.vite"
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$APP_DIR/frontend" install --no-audit --no-fund
        runuser -u "$APP_USER" -- env NPM_CONFIG_CACHE="$NPM_CACHE_DIR" npm --prefix "$APP_DIR/frontend" run build
        ok "Frontend built"
    fi

    # ── Systemd service ──
    header "systemd" "Creating systemd service..."
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=PMBot ${name} Backend (FastAPI)
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/backend
ExecStart=$APP_DIR/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PATH=$APP_DIR/venv/bin:/usr/bin:/bin
Environment=PORT=$BACKEND_PORT
EnvironmentFile=$APP_DIR/backend/.env

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    ok "Service $SERVICE_NAME started"

    # ── PM2 frontend ──
    if [ "$HAS_FRONTEND" = true ]; then
        $PM2_BIN delete "$PM2_NAME" 2>/dev/null || true
        $PM2_BIN start "npx vite preview --host 0.0.0.0 --port 3000" \
            --name "$PM2_NAME" \
            --cwd "$APP_DIR/frontend" \
            --uid "$APP_USER"
        $PM2_BIN save
        ok "PM2 $PM2_NAME started (internal port 3000)"
    fi

    # ── Nginx ──
    header "nginx" "Configuring Nginx..."
    NGINX_CONF="/etc/nginx/sites-available/${NGINX_SITE}"
    rm -f "/etc/nginx/conf.d/pmbot-ratelimit-${inst_name}.conf"

    SERVER_NAME="${GLOBAL_DOMAIN:-_}"

    cat > "$NGINX_CONF" << NGINXEOF
limit_req_zone \$binary_remote_addr zone=login_${inst_name}:10m rate=5r/m;

server {
    listen ${NGINX_PORT};
    server_name $SERVER_NAME;

    client_max_body_size 1m;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location ~ /\. {
        deny all;
        return 404;
    }

    location ~* \.(py|db|sqlite|json|sh|txt|md)\$ {
        deny all;
        return 404;
    }

    location = /api/auth/login {
        limit_req zone=login_${inst_name} burst=3 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location / {
NGINXEOF

    if [ "$HAS_FRONTEND" = true ]; then
        cat >> "$NGINX_CONF" << NGINXEOF
        root $APP_DIR/frontend/dist;
        try_files \$uri \$uri/ /index.html;
NGINXEOF
    else
        cat >> "$NGINX_CONF" << NGINXEOF
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
NGINXEOF
    fi

    cat >> "$NGINX_CONF" << NGINXEOF
    }

    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location /ws {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 86400;
    }
}
NGINXEOF

    ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/${NGINX_SITE}"
    nginx -t && systemctl reload nginx
    ok "Nginx configured on port ${NGINX_PORT}"

    DEPLOYED+=("${inst_name}:${BACKEND_PORT}:${NGINX_PORT}")
done

# ============================================
#  SSL (once, after all nginx configs are set)
# ============================================
if [ "$GLOBAL_SSL" = true ] && [ ${#DEPLOYED[@]} -gt 0 ]; then
    section "SSL — Let's Encrypt"
    echo ""
    # Gather all domains (just the one global domain for now)
    info "Requesting certificate for ${GLOBAL_DOMAIN}..."
    if certbot --nginx \
        -d "$GLOBAL_DOMAIN" \
        --non-interactive \
        --agree-tos \
        -m "$GLOBAL_SSL_EMAIL" \
        --redirect; then
        ok "SSL enabled for ${GLOBAL_DOMAIN}"
    else
        warn "SSL setup failed — sites still available over HTTP"
    fi
fi

# PM2 startup persistence
$PM2_BIN startup systemd 2>/dev/null || true
$PM2_BIN save 2>/dev/null || true

# ============================================
#  Final summary
# ============================================
IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  ${GREEN}✓ Onboarding complete!${NC}"
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Deployed stacks:${NC}"
echo ""
printf "  ${BOLD}%-16s %-32s %-8s${NC}\n" "Instance" "Dashboard URL" "Backend"
printf "  %-16s %-32s %-8s\n" "────────" "─────────────" "───────"

for entry in "${DEPLOYED[@]}"; do
    inst=$(echo "$entry" | cut -d: -f1)
    bport=$(echo "$entry" | cut -d: -f2)
    nport=$(echo "$entry" | cut -d: -f3)
    if [ -n "$GLOBAL_DOMAIN" ]; then
        url="http://${GLOBAL_DOMAIN}:${nport}"
        [ "$GLOBAL_SSL" = true ] && url="https://${GLOBAL_DOMAIN}:${nport}"
    else
        url="http://${IP}:${nport}"
    fi
    printf "  ${CYAN}%-16s${NC} %-32s ${GREEN}:%-6s${NC}\n" "$inst" "$url" "$bport"
done

echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo ""
echo -e "  ${CYAN}systemctl status pmbot-<stack>-backend${NC}   # check service"
echo -e "  ${CYAN}journalctl -u pmbot-<stack>-backend -f${NC}   # tail logs"
echo -e "  ${CYAN}pm2 status${NC}                               # frontend processes"
echo -e "  ${CYAN}nano /opt/pmbot-<stack>/backend/.env${NC}     # edit config"
echo -e "  ${CYAN}systemctl restart pmbot-<stack>-backend${NC}  # apply .env changes"
echo ""

# Warn about any stacks with no private key set
for entry in "${DEPLOYED[@]}"; do
    inst=$(echo "$entry" | cut -d: -f1)
    env_file="/opt/pmbot-${inst}/backend/.env"
    if grep -q "^DRY_RUN=true" "$env_file" 2>/dev/null; then
        warn "${inst}: running in dry-run mode — edit .env to add PRIVATE_KEY"
        echo -e "     ${YELLOW}nano /opt/pmbot-${inst}/backend/.env${NC}"
        echo -e "     ${YELLOW}systemctl restart pmbot-${inst}-backend${NC}"
    fi
done

echo ""
