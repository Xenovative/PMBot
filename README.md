# Polymarket Arbitrage Bot

Automated arbitrage system for Polymarket 15-minute crypto UP/DOWN markets with real-time Web GUI dashboard.

## How It Works

Polymarket binary markets pay out exactly **$1.00** per share regardless of outcome. When **UP ask + DOWN ask < $1.00**, buying both sides locks in a risk-free profit.

```
Example: UP = $0.48 + DOWN = $0.50 = $0.98 total
Buy 10 shares each → spend $9.80 → guaranteed payout $10.00 → profit $0.20
```

## Features

- **Real-time market scanning** — auto-discovers 15-min BTC/ETH/SOL UP/DOWN markets
- **Safe FOK execution** — adaptive sizing based on order book depth, $1 minimum check
- **Unwind protection** — if one side fails, immediately sells the other back
- **Auto-merge** — CTF merge paired positions back to USDC on-chain
- **Web dashboard** — real-time prices, trade history, config controls (Traditional Chinese UI)
- **Dry-run mode** — simulate trades without spending real funds

## Architecture

```
┌─────────────────┐     WebSocket/REST     ┌──────────────────┐
│  React Frontend │ ◄──────────────────────► │  FastAPI Backend  │
│  Vite + Tailwind│                         │  py-clob-client   │
│  Port 5173      │                         │  Port 8888        │
└─────────────────┘                         └────────┬─────────┘
                                                     │
                                            ┌────────▼─────────┐
                                            │  Polymarket CLOB  │
                                            │  Gamma API        │
                                            │  Polygon Chain    │
                                            └──────────────────┘
```

## Quick Start (Local)

### Windows

```bash
# 1. Clone and configure
git clone <repo-url> && cd PMBot
copy backend\.env.example backend\.env
# Edit backend\.env with your private key and funder address

# 2. Install dependencies
cd backend && pip install -r requirements.txt
cd ../frontend && npm install

# 3. Run
cd .. && start.bat
```

### Linux / macOS

```bash
# 1. Clone and configure
git clone <repo-url> && cd PMBot
cp backend/.env.example backend/.env
# Edit backend/.env with your private key and funder address

# 2. Install dependencies
cd backend && pip install -r requirements.txt
cd ../frontend && npm install

# 3. Run
cd ../backend && python main.py &
cd ../frontend && npm run dev &
```

Dashboard: `http://localhost:5173`

## VPS Deployment

See [`deploy.sh`](deploy.sh) for one-command Ubuntu/Debian deployment with systemd services and Nginx reverse proxy.

```bash
# On your VPS (as root):
curl -o deploy.sh <raw-github-url>/deploy.sh
chmod +x deploy.sh
./deploy.sh
```

## Configuration

All settings can be changed live via the Web UI or preset in `backend/.env`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PRIVATE_KEY` | — | Polygon wallet private key |
| `FUNDER_ADDRESS` | — | Funder/proxy wallet address |
| `TARGET_PAIR_COST` | 0.99 | Max total cost to trigger arbitrage (< $1.00) |
| `ORDER_SIZE` | 50 | Shares per trade |
| `DRY_RUN` | true | Simulate trades (no real funds) |
| `SIGNATURE_TYPE` | 0 | 0=EOA, 1=Email/Magic, 2=Proxy |
| `MIN_TIME_REMAINING_SECONDS` | 120 | Skip markets expiring too soon |
| `MAX_TRADES_PER_MARKET` | 10 | Max trades per market window |
| `TRADE_COOLDOWN_SECONDS` | 60 | Cooldown between trades |
| `MIN_LIQUIDITY` | 100 | Min order book depth (shares) |
| `CRYPTO_SYMBOLS` | btc,eth,sol | Comma-separated symbols to monitor |

## Trade Execution Safety

1. **Adaptive sizing** — caps order to 80% of available book depth
2. **$1 minimum check** — skips trades where either side < $1 USD
3. **Buy weaker side first** — less liquid side bought first (fails safely with no exposure)
4. **Half-size retry** — retries at 50% if first attempt FOK-fails
5. **Auto-unwind** — if second side fails, immediately sells first side back (FOK → GTC fallback)
6. **Price pinning** — orders use the checked price, preventing slippage

## Risk Warning

⚠️ This software is provided for educational and research purposes only. Cryptocurrency trading involves substantial risk of loss. Use at your own risk.
