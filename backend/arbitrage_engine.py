"""
套利引擎 - 核心套利邏輯、風險控制、交易執行
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
                        price_info.up_best_ask = float(asks[0].get("price", 0))
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
                        price_info.down_best_ask = float(asks[0].get("price", 0))
                        price_info.down_liquidity = sum(
                            float(a.get("size", 0)) for a in asks[:5]
                        )
                        price_info.down_asks = [
                            {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
                            for a in asks[:10]
                        ]

                # 用訂單簿 best ask 作為實際買入成本（比 /price 端點更準確）
                up_cost = price_info.up_best_ask if price_info.up_best_ask > 0 else price_info.up_price
                down_cost = price_info.down_best_ask if price_info.down_best_ask > 0 else price_info.down_price
                price_info.total_cost = up_cost + down_cost
                price_info.spread = 1.0 - price_info.total_cost

                return price_info

            except Exception as e:
                self.status.add_log(f"❌ 獲取價格失敗: {e}")
                return None

    def check_arbitrage(self, market: MarketInfo, price_info: PriceInfo) -> ArbitrageOpportunity:
        """檢查是否存在套利機會（含滑價容忍度）"""
        MAX_SLIPPAGE = 0.02  # 兩側各 +0.01 最大滑價
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
        """建立並返回 CLOB 客戶端（快取避免重複建立）"""
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
        """
        根據訂單簿深度計算安全的下單數量，確保兩側 USD 金額都 >= $1
        """
        import math
        MIN_ORDER_USD = 1.0

        # 取兩邊流動性的最小值，留 20% 安全邊際
        available_up = price_info.up_liquidity * 0.8
        available_down = price_info.down_liquidity * 0.8
        safe_size = min(desired_size, available_up, available_down)
        safe_size = max(round(safe_size, 2), 1.0) if safe_size >= 1.0 else 0.0

        # 確保兩側 USD 金額都 >= $1
        if safe_size > 0:
            min_price = min(price_info.up_price, price_info.down_price)
            if min_price > 0:
                min_shares_for_dollar = math.ceil(MIN_ORDER_USD / min_price)
                if safe_size < min_shares_for_dollar:
                    safe_size = float(min_shares_for_dollar)
                # 再次檢查是否超過流動性
                if safe_size > min(available_up, available_down) and safe_size > desired_size:
                    return 0.0
            up_usd = safe_size * price_info.up_price
            down_usd = safe_size * price_info.down_price
            if up_usd < MIN_ORDER_USD or down_usd < MIN_ORDER_USD:
                return 0.0

        return safe_size

    def _try_buy_one_side(self, clob_client, token_id: str, amount_usd: float,
                          price: float, side_label: str) -> dict:
        """
        FOK only — 加滑價容忍度讓 FOK 能掃更深的訂單簿
        嘗試 3 個價格層級: 原價, +0.01, +0.02
        返回 {success, response, shares_bought}
        """
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        estimated_shares = amount_usd / price if price > 0 else 0

        # 確保 amount >= $1
        if amount_usd < 1.0:
            self.status.add_log(f"  ⚠️ {side_label} 金額 ${amount_usd:.2f} < $1 最低限制，跳過")
            return {"success": False, "error": "amount below $1 minimum", "shares": 0, "price": price}

        # 嘗試 FOK: 原價 和 +0.01 滑價（最多 +1分）
        slippage_steps = [0.00, 0.01]
        last_error = ""

        for slip in slippage_steps:
            try_price = min(round(price + slip, 2), 0.99)
            try:
                order = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount_usd,
                    side=BUY,
                    price=try_price,
                    order_type=OrderType.FOK,
                )
                signed = clob_client.create_market_order(order)
                resp = clob_client.post_order(signed, OrderType.FOK)
                slip_label = f" (滑價 +{slip})" if slip > 0 else ""
                self.status.add_log(
                    f"  ✅ {side_label} FOK 成交{slip_label} | ${amount_usd:.4f} @ {try_price:.4f} ≈ {estimated_shares:.1f} 股"
                )
                return {"success": True, "response": resp, "shares": estimated_shares, "price": try_price}
            except Exception as e:
                last_error = str(e)
                if slip == 0:
                    self.status.add_log(f"  ⚠️ {side_label} FOK @ {try_price:.4f} 失敗: {last_error[:100]}")
                else:
                    self.status.add_log(f"  ⚠️ {side_label} FOK @ {try_price:.4f} (+{slip}) 也失敗")

        return {"success": False, "error": last_error[:120], "shares": 0, "price": price}

    def _try_unwind_position(self, clob_client, token_id: str, shares: float,
                             buy_price: float, side_label: str):
        """
        緊急平倉：賣出已買入的一側代幣以避免單邊風險
        SELL amount = 股數 (不是 USD)
        先嘗試 FOK（快速），失敗再嘗試 GTC（掛單等成交）
        """
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        self.status.add_log(f"  🔥 緊急平倉 {side_label} | 賣出 {shares:.2f} 股 @ ~{buy_price:.4f}")

        for otype in [OrderType.FOK, OrderType.GTC]:
            try:
                order = MarketOrderArgs(
                    token_id=token_id,
                    amount=shares,  # SELL: amount = 股數
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
        """
        執行套利交易 — 安全版本
        1. 自適應下單量（根據訂單簿深度，只買 book 上有的量）
        2. FOK 下單（全部成交或取消，不留掛單）
        3. 買流動性較低的一側先（更可能失敗的先買，失敗無風險）
        4. 如果第二側失敗，立即 FOK 賣回第一側（防止單邊風險）
        5. 失敗後嘗試半量重試
        """
        market = opportunity.market
        price_info = opportunity.price_info
        desired_size = self.config.order_size

        # 自適應下單量
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
            # 模擬模式
            record.status = "simulated"
            record.details = "🔸 模擬交易 - 未使用真實資金"
            self.status.add_log(
                f"🔸 [模擬] 買入 {order_size} 股 UP@{price_info.up_price:.4f} + "
                f"{order_size} 股 DOWN@{price_info.down_price:.4f} | "
                f"預期利潤: ${record.expected_profit:.4f}"
            )
        else:
            # 真實交易 — 安全執行
            try:
                clob_client = self._get_clob_client()

                # 計算掃單價格（遍歷訂單簿找到能填滿的價格）
                up_sweep, up_amount_usd = self._get_sweep_price(price_info.up_asks, order_size)
                down_sweep, down_amount_usd = self._get_sweep_price(price_info.down_asks, order_size)

                if up_sweep == 0 or down_sweep == 0:
                    no_depth_side = "UP" if up_sweep == 0 else "DOWN"
                    self.status.add_log(
                        f"📕 {no_depth_side} 訂單簿深度不足 {order_size} 股 | "
                        f"UP asks: {price_info.up_asks[:3]} | DOWN asks: {price_info.down_asks[:3]}"
                    )
                    record.status = "failed"
                    record.details = f"訂單簿深度不足 ({no_depth_side})"
                    await self._update_trade_stats(record, opportunity, order_size, market, price_info)
                    return record

                actual_cost = (up_amount_usd + down_amount_usd) / order_size
                if actual_cost >= 1.0:
                    self.status.add_log(
                        f"⛔ 掃單價格無利潤 | VWAP/share: {actual_cost:.4f} >= 1.0 (UP sweep: {up_sweep:.4f}, ${up_amount_usd:.2f} | DOWN sweep: {down_sweep:.4f}, ${down_amount_usd:.2f})"
                    )
                    record.status = "failed"
                    record.details = f"掃單價格無利潤 ({actual_cost:.4f})"
                    await self._update_trade_stats(record, opportunity, order_size, market, price_info)
                    return record

                self.status.add_log(
                    f"🔴 [真實] 開始配對交易 | {order_size} 股 | "
                    f"UP: ${up_amount_usd:.4f} (sweep@{up_sweep:.4f}) "
                    f"DOWN: ${down_amount_usd:.4f} (sweep@{down_sweep:.4f})"
                )

                # ── 第一步: 買入流動性較低的一側（更可能失敗的先買）──
                if price_info.up_liquidity <= price_info.down_liquidity:
                    first_token, first_amt, first_price, first_label = (
                        market.up_token_id, up_amount_usd, up_sweep, "UP")
                    second_token, second_amt, second_price, second_label = (
                        market.down_token_id, down_amount_usd, down_sweep, "DOWN")
                    first_asks, second_asks = price_info.up_asks, price_info.down_asks
                else:
                    first_token, first_amt, first_price, first_label = (
                        market.down_token_id, down_amount_usd, down_sweep, "DOWN")
                    second_token, second_amt, second_price, second_label = (
                        market.up_token_id, up_amount_usd, up_sweep, "UP")
                    first_asks, second_asks = price_info.down_asks, price_info.up_asks

                # 買入第一側 (FOK)
                first_result = self._try_buy_one_side(
                    clob_client, first_token, first_amt, first_price, first_label
                )

                if not first_result["success"]:
                    # 逐步縮小數量重試: 50%, 25%, 最小可行量
                    import math
                    min_price = min(price_info.up_price, price_info.down_price)
                    min_shares = math.ceil(1.0 / min_price) if min_price > 0 else order_size
                    retry_sizes = sorted(set([
                        max(round(order_size * 0.5, 2), float(min_shares)),
                        max(round(order_size * 0.25, 2), float(min_shares)),
                        float(min_shares),
                    ]))

                    for try_size in retry_sizes:
                        if try_size >= order_size:
                            continue
                        retry_sweep, retry_usd = self._get_sweep_price(first_asks, try_size)
                        if retry_sweep == 0:
                            continue
                        if retry_usd < 1.0:
                            continue
                        self.status.add_log(f"  🔄 重試較小數量: {try_size} (${retry_usd:.2f} @ sweep {retry_sweep:.4f})")
                        first_result = self._try_buy_one_side(
                            clob_client, first_token,
                            retry_usd,
                            retry_sweep, first_label
                        )
                        if first_result["success"]:
                            order_size = try_size
                            new_second_sweep, new_second_usd = self._get_sweep_price(second_asks, try_size)
                            if new_second_sweep > 0:
                                second_amt = new_second_usd
                                second_price = new_second_sweep
                            else:
                                if first_label == "UP":
                                    second_amt = try_size * down_sweep
                                else:
                                    second_amt = try_size * up_sweep
                            break

                    if not first_result["success"]:
                        record.status = "failed"
                        record.details = f"❌ {first_label} 買入失敗 (含重試): {first_result.get('error', '')[:100]}"
                        self.status.add_log(f"❌ 交易失敗: {first_label} 側無法成交")
                        await self._update_trade_stats(record, opportunity, order_size, market, price_info)
                        return record

                # ── 第二步: 買入另一側 ──
                second_result = self._try_buy_one_side(
                    clob_client, second_token, second_amt, second_price, second_label
                )

                if not second_result["success"]:
                    # 第二側失敗！第一側已成交 → 必須平倉第一側
                    self.status.add_log(
                        f"  ⚠️ {second_label} 失敗，需要平倉 {first_label} 以避免單邊風險"
                    )
                    # SELL amount = 股數，不是 USD
                    unwind_shares = first_result.get("shares", order_size)
                    unwind_ok = self._try_unwind_position(
                        clob_client, first_token, unwind_shares,
                        first_result.get("price", first_price), first_label
                    )

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
                    # 兩側都成功！
                    record.status = "executed"
                    record.order_size = order_size
                    record.details = (
                        f"🔴 配對交易成功 | {order_size} 股 | "
                        f"UP: {first_result['response'] if first_label == 'UP' else second_result['response']} | "
                        f"DOWN: {first_result['response'] if first_label == 'DOWN' else second_result['response']}"
                    )
                    self.status.add_log(
                        f"🔴 [真實] 配對成功 {order_size} 股 UP@{price_info.up_price:.4f} + "
                        f"DOWN@{price_info.down_price:.4f} | "
                        f"預期利潤: ${record.expected_profit:.4f}"
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

        # 追蹤持倉並自動合併
        if record.status in ("executed", "simulated") and market.condition_id:
            self.merger.track_trade(
                market_slug=market.slug,
                condition_id=market.condition_id,
                up_token_id=market.up_token_id or "",
                down_token_id=market.down_token_id or "",
                amount=order_size,
                total_cost=price_info.total_cost,
            )
            # 自動合併
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
