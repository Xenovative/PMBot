"""
套利引擎 - 核心套利邏輯、風險控制、交易執行（每日 Up or Down 市場版本）
"""
import asyncio
import math
import time
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from config import BotConfig
from market_finder import MarketInfo
from position_merger import PositionMerger
import trade_db


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
class BargainHolding:
    """撿便宜策略的單側持倉記錄（支援堆疊輪次）"""
    market_slug: str
    market: MarketInfo
    side: str  # "UP" or "DOWN"
    token_id: str
    complement_token_id: str
    buy_price: float
    shares: float
    amount_usd: float
    timestamp: str
    status: str = "holding"  # "holding", "paired", "stopped_out"
    round: int = 1  # 堆疊輪次
    paired_with: Optional[str] = None  # 配對的另一側 holding timestamp (用於追蹤)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_slug": self.market_slug,
            "side": self.side,
            "token_id": self.token_id[:16] + "...",
            "buy_price": self.buy_price,
            "shares": self.shares,
            "amount_usd": self.amount_usd,
            "timestamp": self.timestamp,
            "status": self.status,
            "round": self.round,
        }


@dataclass
class BotStatus:
    running: bool = False
    current_market: Optional[str] = None
    active_markets: List[str] = field(default_factory=list)
    mode: str = "模擬"
    total_trades: int = 0
    total_profit: float = 0.0
    trades_per_market: Dict[str, int] = field(default_factory=dict)
    last_trade_time: float = 0.0
    last_price: Optional[PriceInfo] = None
    market_prices: Dict[str, PriceInfo] = field(default_factory=dict)
    opportunities_found: int = 0
    scan_count: int = 0
    start_time: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    trade_history: List[TradeRecord] = field(default_factory=list)
    current_opportunities: List[ArbitrageOpportunity] = field(default_factory=list)
    bargain_holdings: List[BargainHolding] = field(default_factory=list)

    def get_trades_for_market(self, slug: str) -> int:
        return self.trades_per_market.get(slug, 0)

    def increment_trades_for_market(self, slug: str):
        self.trades_per_market[slug] = self.trades_per_market.get(slug, 0) + 1

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
            "active_markets": self.active_markets,
            "mode": self.mode,
            "total_trades": self.total_trades,
            "total_profit": round(self.total_profit, 4),
            "trades_per_market": self.trades_per_market,
            "last_price": self.last_price.to_dict() if self.last_price else None,
            "market_prices": {slug: p.to_dict() for slug, p in self.market_prices.items()},
            "opportunities_found": self.opportunities_found,
            "scan_count": self.scan_count,
            "start_time": self.start_time,
            "logs": self.logs[-50:],
            "trade_history": [t.to_dict() for t in self.trade_history[-20:]],
            "current_opportunities": [o.to_dict() for o in self.current_opportunities],
            "bargain_holdings": [h.to_dict() for h in self.bargain_holdings if h.status == "holding"],
        }


class ArbitrageEngine:
    def __init__(self, config: BotConfig):
        self.config = config
        self.status = BotStatus()
        self.merger = PositionMerger(config)
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stop_loss_cooldown_until: Optional[datetime] = None

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
        elif self.status.get_trades_for_market(market.slug) >= self.config.max_trades_per_market:
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
            funder = self.config.funder_address or None  # avoid empty-string normalization errors
            # Debug: log signer/funder/sig type
            try:
                from eth_account import Account
                signer_addr = Account.from_key(self.config.private_key).address if self.config.private_key else ""
            except Exception:
                signer_addr = "<invalid key>"
            self.status.add_log(
                f"🔑 Clob signer={signer_addr[:10]}..., sig_type={self.config.signature_type}, funder={funder or '<none>'}"
            )
            self._clob_client = ClobClient(
                self.config.CLOB_HOST,
                key=self.config.private_key,
                chain_id=self.config.CHAIN_ID,
                signature_type=self.config.signature_type,
                funder=funder,
            )
            self._clob_client.set_api_creds(
                self._clob_client.create_or_derive_api_creds()
            )
        return self._clob_client

    def check_credentials(self) -> Dict[str, Any]:
        """快速檢查簽名配置，回傳狀態與提示。"""
        sig_type = self.config.signature_type
        pk = (self.config.private_key or "").strip()
        funder = (self.config.funder_address or "").strip()

        issues: list[str] = []
        status = "ok"

        if sig_type == 0:
            if not funder:
                issues.append("signature_type=0 需要 funder_address (投資組合頁的錢包地址)")
            if pk:
                issues.append("signature_type=0 不應提供 PRIVATE_KEY（email/custodial 由伺服器簽）")
        else:
            if not pk:
                issues.append("signature_type=1/2 需要 PRIVATE_KEY 來本地簽名")

        # 只在需要本地簽名時嘗試初始化客戶端
        if sig_type != 0 or pk:
            try:
                from py_clob_client.client import ClobClient

                client = ClobClient(
                    self.config.CLOB_HOST,
                    key=pk,
                    chain_id=self.config.CHAIN_ID,
                    signature_type=sig_type,
                    funder=funder,
                )
                client.set_api_creds(client.create_or_derive_api_creds())
            except Exception as e:
                issues.append(f"ClobClient 初始化失敗: {e}")

        if any("需要" in i or "失敗" in i for i in issues):
            status = "error"
        elif issues:
            status = "warn"

        return {
            "status": status,
            "signature_type": sig_type,
            "has_private_key": bool(pk),
            "funder_address": funder,
            "issues": issues,
        }

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
        成交後透過 get_trades 取得真實成交均價與股數
        """
        from py_clob_client.clob_types import MarketOrderArgs, OrderType, TradeParams
        from py_clob_client.order_builder.constants import BUY
        import time as _time

        estimated_shares = amount_usd / price if price > 0 else 0

        # 確保 amount >= $1
        if amount_usd < 1.0:
            self.status.add_log(f"  ⚠️ {side_label} 金額 ${amount_usd:.2f} < $1 最低限制，跳過")
            return {"success": False, "error": "amount below $1 minimum", "shares": 0, "price": price}

        # price=None → CLOB 自動呼叫 calculate_market_price 從訂單簿取得真實價格
        # 先記錄 CLOB 自動計算的邊際價格（用於診斷，注意: 這是最差價位，非均價）
        try:
            marginal_price = clob_client.calculate_market_price(
                token_id, "BUY", amount_usd, OrderType.FOK
            )
            self.status.add_log(
                f"  📖 {side_label} 訂單簿邊際價={marginal_price:.4f} | "
                f"${amount_usd:.2f} (估算: {estimated_shares:.2f}股 @ {price:.4f})"
            )
        except Exception as e:
            self.status.add_log(f"  ⚠️ {side_label} 訂單簿深度不足: {str(e)[:80]}")
            return {"success": False, "error": f"orderbook depth: {str(e)[:80]}", "shares": 0, "price": price}

        # 記錄下單前時間戳（用於篩選成交記錄）
        before_ts = int(_time.time())

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
            self.status.add_log(f"  📋 {side_label} post_order 回應: {str(resp)[:200]}")
        except Exception as e:
            last_error = str(e)
            self.status.add_log(f"  ⚠️ {side_label} FOK 失敗: {last_error[:120]}")
            return {"success": False, "error": last_error[:120], "shares": 0, "price": price}

        # ── 從 get_trades 取得真實成交數據 ──
        fill_shares = 0.0
        fill_cost = 0.0
        fill_price = marginal_price  # fallback
        order_id = resp.get("orderId") or resp.get("order_id") or resp.get("id")

        try:
            trades = []
            # 最多嘗試 3 次，避免寫入延遲
            for _ in range(3):
                _time.sleep(0.4)
                params = TradeParams(order_id=order_id) if order_id else TradeParams(asset_id=token_id, after=before_ts)
                trades = clob_client.get_trades(params)
                if trades:
                    break
            if trades:
                for t in trades:
                    t_size = float(t.get("size", 0))
                    t_price = float(t.get("price", 0))
                    fill_shares += t_size
                    fill_cost += t_size * t_price
                if fill_shares > 0:
                    fill_price = fill_cost / fill_shares
                self.status.add_log(
                    f"  ✅ {side_label} 實際成交 | {fill_shares:.2f} 股 @ 均價 {fill_price:.4f} "
                    f"(${fill_cost:.2f}) | {len(trades)} 筆成交"
                )
            else:
                # 沒拿到成交記錄，用估算值
                fill_shares = amount_usd / marginal_price if marginal_price > 0 else estimated_shares
                fill_price = marginal_price
                self.status.add_log(
                    f"  ⚠️ {side_label} 未取得成交記錄，使用估算: {fill_shares:.2f} 股 @ {fill_price:.4f}"
                )
        except Exception as e:
            # get_trades 失敗，用估算值
            fill_shares = amount_usd / marginal_price if marginal_price > 0 else estimated_shares
            fill_price = marginal_price
            self.status.add_log(
                f"  ⚠️ {side_label} 取得成交記錄失敗: {str(e)[:80]} | 使用估算: {fill_shares:.2f} 股 @ {fill_price:.4f}"
            )

        return {"success": True, "response": resp, "shares": fill_shares, "price": fill_price}

    def _try_unwind_position(self, clob_client, token_id: str, shares: float,
                             buy_price: float, side_label: str):
        """
        緊急平倉：賣出已買入的一側代幣以避免單邊風險
        注意: MarketOrderArgs + create_market_order 對 SELL 有 bug（price 驗證失敗）
        改用 OrderArgs + create_order 限價賣單
        嘗試順序: 買入價賣出 → 低價賣出 (0.01) → GTC 掛單
        """
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        # 將股數截斷到 2 位小數（CLOB 精度限制）
        shares = math.floor(shares * 100) / 100
        if shares <= 0:
            self.status.add_log(f"  ⚠️ {side_label} 股數過小，無法平倉")
            return False

        self.status.add_log(f"  🔥 緊急平倉 {side_label} | 賣出 {shares:.2f} 股 @ ~{buy_price:.4f}")

        # 嘗試不同價格賣出: 買入價 → 略低於買入價 → 最低價 0.01
        sell_prices = [
            round(buy_price, 2),
            round(max(buy_price - 0.05, 0.01), 2),
            0.01,
        ]
        # 去重
        sell_prices = list(dict.fromkeys(sell_prices))

        for sell_price in sell_prices:
            for otype in [OrderType.FOK, OrderType.GTC]:
                try:
                    order = OrderArgs(
                        token_id=token_id,
                        price=sell_price,
                        size=shares,
                        side=SELL,
                    )
                    signed = clob_client.create_order(order)
                    resp = clob_client.post_order(signed, otype)
                    self.status.add_log(
                        f"  ✅ {side_label} 平倉成功 ({otype}) @ {sell_price:.2f}: {resp}"
                    )
                    return True
                except Exception as e:
                    self.status.add_log(
                        f"  ⚠️ {side_label} 平倉 {otype} @ {sell_price:.2f} 失敗: {str(e)[:150]}"
                    )
                    continue

        self.status.add_log(f"  ❌ {side_label} 所有平倉方式均失敗!")
        return False

    def _convert_orphan_to_bargain(self, market: 'MarketInfo', side: str,
                                    token_id: str, complement_token_id: str,
                                    buy_price: float, shares: float, amount_usd: float):
        """
        平倉失敗時，將孤兒持倉轉入撿便宜策略繼續配對，
        而非要求使用者手動處理。
        """
        holding = BargainHolding(
            market_slug=market.slug,
            market=market,
            side=side,
            token_id=token_id,
            complement_token_id=complement_token_id,
            buy_price=buy_price,
            shares=shares,
            amount_usd=amount_usd,
            timestamp=datetime.now(timezone.utc).isoformat(),
            status="holding",
            round=1,
        )
        self.status.bargain_holdings.append(holding)
        self.status.add_log(
            f"🏷️ [孤兒轉撿便宜] {market.slug} {side} | "
            f"{shares:.1f} 股 @ {buy_price:.4f} → 等待配對"
        )
        return holding

    def _is_on_cooldown(self) -> bool:
        """止損冷卻期檢查"""
        if self._stop_loss_cooldown_until and datetime.now(timezone.utc) < self._stop_loss_cooldown_until:
            remaining = (self._stop_loss_cooldown_until - datetime.now(timezone.utc)).seconds
            self.status.add_log(f"⏳ 止損冷卻中，剩餘 {remaining}s")
            return True
        return False

    async def execute_trade(self, opportunity: ArbitrageOpportunity) -> TradeRecord:
        """執行套利交易 — 安全版本"""
        if self._is_on_cooldown():
            record = TradeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                market_slug=opportunity.market.slug,
                status="skipped",
                details="止損冷卻中",
            )
            return record

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
                        if unwind_ok:
                            unwind_status = "已平倉"
                        else:
                            comp_token = second_token
                            self._convert_orphan_to_bargain(
                                market, first_label, first_token, comp_token,
                                first_result.get("price", first_price),
                                unwind_shares, round(unwind_shares * first_result.get("price", first_price), 2),
                            )
                            unwind_status = "🏷️ 已轉入撿便宜策略"
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
                    if unwind_ok:
                        unwind_status = "已平倉"
                    else:
                        comp_token = second_token
                        self._convert_orphan_to_bargain(
                            market, first_label, first_token, comp_token,
                            first_result.get("price", first_price),
                            unwind_shares, round(unwind_shares * first_result.get("price", first_price), 2),
                        )
                        unwind_status = "🏷️ 已轉入撿便宜策略"
                    record.details = (
                        f"❌ {second_label} 買入失敗 | {first_label} {unwind_status} | "
                        f"錯誤: {second_result.get('error', '')[:80]}"
                    )
                    self.status.add_log(f"❌ 配對交易失敗 | {first_label}: {unwind_status}")
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
        self.status.increment_trades_for_market(market.slug)
        self.status.last_trade_time = time.time()
        if record.status in ("executed", "simulated"):
            self.status.total_profit += record.expected_profit
        self.status.trade_history.append(record)

        # 持久化到 SQLite
        try:
            trade_db.record_trade(
                timestamp=record.timestamp,
                market_slug=record.market_slug,
                trade_type="arbitrage",
                side="BOTH",
                up_price=record.up_price,
                down_price=record.down_price,
                total_cost=record.total_cost,
                order_size=record.order_size,
                profit=record.expected_profit,
                profit_pct=record.profit_pct,
                status=record.status,
                details=record.details,
            )
            trade_db.rebuild_daily_summary()
        except Exception:
            pass

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

    # ─── 撿便宜堆疊策略 (Bargain Hunter — Stacking) ───
    #
    # 策略邏輯（以熊市為例）:
    #   Round 1: DOWN < 0.49 → 買 1 股 DOWN @ 0.49
    #   Round 1: UP   < 0.49 → 買 1 股 UP   @ 0.48 → 配對完成 (0.49+0.48=0.97)
    #   Round 2: DOWN < 0.48 → 買 1 股 DOWN @ 0.45 (必須低於上一輪買價)
    #   Round 2: UP   < 0.45 → 買 1 股 UP   @ 0.43 → 配對完成 (0.45+0.43=0.88)
    #   ... 每輪價差越來越大，利潤越來越高
    #
    # 止損: 未配對的持倉跌超過 stop_loss_cents → 賣出

    @property
    def BARGAIN_PRICE_THRESHOLD(self) -> float:
        return self.config.bargain_price_threshold

    @property
    def BARGAIN_PAIR_THRESHOLD(self) -> float:
        return self.config.bargain_pair_threshold

    @property
    def BARGAIN_STOP_LOSS_CENTS(self) -> float:
        return self.config.bargain_stop_loss_cents

    @property
    def BARGAIN_MIN_PRICE(self) -> float:
        return self.config.bargain_min_price

    def _bargain_trades_remaining(self, slug: str) -> int:
        """撿便宜策略剩餘可用交易次數（與套利共享 max_trades_per_market）"""
        used = self.status.get_trades_for_market(slug)
        return max(0, self.config.max_trades_per_market - used)

    def _get_bargain_stack(self, slug: str) -> Dict[str, Any]:
        """
        取得某市場的堆疊狀態:
        - unpaired: 最新一筆未配對的 holding (等待另一側)
        - last_buy_price: 上一輪的買入價 (下一輪必須低於此價)
        - round: 當前輪次
        - holdings: 所有活躍持倉
        """
        holdings = [
            h for h in self.status.bargain_holdings
            if h.market_slug == slug and h.status == "holding"
        ]
        paired = [
            h for h in self.status.bargain_holdings
            if h.market_slug == slug and h.status == "paired"
        ]
        stopped = [
            h for h in self.status.bargain_holdings
            if h.market_slug == slug and h.status == "stopped_out"
        ]

        # 找未配對的持倉（最新一筆 holding）
        unpaired = None
        if holdings:
            unpaired = holdings[-1]  # 最新的未配對持倉

        # 輪次: 包含止損過的（防止同輪重入）
        all_for_round = holdings + paired + stopped
        # 價格天花板: 只看未配對持倉（holding）
        # 已配對的輪次已完成，不應限制下一輪的進場價格

        if all_for_round:
            max_round = max(h.round for h in all_for_round)
        else:
            max_round = 0

        if holdings:
            # 有未配對持倉 → 用該持倉的買價作為天花板（配對側必須更便宜）
            latest_round = max(h.round for h in holdings)
            last_buy_price = min(h.buy_price for h in holdings if h.round == latest_round)
        else:
            # 全部已配對或無持倉 → 重置天花板，新一輪獨立進場
            last_buy_price = self.BARGAIN_PRICE_THRESHOLD

        return {
            "unpaired": unpaired,
            "last_buy_price": last_buy_price,
            "round": max_round,
            "holdings": holdings,
        }

    async def check_bargain_opportunities(self, markets: List[MarketInfo]) -> List[Dict[str, Any]]:
        """
        掃描所有市場，找出堆疊撿便宜機會。

        邏輯:
        - 無持倉: 任一側 < price_threshold 且 >= min_price → 買入（Round 1 開始）
        - 有未配對持倉: 另一側 < 未配對買價 → 買入配對（完成本輪）
        - 已配對: 任一側 < 上輪最低買價 → 開始新一輪堆疊
        """
        opportunities = []

        if self._is_on_cooldown():
            return opportunities

        for market in markets:
            if not market.up_token_id or not market.down_token_id:
                continue
            if self._bargain_trades_remaining(market.slug) <= 0:
                continue

            price_info = self.status.market_prices.get(market.slug)
            if not price_info:
                price_info = await self.get_prices(market)
                if not price_info:
                    continue
                self.status.market_prices[market.slug] = price_info

            up_ask = price_info.up_best_ask if price_info.up_best_ask > 0 else price_info.up_price
            down_ask = price_info.down_best_ask if price_info.down_best_ask > 0 else price_info.down_price

            stack = self._get_bargain_stack(market.slug)
            unpaired = stack["unpaired"]

            if unpaired:
                # ── 有未配對持倉: 買另一側，兩側合計 < pair_threshold ──
                # 配對加價: 未配對超過設定時間，放寬 +0.05
                escalation = 0.0
                try:
                    created_at = datetime.fromisoformat(unpaired.timestamp)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    wait_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
                    esc_hours = self.config.bargain_pair_escalation_hours
                    if esc_hours > 0 and wait_hours >= esc_hours:
                        escalation = 0.05
                        self.status.add_log(
                            f"⏰ [R{unpaired.round}] {unpaired.side} 等待配對 {wait_hours:.1f}h >= {esc_hours}h，配對上限 +5¢"
                        )
                except Exception:
                    pass

                if unpaired.side == "UP":
                    target_price = self.BARGAIN_PAIR_THRESHOLD - unpaired.buy_price + escalation
                    if (down_ask >= self.BARGAIN_MIN_PRICE
                            and down_ask < target_price):
                        opportunities.append({
                            "market": market,
                            "side": "DOWN",
                            "token_id": market.down_token_id,
                            "complement_token_id": market.up_token_id,
                            "price": price_info.down_price,
                            "best_ask": down_ask,
                            "price_info": price_info,
                            "round": unpaired.round,
                            "is_pairing": True,
                            "pair_with": unpaired,
                        })
                else:  # unpaired.side == "DOWN"
                    target_price = self.BARGAIN_PAIR_THRESHOLD - unpaired.buy_price + escalation
                    if (up_ask >= self.BARGAIN_MIN_PRICE
                            and up_ask < target_price):
                        opportunities.append({
                            "market": market,
                            "side": "UP",
                            "token_id": market.up_token_id,
                            "complement_token_id": market.down_token_id,
                            "price": price_info.up_price,
                            "best_ask": up_ask,
                            "price_info": price_info,
                            "round": unpaired.round,
                            "is_pairing": True,
                            "pair_with": unpaired,
                        })
            else:
                # ── 無未配對持倉: 開始新一輪 ──
                # 如果其他市場有未配對持倉，不開新倉（避免跨市場重複開倉）
                other_unpaired = any(
                    h.status == "holding" and h.market_slug != market.slug
                    for h in self.status.bargain_holdings
                )
                if other_unpaired:
                    continue
                next_round = stack["round"] + 1
                if next_round > self.config.bargain_max_rounds:
                    continue  # 已達堆疊上限

                price_ceiling = stack["last_buy_price"]

                # 第一輪用 price_threshold 作為天花板
                if stack["round"] == 0:
                    price_ceiling = self.BARGAIN_PRICE_THRESHOLD

                # 找最便宜的一側開始新一輪
                candidates = []
                if (up_ask >= self.BARGAIN_MIN_PRICE and up_ask < price_ceiling):
                    candidates.append(("UP", up_ask, market.up_token_id, market.down_token_id))
                if (down_ask >= self.BARGAIN_MIN_PRICE and down_ask < price_ceiling):
                    candidates.append(("DOWN", down_ask, market.down_token_id, market.up_token_id))

                if candidates:
                    # R1 開倉: 套用偏好方向（但仍須滿足閾值與天花板）
                    bias = self.config.bargain_first_buy_bias.upper()
                    if next_round == 1 and bias in ("UP", "DOWN"):
                        biased = [c for c in candidates if c[0] == bias]
                        if biased:
                            candidates = biased
                        else:
                            # 偏好側未達條件 → 不開倉，等待價格進入區間
                            continue
                    # 買最便宜的那側（或偏好側）
                    candidates.sort(key=lambda c: c[1])
                    side, ask, token_id, comp_id = candidates[0]
                    opportunities.append({
                        "market": market,
                        "side": side,
                        "token_id": token_id,
                        "complement_token_id": comp_id,
                        "price": up_ask if side == "UP" else down_ask,
                        "best_ask": ask,
                        "price_info": price_info,
                        "round": next_round,
                        "is_pairing": False,
                        "pair_with": None,
                    })

        # 最便宜的排前面
        opportunities.sort(key=lambda o: o["best_ask"])
        return opportunities

    async def execute_bargain_buy(self, opp: Dict[str, Any]) -> Optional[BargainHolding]:
        """執行撿便宜買入 — 支援堆疊輪次"""
        market: MarketInfo = opp["market"]
        side: str = opp["side"]
        token_id: str = opp["token_id"]
        complement_token_id: str = opp["complement_token_id"]
        price: float = opp["best_ask"]
        buy_round: int = opp.get("round", 1)
        is_pairing: bool = opp.get("is_pairing", False)
        pair_with: Optional[BargainHolding] = opp.get("pair_with")

        # 即時檢查: 非配對開倉時，若其他市場有未配對持倉則跳過（防止跨市場重複開倉）
        if not is_pairing:
            other_unpaired = any(
                h.status == "holding" and h.market_slug != market.slug
                for h in self.status.bargain_holdings
            )
            if other_unpaired:
                self.status.add_log(
                    f"🏷️ [撿便宜] 跳過 {market.slug} {side} — 其他市場有未配對持倉"
                )
                return None

        order_size = self.config.order_size
        amount_usd = round(order_size * price, 2)

        if amount_usd < 1.0:
            self.status.add_log(f"🏷️ [撿便宜] {market.slug} {side} 金額 ${amount_usd:.2f} < $1，跳過")
            return None

        action = "配對" if is_pairing else "開倉"
        self.status.add_log(
            f"🏷️ [撿便宜R{buy_round}{action}] {market.slug} {side} @ {price:.4f} "
            f"| 剩餘: {market.time_remaining_display}"
        )

        if self.config.dry_run:
            estimated_shares = amount_usd / price if price > 0 else 0
            self.status.add_log(
                f"🏷️ [模擬R{buy_round}] 買入 {side} | ${amount_usd:.2f} @ {price:.4f} ≈ {estimated_shares:.1f} 股"
            )
            holding = BargainHolding(
                market_slug=market.slug,
                market=market,
                side=side,
                token_id=token_id,
                complement_token_id=complement_token_id,
                buy_price=price,
                shares=estimated_shares,
                amount_usd=amount_usd,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status="holding",
                round=buy_round,
            )
        else:
            try:
                clob_client = self._get_clob_client()
                result = self._try_buy_one_side(clob_client, token_id, amount_usd, price, f"撿便宜R{buy_round}-{side}")
                if not result["success"]:
                    self.status.add_log(f"🏷️ [撿便宜] {side} 買入失敗: {result.get('error', '')[:100]}")
                    return None

                holding = BargainHolding(
                    market_slug=market.slug,
                    market=market,
                    side=side,
                    token_id=token_id,
                    complement_token_id=complement_token_id,
                    buy_price=result["price"],
                    shares=result["shares"],
                    amount_usd=amount_usd,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    status="holding",
                    round=buy_round,
                )
                self.status.add_log(
                    f"🏷️ [撿便宜R{buy_round}] {side} 成交 | {holding.shares:.1f} 股 @ {holding.buy_price:.4f}"
                )
            except Exception as e:
                self.status.add_log(f"🏷️ [撿便宜] 執行失敗: {str(e)[:120]}")
                return None

        self.status.bargain_holdings.append(holding)
        self.status.total_trades += 1
        self.status.increment_trades_for_market(market.slug)

        # 持久化開倉記錄
        try:
            trade_db.record_trade(
                timestamp=holding.timestamp,
                market_slug=market.slug,
                trade_type="bargain_open",
                side=side,
                up_price=opp["price_info"].up_price,
                down_price=opp["price_info"].down_price,
                total_cost=holding.buy_price,
                order_size=holding.shares,
                profit=0,
                profit_pct=0,
                status="executed" if not self.config.dry_run else "simulated",
                details=f"R{buy_round} {'配對' if is_pairing else '開倉'} {side}@{holding.buy_price:.4f}",
            )
        except Exception:
            pass

        # 如果是配對買入，標記兩邊為 paired
        if is_pairing and pair_with:
            combined = pair_with.buy_price + holding.buy_price
            profit_per_share = 1.0 - combined
            shares = min(pair_with.shares, holding.shares)

            holding.status = "paired"
            holding.paired_with = pair_with.timestamp
            pair_with.status = "paired"
            pair_with.paired_with = holding.timestamp

            self.status.add_log(
                f"🏷️ [R{buy_round}配對完成] {market.slug} | "
                f"{pair_with.side}@{pair_with.buy_price:.4f} + {side}@{holding.buy_price:.4f} "
                f"= {combined:.4f} | 利潤: ${profit_per_share * shares:.4f} ({(profit_per_share/combined*100):.1f}%)"
            )

            # 記錄交易
            record = TradeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                market_slug=market.slug,
                up_price=opp["price_info"].up_price,
                down_price=opp["price_info"].down_price,
                total_cost=combined,
                order_size=shares,
                expected_profit=profit_per_share * shares,
                profit_pct=(profit_per_share / combined * 100) if combined > 0 else 0,
                status="executed" if not self.config.dry_run else "simulated",
                details=f"🏷️ R{buy_round}配對 {pair_with.side}@{pair_with.buy_price:.4f}+{side}@{holding.buy_price:.4f}={combined:.4f}",
            )
            self.status.trade_history.append(record)
            self.status.total_profit += record.expected_profit

            # 持久化配對記錄
            try:
                trade_db.record_trade(
                    timestamp=record.timestamp,
                    market_slug=record.market_slug,
                    trade_type="bargain_pair",
                    side="BOTH",
                    up_price=record.up_price,
                    down_price=record.down_price,
                    total_cost=record.total_cost,
                    order_size=record.order_size,
                    profit=record.expected_profit,
                    profit_pct=record.profit_pct,
                    status=record.status,
                    details=record.details,
                )
                trade_db.rebuild_daily_summary()
            except Exception:
                pass

            # 追蹤合併
            if not self.config.dry_run and market.condition_id:
                self.merger.track_trade(
                    market_slug=market.slug,
                    condition_id=market.condition_id,
                    up_token_id=market.up_token_id or "",
                    down_token_id=market.down_token_id or "",
                    amount=shares,
                    total_cost=combined,
                )
                if self.merger.auto_merge_enabled:
                    merge_results = await self.merger.auto_merge_all()
                    for mr in merge_results:
                        self.status.add_log(
                            f"🔄 合併結果: {mr.status} | {mr.amount:.0f} 對 → "
                            f"{mr.usdc_received:.2f} USDC | {mr.details}"
                        )

        return holding

    async def scan_bargain_holdings(self):
        """
        掃描所有活躍的未配對撿便宜持倉:
        - 如果持倉價格下跌 >= 止損閾值 → 止損賣出
        (配對邏輯已移至 check_bargain_opportunities + execute_bargain_buy)
        """
        active = [h for h in self.status.bargain_holdings if h.status == "holding"]
        if not active:
            return

        for holding in active:
            # 獲取最新價格
            price_info = await self.get_prices(holding.market)
            if not price_info:
                continue

            # 當前持倉側的最新價格
            if holding.side == "UP":
                current_price = price_info.up_best_ask if price_info.up_best_ask > 0 else price_info.up_price
            else:
                current_price = price_info.down_best_ask if price_info.down_best_ask > 0 else price_info.down_price

            # ── 價格回升 → 重置延遲計時器 ──
            if current_price >= holding.buy_price:
                fresh_ts = datetime.now(timezone.utc).isoformat()
                if holding.timestamp != fresh_ts[:19]:  # 避免重複 log
                    holding.timestamp = fresh_ts
                    self.status.add_log(
                        f"📈 [R{holding.round}] {holding.side} 回升至 {current_price:.4f} >= 買入價 {holding.buy_price:.4f}，重置止損延遲"
                    )

            # ── 前 N 輪免止損，只等配對 ──
            if holding.round <= self.config.bargain_stop_loss_immune_rounds:
                if self.status.scan_count % 10 == 0:
                    self.status.add_log(
                        f"🛡️ [R{holding.round}] {holding.side} 免止損 (≤R{self.config.bargain_stop_loss_immune_rounds}) | "
                        f"買入: {holding.buy_price:.4f} 現價: {current_price:.4f} | 等待配對"
                    )
                continue

            # ── 止損檢查: 跌超過閾值 → 延遲後才賣出 ──
            price_drop = holding.buy_price - current_price
            if price_drop >= self.BARGAIN_STOP_LOSS_CENTS:
                # 計算持倉時間
                from datetime import timedelta
                try:
                    created_at = datetime.fromisoformat(holding.timestamp)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                except Exception:
                    created_at = datetime.now(timezone.utc)
                holding_age = (datetime.now(timezone.utc) - created_at).total_seconds()
                market_remaining = holding.market.time_remaining_seconds

                defer_seconds = self.config.bargain_stop_loss_defer_minutes * 60
                MIN_MARKET_TIME_FOR_DEFER = defer_seconds + 5 * 60  # 市場剩餘不足時不延遲

                if holding_age < defer_seconds and market_remaining > MIN_MARKET_TIME_FOR_DEFER:
                    defer_remaining = int(defer_seconds - holding_age)
                    self.status.add_log(
                        f"⏳ [R{holding.round}] {holding.side} 跌 {price_drop:.4f} 達止損線，"
                        f"但持倉僅 {int(holding_age)}s，延遲 {defer_remaining}s 後再止損"
                    )
                    continue

                self.status.add_log(
                    f"🛑 [R{holding.round}止損] {holding.market_slug} {holding.side} | "
                    f"買入: {holding.buy_price:.4f} → 現價: {current_price:.4f} "
                    f"(跌 {price_drop:.4f} >= {self.BARGAIN_STOP_LOSS_CENTS})"
                )
                if self.config.dry_run:
                    self.status.add_log(
                        f"🛑 [模擬止損] 賣出 {holding.shares:.1f} 股 {holding.side} @ ~{current_price:.4f}"
                    )
                    holding.status = "stopped_out"
                else:
                    try:
                        clob_client = self._get_clob_client()
                        unwind_ok = self._try_unwind_position(
                            clob_client, holding.token_id, holding.shares,
                            current_price, f"止損R{holding.round}-{holding.side}"
                        )
                        holding.status = "stopped_out"
                        if unwind_ok:
                            self.status.add_log(f"🛑 [止損成功] {holding.side} 已賣出")
                        else:
                            self.status.add_log(f"🛑 [止損失敗] {holding.side} 需手動處理!")
                    except Exception as e:
                        self.status.add_log(f"🛑 [止損異常] {str(e)[:120]}")

                # 止損後冷卻（防止「高買低賣」循環）
                from datetime import timedelta
                cooldown_min = self.config.bargain_stop_loss_cooldown_minutes
                self._stop_loss_cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_min)
                self.status.add_log(f"⏳ 止損冷卻中，{cooldown_min} 分鐘內不開新倉")

                self.status.total_trades += 1
                self.status.increment_trades_for_market(holding.market_slug)

                record = TradeRecord(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    market_slug=holding.market_slug,
                    up_price=price_info.up_price,
                    down_price=price_info.down_price,
                    total_cost=price_info.total_cost,
                    order_size=holding.shares,
                    expected_profit=-(price_drop * holding.shares),
                    profit_pct=-(price_drop / holding.buy_price * 100) if holding.buy_price > 0 else 0,
                    status="executed" if not self.config.dry_run else "simulated",
                    details=f"🛑 R{holding.round}止損 {holding.side} | -{price_drop:.4f}/share",
                )
                self.status.trade_history.append(record)
                self.status.total_profit += record.expected_profit

                # 持久化止損記錄
                try:
                    trade_db.record_trade(
                        timestamp=record.timestamp,
                        market_slug=record.market_slug,
                        trade_type="bargain_stop",
                        side=holding.side,
                        up_price=record.up_price,
                        down_price=record.down_price,
                        total_cost=record.total_cost,
                        order_size=record.order_size,
                        profit=record.expected_profit,
                        profit_pct=record.profit_pct,
                        status=record.status,
                        details=record.details,
                    )
                    trade_db.rebuild_daily_summary()
                except Exception:
                    pass

    async def scan_market(self, market: MarketInfo) -> Optional[ArbitrageOpportunity]:
        """掃描單個市場的套利機會"""
        price_info = await self.get_prices(market)
        if not price_info:
            return None

        self.status.last_price = price_info
        self.status.market_prices[market.slug] = price_info
        self.status.scan_count += 1

        opportunity = self.check_arbitrage(market, price_info)

        # 每 10 次掃描持久化一次（避免寫入過頻）
        if self.status.scan_count % 10 == 0:
            try:
                trade_db.record_scan(
                    timestamp=price_info.timestamp,
                    market_slug=market.slug,
                    up_price=price_info.up_price,
                    down_price=price_info.down_price,
                    total_cost=price_info.total_cost,
                    spread=price_info.spread,
                    up_liquidity=price_info.up_liquidity,
                    down_liquidity=price_info.down_liquidity,
                    opportunity_viable=opportunity.is_viable,
                )
            except Exception:
                pass

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
        cred_keys = {"private_key", "funder_address", "signature_type"}
        if cred_keys & new_config.keys():
            self._clob_client = None
            self.status.add_log("🔑 憑證已變更，將重新建立 CLOB 連線")
        for key, value in new_config.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.status.mode = "模擬" if self.config.dry_run else "🔴 真實交易"
        self.status.add_log(f"⚙️ 配置已更新: {new_config}")

    def ensure_clob_connected(self):
        """啟動時主動建立 CLOB 連線（非 dry_run）。"""
        if self.config.dry_run:
            return
        try:
            self._get_clob_client()
            self.status.add_log("✅ CLOB API 憑證已驗證/註冊")
        except Exception as e:
            self.status.add_log(f"⚠️ CLOB 啟動連線失敗: {e}")
        self._approvals_ok = self.ensure_approvals()

    def ensure_approvals(self) -> bool:
        """確保 EOA 錢包已對 Polymarket 合約設定 USDC 和 CTF ERC-1155 授權。dry_run / sig_type=0 時跳過。"""
        if self.config.dry_run or self.config.signature_type == 0:
            return True
        pk = (self.config.private_key or "").strip()
        if not pk:
            return False
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
            from eth_account import Account
            w3 = Web3(Web3.HTTPProvider("https://polygon-bor.publicnode.com"))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if not w3.is_connected():
                self.status.add_log("⚠️ 無法連線 Polygon RPC，跳過授權檢查")
                return
            wallet = Account.from_key(pk).address
            USDC = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
            CTF  = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
            SPENDERS = [
                ("CTF Exchange",         "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
                ("NegRisk CTF Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80a"),
                ("NegRisk Adapter",      "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
            ]
            CTF_OPERATORS = [
                ("CTF Exchange",         "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
                ("NegRisk CTF Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80a"),
            ]
            erc20_abi = [
                {"name": "approve",   "type": "function", "stateMutability": "nonpayable",
                 "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                 "outputs": [{"name": "", "type": "bool"}]},
                {"name": "allowance", "type": "function", "stateMutability": "view",
                 "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
                 "outputs": [{"name": "", "type": "uint256"}]},
            ]
            erc1155_abi = [
                {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
                 "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
                 "outputs": []},
                {"name": "isApprovedForAll", "type": "function", "stateMutability": "view",
                 "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
                 "outputs": [{"name": "", "type": "bool"}]},
            ]
            usdc_c = w3.eth.contract(address=USDC, abi=erc20_abi)
            ctf_c  = w3.eth.contract(address=CTF,  abi=erc1155_abi)
            acct   = Account.from_key(pk)
            MAX    = 2**256 - 1

            def send(tx):
                tx.pop("maxFeePerGas", None)
                tx.pop("maxPriorityFeePerGas", None)
                tx.pop("type", None)
                tx["nonce"]    = w3.eth.get_transaction_count(wallet)
                tx["chainId"]  = 137
                tx["gasPrice"] = w3.eth.gas_price
                try:
                    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.3)
                except Exception:
                    tx["gas"] = 120_000
                signed = acct.sign_transaction(tx)
                raw = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
                h = w3.eth.send_raw_transaction(raw)
                r = w3.eth.wait_for_transaction_receipt(h, timeout=90)
                return r.status == 1

            for name, sp in SPENDERS:
                sp_cs = Web3.to_checksum_address(sp)
                if usdc_c.functions.allowance(wallet, sp_cs).call() < 10**18:
                    self.status.add_log(f"🔓 USDC approve → {name}...")
                    ok = send(usdc_c.functions.approve(sp_cs, MAX).build_transaction({"from": wallet}))
                    self.status.add_log("✅ USDC approved" if ok else f"❌ USDC approve failed for {name}")

            for name, op in CTF_OPERATORS:
                op_cs = Web3.to_checksum_address(op)
                if not ctf_c.functions.isApprovedForAll(wallet, op_cs).call():
                    self.status.add_log(f"🔓 CTF setApprovalForAll → {name}...")
                    ok = send(ctf_c.functions.setApprovalForAll(op_cs, True).build_transaction({"from": wallet}))
                    self.status.add_log("✅ CTF approved" if ok else f"❌ CTF approval failed for {name}")

            self.status.add_log("✅ 授權檢查完成")
            return True
        except Exception as e:
            self.status.add_log(f"⚠️ 授權檢查失敗: {e}")
            return False

    async def test_connection(self, markets: List[MarketInfo]):
        """執行最小測試單（$1 買入後立立即平倉）驗證錢包連線。dry_run 時跳過。"""
        if self.config.dry_run:
            self.status.add_log("🧪 dry_run 模式，跳過連線測試")
            return
        if not getattr(self, '_approvals_ok', True):
            self.status.add_log("⚠️ 授權未完成，跳過連線測試以避免遺留持倉")
            return
        self.status.add_log("🔌 開始連線測試（$1 測試單）...")
        clob = self._get_clob_client()
        if not clob:
            self.status.add_log("⚠️ 無法取得 CLOB 客戶端，跳過測試")
            return

        test_market = None
        price_info = None
        for m in markets:
            if not m.up_token_id or not m.down_token_id:
                continue
            pi = await self.get_prices(m)
            if pi and (pi.up_best_ask > 0 or pi.down_best_ask > 0):
                test_market = m
                price_info = pi
                break

        if not test_market or not price_info:
            self.status.add_log("⚠️ 找不到可用市場進行連線測試")
            return

        up_ask = price_info.up_best_ask if price_info.up_best_ask > 0 else price_info.up_price
        down_ask = price_info.down_best_ask if price_info.down_best_ask > 0 else price_info.down_price
        if up_ask <= down_ask and up_ask > 0:
            side, token_id = "UP", test_market.up_token_id
            ask = up_ask
        else:
            side, token_id = "DOWN", test_market.down_token_id
            ask = down_ask

        self.status.add_log(f"🔌 測試市場: {test_market.slug} | {side} @ {ask:.4f}")
        result = self._try_buy_one_side(clob, token_id, 1.0, ask, f"[測試]{side}")
        if not result.get("success"):
            self.status.add_log(f"❌ 連線測試買入失敗: {result.get('error')}")
            return

        shares = result.get("shares", 0)
        buy_price = result.get("price", ask)
        self.status.add_log(f"✅ 測試買入成功 {shares:.2f} 股 @ {buy_price:.4f}，立即平倉...")
        unwound = self._try_unwind_position(clob, token_id, shares, buy_price, f"[測試]{side}")
        if unwound:
            self.status.add_log("✅ 連線測試完成：買入+平倉成功，錢包連線正常")
        else:
            self.status.add_log("⚠️ 連線測試：買入成功但平倉失敗，請手動檢查持倉")
