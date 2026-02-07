"""
套利引擎 - 核心套利邏輯、風險控制、交易執行（每日 Up or Down 市場版本）
"""
import asyncio
import time
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from config import BotConfig
from market_finder import MarketInfo
from position_merger import PositionMerger


@dataclass
class PriceInfo:
    up_price: float = 0.0
    down_price: float = 0.0
    total_cost: float = 0.0
    spread: float = 0.0
    up_best_ask: float = 0.0
    down_best_ask: float = 0.0
    up_liquidity: float = 0.0
    down_liquidity: float = 0.0
    up_asks: List[Dict[str, float]] = field(default_factory=list)
    down_asks: List[Dict[str, float]] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "up_price": self.up_price,
            "down_price": self.down_price,
            "total_cost": self.total_cost,
            "spread": self.spread,
            "up_best_ask": self.up_best_ask,
            "down_best_ask": self.down_best_ask,
            "up_liquidity": self.up_liquidity,
            "down_liquidity": self.down_liquidity,
            "timestamp": self.timestamp,
        }


@dataclass
class TradeRecord:
    timestamp: str
    market_slug: str
    up_price: float
    down_price: float
    total_cost: float
    order_size: float
    expected_profit: float
    profit_pct: float
    status: str  # "executed", "simulated", "failed"
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "market_slug": self.market_slug,
            "up_price": self.up_price,
            "down_price": self.down_price,
            "total_cost": self.total_cost,
            "order_size": self.order_size,
            "expected_profit": self.expected_profit,
            "profit_pct": self.profit_pct,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class ArbitrageOpportunity:
    market: MarketInfo
    price_info: PriceInfo
    potential_profit: float
    profit_pct: float
    is_viable: bool
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market.to_dict(),
            "price_info": self.price_info.to_dict(),
            "potential_profit": self.potential_profit,
            "profit_pct": self.profit_pct,
            "is_viable": self.is_viable,
            "reason": self.reason,
        }


@dataclass
class BotStatus:
    running: bool = False
    current_market: Optional[str] = None
    mode: str = "模擬"
    total_trades: int = 0
    total_profit: float = 0.0
    trades_this_market: int = 0
    last_trade_time: float = 0.0
    last_price: Optional[PriceInfo] = None
    opportunities_found: int = 0
    scan_count: int = 0
    start_time: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    trade_history: List[TradeRecord] = field(default_factory=list)
    current_opportunities: List[ArbitrageOpportunity] = field(default_factory=list)

    def add_log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "current_market": self.current_market,
            "mode": self.mode,
            "total_trades": self.total_trades,
            "total_profit": round(self.total_profit, 4),
            "trades_this_market": self.trades_this_market,
            "last_price": self.last_price.to_dict() if self.last_price else None,
            "opportunities_found": self.opportunities_found,
            "scan_count": self.scan_count,
            "start_time": self.start_time,
            "logs": self.logs[-50:],
            "trade_history": [t.to_dict() for t in self.trade_history[-20:]],
            "current_opportunities": [o.to_dict() for o in self.current_opportunities],
        }


class ArbitrageEngine:
    def __init__(self, config: BotConfig):
        self.config = config
        self.status = BotStatus()
        self.merger = PositionMerger(config)
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def get_prices(self, market: MarketInfo) -> Optional[PriceInfo]:
        """從 CLOB API 獲取 UP/DOWN 代幣的當前價格和訂單簿深度"""
        up_id = market.up_token_id
        down_id = market.down_token_id
        if not up_id or not down_id:
            return None

        price_info = PriceInfo()
        price_info.timestamp = datetime.now(timezone.utc).isoformat()

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                # 獲取 UP 代幣價格
                up_resp = await client.get(
                    f"{self.config.CLOB_HOST}/price",
                    params={"token_id": up_id, "side": "buy"}
                )
                if up_resp.status_code == 200:
                    price_info.up_price = float(up_resp.json().get("price", 0))

                # 獲取 DOWN 代幣價格
                down_resp = await client.get(
                    f"{self.config.CLOB_HOST}/price",
                    params={"token_id": down_id, "side": "buy"}
                )
                if down_resp.status_code == 200:
                    price_info.down_price = float(down_resp.json().get("price", 0))

                # 獲取訂單簿深度
                up_book_resp = await client.get(
                    f"{self.config.CLOB_HOST}/book",
                    params={"token_id": up_id}
                )
                if up_book_resp.status_code == 200:
                    book = up_book_resp.json()
                    asks = book.get("asks", [])
                    if asks:
                        price_info.up_best_ask = min(float(a.get("price", 0)) for a in asks)
                        price_info.up_liquidity = sum(
                            float(a.get("size", 0)) for a in asks[:5]
                        )
                        price_info.up_asks = [
                            {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
                            for a in asks[:10]
                        ]

                down_book_resp = await client.get(
                    f"{self.config.CLOB_HOST}/book",
                    params={"token_id": down_id}
                )
                if down_book_resp.status_code == 200:
                    book = down_book_resp.json()
                    asks = book.get("asks", [])
                    if asks:
                        price_info.down_best_ask = min(float(a.get("price", 0)) for a in asks)
                        price_info.down_liquidity = sum(
                            float(a.get("size", 0)) for a in asks[:5]
                        )
                        price_info.down_asks = [
                            {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
                            for a in asks[:10]
                        ]

                # Use best_ask for cost calculation — that's the actual price we pay
                if price_info.up_best_ask > 0 and price_info.down_best_ask > 0:
                    price_info.total_cost = price_info.up_best_ask + price_info.down_best_ask
                else:
                    price_info.total_cost = price_info.up_price + price_info.down_price
                price_info.spread = 1.0 - price_info.total_cost

                return price_info

            except Exception as e:
                self.status.add_log(f"❌ 獲取價格失敗: {e}")
                return None

    def check_arbitrage(self, market: MarketInfo, price_info: PriceInfo) -> ArbitrageOpportunity:
        """檢查是否存在套利機會（含滑價容忍度）"""
        MAX_SLIPPAGE = 0.005  # 滑價容忍度（total_cost 已用 best_ask，僅需覆蓋市場衝擊）
        order_size = self.config.order_size
        total_cost = price_info.total_cost
        target = self.config.target_pair_cost

        # 用最壞情況（含滑價）計算利潤
        worst_cost = total_cost + MAX_SLIPPAGE
        investment = worst_cost * order_size
        payout = 1.0 * order_size
        profit = payout - investment
        profit_pct = (profit / investment * 100) if investment > 0 else 0

        is_viable = True
        reason = ""

        # 檢查 1: 含滑價的最壞總成本必須 < 1.0 且原始成本 < 目標
        if worst_cost >= 1.0:
            is_viable = False
            reason = f"含滑價成本 {worst_cost:.4f} >= 1.0，無利潤"
        elif total_cost >= target:
            is_viable = False
            reason = f"總成本 {total_cost:.4f} >= 目標 {target}"

        # 檢查 2: 價格是否合理
        elif price_info.up_price <= 0 or price_info.down_price <= 0:
            is_viable = False
            reason = "價格數據無效"

        # 檢查 3: 剩餘時間
        elif market.time_remaining_seconds < self.config.min_time_remaining_seconds:
            is_viable = False
            reason = f"剩餘時間不足 ({market.time_remaining_display})"

        # 檢查 4: 交易次數限制
        elif self.status.trades_this_market >= self.config.max_trades_per_market:
            is_viable = False
            reason = f"已達交易上限 ({self.config.max_trades_per_market})"

        # 檢查 5: 冷卻期
        elif time.time() - self.status.last_trade_time < self.config.trade_cooldown_seconds:
            cooldown_remaining = self.config.trade_cooldown_seconds - (time.time() - self.status.last_trade_time)
            is_viable = False
            reason = f"冷卻期中 (剩餘 {int(cooldown_remaining)} 秒)"

        # 檢查 6: 流動性
        elif price_info.up_liquidity < self.config.min_liquidity or price_info.down_liquidity < self.config.min_liquidity:
            is_viable = False
            reason = f"流動性不足 (UP: {price_info.up_liquidity:.0f}, DOWN: {price_info.down_liquidity:.0f})"

        # 檢查 7: 兩側 USD 金額都必須 >= $1（Polymarket 最低限制）
        elif order_size * min(price_info.up_price, price_info.down_price) < 1.0:
            is_viable = False
            low_side = "DOWN" if price_info.down_price < price_info.up_price else "UP"
            low_price = min(price_info.up_price, price_info.down_price)
            reason = f"{low_side} 金額不足 $1 ({order_size} × {low_price:.4f} = ${order_size * low_price:.2f})"

        else:
            reason = f"✅ 套利機會! 利潤: ${profit:.4f} ({profit_pct:.2f}%)"

        return ArbitrageOpportunity(
            market=market,
            price_info=price_info,
            potential_profit=round(profit, 4),
            profit_pct=round(profit_pct, 4),
            is_viable=is_viable,
            reason=reason,
        )

    def _get_sweep_price(self, asks: List[Dict[str, float]], shares_needed: float) -> tuple:
        """
        計算能填滿指定股數的掃單價格和實際 USD 成本（VWAP）
        返回 (worst_price, actual_usd_cost)
        - worst_price: FOK 限價（訂單簿中最差的成交價格層級）
        - actual_usd_cost: 實際需要的 USD（按每層 size*price 加總）
        如果深度不足，返回 (0.0, 0.0)
        """
        sorted_asks = sorted(asks, key=lambda x: x["price"])
        remaining = shares_needed
        sweep_price = 0.0
        total_cost = 0.0
        for level in sorted_asks:
            if remaining <= 0:
                break
            filled = min(remaining, level["size"])
            total_cost += filled * level["price"]
            remaining -= level["size"]
            sweep_price = level["price"]
        if remaining > 0:
            return (0.0, 0.0)
        return (sweep_price, total_cost)

    def _get_clob_client(self):
        """建立並返回 CLOB 客戶端"""
        from py_clob_client.client import ClobClient
        if not hasattr(self, '_clob_client') or self._clob_client is None:
            self._clob_client = ClobClient(
                self.config.CLOB_HOST,
                key=self.config.private_key,
                chain_id=self.config.CHAIN_ID,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address,
            )
            self._clob_client.set_api_creds(
                self._clob_client.create_or_derive_api_creds()
            )
        return self._clob_client

    def _calculate_safe_order_size(self, price_info: PriceInfo, desired_size: float) -> float:
        """根據訂單簿深度計算安全的下單數量，確保兩側 USD 金額都 >= $1"""
        MIN_ORDER_USD = 1.0

        available_up = price_info.up_liquidity * 0.8
        available_down = price_info.down_liquidity * 0.8
        safe_size = min(desired_size, available_up, available_down)
        safe_size = max(round(safe_size, 2), 1.0) if safe_size >= 1.0 else 0.0

        # 確保兩側 USD 金額都 >= $1，不超過 desired_size
        if safe_size > 0:
            up_usd = safe_size * price_info.up_price
            down_usd = safe_size * price_info.down_price
            if up_usd < MIN_ORDER_USD or down_usd < MIN_ORDER_USD:
                return 0.0

        return safe_size

    def _try_buy_one_side(self, clob_client, token_id: str, amount_usd: float,
                          price: float, side_label: str) -> dict:
        """
        FOK 買入 — price 僅用於估算股數，不傳入 MarketOrderArgs
        讓 CLOB 自動從訂單簿計算真實成交價（避免限價過緊導致 FOK 失敗）
        """
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        estimated_shares = amount_usd / price if price > 0 else 0

        # 確保 amount >= $1
        if amount_usd < 1.0:
            self.status.add_log(f"  ⚠️ {side_label} 金額 ${amount_usd:.2f} < $1 最低限制，跳過")
            return {"success": False, "error": "amount below $1 minimum", "shares": 0, "price": price}

        # price=None → CLOB 自動呼叫 calculate_market_price 從訂單簿取得真實價格
        # 先記錄 CLOB 自動計算的價格（用於診斷）
        try:
            auto_price = clob_client.calculate_market_price(
                token_id, "BUY", amount_usd, OrderType.FOK
            )
            actual_shares = amount_usd / auto_price if auto_price > 0 else 0
            self.status.add_log(
                f"  📖 {side_label} 訂單簿價格={auto_price:.4f} | "
                f"${amount_usd:.2f}/{auto_price:.4f}={actual_shares:.2f}股 "
                f"(effective估算: {estimated_shares:.2f}股)"
            )
        except Exception as e:
            self.status.add_log(f"  ⚠️ {side_label} 訂單簿深度不足: {str(e)[:80]}")
            return {"success": False, "error": f"orderbook depth: {str(e)[:80]}", "shares": 0, "price": price}

        try:
            order = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usd,
                side=BUY,
                price=None,
                order_type=OrderType.FOK,
            )
            signed = clob_client.create_market_order(order)
            resp = clob_client.post_order(signed, OrderType.FOK)
            self.status.add_log(
                f"  ✅ {side_label} FOK 成交 | ${amount_usd:.2f} @ {auto_price:.4f} ≈ {actual_shares:.1f} 股"
            )
            return {"success": True, "response": resp, "shares": actual_shares, "price": auto_price}
        except Exception as e:
            last_error = str(e)
            self.status.add_log(f"  ⚠️ {side_label} FOK 失敗: {last_error[:120]}")

        return {"success": False, "error": last_error[:120], "shares": 0, "price": price}

    def _try_unwind_position(self, clob_client, token_id: str, shares: float,
                             buy_price: float, side_label: str):
        """緊急平倉：賣出已買入的一側代幣以避免單邊風險"""
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        self.status.add_log(f"  🔥 緊急平倉 {side_label} | 賣出 {shares:.2f} 股 @ ~{buy_price:.4f}")

        for otype in [OrderType.FOK, OrderType.GTC]:
            try:
                order = MarketOrderArgs(
                    token_id=token_id,
                    amount=shares,
                    side=SELL,
                    order_type=otype,
                )
                signed = clob_client.create_market_order(order)
                resp = clob_client.post_order(signed, otype)
                self.status.add_log(f"  ✅ {side_label} 平倉成功 ({otype}): {resp}")
                return True
            except Exception as e:
                self.status.add_log(f"  ⚠️ {side_label} 平倉 {otype} 失敗: {str(e)[:150]}")
                continue

        self.status.add_log(f"  ❌ {side_label} 所有平倉方式均失敗!")
        return False

    async def execute_trade(self, opportunity: ArbitrageOpportunity) -> TradeRecord:
        """執行套利交易 — 安全版本"""
        market = opportunity.market
        price_info = opportunity.price_info
        desired_size = self.config.order_size

        safe_size = self._calculate_safe_order_size(price_info, desired_size)
        if safe_size < 1.0:
            self.status.add_log(
                f"⚠️ 流動性不足，無法安全下單 | "
                f"UP深度: {price_info.up_liquidity:.0f} DOWN深度: {price_info.down_liquidity:.0f}"
            )
            return TradeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                market_slug=market.slug,
                up_price=price_info.up_price,
                down_price=price_info.down_price,
                total_cost=price_info.total_cost,
                order_size=0,
                expected_profit=0,
                profit_pct=0,
                status="failed",
                details="流動性不足，跳過交易",
            )

        order_size = safe_size
        if order_size < desired_size:
            self.status.add_log(
                f"📉 自適應下單: {desired_size} → {order_size} "
                f"(UP深度: {price_info.up_liquidity:.0f}, DOWN深度: {price_info.down_liquidity:.0f})"
            )

        record = TradeRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            market_slug=market.slug,
            up_price=price_info.up_price,
            down_price=price_info.down_price,
            total_cost=price_info.total_cost,
            order_size=order_size,
            expected_profit=opportunity.potential_profit * (order_size / desired_size),
            profit_pct=opportunity.profit_pct,
            status="pending",
        )

        if self.config.dry_run:
            record.status = "simulated"
            record.details = "🔸 模擬交易 - 未使用真實資金"
            self.status.add_log(
                f"🔸 [模擬] 買入 {order_size} 股 UP@{price_info.up_price:.4f} + "
                f"{order_size} 股 DOWN@{price_info.down_price:.4f} | "
                f"預期利潤: ${record.expected_profit:.4f}"
            )
        else:
            try:
                clob_client = self._get_clob_client()

                # 重新獲取最新 best_ask（從訂單簿，而非 /price 參考價）
                import httpx
                try:
                    up_book = httpx.get(
                        f"{self.config.CLOB_HOST}/book",
                        params={"token_id": market.up_token_id}
                    ).json()
                    down_book = httpx.get(
                        f"{self.config.CLOB_HOST}/book",
                        params={"token_id": market.down_token_id}
                    ).json()
                    up_asks = up_book.get("asks", [])
                    down_asks = down_book.get("asks", [])
                    up_price = min(float(a["price"]) for a in up_asks) if up_asks else price_info.up_best_ask
                    down_price = min(float(a["price"]) for a in down_asks) if down_asks else price_info.down_best_ask
                    self.status.add_log(
                        f"🔄 最新 best_ask | UP={up_price:.4f} DOWN={down_price:.4f} "
                        f"(舊: UP={price_info.up_best_ask:.4f} DOWN={price_info.down_best_ask:.4f})"
                    )
                except Exception as e:
                    self.status.add_log(f"⚠️ 重新獲取價格失敗，使用舊 best_ask: {str(e)[:60]}")
                    up_price = price_info.up_best_ask if price_info.up_best_ask > 0 else price_info.up_price
                    down_price = price_info.down_best_ask if price_info.down_best_ask > 0 else price_info.down_price

                actual_cost = up_price + down_price

                up_amount_usd = round(order_size * up_price, 2)
                down_amount_usd = round(order_size * down_price, 2)

                self.status.add_log(
                    f"📊 價格 | UP={up_price:.4f} DOWN={down_price:.4f} | "
                    f"總成本/share: {actual_cost:.4f} | "
                    f"UP ${up_amount_usd:.2f} DOWN ${down_amount_usd:.2f} | "
                    f"原始asks: UP={price_info.up_best_ask:.4f} DOWN={price_info.down_best_ask:.4f}"
                )

                if actual_cost >= 1.0:
                    self.status.add_log(
                        f"⛔ 無利潤 | UP: {up_price:.4f} + DOWN: {down_price:.4f} = {actual_cost:.4f} >= 1.0"
                    )
                    record.status = "failed"
                    record.details = f"無利潤 ({actual_cost:.4f})"
                    await self._update_trade_stats(record, opportunity, order_size, market, price_info)
                    return record

                self.status.add_log(
                    f"🔴 [真實] 開始配對交易 | {order_size} 股 | "
                    f"UP: ${up_amount_usd:.4f} (@{up_price:.4f}) "
                    f"DOWN: ${down_amount_usd:.4f} (@{down_price:.4f})"
                )

                # 買入流動性較低的一側先
                if price_info.up_liquidity <= price_info.down_liquidity:
                    first_token, first_amt, first_price, first_label = (
                        market.up_token_id, up_amount_usd, up_price, "UP")
                    second_token, second_amt, second_price, second_label = (
                        market.down_token_id, down_amount_usd, down_price, "DOWN")
                else:
                    first_token, first_amt, first_price, first_label = (
                        market.down_token_id, down_amount_usd, down_price, "DOWN")
                    second_token, second_amt, second_price, second_label = (
                        market.up_token_id, up_amount_usd, up_price, "UP")

                first_result = self._try_buy_one_side(
                    clob_client, first_token, first_amt, first_price, first_label
                )

                if not first_result["success"]:
                    # 逐步縮小數量重試: 50%, 25%
                    retry_sizes = sorted(set([
                        round(order_size * 0.5, 2),
                        round(order_size * 0.25, 2),
                    ]))

                    for try_size in retry_sizes:
                        if try_size >= order_size:
                            continue
                        retry_usd = try_size * first_price
                        other_usd = try_size * second_price
                        if retry_usd < 1.0 or other_usd < 1.0:
                            self.status.add_log(f"  ⏭️ 跳過 {try_size} 股: 某側 < $1 (${retry_usd:.2f} / ${other_usd:.2f})")
                            continue
                        self.status.add_log(f"  🔄 重試較小數量: {try_size} (${retry_usd:.2f} @ {first_price:.4f})")
                        first_result = self._try_buy_one_side(
                            clob_client, first_token,
                            retry_usd,
                            first_price, first_label
                        )
                        if first_result["success"]:
                            order_size = try_size
                            second_amt = other_usd
                            break

                    if not first_result["success"]:
                        record.status = "failed"
                        record.details = f"❌ {first_label} 買入失敗 (含重試): {first_result.get('error', '')[:100]}"
                        self.status.add_log(f"❌ 交易失敗: {first_label} 側無法成交")
                        await self._update_trade_stats(record, opportunity, order_size, market, price_info)
                        return record

                # ── 第二步: 重新查詢訂單簿 best_ask 確認仍有利潤再買另一側 ──
                import httpx
                try:
                    re_up_book = httpx.get(
                        f"{self.config.CLOB_HOST}/book",
                        params={"token_id": market.up_token_id}
                    ).json()
                    re_down_book = httpx.get(
                        f"{self.config.CLOB_HOST}/book",
                        params={"token_id": market.down_token_id}
                    ).json()
                    re_up_asks = re_up_book.get("asks", [])
                    re_down_asks = re_down_book.get("asks", [])
                    re_up = min(float(a["price"]) for a in re_up_asks) if re_up_asks else up_price
                    re_down = min(float(a["price"]) for a in re_down_asks) if re_down_asks else down_price
                    recheck_cost = re_up + re_down
                    if recheck_cost >= 1.0:
                        self.status.add_log(
                            f"⛔ 二次檢查: best_ask 已變動 UP={re_up:.4f}+DOWN={re_down:.4f}={recheck_cost:.4f} >= 1.0，放棄第二側"
                        )
                        # 平倉第一側
                        unwind_shares = first_result.get("shares", order_size)
                        unwind_ok = False
                        for attempt in range(3):
                            wait_secs = 5 * (attempt + 1)
                            self.status.add_log(f"  ⏳ 等待 {wait_secs}s 鏈上結算後平倉 (第 {attempt+1}/3 次)")
                            await asyncio.sleep(wait_secs)
                            unwind_ok = self._try_unwind_position(
                                clob_client, first_token, unwind_shares,
                                first_result.get("price", first_price), first_label
                            )
                            if unwind_ok:
                                break
                        record.status = "failed"
                        unwind_status = "已平倉" if unwind_ok else "⚠️ 平倉失敗，需手動處理!"
                        record.details = f"二次檢查無利潤 ({recheck_cost:.4f}) | {first_label}: {unwind_status}"
                        self.status.add_log(f"❌ 二次檢查放棄交易 | {first_label}: {unwind_status}")
                        await self._update_trade_stats(record, opportunity, order_size, market, price_info)
                        return record
                    # 用最新 best_ask 更新第二側金額
                    new_second_price = re_up if second_label == "UP" else re_down
                    second_amt = round(order_size * new_second_price, 2)
                    second_price = new_second_price
                    self.status.add_log(f"📋 二次檢查通過 | {recheck_cost:.4f} < 1.0 | {second_label} 更新: ${second_amt:.2f} @ {second_price:.4f}")
                except Exception as e:
                    self.status.add_log(f"⚠️ 二次檢查失敗 (繼續執行): {str(e)[:80]}")

                second_result = self._try_buy_one_side(
                    clob_client, second_token, second_amt, second_price, second_label
                )

                if not second_result["success"]:
                    self.status.add_log(
                        f"  ⚠️ {second_label} 失敗，需要平倉 {first_label} 以避免單邊風險"
                    )
                    unwind_shares = first_result.get("shares", order_size)
                    # 等待鏈上結算後再嘗試平倉（重試 3 次，間隔遞增）
                    unwind_ok = False
                    for attempt in range(3):
                        wait_secs = 5 * (attempt + 1)
                        self.status.add_log(f"  ⏳ 等待 {wait_secs}s 鏈上結算後平倉 (第 {attempt+1}/3 次)")
                        await asyncio.sleep(wait_secs)
                        unwind_ok = self._try_unwind_position(
                            clob_client, first_token, unwind_shares,
                            first_result.get("price", first_price), first_label
                        )
                        if unwind_ok:
                            break

                    record.status = "failed"
                    unwind_status = "已平倉" if unwind_ok else "⚠️ 平倉失敗，需手動處理!"
                    record.details = (
                        f"❌ {second_label} 買入失敗 | {first_label} {unwind_status} | "
                        f"錯誤: {second_result.get('error', '')[:80]}"
                    )
                    self.status.add_log(f"❌ 配對交易失敗 | {first_label}: {unwind_status}")

                    if not unwind_ok:
                        self.status.add_log(
                            f"🚨 警告: {first_label} 平倉失敗! "
                            f"Token: {first_token[:16]}... 數量: {unwind_shares}"
                        )
                else:
                    # Update record with actual fill prices
                    actual_up = first_result["price"] if first_label == "UP" else second_result["price"]
                    actual_down = first_result["price"] if first_label == "DOWN" else second_result["price"]
                    actual_total = actual_up + actual_down
                    actual_profit = (1.0 - actual_total) * order_size

                    record.status = "executed"
                    record.order_size = order_size
                    record.up_price = actual_up
                    record.down_price = actual_down
                    record.total_cost = actual_total
                    record.expected_profit = actual_profit
                    record.profit_pct = (actual_profit / (actual_total * order_size) * 100) if actual_total > 0 else 0
                    record.details = (
                        f"🔴 配對交易成功 | {order_size} 股 | "
                        f"UP: {first_result['response'] if first_label == 'UP' else second_result['response']} | "
                        f"DOWN: {first_result['response'] if first_label == 'DOWN' else second_result['response']}"
                    )
                    self.status.add_log(
                        f"🔴 [真實] 配對成功 {order_size} 股 UP@{actual_up:.4f} + "
                        f"DOWN@{actual_down:.4f} | 總成本: {actual_total:.4f} | "
                        f"實際利潤: ${actual_profit:.4f}"
                    )

            except Exception as e:
                record.status = "failed"
                record.details = f"❌ 交易失敗: {str(e)}"
                self.status.add_log(f"❌ 交易執行失敗: {e}")

        await self._update_trade_stats(record, opportunity, order_size, market, price_info)
        return record

    async def _update_trade_stats(self, record: TradeRecord, opportunity: ArbitrageOpportunity,
                                  order_size: float, market: MarketInfo, price_info: PriceInfo):
        """更新交易統計並觸發自動合併"""
        self.status.total_trades += 1
        self.status.trades_this_market += 1
        self.status.last_trade_time = time.time()
        if record.status in ("executed", "simulated"):
            self.status.total_profit += record.expected_profit
        self.status.trade_history.append(record)

        if record.status in ("executed", "simulated") and market.condition_id:
            self.merger.track_trade(
                market_slug=market.slug,
                condition_id=market.condition_id,
                up_token_id=market.up_token_id or "",
                down_token_id=market.down_token_id or "",
                amount=order_size,
                total_cost=record.total_cost,
            )
            if self.merger.auto_merge_enabled:
                merge_results = await self.merger.auto_merge_all()
                for mr in merge_results:
                    self.status.add_log(
                        f"🔄 合併結果: {mr.status} | {mr.amount:.0f} 對 → "
                        f"{mr.usdc_received:.2f} USDC | {mr.details}"
                    )

    async def scan_market(self, market: MarketInfo) -> Optional[ArbitrageOpportunity]:
        """掃描單個市場的套利機會"""
        price_info = await self.get_prices(market)
        if not price_info:
            return None

        self.status.last_price = price_info
        self.status.scan_count += 1

        opportunity = self.check_arbitrage(market, price_info)

        if opportunity.is_viable:
            self.status.opportunities_found += 1
            self.status.add_log(
                f"💰 發現套利機會! {market.slug} | "
                f"UP: {price_info.up_price:.4f} DOWN: {price_info.down_price:.4f} | "
                f"總成本: {price_info.total_cost:.4f} | "
                f"利潤: ${opportunity.potential_profit:.4f} ({opportunity.profit_pct:.2f}%)"
            )
        else:
            if self.status.scan_count % 5 == 0:
                self.status.add_log(
                    f"🔍 掃描 #{self.status.scan_count} | {market.slug} | "
                    f"UP: {price_info.up_price:.4f} DOWN: {price_info.down_price:.4f} | "
                    f"總成本: {price_info.total_cost:.4f} | {opportunity.reason}"
                )

        return opportunity

    def update_config(self, new_config: Dict[str, Any]):
        """動態更新配置"""
        for key, value in new_config.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.status.mode = "模擬" if self.config.dry_run else "🔴 真實交易"
        self.status.add_log(f"⚙️ 配置已更新: {new_config}")
