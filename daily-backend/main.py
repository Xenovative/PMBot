"""
Polymarket 套利機器人 - 每日 Up or Down 市場版本 - FastAPI 後端
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_config, BotConfig
from market_finder import MarketFinder, MarketInfo
from arbitrage_engine import ArbitrageEngine
from position_merger import PositionMerger

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

            # 更新活躍市場列表
            engine.status.active_markets = [m.slug for m in valid_markets]
            engine.status.current_market = f"{len(valid_markets)} 個市場"

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
async def get_status():
    return engine.status.to_dict()


@app.get("/api/markets")
async def get_markets():
    markets = await market_finder.find_all_crypto_markets()
    return [m.to_dict() for m in markets]


@app.get("/api/config")
async def get_current_config():
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


@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    updates = {k: v for k, v in update.model_dump().items() if v is not None}
    engine.update_config(updates)
    for k, v in updates.items():
        if hasattr(config, k):
            setattr(config, k, v)
    return {"status": "ok", "updated": list(updates.keys())}


@app.post("/api/bot/start")
async def start_bot():
    global bot_task
    if engine.status.running:
        return {"status": "already_running"}

    engine.status = type(engine.status)()
    bot_task = asyncio.create_task(bot_loop())
    return {"status": "started"}


@app.post("/api/bot/stop")
async def stop_bot():
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
async def get_merge_status():
    return engine.merger.get_status()


@app.post("/api/merge/toggle")
async def toggle_auto_merge():
    engine.merger.auto_merge_enabled = not engine.merger.auto_merge_enabled
    state = "啟用" if engine.merger.auto_merge_enabled else "停用"
    engine.status.add_log(f"🔄 自動合併已{state}")
    return {"auto_merge_enabled": engine.merger.auto_merge_enabled}


class MergeRequest(BaseModel):
    condition_id: str
    amount: Optional[float] = None


@app.post("/api/merge/execute")
async def execute_merge(req: MergeRequest):
    record = await engine.merger.merge_positions(req.condition_id, req.amount)
    if record:
        await broadcast({"type": "merge", "data": record.to_dict()})
        return record.to_dict()
    return {"error": "合併失敗"}


@app.post("/api/merge/all")
async def merge_all_positions():
    results = await engine.merger.auto_merge_all()
    for r in results:
        await broadcast({"type": "merge", "data": r.to_dict()})
    return [r.to_dict() for r in results]


@app.get("/api/price/{crypto}")
async def get_price(crypto: str):
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
async def websocket_endpoint(websocket: WebSocket):
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
    uvicorn.run(app, host="0.0.0.0", port=8889)
