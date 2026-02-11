#!/bin/bash
set -e

# ============================================
#  Polymarket Bot — Multi-Instance Deployment
#  Supports deploying multiple bot instances
#  Tested on: Ubuntu 22.04 / Debian 12
#  Run as root: ./deploy-instance.sh
# ============================================

NODE_VERSION=20

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── Helper functions ──
info()  { echo -e "${CYAN}  $1${NC}"; }
warn()  { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
ok()    { echo -e "${GREEN}  ✓ $1${NC}"; }
err()   { echo -e "${RED}  ✗ $1${NC}"; }
header(){ echo -e "\n${BOLD}${CYAN}[$1]${NC} $2"; }

# ============================================
#  Interactive Instance Configuration
# ============================================

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║  ${CYAN}Polymarket Bot — Multi-Instance Deployer${NC}${BOLD}    ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    err "Please run as root: sudo ./deploy-instance.sh"
    exit 1
fi

# ── Detect available bot source directories ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo -e "${BOLD}Available bot sources in ${SCRIPT_DIR}:${NC}"
echo ""

SOURCES=()
idx=1
for dir in "$SCRIPT_DIR"/*/; do
    dirname=$(basename "$dir")
    # Only show directories that have a backend-like structure (main.py or requirements.txt)
    if [ -f "$dir/main.py" ] || [ -f "$dir/requirements.txt" ]; then
        SOURCES+=("$dirname")
        echo -e "  ${CYAN}${idx})${NC} ${BOLD}${dirname}${NC}"
        # Check if it has a frontend sibling
        frontend_dir="${dirname/backend/frontend}"
        if [ -d "$SCRIPT_DIR/$frontend_dir" ]; then
            echo -e "     └─ Frontend: ${GREEN}${frontend_dir}${NC}"
        fi
        ((idx++))
    fi
done

if [ ${#SOURCES[@]} -eq 0 ]; then
    err "No bot source directories found (looking for dirs with main.py or requirements.txt)"
    exit 1
fi

echo ""
echo -e "${BOLD}── Instance Configuration ──${NC}"
echo ""

# ── Select backend source ──
if [ ${#SOURCES[@]} -eq 1 ]; then
    BACKEND_SRC="${SOURCES[0]}"
    info "Auto-selected backend source: $BACKEND_SRC"
else
    read -p "  Select backend source [1-${#SOURCES[@]}]: " src_choice
    src_choice=${src_choice:-1}
    if [ "$src_choice" -lt 1 ] || [ "$src_choice" -gt ${#SOURCES[@]} ]; then
        err "Invalid selection"; exit 1
    fi
    BACKEND_SRC="${SOURCES[$((src_choice-1))]}"
fi

# ── Auto-detect frontend source ──
FRONTEND_SRC="${BACKEND_SRC/backend/frontend}"
if [ ! -d "$SCRIPT_DIR/$FRONTEND_SRC" ]; then
    FRONTEND_SRC=""
    warn "No matching frontend directory found for $BACKEND_SRC"
    read -p "  Enter frontend source directory (or leave empty to skip): " FRONTEND_SRC
fi

if [ -n "$FRONTEND_SRC" ] && [ ! -d "$SCRIPT_DIR/$FRONTEND_SRC" ]; then
    err "Frontend directory '$FRONTEND_SRC' not found"; exit 1
fi

echo ""

# ── Instance name ──
# Derive a default name from the source dir
DEFAULT_NAME=$(echo "$BACKEND_SRC" | sed 's/-backend//' | sed 's/_backend//')
read -p "  Instance name [${DEFAULT_NAME}]: " INSTANCE_NAME
INSTANCE_NAME=${INSTANCE_NAME:-$DEFAULT_NAME}
# Sanitize: lowercase, replace spaces with dashes
INSTANCE_NAME=$(echo "$INSTANCE_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')

echo ""

# ── Ports ──
# Suggest ports based on existing instances to avoid conflicts
USED_PORTS=$(ss -tlnp 2>/dev/null | grep -oP ':\K[0-9]+' | sort -un || true)

suggest_port() {
    local base=$1
    local port=$base
    while echo "$USED_PORTS" | grep -qw "$port" 2>/dev/null; do
        ((port++))
    done
    echo $port
}

DEFAULT_BACKEND_PORT=$(suggest_port 8889)
DEFAULT_FRONTEND_PORT=$(suggest_port 5174)
DEFAULT_NGINX_PORT=$(suggest_port 81)

read -p "  Backend port [${DEFAULT_BACKEND_PORT}]: " BACKEND_PORT
BACKEND_PORT=${BACKEND_PORT:-$DEFAULT_BACKEND_PORT}

if [ -n "$FRONTEND_SRC" ]; then
    read -p "  Frontend dev port [${DEFAULT_FRONTEND_PORT}]: " FRONTEND_PORT
    FRONTEND_PORT=${FRONTEND_PORT:-$DEFAULT_FRONTEND_PORT}
fi

read -p "  Nginx listen port [${DEFAULT_NGINX_PORT}]: " NGINX_PORT
NGINX_PORT=${NGINX_PORT:-$DEFAULT_NGINX_PORT}

echo ""

# ── Domain (optional) ──
read -p "  Domain name (leave empty for IP-only): " DOMAIN

# ── Derived paths ──
APP_DIR="/opt/pmbot-${INSTANCE_NAME}"
APP_USER="pmbot"
SERVICE_NAME="pmbot-${INSTANCE_NAME}-backend"
PM2_NAME="pmbot-${INSTANCE_NAME}-frontend"
NGINX_SITE="pmbot-${INSTANCE_NAME}"

echo ""
echo -e "${BOLD}── Deployment Summary ──${NC}"
echo ""
echo -e "  Instance:     ${CYAN}${INSTANCE_NAME}${NC}"
echo -e "  Backend src:  ${BACKEND_SRC}/"
[ -n "$FRONTEND_SRC" ] && echo -e "  Frontend src: ${FRONTEND_SRC}/"
echo -e "  Install dir:  ${APP_DIR}/"
echo -e "  Backend port: ${BACKEND_PORT}"
[ -n "$FRONTEND_SRC" ] && echo -e "  Frontend port:${FRONTEND_PORT}"
echo -e "  Nginx port:   ${NGINX_PORT}"
[ -n "$DOMAIN" ] && echo -e "  Domain:       ${DOMAIN}"
echo -e "  Service:      ${SERVICE_NAME}"
[ -n "$FRONTEND_SRC" ] && echo -e "  PM2 process:  ${PM2_NAME}"
echo ""

read -p "  Proceed with deployment? [Y/n]: " CONFIRM
CONFIRM=${CONFIRM:-Y}
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Aborted."
    exit 0
fi

echo ""
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Deploying ${CYAN}${INSTANCE_NAME}${NC}${BOLD}...${NC}"
echo -e "${BOLD}════════════════════════════════════════════════${NC}"

# ============================================
#  1. System Dependencies
# ============================================
header "1/7" "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq software-properties-common nginx curl git rsync

# Python 3.12
if ! python3.12 --version &>/dev/null; then
    info "Installing Python 3.12..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.12 python3.12-venv python3.12-dev
fi
PYTHON_BIN=$(command -v python3.12)

# Node.js
if [ -n "$FRONTEND_SRC" ]; then
    if ! command -v node &>/dev/null; then
        info "Installing Node.js ${NODE_VERSION}..."
        curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash -
        apt-get install -y -qq nodejs
    fi
fi

# PM2
if [ -n "$FRONTEND_SRC" ]; then
    if ! command -v pm2 &>/dev/null; then
        info "Installing PM2..."
        npm install -g pm2
    fi
    PM2_BIN=$(which pm2)
fi

ok "Python: $($PYTHON_BIN --version)"
[ -n "$FRONTEND_SRC" ] && ok "Node: $(node --version)"

# ============================================
#  2. Create app user
# ============================================
header "2/7" "Setting up app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -d /opt -s /bin/bash "$APP_USER"
    ok "Created user: $APP_USER"
else
    ok "User $APP_USER already exists"
fi

# ============================================
#  3. Deploy code
# ============================================
header "3/7" "Deploying application..."
mkdir -p "$APP_DIR"

IS_UPDATE=false
if [ -d "$APP_DIR/backend" ]; then
    IS_UPDATE=true
    info "Existing install detected — updating code..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    [ -n "$FRONTEND_SRC" ] && $PM2_BIN stop "$PM2_NAME" 2>/dev/null || true
else
    info "Fresh install — copying files..."
fi

# Sync backend (preserve .env, .auth.json, databases)
rsync -a --delete \
    --exclude '.env' \
    --exclude '.auth.json' \
    --exclude '*.db' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$SCRIPT_DIR/$BACKEND_SRC/" "$APP_DIR/backend/"

# Sync frontend
if [ -n "$FRONTEND_SRC" ]; then
    rsync -a --delete \
        --exclude 'node_modules' \
        --exclude 'dist' \
        "$SCRIPT_DIR/$FRONTEND_SRC/" "$APP_DIR/frontend/"
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
ok "Code synced to $APP_DIR"

# ============================================
#  4. Backend setup
# ============================================
header "4/7" "Setting up backend..."
cd "$APP_DIR/backend"

# Recreate venv if wrong Python version
CURRENT_PY=$("$APP_DIR/venv/bin/python" --version 2>/dev/null || echo "none")
if [[ "$CURRENT_PY" != *"3.12"* ]]; then
    info "Creating venv with Python 3.12 (was: $CURRENT_PY)..."
    rm -rf "$APP_DIR/venv"
fi
sudo -u "$APP_USER" $PYTHON_BIN -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r requirements.txt

if [ ! -f "$APP_DIR/backend/.env" ]; then
    if [ -f "$APP_DIR/backend/.env.example" ]; then
        cp "$APP_DIR/backend/.env.example" "$APP_DIR/backend/.env"
        warn "Created .env from template — edit it: nano $APP_DIR/backend/.env"
    else
        warn "No .env.example found — create $APP_DIR/backend/.env manually"
    fi
else
    ok ".env preserved from previous install"
fi

if [ -f "$APP_DIR/backend/.auth.json" ]; then
    ok "Auth credentials preserved (.auth.json)"
fi

# ============================================
#  5. Frontend setup
# ============================================
if [ -n "$FRONTEND_SRC" ]; then
    header "5/7" "Building frontend..."
    cd "$APP_DIR/frontend"
    sudo -u "$APP_USER" npm install --silent
    sudo -u "$APP_USER" npm run build
    ok "Frontend built"
else
    header "5/7" "No frontend — skipping..."
fi

# ============================================
#  6. Systemd service
# ============================================
header "6/7" "Creating systemd service..."

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=PMBot ${INSTANCE_NAME} Backend (FastAPI)
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

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
ok "Service $SERVICE_NAME started"

# Frontend via PM2
if [ -n "$FRONTEND_SRC" ]; then
    info "Starting frontend with PM2..."
    $PM2_BIN delete "$PM2_NAME" 2>/dev/null || true
    $PM2_BIN start "npx vite preview --host 0.0.0.0 --port $FRONTEND_PORT" \
        --name "$PM2_NAME" \
        --cwd "$APP_DIR/frontend" \
        --uid "$APP_USER"
    $PM2_BIN save
    $PM2_BIN startup systemd 2>/dev/null || true
    ok "PM2 process $PM2_NAME started"
fi

# ============================================
#  7. Nginx reverse proxy
# ============================================
header "7/7" "Configuring Nginx..."

SERVER_NAME="${DOMAIN:-_}"

NGINX_CONF="/etc/nginx/sites-available/${NGINX_SITE}"

cat > "$NGINX_CONF" << EOF
server {
    listen ${NGINX_PORT};
    server_name $SERVER_NAME;

    # Frontend (static build)
    location / {
EOF

if [ -n "$FRONTEND_SRC" ]; then
cat >> "$NGINX_CONF" << EOF
        root $APP_DIR/frontend/dist;
        try_files \$uri \$uri/ /index.html;
EOF
else
cat >> "$NGINX_CONF" << EOF
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
EOF
fi

cat >> "$NGINX_CONF" << EOF
    }

    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    # WebSocket
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
EOF

ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/${NGINX_SITE}"
nginx -t && systemctl reload nginx
ok "Nginx configured on port ${NGINX_PORT}"

# ============================================
#  Done!
# ============================================
IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  ${GREEN}✓ Instance '${INSTANCE_NAME}' deployed!${NC}"
echo -e "${BOLD}════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:    ${CYAN}http://${IP}:${NGINX_PORT}${NC}"
echo -e "  Backend:      http://127.0.0.1:${BACKEND_PORT}"
echo -e "  Install dir:  ${APP_DIR}/"
echo ""
echo -e "  ${BOLD}Services:${NC}"
echo -e "    systemctl status ${SERVICE_NAME}        # backend"
[ -n "$FRONTEND_SRC" ] && echo -e "    pm2 status                              # frontend"
echo -e "    journalctl -u ${SERVICE_NAME} -f       # backend logs"
[ -n "$FRONTEND_SRC" ] && echo -e "    pm2 logs ${PM2_NAME}                    # frontend logs"
echo ""
echo -e "  ${BOLD}Config:${NC}  nano ${APP_DIR}/backend/.env"
echo ""

if [ ! -s "$APP_DIR/backend/.env" ] || grep -q "your_private_key_here" "$APP_DIR/backend/.env" 2>/dev/null; then
    warn "Don't forget to edit .env with your private key!"
    echo -e "     nano $APP_DIR/backend/.env"
    echo -e "     systemctl restart $SERVICE_NAME"
    echo ""
fi
