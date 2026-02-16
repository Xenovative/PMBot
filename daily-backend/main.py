"""
Polymarket 套利機器人 - 每日 Up or Down 市場版本 - FastAPI 後端
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_config, BotConfig
from market_finder import MarketFinder, MarketInfo
from arbitrage_engine import ArbitrageEngine
from position_merger import PositionMerger
import trade_db
import auth

app = FastAPI(title="Polymarket 每日套利機器人")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全域狀態
config = get_config()

# 初始化 SQLite（模擬/真實分開存檔）
trade_db.init_db(dry_run=config.dry_run)

market_finder = MarketFinder(config)
engine = ArbitrageEngine(config)
bot_task: Optional[asyncio.Task] = None
connected_clients: list[WebSocket] = []


# ─── WebSocket 廣播 ───
async def broadcast(data: Dict[str, Any]):
    message = json.dumps(data, ensure_ascii=False)
    disconnected = []
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except:
            disconnected.append(ws)
    for ws in disconnected:
        connected_clients.remove(ws)


# ─── Auth API (公開路由，不需要 token) ───

class SetupRequest(BaseModel):
    password: str

class LoginRequest(BaseModel):
    password: str
    totp_code: Optional[str] = None

class TotpSetupRequest(BaseModel):
    device_name: Optional[str] = "Authenticator"

class TotpVerifyRequest(BaseModel):
    code: str

class DeviceRemoveRequest(BaseModel):
    device_id: str


@app.get("/api/auth/status")
async def auth_status():
    """Check if initial setup is done and 2FA is enabled"""
    return {
        "setup_complete": auth.is_setup_complete(),
        "totp_enabled": auth.is_2fa_enabled(),
    }


@app.post("/api/auth/setup")
async def auth_setup(req: SetupRequest):
    """First-time password setup (only works if no password set yet)"""
    if auth.is_setup_complete():
        return {"error": "Already set up. Use login instead."}
    try:
        auth.setup_password(req.password)
        token = auth.create_token()
        return {"status": "ok", "token": token}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """Login with password + optional TOTP"""
    if not auth.is_setup_complete():
        return {"error": "Not set up yet. Use /api/auth/setup first."}

    if not auth.verify_password(req.password):
        return {"error": "Invalid password"}

    if auth.is_2fa_enabled():
        if not req.totp_code:
            return {"error": "2FA code required", "needs_totp": True}
        if not auth.verify_totp(req.totp_code):
            return {"error": "Invalid 2FA code"}

    token = auth.create_token()
    return {"status": "ok", "token": token}


@app.post("/api/auth/2fa/setup")
async def auth_2fa_setup(req: TotpSetupRequest = TotpSetupRequest(), _user=Depends(auth.require_auth)):
    """Start 2FA setup for a new device"""
    result = auth.setup_2fa(req.device_name or "Authenticator")
    return result


@app.post("/api/auth/2fa/verify")
async def auth_2fa_verify(req: TotpVerifyRequest, _user=Depends(auth.require_auth)):
    """Verify 2FA setup with a code from authenticator"""
    device = auth.verify_2fa_setup(req.code)
    if device:
        return {"status": "ok", "totp_enabled": True, "device": device}
    return {"error": "Invalid code. Try again."}


@app.get("/api/auth/2fa/devices")
async def auth_2fa_devices(_user=Depends(auth.require_auth)):
    """List all registered 2FA devices"""
    return {"devices": auth.list_devices()}


@app.post("/api/auth/2fa/remove")
async def auth_2fa_remove(req: DeviceRemoveRequest, _user=Depends(auth.require_auth)):
    """Remove a specific 2FA device"""
    if auth.remove_device(req.device_id):
        return {"status": "ok", "totp_enabled": auth.is_2fa_enabled()}
    return {"error": "Device not found"}


@app.post("/api/auth/2fa/disable")
async def auth_2fa_disable(_user=Depends(auth.require_auth)):
    """Remove all 2FA devices"""
    auth.disable_2fa()
    return {"status": "ok", "totp_enabled": False}


@app.post("/api/auth/change-password")
async def auth_change_password(req: SetupRequest, _user=Depends(auth.require_auth)):
    """Change password (requires auth)"""
    try:
        auth.setup_password(req.password)
        return {"status": "ok"}
    except ValueError as e:
        return {"error": str(e)}


@app.get("/api/auth/verify")
async def auth_verify_token(_user=Depends(auth.require_auth)):
    """Verify current token is valid"""
    return {"status": "ok", "user": _user}


# ─── 機器人主循環 ───
async def bot_loop():
    engine.status.running = True
    engine.status.start_time = datetime.now(timezone.utc).isoformat()
    engine.status.mode = "模擬" if config.dry_run else "🔴 真實交易"
    engine.status.add_log(f"🚀 每日套利機器人啟動 | 模式: {engine.status.mode}")
    engine.status.add_log(f"⚙️ 目標成本: {config.target_pair_cost} | 每筆數量: {config.order_size}")
    engine.status.add_log(f"🔍 監控幣種: {', '.join(config.crypto_symbols)}")

    await broadcast({"type": "status", "data": engine.status.to_dict()})

    try:
        while engine.status.running:
            # 搜尋市場
            all_markets = await market_finder.find_all_crypto_markets()

            if not all_markets:
                engine.status.add_log("⏳ 未找到活躍市場，5 秒後重試...")
                engine.status.current_market = None
                engine.status.active_markets = []
                await broadcast({"type": "status", "data": engine.status.to_dict()})
                for _ in range(5):
                    if not engine.status.running:
                        break
                    await asyncio.sleep(1)
                continue

            # 過濾有效市場
            valid_markets = [
                m for m in all_markets
                if m.time_remaining_seconds >= config.min_time_remaining_seconds
                and m.up_token_id and m.down_token_id
            ]

            if not valid_markets:
                engine.status.add_log("⏳ 無符合條件的市場，5 秒後重試...")
                engine.status.active_markets = []
                await broadcast({"type": "status", "data": engine.status.to_dict()})
                for _ in range(5):
                    if not engine.status.running:
                        break
                    await asyncio.sleep(1)
                continue

            # 更新活躍市場列表，清除過期市場價格
            valid_slugs = {m.slug for m in valid_markets}
            engine.status.active_markets = list(valid_slugs)
            engine.status.current_market = f"{len(valid_markets)} 個市場"
            stale = [s for s in engine.status.market_prices if s not in valid_slugs]
            for s in stale:
                del engine.status.market_prices[s]

            # 廣播找到的市場
            await broadcast({
                "type": "markets",
                "data": [m.to_dict() for m in valid_markets]
            })

            if engine.status.scan_count % 10 == 0:
                engine.status.add_log(f"📊 監控 {len(valid_markets)} 個活躍每日市場")

            # 並行掃描所有市場
            scan_tasks = [engine.scan_market(m) for m in valid_markets]
            results = await asyncio.gather(*scan_tasks, return_exceptions=True)

            # 收集所有機會
            all_opportunities = []
            for market, result in zip(valid_markets, results):
                if isinstance(result, Exception):
                    engine.status.add_log(f"⚠️ 掃描 {market.slug} 失敗: {str(result)[:80]}")
                    continue
                if result and result.is_viable:
                    all_opportunities.append(result)

            engine.status.current_opportunities = all_opportunities

            # 依利潤排序，逐一執行（避免同時下單衝突）
            all_opportunities.sort(key=lambda o: o.potential_profit, reverse=True)
            for opportunity in all_opportunities:
                if not engine.status.running:
                    break
                trade = await engine.execute_trade(opportunity)
                await broadcast({
                    "type": "trade",
                    "data": trade.to_dict()
                })

            # ─── 撿便宜策略: 掃描當前市場低價機會 ───
            if engine.status.running and config.bargain_enabled:
                bargain_opps = await engine.check_bargain_opportunities(all_markets)
                for opp in bargain_opps:
                    if not engine.status.running:
                        break
                    holding = await engine.execute_bargain_buy(opp)
                    if holding:
                        await broadcast({
                            "type": "bargain",
                            "data": holding.to_dict()
                        })

            # ─── 撿便宜策略: 監控持倉（配對 or 止損）───
            if engine.status.running:
                await engine.scan_bargain_holdings()

            await broadcast({"type": "status", "data": engine.status.to_dict()})
            await broadcast({"type": "merge_status", "data": engine.merger.get_status()})

            # 掃描間隔
            for _ in range(5):
                if not engine.status.running:
                    break
                await asyncio.sleep(1)

    except asyncio.CancelledError:
        engine.status.add_log("⛔ 機器人已停止")
    except Exception as e:
        engine.status.add_log(f"❌ 嚴重錯誤: {e}")
    finally:
        engine.status.running = False
        await broadcast({"type": "status", "data": engine.status.to_dict()})


# ─── API 路由 ───

@app.get("/api/status")
async def get_status(_user=Depends(auth.require_auth)):
    return engine.status.to_dict()


@app.get("/api/markets")
async def get_markets(_user=Depends(auth.require_auth)):
    markets = await market_finder.find_all_crypto_markets()
    return [m.to_dict() for m in markets]


@app.get("/api/config")
async def get_current_config(_user=Depends(auth.require_auth)):
    return {
        "target_pair_cost": config.target_pair_cost,
        "order_size": config.order_size,
        "dry_run": config.dry_run,
        "min_time_remaining_seconds": config.min_time_remaining_seconds,
        "max_trades_per_market": config.max_trades_per_market,
        "trade_cooldown_seconds": config.trade_cooldown_seconds,
        "min_liquidity": config.min_liquidity,
        "crypto_symbols": config.crypto_symbols,
        "private_key_set": bool(config.private_key),
        "funder_address_set": bool(config.funder_address),
        "bargain_enabled": config.bargain_enabled,
        "bargain_price_threshold": config.bargain_price_threshold,
        "bargain_pair_threshold": config.bargain_pair_threshold,
        "bargain_stop_loss_cents": config.bargain_stop_loss_cents,
        "bargain_min_price": config.bargain_min_price,
        "bargain_max_rounds": config.bargain_max_rounds,
        "bargain_stop_loss_defer_minutes": config.bargain_stop_loss_defer_minutes,
        "bargain_first_buy_bias": config.bargain_first_buy_bias,
        "bargain_pair_escalation_hours": config.bargain_pair_escalation_hours,
    }


class ConfigUpdate(BaseModel):
    target_pair_cost: Optional[float] = None
    order_size: Optional[float] = None
    dry_run: Optional[bool] = None
    min_time_remaining_seconds: Optional[int] = None
    max_trades_per_market: Optional[int] = None
    trade_cooldown_seconds: Optional[int] = None
    min_liquidity: Optional[float] = None
    crypto_symbols: Optional[list] = None
    private_key: Optional[str] = None
    funder_address: Optional[str] = None
    signature_type: Optional[int] = None
    bargain_enabled: Optional[bool] = None
    bargain_price_threshold: Optional[float] = None
    bargain_pair_threshold: Optional[float] = None
    bargain_stop_loss_cents: Optional[float] = None
    bargain_min_price: Optional[float] = None
    bargain_max_rounds: Optional[int] = None
    bargain_stop_loss_defer_minutes: Optional[int] = None
    bargain_first_buy_bias: Optional[str] = None
    bargain_pair_escalation_hours: Optional[int] = None


@app.post("/api/config")
async def update_config(update: ConfigUpdate, _user=Depends(auth.require_auth)):
    updates = {k: v for k, v in update.model_dump().items() if v is not None}
    engine.update_config(updates)
    for k, v in updates.items():
        if hasattr(config, k):
            setattr(config, k, v)
    # 切換模式時重新初始化 DB（模擬/真實分開存檔）
    if "dry_run" in updates:
        trade_db.init_db(dry_run=config.dry_run)
    return {"status": "ok", "updated": list(updates.keys())}


@app.post("/api/bot/start")
async def start_bot(_user=Depends(auth.require_auth)):
    global bot_task
    if engine.status.running:
        return {"status": "already_running"}

    engine.status = type(engine.status)()
    bot_task = asyncio.create_task(bot_loop())
    return {"status": "started"}


@app.post("/api/bot/stop")
async def stop_bot(_user=Depends(auth.require_auth)):
    global bot_task
    if not engine.status.running:
        return {"status": "not_running"}

    engine.status.running = False
    engine.status.add_log("⛔ 正在停止機器人...")
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        bot_task = None
    return {"status": "stopped"}


# ─── 合併 API ───

@app.get("/api/merge/status")
async def get_merge_status(_user=Depends(auth.require_auth)):
    return engine.merger.get_status()


@app.post("/api/merge/toggle")
async def toggle_auto_merge(_user=Depends(auth.require_auth)):
    engine.merger.auto_merge_enabled = not engine.merger.auto_merge_enabled
    state = "啟用" if engine.merger.auto_merge_enabled else "停用"
    engine.status.add_log(f"🔄 自動合併已{state}")
    return {"auto_merge_enabled": engine.merger.auto_merge_enabled}


class MergeRequest(BaseModel):
    condition_id: str
    amount: Optional[float] = None


@app.post("/api/merge/execute")
async def execute_merge(req: MergeRequest, _user=Depends(auth.require_auth)):
    record = await engine.merger.merge_positions(req.condition_id, req.amount)
    if record:
        await broadcast({"type": "merge", "data": record.to_dict()})
        return record.to_dict()
    return {"error": "合併失敗"}


@app.post("/api/merge/all")
async def merge_all_positions(_user=Depends(auth.require_auth)):
    results = await engine.merger.auto_merge_all()
    for r in results:
        await broadcast({"type": "merge", "data": r.to_dict()})
    return [r.to_dict() for r in results]


# ─── Analytics API ───

@app.get("/api/analytics/overview")
async def analytics_overview(_user=Depends(auth.require_auth)):
    return trade_db.get_overview()


@app.get("/api/analytics/cumulative-profit")
async def analytics_cumulative_profit(days: int = 30, _user=Depends(auth.require_auth)):
    return trade_db.get_cumulative_profit(days)


@app.get("/api/analytics/daily-pnl")
async def analytics_daily_pnl(days: int = 30, _user=Depends(auth.require_auth)):
    return trade_db.get_daily_pnl(days)


@app.get("/api/analytics/trade-frequency")
async def analytics_trade_frequency(days: int = 30, _user=Depends(auth.require_auth)):
    return trade_db.get_trade_frequency(days)


@app.get("/api/analytics/win-rate")
async def analytics_win_rate(days: int = 30, _user=Depends(auth.require_auth)):
    return trade_db.get_win_rate_over_time(days)


@app.get("/api/analytics/per-market")
async def analytics_per_market(_user=Depends(auth.require_auth)):
    return trade_db.get_per_market_stats()


@app.get("/api/analytics/trades")
async def analytics_trades(limit: int = 100, offset: int = 0, status: Optional[str] = None, _user=Depends(auth.require_auth)):
    return trade_db.get_trades(limit, offset, status)


@app.get("/api/analytics/merges")
async def analytics_merges(limit: int = 50, _user=Depends(auth.require_auth)):
    return trade_db.get_merges(limit)


@app.get("/api/price/{crypto}")
async def get_price(crypto: str, _user=Depends(auth.require_auth)):
    """手動獲取指定加密貨幣的當前市場價格"""
    market = await market_finder.find_active_tradeable_market(crypto.lower())
    if not market:
        return {"error": f"未找到 {crypto.upper()} 的活躍市場"}

    price_info = await engine.get_prices(market)
    if not price_info:
        return {"error": "無法獲取價格"}

    return {
        "market": market.to_dict(),
        "price": price_info.to_dict(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)):
    # Require auth if setup is complete
    if auth.is_setup_complete():
        if not token or not auth._jwt_verify(token):
            await websocket.close(code=4001, reason="Unauthorized")
            return
    await websocket.accept()
    connected_clients.append(websocket)
    engine.status.add_log("🔗 新的 WebSocket 連接")

    try:
        await websocket.send_text(json.dumps(
            {"type": "status", "data": engine.status.to_dict()},
            ensure_ascii=False
        ))
        await websocket.send_text(json.dumps(
            {"type": "merge_status", "data": engine.merger.get_status()},
            ensure_ascii=False
        ))

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
    except Exception:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8889))
    uvicorn.run(app, host="0.0.0.0", port=port)
