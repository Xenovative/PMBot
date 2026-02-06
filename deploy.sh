#!/bin/bash
set -e

# ============================================
#  Polymarket Arbitrage Bot — VPS Deployment
#  Tested on: Ubuntu 22.04 / Debian 12
#  Run as root: ./deploy.sh
# ============================================

APP_DIR="/opt/pmbot"
APP_USER="pmbot"
DOMAIN=""  # Set to your domain for HTTPS, leave empty for IP-only
BACKEND_PORT=8888
FRONTEND_PORT=5173
NODE_VERSION=20

echo "========================================"
echo "  Polymarket Bot — VPS Deployment"
echo "========================================"

# ── 1. System dependencies ──
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq software-properties-common nginx curl git

# Python 3.12 (required by web3 and py-clob-client)
if ! python3.12 --version &>/dev/null; then
    echo "  Installing Python 3.12..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.12 python3.12-venv python3.12-dev
fi
PYTHON_BIN=$(command -v python3.12)

# Node.js via NodeSource
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

echo "  Python: $($PYTHON_BIN --version)"
echo "  Node:   $(node --version)"
echo "  npm:    $(npm --version)"

# ── 2. Create app user ──
echo "[2/7] Setting up app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"
fi

# ── 3. Deploy code ──
echo "[3/7] Deploying application..."
if [ -d "$APP_DIR/.git" ]; then
    echo "  Pulling latest changes..."
    cd "$APP_DIR" && sudo -u "$APP_USER" git pull
else
    echo "  Copying files to $APP_DIR..."
    mkdir -p "$APP_DIR"
    cp -r . "$APP_DIR/"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# ── 4. Backend setup ──
echo "[4/7] Setting up backend..."
cd "$APP_DIR/backend"

sudo -u "$APP_USER" $PYTHON_BIN -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r requirements.txt

if [ ! -f "$APP_DIR/backend/.env" ]; then
    cp "$APP_DIR/backend/.env.example" "$APP_DIR/backend/.env"
    echo ""
    echo "  ⚠️  IMPORTANT: Edit $APP_DIR/backend/.env with your private key!"
    echo ""
fi

# ── 5. Frontend setup ──
echo "[5/7] Building frontend..."
cd "$APP_DIR/frontend"
sudo -u "$APP_USER" npm install --silent
sudo -u "$APP_USER" npm run build

# ── 6. Systemd services ──
echo "[6/7] Creating systemd services..."

cat > /etc/systemd/system/pmbot-backend.service << EOF
[Unit]
Description=PMBot Backend (FastAPI)
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR/backend
ExecStart=$APP_DIR/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PATH=$APP_DIR/venv/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pmbot-backend
systemctl restart pmbot-backend

# Frontend via PM2
echo "  Starting frontend with PM2..."
sudo -u "$APP_USER" bash -c "cd $APP_DIR/frontend && pm2 delete pmbot-frontend 2>/dev/null; pm2 start 'npx vite preview --host 0.0.0.0 --port $FRONTEND_PORT' --name pmbot-frontend"
sudo -u "$APP_USER" pm2 save
pm2 startup systemd -u "$APP_USER" --hp "$APP_DIR" 2>/dev/null || true

# ── 7. Nginx reverse proxy ──
echo "[7/7] Configuring Nginx..."

SERVER_NAME="${DOMAIN:-_}"

cat > /etc/nginx/sites-available/pmbot << EOF
server {
    listen 80;
    server_name $SERVER_NAME;

    # Frontend (static build)
    location / {
        root $APP_DIR/frontend/dist;
        try_files \$uri \$uri/ /index.html;
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

ln -sf /etc/nginx/sites-available/pmbot /etc/nginx/sites-enabled/pmbot
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""
echo "  Dashboard:  http://$(hostname -I | awk '{print $1}')"
echo "  Backend:    http://127.0.0.1:$BACKEND_PORT"
echo ""
echo "  Services:"
echo "    systemctl status pmbot-backend     # backend"
echo "    sudo -u $APP_USER pm2 status       # frontend"
echo "    journalctl -u pmbot-backend -f     # backend logs"
echo "    sudo -u $APP_USER pm2 logs pmbot-frontend  # frontend logs"
echo ""
echo "  Config:     $APP_DIR/backend/.env"
echo ""
if [ ! -s "$APP_DIR/backend/.env" ] || grep -q "your_private_key_here" "$APP_DIR/backend/.env" 2>/dev/null; then
    echo "  ⚠️  Don't forget to edit .env with your private key!"
    echo "     nano $APP_DIR/backend/.env"
    echo "     systemctl restart pmbot-backend"
    echo ""
fi
