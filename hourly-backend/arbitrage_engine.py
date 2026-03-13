"""
套利引擎 - 核心套利邏輯、風險控制、交易執行（每日 Up or Down 市場版本）
"""
import asyncio
import math
import time
import json
import re
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from collections import deque
from config import BotConfig
from market_finder import MarketInfo
from position_merger import PositionMerger
import trade_db

LOG_FILE = Path(__file__).resolve().parent / "bot.log"
LOG_TIMEZONE = timezone(timedelta(hours=8))

SENSITIVE_LOG_PATTERNS = [
    re.compile(r"(?i)(token=)([^\s&]+)"),
    re.compile(r"(?i)(authorization[:=]\s*bearer\s+)([^\s]+)"),
    re.compile(r"(?i)(jwt[:=]\s*)([^\s]+)"),
    re.compile(r"(?i)(password|private[_-]?key|api[_-]?key|secret|passphrase|funder[_-]?address)(\s*[:=]\s*)([^,\s]+)"),
]


def _redact_log_message(message: str) -> str:
    sanitized_message = str(message or "")
    for compiled_pattern in SENSITIVE_LOG_PATTERNS:
        if compiled_pattern.groups == 2:
            sanitized_message = compiled_pattern.sub(r"\1<redacted>", sanitized_message)
        else:
            sanitized_message = compiled_pattern.sub(r"\1\2<redacted>", sanitized_message)
    return sanitized_message


def _read_log_tail(limit: int = 200) -> List[str]:
    if not LOG_FILE.exists():
        return []
    lines: deque[str] = deque(maxlen=limit)
    with LOG_FILE.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            lines.append(line.rstrip("\n"))
    return list(lines)


def _safe_parse_json_response(response: httpx.Response, response_label: str) -> Dict[str, Any]:
    response_text = response.text or ""
    try:
        parsed_payload = response.json()
    except json.JSONDecodeError as decode_error:
        response_preview = response_text.strip().replace("\r", " ").replace("\n", " ")[:160]
        raise ValueError(
            f"{response_label} JSON 解析失敗 | status={response.status_code} | body={response_preview or '<empty>'} | error={decode_error}"
        ) from decode_error

    if not isinstance(parsed_payload, dict):
        raise ValueError(
            f"{response_label} 回傳格式異常 | status={response.status_code} | type={type(parsed_payload).__name__}"
        )

    return parsed_payload


@dataclass
class PriceInfo:
    up_price: float = 0.0
    down_price: float = 0.0
    total_cost: float = 0.0
    spread: float = 0.0
    up_best_bid: float = 0.0
    down_best_bid: float = 0.0
    up_best_ask: float = 0.0
    down_best_ask: float = 0.0
    up_liquidity: float = 0.0
    down_liquidity: float = 0.0
    up_bids: List[Dict[str, float]] = field(default_factory=list)
    down_bids: List[Dict[str, float]] = field(default_factory=list)
    up_asks: List[Dict[str, float]] = field(default_factory=list)
    down_asks: List[Dict[str, float]] = field(default_factory=list)
    timestamp: str = ""
    time_remaining_seconds: float = 0.0
    time_remaining_display: str = ""
    underlying_symbol: Optional[str] = None
    underlying_price: Optional[float] = None
    reference_price: Optional[float] = None
    reference_source: Optional[str] = None
    distance_to_reference: Optional[float] = None
    distance_to_reference_pct: Optional[float] = None
    spot_momentum_pct_30s: Optional[float] = None
    implied_up_probability: Optional[float] = None
    implied_down_probability: Optional[float] = None
    price_edge_score: Optional[float] = None
    price_edge_side: Optional[str] = None
    trend_lock_side: Optional[str] = None
    trend_lock_active: bool = False
    price_edge_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "up_price": self.up_price,
            "down_price": self.down_price,
            "total_cost": self.total_cost,
            "spread": self.spread,
            "up_best_bid": self.up_best_bid,
            "down_best_bid": self.down_best_bid,
            "up_best_ask": self.up_best_ask,
            "down_best_ask": self.down_best_ask,
            "up_liquidity": self.up_liquidity,
            "down_liquidity": self.down_liquidity,
            "up_bids": self.up_bids,
            "down_bids": self.down_bids,
            "timestamp": self.timestamp,
            "time_remaining_seconds": self.time_remaining_seconds,
            "time_remaining_display": self.time_remaining_display,
            "underlying_symbol": self.underlying_symbol,
            "underlying_price": self.underlying_price,
            "reference_price": self.reference_price,
            "reference_source": self.reference_source,
            "distance_to_reference": self.distance_to_reference,
            "distance_to_reference_pct": self.distance_to_reference_pct,
            "spot_momentum_pct_30s": self.spot_momentum_pct_30s,
            "implied_up_probability": self.implied_up_probability,
            "implied_down_probability": self.implied_down_probability,
            "price_edge_score": self.price_edge_score,
            "price_edge_side": self.price_edge_side,
            "trend_lock_side": self.trend_lock_side,
            "trend_lock_active": self.trend_lock_active,
            "price_edge_summary": self.price_edge_summary,
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
    plummet_last_price: Optional[float] = None
    plummet_last_ts: Optional[str] = None
    plummet_high_price: Optional[float] = None
    plummet_window_start_ts: Optional[str] = None
    pending_exit_order_id: Optional[str] = None
    pending_exit_reason: Optional[str] = None
    pending_exit_trade_id: Optional[int] = None

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
            "plummet_last_price": self.plummet_last_price,
            "plummet_high_price": self.plummet_high_price,
            "pending_exit_reason": self.pending_exit_reason,
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
    plummet_blocked_markets: List[str] = field(default_factory=list)
    dynamic_scan_interval_seconds: int = 0
    dynamic_bargain_window_seconds: Optional[int] = None
    velocity_trend: Optional[str] = None
    dynamic_bargain_min_price: Optional[float] = None
    dynamic_bargain_max_price: Optional[float] = None
    dynamic_bargain_min_bound: Optional[float] = None
    dynamic_bargain_max_bound: Optional[float] = None

    def get_trades_for_market(self, slug: str) -> int:
        return self.trades_per_market.get(slug, 0)

    def increment_trades_for_market(self, slug: str):
        self.trades_per_market[slug] = self.trades_per_market.get(slug, 0) + 1

    def add_log(self, msg: str):
        ts = datetime.now(LOG_TIMEZONE).strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            # Fail silently to avoid crashing the bot if disk is unavailable
            pass
        try:
            print(_redact_log_message(entry), flush=True)
        except Exception:
            pass

    def to_dict(self) -> Dict[str, Any]:
        persisted_logs = _read_log_tail(200)
        logs_for_status = persisted_logs if persisted_logs else self.logs
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
            # Feed UI from persisted log file if available (falls back to memory buffer)
            "logs": logs_for_status,
            "trade_history": [t.to_dict() for t in self.trade_history[-20:]],
            "current_opportunities": [o.to_dict() for o in self.current_opportunities],
            "bargain_holdings": [h.to_dict() for h in self.bargain_holdings if h.status == "holding"],
            "plummet_blocked_markets": list(self.plummet_blocked_markets),
            "dynamic_scan_interval_seconds": self.dynamic_scan_interval_seconds,
            "dynamic_bargain_window_seconds": self.dynamic_bargain_window_seconds,
            "velocity_trend": self.velocity_trend,
            "dynamic_bargain_min_price": self.dynamic_bargain_min_price,
            "dynamic_bargain_max_price": self.dynamic_bargain_max_price,
            "dynamic_bargain_min_bound": self.dynamic_bargain_min_bound,
            "dynamic_bargain_max_bound": self.dynamic_bargain_max_bound,
        }


class ArbitrageEngine:
    def __init__(self, config: BotConfig):
        self.config = config
        self.status = BotStatus()
        self.merger = PositionMerger(config)
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stop_loss_cooldown_until: Optional[datetime] = None
        self._clob_client = None
        self._underlying_price_history: Dict[str, deque] = {}
        self._pending_unwind_kv_key = "pending_gtc_unwinds"
        self._plummet_blocked_markets: set[str] = set()

    def _is_market_plummet_blocked(self, market_slug: Optional[str]) -> bool:
        normalized_market_slug = str(market_slug or "").strip()
        if not normalized_market_slug:
            return False
        return normalized_market_slug in self._plummet_blocked_markets

    def _mark_market_plummet_blocked(self, market_slug: Optional[str], reason: str = "") -> None:
        normalized_market_slug = str(market_slug or "").strip()
        if not normalized_market_slug:
            return
        if normalized_market_slug in self._plummet_blocked_markets:
            return
        self._plummet_blocked_markets.add(normalized_market_slug)
        self.status.plummet_blocked_markets = sorted(self._plummet_blocked_markets)
        reason_suffix = f" | {reason}" if reason else ""
        self.status.add_log(f"⛔ [市場封鎖] {normalized_market_slug} 已因急跌護欄列入忽略名單{reason_suffix}")

    async def get_prices(self, market: MarketInfo) -> Optional[PriceInfo]:
        """從 CLOB API 獲取 UP/DOWN 代幣的當前價格和訂單簿深度"""
        up_id = market.up_token_id
        down_id = market.down_token_id
        if not up_id or not down_id:
            return None

        price_info = PriceInfo()
        price_info.timestamp = datetime.now(timezone.utc).isoformat()
        price_info.time_remaining_seconds = float(getattr(market, "time_remaining_seconds", 0.0) or 0.0)
        price_info.time_remaining_display = str(getattr(market, "time_remaining_display", "") or "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                # 獲取 UP 代幣價格
                up_resp = await client.get(
                    f"{self.config.CLOB_HOST}/price",
                    params={"token_id": up_id, "side": "buy"}
                )
                if up_resp.status_code == 200:
                    up_price_payload = _safe_parse_json_response(up_resp, f"價格查詢 UP {market.slug}")
                    price_info.up_price = float(up_price_payload.get("price", 0))
                else:
                    self.status.add_log(
                        f"⚠️ 價格查詢失敗 | {market.slug} UP | status={up_resp.status_code}"
                    )

                # 獲取 DOWN 代幣價格
                down_resp = await client.get(
                    f"{self.config.CLOB_HOST}/price",
                    params={"token_id": down_id, "side": "buy"}
                )
                if down_resp.status_code == 200:
                    down_price_payload = _safe_parse_json_response(down_resp, f"價格查詢 DOWN {market.slug}")
                    price_info.down_price = float(down_price_payload.get("price", 0))
                else:
                    self.status.add_log(
                        f"⚠️ 價格查詢失敗 | {market.slug} DOWN | status={down_resp.status_code}"
                    )

                # 獲取訂單簿深度
                if up_id:
                    up_book_resp = await client.get(f"{self.config.CLOB_HOST}/book?token_id={up_id}")
                    book = _safe_parse_json_response(up_book_resp, f"訂單簿查詢 UP {market.slug}")
                    bids = book.get("bids", [])
                    if bids:
                        price_info.up_best_bid = max(float(b.get("price", 0)) for b in bids)
                        price_info.up_bids = [
                            {"price": float(b.get("price", 0)), "size": float(b.get("size", 0))}
                            for b in bids[:10]
                        ]
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

                if down_id:
                    down_book_resp = await client.get(f"{self.config.CLOB_HOST}/book?token_id={down_id}")
                    book = _safe_parse_json_response(down_book_resp, f"訂單簿查詢 DOWN {market.slug}")
                    bids = book.get("bids", [])
                    if bids:
                        price_info.down_best_bid = max(float(b.get("price", 0)) for b in bids)
                        price_info.down_bids = [
                            {"price": float(b.get("price", 0)), "size": float(b.get("size", 0))}
                            for b in bids[:10]
                        ]
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
                self._populate_price_context(market, price_info)

                return price_info

            except Exception as e:
                self.status.add_log(f"❌ 獲取價格失敗 | {market.slug}: {e}")
                return None

    def check_arbitrage(self, market: MarketInfo, price_info: PriceInfo) -> ArbitrageOpportunity:
        """檢查是否存在套利機會（含滑價容忍度）"""
        MAX_SLIPPAGE = 0.005  # 滑價容忍度（total_cost 已用 best_ask，僅需覆蓋市場衝擊）
        order_size = self.config.order_size
        total_cost = price_info.total_cost
        target = self.config.target_pair_cost
        self._populate_price_context(market, price_info)

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

    def _populate_price_context(self, market: MarketInfo, price_info: PriceInfo):
        underlying_symbol = str(getattr(market, "underlying_symbol", "") or "").strip().upper()
        underlying_price_raw = getattr(market, "underlying_price", None)
        reference_price_raw = getattr(market, "reference_price", None)
        reference_source = getattr(market, "reference_source", None)
        try:
            underlying_price = float(underlying_price_raw) if underlying_price_raw is not None else None
        except (TypeError, ValueError):
            underlying_price = None
        try:
            reference_price = float(reference_price_raw) if reference_price_raw is not None else None
        except (TypeError, ValueError):
            reference_price = None
        price_info.time_remaining_seconds = float(getattr(market, "time_remaining_seconds", 0.0) or 0.0)
        price_info.time_remaining_display = str(getattr(market, "time_remaining_display", "") or "")
        price_info.underlying_symbol = underlying_symbol or None
        price_info.underlying_price = underlying_price
        price_info.reference_price = reference_price
        price_info.reference_source = reference_source
        price_info.implied_up_probability = price_info.up_price if price_info.up_price > 0 else None
        price_info.implied_down_probability = price_info.down_price if price_info.down_price > 0 else None

        momentum_pct_30s: Optional[float] = None
        if underlying_symbol and underlying_price is not None and underlying_price > 0:
            history_window = self._underlying_price_history.get(underlying_symbol)
            if history_window is None:
                history_window = deque(maxlen=120)
                self._underlying_price_history[underlying_symbol] = history_window
            now_ts = time.time()
            history_window.append((now_ts, underlying_price))
            cutoff_ts = now_ts - 180.0
            while history_window and history_window[0][0] < cutoff_ts:
                history_window.popleft()
            baseline_price: Optional[float] = None
            baseline_cutoff_ts = now_ts - 30.0
            for observed_ts, observed_price in history_window:
                if observed_ts <= baseline_cutoff_ts:
                    baseline_price = observed_price
                else:
                    break
            if baseline_price is None and history_window:
                baseline_price = history_window[0][1]
            if baseline_price and baseline_price > 0:
                momentum_pct_30s = (underlying_price - baseline_price) / baseline_price
        price_info.spot_momentum_pct_30s = momentum_pct_30s

        if reference_price is None or reference_price <= 0 or underlying_price is None or underlying_price <= 0:
            price_info.distance_to_reference = None
            price_info.distance_to_reference_pct = None
            price_info.price_edge_score = None
            price_info.price_edge_side = None
            price_info.trend_lock_side = None
            price_info.trend_lock_active = False
            if underlying_price is None:
                price_info.price_edge_summary = "缺少現貨價格"
            elif reference_price is None:
                price_info.price_edge_summary = "缺少市場參考價"
            else:
                price_info.price_edge_summary = None
            return

        distance_to_reference = underlying_price - reference_price
        distance_to_reference_pct = distance_to_reference / reference_price
        price_info.distance_to_reference = distance_to_reference
        price_info.distance_to_reference_pct = distance_to_reference_pct

        time_scale = min(1.0, max(0.15, price_info.time_remaining_seconds / 300.0))
        distance_component = distance_to_reference_pct / max(time_scale, 0.15)
        momentum_component = momentum_pct_30s or 0.0
        composite_up_signal = distance_component + (momentum_component * 1.5)
        composite_down_signal = (-distance_component) + ((-momentum_component) * 1.5)
        implied_up_probability = price_info.implied_up_probability or 0.0
        implied_down_probability = price_info.implied_down_probability or 0.0
        edge_up_score = composite_up_signal - implied_up_probability
        edge_down_score = composite_down_signal - implied_down_probability
        if edge_up_score >= edge_down_score:
            price_info.price_edge_score = edge_up_score
            price_info.price_edge_side = "UP"
        else:
            price_info.price_edge_score = edge_down_score
            price_info.price_edge_side = "DOWN"
        locked_side_summary = f" | 鎖定 {price_info.trend_lock_side}" if price_info.trend_lock_active and price_info.trend_lock_side else ""
        price_info.price_edge_summary = (
            f"現價 {underlying_price:.2f} vs 參考 {reference_price:.2f} | "
            f"距離 {distance_to_reference_pct * 100:.3f}% | "
            f"30s 動能 {(momentum_pct_30s or 0.0) * 100:.3f}% | "
            f"偏向 {price_info.price_edge_side} edge {price_info.price_edge_score:.4f}{locked_side_summary}"
        )

    def _load_pending_unwinds(self) -> List[Dict[str, Any]]:
        raw_pending = trade_db.kv_get("hourly_backend_pending_unwinds", "[]")
        try:
            parsed_pending = json.loads(raw_pending)
            if isinstance(parsed_pending, list):
                return parsed_pending
        except Exception:
            pass
        return []

    def _save_pending_unwinds(self, pending_unwinds: List[Dict[str, Any]]):
        try:
            trade_db.kv_set("hourly_backend_pending_unwinds", json.dumps(pending_unwinds, ensure_ascii=False))
        except Exception as e:
            self.status.add_log(f"⚠️ 儲存待成交 GTC 清單失敗: {str(e)[:120]}")

    def _queue_pending_unwind(self, pending_payload: Dict[str, Any]):
        pending_unwinds = self._load_pending_unwinds()
        pending_unwinds = [
            existing_pending
            for existing_pending in pending_unwinds
            if str(existing_pending.get("order_id", "") or "") != str(pending_payload.get("order_id", "") or "")
        ]
        pending_unwinds.append(pending_payload)
        self._save_pending_unwinds(pending_unwinds)

    def _remove_pending_unwind(self, order_id: str):
        pending_unwinds = self._load_pending_unwinds()
        filtered_pending = [
            existing_pending for existing_pending in pending_unwinds if str(existing_pending.get("order_id", "") or "") != str(order_id or "")
        ]
        if len(filtered_pending) != len(pending_unwinds):
            self._save_pending_unwinds(filtered_pending)

    def _find_trade_fill_for_asset_sync(self, clob_client, asset_id: str, after_ts_ms: int) -> Optional[Dict[str, Any]]:
        from py_clob_client.clob_types import TradeParams

        recent_trades = clob_client.get_trades(TradeParams(asset_id=asset_id, after=after_ts_ms))

        if not recent_trades:
            return None

        def _trade_timestamp(trade_payload: Dict[str, Any]) -> int:
            for timestamp_key in ("createdAt", "created_at", "timestamp", "time"):
                raw_timestamp = trade_payload.get(timestamp_key)
                if raw_timestamp is None:
                    continue
                try:
                    return int(float(raw_timestamp))
                except (TypeError, ValueError):
                    continue
            return 0

        sorted_trades = sorted(recent_trades, key=_trade_timestamp, reverse=True)
        return sorted_trades[0] if sorted_trades else None

    async def _find_trade_fill_for_asset(self, clob_client, asset_id: str, after_ts_ms: int) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.to_thread(
                self._find_trade_fill_for_asset_sync,
                clob_client,
                asset_id,
                after_ts_ms,
            )
        except Exception as e:
            self.status.add_log(f"⚠️ 查詢待成交 GTC 成交失敗: {str(e)[:120]}")
            return None

    def _parse_order_fill_state(self, order_payload: Dict[str, Any]) -> Tuple[float, bool]:
        if not isinstance(order_payload, dict):
            return 0.0, False

        original_size = 0.0
        for size_key in ("original_size", "size", "initial_size"):
            raw_size = order_payload.get(size_key)
            if raw_size is None:
                continue
            try:
                original_size = float(raw_size)
                break
            except (TypeError, ValueError):
                continue

        filled_size = 0.0
        for filled_key in ("filled_size", "matched_size", "filled", "size_matched"):
            raw_filled = order_payload.get(filled_key)
            if raw_filled is None:
                continue
            try:
                filled_size = float(raw_filled)
                break
            except (TypeError, ValueError):
                continue

        status_text = str(order_payload.get("status", "")).lower()
        is_filled = status_text in {"filled", "matched", "executed", "complete", "completed"}
        if original_size > 0 and filled_size >= original_size - 0.0001:
            is_filled = True
        return filled_size, is_filled

    def _find_holding_by_timestamp(self, holding_timestamp: str) -> Optional[BargainHolding]:
        if not holding_timestamp:
            return None
        for bargain_holding in self.status.bargain_holdings:
            if bargain_holding.timestamp == holding_timestamp:
                return bargain_holding
        return None

    def _find_pending_unwind_holding(self, pending_payload: Dict[str, Any]) -> Optional[BargainHolding]:
        holding_timestamp = str(pending_payload.get("holding_timestamp", "") or "")
        matched_holding = self._find_holding_by_timestamp(holding_timestamp)
        if matched_holding:
            return matched_holding

        pending_token_id = str(pending_payload.get("token_id", "") or "")
        pending_market_slug = str(pending_payload.get("market_slug", "") or "")
        pending_side_label = str(pending_payload.get("side_label", "") or "")
        pending_trade_id = int(pending_payload.get("trade_id", 0) or 0)
        pending_order_id = str(pending_payload.get("order_id", "") or "")

        for bargain_holding in self.status.bargain_holdings:
            if pending_token_id and bargain_holding.token_id == pending_token_id:
                return bargain_holding
            if pending_trade_id and bargain_holding.pending_exit_trade_id == pending_trade_id:
                return bargain_holding
            if pending_order_id and bargain_holding.pending_exit_order_id == pending_order_id:
                return bargain_holding
            if (
                pending_market_slug
                and pending_side_label
                and bargain_holding.market_slug == pending_market_slug
                and bargain_holding.side == pending_side_label
            ):
                return bargain_holding
        return None

    def _remove_bargain_holding(self, holding: Optional[BargainHolding]):
        if not holding:
            return
        self.status.bargain_holdings = [
            bargain_holding
            for bargain_holding in self.status.bargain_holdings
            if bargain_holding.timestamp != holding.timestamp
        ]

    def _clear_holding_pending_exit(self, holding: Optional[BargainHolding]):
        if not holding:
            return
        holding.pending_exit_order_id = None
        holding.pending_exit_reason = None
        holding.pending_exit_trade_id = None

    def _cancel_pending_unwind_order_sync(self, clob_client, order_id: str) -> bool:
        normalized_order_id = str(order_id or "")
        if not normalized_order_id:
            return False
        cancel_response = clob_client.cancel_orders([normalized_order_id])
        return bool(cancel_response is not None)

    async def _cancel_pending_unwind_order(self, clob_client, order_id: str) -> bool:
        normalized_order_id = str(order_id or "")
        if not normalized_order_id:
            return False
        try:
            cancel_result = await asyncio.to_thread(
                self._cancel_pending_unwind_order_sync,
                clob_client,
                normalized_order_id,
            )
            if cancel_result:
                self.status.add_log(f"⚠️ 已取消待成交 GTC 剩餘單: {normalized_order_id[:12]}")
            return bool(cancel_result)
        except Exception as e:
            self.status.add_log(f"⚠️ 取消待成交 GTC {normalized_order_id[:12]} 失敗: {str(e)[:120]}")
            return False

    def _should_cancel_stale_pending_unwind(self, pending_payload: Dict[str, Any], matched_holding: Optional[BargainHolding]) -> Tuple[bool, str]:
        pending_created_at_ms = int(pending_payload.get("created_at_ms", 0) or 0)
        pending_age_seconds = 0.0
        if pending_created_at_ms > 0:
            pending_age_seconds = max(0.0, (time.time() * 1000 - pending_created_at_ms) / 1000.0)

        stale_timeout_seconds = 30.0
        if pending_age_seconds >= stale_timeout_seconds:
            return True, f"逾時 {pending_age_seconds:.1f}s"

        holding_time_remaining = getattr(getattr(matched_holding, "market", None), "time_remaining_seconds", None)
        if holding_time_remaining is not None and holding_time_remaining <= 5:
            return True, f"臨近到期 {holding_time_remaining:.1f}s"

        pending_remaining_shares = float(pending_payload.get("shares", 0) or 0)
        if pending_remaining_shares <= 0.01:
            return True, f"剩餘股數過小 {pending_remaining_shares:.4f}"

        return False, ""

    def _clear_stale_blocking_pending_exit(self, holding: Optional[BargainHolding], urgent_reason: str) -> bool:
        if not holding:
            return False
        pending_order_id = str(getattr(holding, "pending_exit_order_id", "") or "")
        pending_reason = str(getattr(holding, "pending_exit_reason", "") or "")
        pending_trade_id = int(getattr(holding, "pending_exit_trade_id", 0) or 0)
        if not pending_order_id and not pending_reason and not pending_trade_id:
            return False

        matched_pending_payload = None
        for pending_payload in self._load_pending_unwinds():
            payload_order_id = str(pending_payload.get("order_id", "") or "")
            payload_trade_id = int(pending_payload.get("trade_id", 0) or 0)
            payload_holding_timestamp = str(pending_payload.get("holding_timestamp", "") or "")
            if pending_order_id and payload_order_id == pending_order_id:
                matched_pending_payload = pending_payload
                break
            if pending_trade_id and payload_trade_id == pending_trade_id:
                matched_pending_payload = pending_payload
                break
            if payload_holding_timestamp and payload_holding_timestamp == str(getattr(holding, "timestamp", "") or ""):
                matched_pending_payload = pending_payload
                break

        should_clear = matched_pending_payload is None
        stale_reason = "未找到對應 pending unwind"
        if matched_pending_payload is not None:
            should_clear, stale_reason = self._should_cancel_stale_pending_unwind(matched_pending_payload, holding)

        if not should_clear:
            return False

        self.status.add_log(
            f"⚠️ [{urgent_reason}] 清除阻塞中的待成交退出狀態 | {holding.market_slug} {holding.side} | {stale_reason}"
        )
        if matched_pending_payload is not None:
            self._remove_pending_unwind(str(matched_pending_payload.get("order_id", "") or ""))
        self._clear_holding_pending_exit(holding)
        return True

    def _register_pending_unwind_trade(
        self,
        record: TradeRecord,
        trade_id: int,
        market: MarketInfo,
        token_id: str,
        side_label: str,
        shares: float,
        buy_price: float,
        unwind_result: Dict[str, Any],
    ):
        response_payload = unwind_result.get("response") or {}
        order_id = str(
            response_payload.get("orderID")
            or response_payload.get("orderId")
            or response_payload.get("id")
            or response_payload.get("order_id")
            or ""
        )
        if not order_id:
            self.status.add_log("⚠️ GTC 已掛出但缺少 order_id，無法自動對帳")
            return

        created_at_ms = int(time.time() * 1000)
        pending_payload = {
            "trade_id": int(trade_id),
            "market_slug": market.slug,
            "token_id": token_id,
            "side_label": side_label,
            "shares": float(getattr(record, "pending_unwind_shares", shares) or shares),
            "buy_price": float(getattr(record, "pending_unwind_buy_price", buy_price) or buy_price),
            "sell_price": float(getattr(record, "pending_unwind_sell_price", unwind_result.get("sell_price", buy_price)) or buy_price),
            "order_id": order_id,
            "created_at_ms": created_at_ms,
            "record_timestamp": record.timestamp,
            "holding_timestamp": str(getattr(record, "pending_unwind_holding_timestamp", "") or ""),
            "holding_exit_reason": str(getattr(record, "pending_unwind_holding_reason", "") or ""),
        }
        self._queue_pending_unwind(pending_payload)
        self.status.add_log(f"📌 已登記待成交 GTC 對帳 | order_id={order_id[:12]} | {market.slug} {side_label}")

    def _finalize_pending_unwind_fill(self, pending_payload: Dict[str, Any], fill_trade: Optional[Dict[str, Any]], order_payload: Optional[Dict[str, Any]]):
        trade_id = pending_payload.get("trade_id")
        if not trade_id:
            return

        stored_trade = trade_db.get_trade_by_id(int(trade_id))
        if not stored_trade:
            self._remove_pending_unwind(str(pending_payload.get("order_id", "")))
            return

        exit_price = pending_payload.get("sell_price") or stored_trade.get("total_cost", 0)
        if fill_trade:
            for price_key in ("price", "match_price"):
                raw_price = fill_trade.get(price_key)
                if raw_price is None:
                    continue
                try:
                    exit_price = float(raw_price)
                    break
                except (TypeError, ValueError):
                    continue

        requested_size = float(pending_payload.get("shares") or 0)
        filled_size, order_marked_filled = self._parse_order_fill_state(order_payload or {})
        realized_size = filled_size if filled_size > 0 else requested_size
        if requested_size > 0:
            realized_size = min(realized_size, requested_size)
        if realized_size <= 0:
            return

        remaining_size = max(0.0, requested_size - realized_size)
        is_partial_fill = remaining_size > 0.0001 and not order_marked_filled
        buy_price = pending_payload.get("buy_price") or 0
        try:
            realized_profit = (float(exit_price) - float(buy_price)) * float(realized_size)
        except (TypeError, ValueError):
            realized_profit = 0.0

        details_text = str(stored_trade.get("details", "") or "")
        status_suffix = (
            f"⚠️ GTC 部分成交 {float(realized_size):.2f}/{float(requested_size):.2f} 股 @ {float(exit_price):.4f}"
            if is_partial_fill else f"✅ GTC 已成交 @ {float(exit_price):.4f}"
        )
        trade_db.update_trade(
            int(trade_id),
            status="pending" if is_partial_fill else "executed",
            details=f"{details_text} | {status_suffix}" if details_text else status_suffix,
        )
        trade_db.rebuild_daily_summary()

        matched_holding = self._find_pending_unwind_holding(pending_payload)
        if is_partial_fill:
            if matched_holding:
                matched_holding.shares = max(0.0, float(matched_holding.shares) - float(realized_size))
                matched_holding.amount_usd = max(0.0, float(matched_holding.buy_price) * float(matched_holding.shares))
                pending_payload["shares"] = remaining_size
                self._queue_pending_unwind(pending_payload)
            self.status.add_log(
                f"⚠️ 已確認待成交 GTC 部分成交 | {pending_payload.get('market_slug', '')} {pending_payload.get('side_label', '')} | "
                f"已成交 {float(realized_size):.2f} / 剩餘 {float(remaining_size):.2f} 股 @ {float(exit_price):.4f}"
            )
            return

        pending_holding_reason = str(pending_payload.get("holding_exit_reason", "") or "")
        if matched_holding:
            if pending_holding_reason == "tp-sniper":
                matched_holding.status = "paired"
                matched_holding.paired_with = "tp-sniper"
            elif pending_holding_reason in {"plummet", "force_liq", "stop_loss"}:
                matched_holding.status = "stopped_out"
            self._clear_holding_pending_exit(matched_holding)
            self._remove_bargain_holding(matched_holding)
        self.status.add_log(
            f"✅ 已確認待成交 GTC 成交 | {pending_payload.get('market_slug', '')} {pending_payload.get('side_label', '')} | "
            f"{float(realized_size):.2f} 股 @ {float(exit_price):.4f}"
        )
        self._remove_pending_unwind(str(pending_payload.get("order_id", "")))

    async def reconcile_pending_unwinds(self):
        pending_unwinds = self._load_pending_unwinds()
        if not pending_unwinds or self.config.dry_run:
            return

        try:
            clob_client = self._get_clob_client()
        except Exception as e:
            self.status.add_log(f"⚠️ 無法建立 CLOB 客戶端以對帳待成交 GTC: {str(e)[:120]}")
            return

        if clob_client is None:
            return

        for pending_payload in list(pending_unwinds):
            order_id = str(pending_payload.get("order_id", "") or "")
            if not order_id:
                self._remove_pending_unwind(order_id)
                continue

            matched_holding = self._find_pending_unwind_holding(pending_payload)
            order_payload = None
            try:
                order_payload = clob_client.get_order(order_id)
            except Exception as e:
                self.status.add_log(f"⚠️ 查詢 GTC 訂單 {order_id[:12]} 失敗: {str(e)[:120]}")

            filled_size, is_filled = self._parse_order_fill_state(order_payload or {})
            if is_filled or filled_size > 0:
                fill_trade = await self._find_trade_fill_for_asset(
                    clob_client,
                    str(pending_payload.get("token_id", "") or ""),
                    int(pending_payload.get("created_at_ms", 0) or 0),
                )
                self._finalize_pending_unwind_fill(pending_payload, fill_trade, order_payload)
                continue

            order_status_text = str((order_payload or {}).get("status", "")).lower()
            if order_status_text in {"live", "open", "active", "partially_filled", "partial", "partially-filled"}:
                should_cancel_stale_order, stale_reason = self._should_cancel_stale_pending_unwind(pending_payload, matched_holding)
                if should_cancel_stale_order:
                    await self._cancel_pending_unwind_order(clob_client, order_id)
                    if matched_holding:
                        self._clear_holding_pending_exit(matched_holding)
                    stored_trade = trade_db.get_trade_by_id(int(pending_payload.get("trade_id", 0) or 0))
                    if stored_trade:
                        details_text = str(stored_trade.get("details", "") or "")
                        trade_db.update_trade(
                            int(pending_payload.get("trade_id")),
                            details=f"{details_text} | ⚠️ GTC 剩餘單已清除 ({stale_reason})" if details_text else f"⚠️ GTC 剩餘單已清除 ({stale_reason})",
                        )
                        trade_db.rebuild_daily_summary()
                    self.status.add_log(f"⚠️ 待成交 GTC 剩餘單已清除: {order_id[:12]} | {stale_reason}")
                    self._remove_pending_unwind(order_id)
                continue

            if order_status_text in {"cancelled", "canceled", "expired"}:
                stored_trade = trade_db.get_trade_by_id(int(pending_payload.get("trade_id", 0) or 0))
                if stored_trade:
                    details_text = str(stored_trade.get("details", "") or "")
                    trade_db.update_trade(
                        int(pending_payload.get("trade_id")),
                        details=f"{details_text} | ⚠️ GTC 未成交 ({order_status_text})" if details_text else f"⚠️ GTC 未成交 ({order_status_text})",
                    )
                    trade_db.rebuild_daily_summary()
                self._clear_holding_pending_exit(matched_holding)
                self.status.add_log(f"⚠️ 待成交 GTC 未完成並已{order_status_text}: {order_id[:12]}")
                self._remove_pending_unwind(order_id)

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
        return self._ensure_clob_client()

    def _estimate_executable_sell_price_from_bids(self, bid_levels: List[Dict[str, float]], shares: float) -> float:
        normalized_shares = max(0.0, float(shares or 0.0))
        if normalized_shares <= 0:
            return 0.0

        remaining_shares = normalized_shares
        last_consumed_bid_price = 0.0
        for bid_level in bid_levels:
            try:
                bid_price = float(bid_level.get("price", 0) or 0)
                bid_size = float(bid_level.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if bid_price <= 0 or bid_size <= 0:
                continue
            last_consumed_bid_price = bid_price
            remaining_shares -= bid_size
            if remaining_shares <= 0:
                return bid_price

        if last_consumed_bid_price > 0:
            return last_consumed_bid_price
        return 0.0

    def _execute_buy_one_side_sync(self, clob_client, token_id: str, amount_usd: float,
                                   price: float, side_label: str) -> dict:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType, TradeParams
        from py_clob_client.order_builder.constants import BUY
        import time as _time

        estimated_shares = amount_usd / price if price > 0 else 0

        if amount_usd < 1.0:
            return {"success": False, "error": "amount below $1 minimum", "shares": 0, "price": price}

        marginal_price = clob_client.calculate_market_price(
            token_id, "BUY", amount_usd, OrderType.FOK
        )

        before_ts = int(_time.time())

        order = MarketOrderArgs(
            token_id=token_id,
            amount=amount_usd,
            side=BUY,
            price=None,
            order_type=OrderType.FOK,
        )
        signed_order = clob_client.create_market_order(order)
        response_payload = clob_client.post_order(signed_order, OrderType.FOK)

        fill_shares = 0.0
        fill_cost = 0.0
        fill_price = marginal_price
        order_id = response_payload.get("orderId") or response_payload.get("order_id") or response_payload.get("id")

        trades = []
        for _ in range(3):
            _time.sleep(0.4)
            trade_params = TradeParams(order_id=order_id) if order_id else TradeParams(asset_id=token_id, after=before_ts)
            trades = clob_client.get_trades(trade_params)
            if trades:
                break

        if trades:
            for trade_payload in trades:
                trade_size = float(trade_payload.get("size", 0))
                trade_price = float(trade_payload.get("price", 0))
                fill_shares += trade_size
                fill_cost += trade_size * trade_price
            if fill_shares > 0:
                fill_price = fill_cost / fill_shares
        else:
            fill_shares = amount_usd / marginal_price if marginal_price > 0 else estimated_shares
            fill_price = marginal_price

        return {
            "success": True,
            "response": response_payload,
            "shares": fill_shares,
            "price": fill_price,
            "marginal_price": marginal_price,
            "trades": trades,
            "estimated_shares": estimated_shares,
        }

    def _try_unwind_position_sync(self, clob_client, token_id: str, shares: float,
                                  buy_price: float, side_label: str, bid_levels: Optional[List[Dict[str, float]]] = None):
        from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs, TradeParams
        from py_clob_client.order_builder.constants import SELL
        import time as _time

        shares = math.floor(shares * 100) / 100
        if shares <= 0:
            return {"success": False, "pending": False, "order_type": None, "response": None, "log_messages": [f"  ⚠️ {side_label} 股數過小，無法平倉"]}

        log_messages: List[str] = []
        available_shares = self._get_available_conditional_balance(clob_client, token_id)
        if available_shares is not None:
            available_shares = math.floor(max(available_shares, 0.0) * 100) / 100
            if available_shares <= 0:
                return {"success": False, "pending": False, "order_type": None, "response": None, "log_messages": [f"  ⏳ {side_label} 尚無可賣餘額，等待結算中"]}
            if available_shares < shares:
                log_messages.append(
                    f"  ℹ️ {side_label} 可賣股數僅 {available_shares:.2f} / 目標 {shares:.2f}，改用可用股數平倉"
                )
                shares = available_shares

        executable_bid_price = self._estimate_executable_sell_price_from_bids(bid_levels or [], shares)
        immediate_reference_price = max(0.01, round(float(executable_bid_price or buy_price or 0.0), 2))
        log_messages.append(f"  🔥 緊急平倉 {side_label} | 賣出 {shares:.2f} 股 @ ~{immediate_reference_price:.4f}")
        if executable_bid_price > 0:
            log_messages.append(
                f"  📘 {side_label} 依 bid 深度估算可成交價 {executable_bid_price:.4f} | 原始參考 {float(buy_price or 0.0):.4f}"
            )

        sell_prices = [
            immediate_reference_price,
            round(max(immediate_reference_price - 0.01, 0.01), 2),
            round(max(immediate_reference_price - 0.03, 0.01), 2),
            0.01,
        ]
        sell_prices = list(dict.fromkeys(sell_prices))
        log_messages.append(
            f"  📉 {side_label} 即時平倉階梯: {', '.join(f'{candidate_price:.2f}' for candidate_price in sell_prices)} | 股數 {shares:.2f}"
        )

        immediate_order_attempts = [
            OrderType.FOK,
            OrderType.FOK,
            OrderType.FOK,
            OrderType.FAK,
            OrderType.FAK,
            OrderType.FAK,
        ]

        for sell_price in sell_prices:
            for order_type in immediate_order_attempts:
                try:
                    order = OrderArgs(
                        token_id=token_id,
                        price=sell_price,
                        size=shares,
                        side=SELL,
                    )
                    signed_order = clob_client.create_order(order)
                    response_payload = clob_client.post_order(signed_order, order_type)
                    log_messages.append(
                        f"  ✅ {side_label} 平倉成功 ({order_type}) @ {sell_price:.2f}: {response_payload}"
                    )
                    return {
                        "success": True,
                        "pending": False,
                        "order_type": str(order_type),
                        "response": response_payload,
                        "shares": shares,
                        "sell_price": sell_price,
                        "log_messages": log_messages,
                    }
                except Exception as e:
                    log_messages.append(
                        f"  ⚠️ {side_label} 平倉 {order_type} @ {sell_price:.2f} 失敗: {str(e)[:150]}"
                    )

            market_reference_price = max(sell_price, 0.01)
            market_sell_attempts = [
                ("shares", shares),
                ("quote_notional", round(shares * market_reference_price, 4)),
            ]
            for market_amount_mode, market_amount_value in market_sell_attempts:
                try:
                    if market_amount_value <= 0:
                        continue
                    before_ts = int(_time.time())
                    market_order = MarketOrderArgs(
                        token_id=token_id,
                        amount=market_amount_value,
                        side=SELL,
                        price=market_reference_price,
                        order_type=OrderType.FOK,
                    )
                    signed_market_order = clob_client.create_market_order(market_order)
                    market_response_payload = clob_client.post_order(signed_market_order, OrderType.FOK)
                    market_order_id = market_response_payload.get("orderId") or market_response_payload.get("order_id") or market_response_payload.get("id")
                    market_trades = []
                    for _ in range(4):
                        _time.sleep(0.4)
                        market_trade_params = TradeParams(order_id=market_order_id) if market_order_id else TradeParams(asset_id=token_id, after=before_ts)
                        market_trades = clob_client.get_trades(market_trade_params)
                        if market_trades:
                            break

                    realized_shares = 0.0
                    realized_notional = 0.0
                    for market_trade_payload in market_trades:
                        realized_trade_size = float(market_trade_payload.get("size", 0) or 0)
                        realized_trade_price = float(market_trade_payload.get("price", 0) or 0)
                        realized_shares += realized_trade_size
                        realized_notional += realized_trade_size * realized_trade_price
                    realized_sell_price = (realized_notional / realized_shares) if realized_shares > 0 else market_reference_price

                    response_status_text = str(
                        market_response_payload.get("status")
                        or market_response_payload.get("state")
                        or market_response_payload.get("orderStatus")
                        or ""
                    ).strip().lower()
                    response_indicates_success = response_status_text in {"filled", "matched", "executed", "success", "completed"}
                    shares_filled_enough = realized_shares > 0 and realized_shares >= max(0.01, shares * 0.95)
                    if not shares_filled_enough and not response_indicates_success:
                        raise RuntimeError(
                            f"market sell 未確認完整成交 | mode={market_amount_mode} | filled={realized_shares:.2f}/{shares:.2f} | status={response_status_text or 'unknown'}"
                        )

                    log_messages.append(
                        f"  ✅ {side_label} 市價平倉成功 mode={market_amount_mode} @ {realized_sell_price:.4f} | filled {max(realized_shares, shares):.2f}: {market_response_payload}"
                    )
                    return {
                        "success": True,
                        "pending": False,
                        "order_type": f"MARKET_SELL_{market_amount_mode.upper()}",
                        "response": market_response_payload,
                        "shares": realized_shares if realized_shares > 0 else shares,
                        "sell_price": realized_sell_price,
                        "log_messages": log_messages,
                    }
                except Exception as e:
                    log_messages.append(
                        f"  ⚠️ {side_label} 市價平倉失敗 mode={market_amount_mode} @ {sell_price:.2f}: {str(e)[:180]}"
                    )

            try:
                order = OrderArgs(
                    token_id=token_id,
                    price=sell_price,
                    size=shares,
                    side=SELL,
                )
                signed_order = clob_client.create_order(order)
                response_payload = clob_client.post_order(signed_order, OrderType.GTC)
                log_messages.append(
                    f"  ✅ {side_label} 平倉成功 ({OrderType.GTC}) @ {sell_price:.2f}: {response_payload}"
                )
                return {
                    "success": False,
                    "pending": True,
                    "order_type": str(OrderType.GTC),
                    "response": response_payload,
                    "shares": shares,
                    "sell_price": sell_price,
                    "log_messages": log_messages,
                }
            except Exception as e:
                log_messages.append(
                    f"  ⚠️ {side_label} 平倉 {OrderType.GTC} @ {sell_price:.2f} 失敗: {str(e)[:150]}"
                )

        log_messages.append(f"  ❌ {side_label} 所有平倉方式均失敗!")
        return {"success": False, "pending": False, "order_type": None, "response": None, "shares": shares, "sell_price": buy_price, "log_messages": log_messages}

    def _flush_merger_logs(self):
        """Forward accumulated merger logs into the main status log."""
        for entry in self.merger.logs:
            if entry not in getattr(self, '_flushed_merger_logs', set()):
                self.status.add_log(f"[merger] {entry.split('] ', 1)[-1]}")
        self._flushed_merger_logs = set(self.merger.logs)

    def _ensure_clob_client(self):
        if self._clob_client is None:
            # dry_run 不需要初始化 CLOB client
            if self.config.dry_run:
                self.status.add_log("🧪 dry_run 模式，跳過簽名客戶端初始化")
                return None

            from py_clob_client.client import ClobClient
            funder = (self.config.funder_address or "").strip()
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

    async def enforce_late_liquidation(self, markets: List[MarketInfo]):
        """強制平倉未配對持倉，避免到期歸零。"""
        threshold = getattr(self.config, "late_liquidation_seconds", 0)
        if threshold <= 0:
            return
        # 建立 slug -> market 快速查詢
        market_map = {m.slug: m for m in markets}
        remaining_holdings = []
        for h in self.status.bargain_holdings:
            if h.status != "holding" or h.market_slug not in market_map:
                remaining_holdings.append(h)
                continue
            m = market_map[h.market_slug]
            if m.time_remaining_seconds is None or m.time_remaining_seconds > threshold:
                remaining_holdings.append(h)
                continue

            # 取得最新價格，用 best_ask 作為可賣出價
            price_info = self.status.market_prices.get(m.slug)
            if not price_info:
                price_info = await self.get_prices(m)
            if not price_info:
                self.status.add_log(f"🛑 [到期平倉] {m.slug} 無法取得價格，跳過")
                remaining_holdings.append(h)
                continue

            sell_price = price_info.up_best_ask if h.side == "UP" else price_info.down_best_ask
            if sell_price <= 0:
                self.status.add_log(f"🛑 [到期平倉] {m.slug} {h.side} 無有效賣價，跳過")
                remaining_holdings.append(h)
                continue

            shares = max(round(h.shares, 2), 0)
            if shares <= 0:
                continue
            pnl = (sell_price - h.buy_price) * shares

            self.status.add_log(
                f"🛑 [到期平倉] {m.slug} {h.side} | {shares:.2f} 股 @ {sell_price:.4f} | PnL {pnl:.4f}"
            )

            status = "simulated" if self.config.dry_run else "executed"
            if not self.config.dry_run:
                try:
                    clob_client = self._get_clob_client()
                    if clob_client:
                        await self._try_unwind_position(clob_client, h.token_id, shares, sell_price, f"到期平倉-{h.side}")
                except Exception as e:
                    self.status.add_log(f"⚠️ [到期平倉] 執行失敗: {str(e)[:120]}")

            # 記錄交易
            try:
                trade_db.record_trade(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    market_slug=m.slug,
                    trade_type="late_liquidation",
                    side=h.side,
                    up_price=price_info.up_price,
                    down_price=price_info.down_price,
                    total_cost=sell_price,
                    order_size=shares,
                    profit=pnl,
                    profit_pct=(pnl / (h.buy_price * shares) * 100) if h.buy_price * shares > 0 else 0,
                    status=status,
                    details=f"到期前強制平倉 {h.side}@{sell_price:.4f} vs cost {h.buy_price:.4f}"
                )
                trade_db.rebuild_daily_summary()
            except Exception:
                pass

            h.status = "stopped_out"
            h.details = "late_liquidation"
            # 不加入 remaining_holdings，等於移除持倉

        self.status.bargain_holdings = remaining_holdings

    def check_credentials(self) -> Dict[str, Any]:
        """快速檢查簽名配置，回傳狀態與提示。dry_run=True 時跳過簽名需求。"""
        sig_type = self.config.signature_type
        pk = (self.config.private_key or "").strip()
        funder = (self.config.funder_address or "").strip()

        issues: list[str] = []
        status = "ok"

        # 在 dry_run 時，不需要任何簽名/錢包即可啟動
        if self.config.dry_run:
            return {
                "status": "ok",
                "signature_type": sig_type,
                "has_private_key": bool(pk),
                "funder_address": funder,
                "issues": ["dry_run 模式，已跳過簽名檢查"],
            }

        if sig_type == 0:
            # EOA: private key required, funder address optional
            if not pk:
                issues.append("signature_type=0 (EOA) 需要 PRIVATE_KEY")
        else:
            # Custodial/Magic/Gnosis: proxy signer key + funder address both required
            if not pk:
                issues.append("signature_type=1/2 (托管帳戶) 需要代理簽名者的 PRIVATE_KEY")
            if not funder:
                issues.append("signature_type=1/2 (托管帳戶) 需要 FUNDER_ADDRESS（Polymarket 帳戶的錢包地址）")

        # 只在有 private key 時嘗試初始化客戶端（dry_run 跳過）
        if not self.config.dry_run and pk:
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

    async def _try_buy_one_side(self, clob_client, token_id: str, amount_usd: float,
                                price: float, side_label: str) -> dict:
        """
        FOK 買入 — price 僅用於估算股數，不傳入 MarketOrderArgs
        讓 CLOB 自動從訂單簿計算真實成交價（避免限價過緊導致 FOK 失敗）
        成交後透過 get_trades 取得真實成交均價與股數
        """
        estimated_shares = amount_usd / price if price > 0 else 0

        # 確保 amount >= $1
        if amount_usd < 1.0:
            self.status.add_log(f"  ⚠️ {side_label} 金額 ${amount_usd:.2f} < $1 最低限制，跳過")
            return {"success": False, "error": "amount below $1 minimum", "shares": 0, "price": price}

        try:
            execution_result = await asyncio.to_thread(
                self._execute_buy_one_side_sync,
                clob_client,
                token_id,
                amount_usd,
                price,
                side_label,
            )
            marginal_price = float(execution_result.get("marginal_price", price) or price)
            self.status.add_log(
                f"  📖 {side_label} 訂單簿邊際價={marginal_price:.4f} | "
                f"${amount_usd:.2f} (估算: {estimated_shares:.2f}股 @ {price:.4f})"
            )
        except Exception as e:
            error_text = str(e)[:120]
            self.status.add_log(f"  ⚠️ {side_label} FOK 失敗: {error_text}")
            return {"success": False, "error": error_text, "shares": 0, "price": price}

        response_payload = execution_result.get("response") or {}
        self.status.add_log(f"  📋 {side_label} post_order 回應: {str(response_payload)[:200]}")

        fill_shares = float(execution_result.get("shares", 0.0) or 0.0)
        fill_price = float(execution_result.get("price", marginal_price) or marginal_price)
        trades = execution_result.get("trades") or []
        fill_cost = fill_shares * fill_price

        if trades:
            self.status.add_log(
                f"  ✅ {side_label} 實際成交 | {fill_shares:.2f} 股 @ 均價 {fill_price:.4f} "
                f"(${fill_cost:.2f}) | {len(trades)} 筆成交"
            )
        else:
            self.status.add_log(
                f"  ⚠️ {side_label} 未取得成交記錄，使用估算: {fill_shares:.2f} 股 @ {fill_price:.4f}"
            )

        return {"success": True, "response": response_payload, "shares": fill_shares, "price": fill_price}

    async def _try_unwind_position(self, clob_client, token_id: str, shares: float,
                                   buy_price: float, side_label: str, bid_levels: Optional[List[Dict[str, float]]] = None):
        """
        緊急平倉：賣出已買入的一側代幣以避免單邊風險
        注意: MarketOrderArgs + create_market_order 對 SELL 有 bug（price 驗證失敗）
        改用 OrderArgs + create_order 限價賣單
        嘗試順序: 每個價格依序嘗試 FOK → FAK → GTC
        """
        unwind_result = await asyncio.to_thread(
            self._try_unwind_position_sync,
            clob_client,
            token_id,
            shares,
            buy_price,
            side_label,
            bid_levels,
        )
        for log_message in unwind_result.get("log_messages", []):
            self.status.add_log(log_message)
        return unwind_result

    def _get_available_conditional_balance(self, clob_client, token_id: str) -> Optional[float]:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        try:
            balance_response = clob_client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
        except Exception as e:
            self.status.add_log(f"  ⚠️ 查詢可賣餘額失敗: {str(e)[:120]}")
            return None

        if not isinstance(balance_response, dict):
            self.status.add_log(f"  ⚠️ 可賣餘額回應格式異常: {str(balance_response)[:120]}")
            return None

        candidate_keys = [
            "balance",
            "available",
            "available_balance",
            "availableBalance",
            "asset_balance",
            "assetBalance",
        ]
        for candidate_key in candidate_keys:
            raw_balance = balance_response.get(candidate_key)
            if raw_balance is None:
                continue
            try:
                parsed_balance = float(raw_balance)
            except (TypeError, ValueError):
                continue
            if parsed_balance > 1000:
                parsed_balance = parsed_balance / 1_000_000
            return parsed_balance

        nested_balance = balance_response.get("balance")
        if isinstance(nested_balance, dict):
            for candidate_key in ("available", "balance", "value"):
                raw_balance = nested_balance.get(candidate_key)
                if raw_balance is None:
                    continue
                try:
                    parsed_balance = float(raw_balance)
                except (TypeError, ValueError):
                    continue
                if parsed_balance > 1000:
                    parsed_balance = parsed_balance / 1_000_000
                return parsed_balance

        self.status.add_log(f"  ⚠️ 無法解析可賣餘額: {str(balance_response)[:120]}")
        return None

    def _convert_orphan_to_bargain(self, market: 'MarketInfo', side: str,
                                    token_id: str, complement_token_id: str,
                                    buy_price: float, shares: float, amount_usd: float):
        """
        平倉失敗時，將孤兒持倉轉入撿便宜策略繼續配對，
        而非要求使用者手動處理。
        """
        if self._is_market_plummet_blocked(getattr(market, "slug", "")):
            self.status.add_log(
                f"⛔ [孤兒轉撿便宜] 略過 {getattr(market, 'slug', '')} {side} | 市場已因急跌護欄封鎖"
            )
            return None
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

                first_result = await self._try_buy_one_side(
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
                        first_result = await self._try_buy_one_side(
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
                            unwind_result = await self._try_unwind_position(
                                clob_client, first_token, unwind_shares,
                                first_result.get("price", first_price), first_label
                            )
                            unwind_ok = bool(unwind_result.get("success") or unwind_result.get("pending"))
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

                second_result = await self._try_buy_one_side(
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
                        unwind_result = await self._try_unwind_position(
                            clob_client, first_token, unwind_shares,
                            first_result.get("price", first_price), first_label
                        )
                        unwind_ok = bool(unwind_result.get("success") or unwind_result.get("pending"))
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
                self._flush_merger_logs()
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

    def _compute_dynamic_price_bounds(self, market: MarketInfo,
                                      base_min: float,
                                      base_max: float) -> tuple[float, float]:
        """
        計算撿便宜動態價格區間。
        若尚未啟用/計算動態邊界，則退回靜態設定值。
        """
        safe_base_min = max(0.0, float(base_min or 0.0))
        safe_base_max = max(safe_base_min, float(base_max or safe_base_min))

        status_dynamic_min_bound = getattr(self.status, "dynamic_bargain_min_bound", None)
        status_dynamic_max_bound = getattr(self.status, "dynamic_bargain_max_bound", None)
        status_dynamic_min_price = getattr(self.status, "dynamic_bargain_min_price", None)
        status_dynamic_max_price = getattr(self.status, "dynamic_bargain_max_price", None)

        computed_min_bound = safe_base_min
        computed_max_bound = safe_base_max

        if status_dynamic_min_bound is not None:
            try:
                computed_min_bound = max(safe_base_min, float(status_dynamic_min_bound))
            except (TypeError, ValueError):
                computed_min_bound = safe_base_min
        elif status_dynamic_min_price is not None:
            try:
                computed_min_bound = max(safe_base_min, float(status_dynamic_min_price))
            except (TypeError, ValueError):
                computed_min_bound = safe_base_min

        if status_dynamic_max_bound is not None:
            try:
                computed_max_bound = min(safe_base_max, float(status_dynamic_max_bound))
            except (TypeError, ValueError):
                computed_max_bound = safe_base_max
        elif status_dynamic_max_price is not None:
            try:
                computed_max_bound = min(safe_base_max, float(status_dynamic_max_price))
            except (TypeError, ValueError):
                computed_max_bound = safe_base_max

        market_time_remaining_seconds = getattr(market, "time_remaining_seconds", None)
        if market_time_remaining_seconds is not None and market_time_remaining_seconds <= 0:
            return computed_min_bound, computed_max_bound

        if computed_max_bound < computed_min_bound:
            computed_max_bound = computed_min_bound

        return computed_min_bound, computed_max_bound

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

    def _find_blocking_unpaired_bargain_holding(self, target_market_slug: str) -> Optional[BargainHolding]:
        for holding in self.status.bargain_holdings:
            if holding.status != "holding":
                continue
            if holding.market_slug == target_market_slug:
                continue
            if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                self._clear_stale_blocking_pending_exit(holding, "撿便宜跨市場阻塞檢查")
            if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                continue

            holding_market = getattr(holding, "market", None)
            holding_time_remaining = getattr(holding_market, "time_remaining_seconds", None)
            if holding_time_remaining is not None and holding_time_remaining <= 0:
                continue

            return holding
        return None

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

        market_pool: Dict[str, MarketInfo] = {m.slug: m for m in markets}
        active_slugs = set(market_pool.keys())
        for holding in self.status.bargain_holdings:
            if holding.status == "holding" and holding.market and holding.market.slug not in market_pool:
                market_pool[holding.market.slug] = holding.market

        for market in market_pool.values():
            if self._is_market_plummet_blocked(getattr(market, "slug", "")):
                continue
            if not market.up_token_id or not market.down_token_id:
                continue

            force_refresh = market.slug not in active_slugs
            price_info = None if force_refresh else self.status.market_prices.get(market.slug)
            if not price_info:
                price_info = await self.get_prices(market)
                if not price_info:
                    continue
                self.status.market_prices[market.slug] = price_info

            self._populate_price_context(market, price_info)

            underlying_symbol = str(price_info.underlying_symbol or "").strip().upper()
            if underlying_symbol == "BTC" and bool(getattr(self.config, "price_edge_distance_gate_enabled_btc", True)):
                btc_min_distance_usd = float(getattr(self.config, "price_edge_min_distance_usd_btc", 70.0) or 70.0)
                btc_decay_start_seconds = max(1, int(getattr(self.config, "price_edge_distance_decay_start_seconds_btc", 300) or 300))
                btc_floor_multiplier = float(getattr(self.config, "price_edge_distance_floor_multiplier_btc", 0.5) or 0.5)
                btc_floor_multiplier = min(1.0, max(0.05, btc_floor_multiplier))
                btc_distance_to_reference = price_info.distance_to_reference
                market_time_remaining_seconds = max(0.0, float(getattr(market, "time_remaining_seconds", 0.0) or 0.0))
                if market_time_remaining_seconds >= btc_decay_start_seconds:
                    btc_effective_distance_usd = btc_min_distance_usd
                else:
                    btc_time_progress_ratio = market_time_remaining_seconds / float(btc_decay_start_seconds)
                    btc_effective_multiplier = btc_floor_multiplier + ((1.0 - btc_floor_multiplier) * (btc_time_progress_ratio ** 2))
                    btc_effective_distance_usd = btc_min_distance_usd * btc_effective_multiplier
                if btc_distance_to_reference is None or abs(btc_distance_to_reference) < btc_effective_distance_usd:
                    if self.config.dry_run:
                        if self.status.scan_count % 5 == 0:
                            btc_distance_text = "--" if btc_distance_to_reference is None else f"${abs(btc_distance_to_reference):.2f}"
                            self.status.add_log(
                                f"🧪 BTC 撿便宜放行(dry_run) | {market.slug} | 現貨差 {btc_distance_text} < RTDS 門檻 ${btc_effective_distance_usd:.2f}"
                            )
                    else:
                        if self.status.scan_count % 5 == 0:
                            btc_distance_text = "--" if btc_distance_to_reference is None else f"${abs(btc_distance_to_reference):.2f}"
                            self.status.add_log(
                                f"🚫 BTC 撿便宜封鎖 | {market.slug} | 現貨差 {btc_distance_text} < RTDS 門檻 ${btc_effective_distance_usd:.2f}"
                            )
                        continue

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
                    wait_minutes = (datetime.now(timezone.utc) - created_at).total_seconds() / 60
                    esc_minutes = getattr(self.config, "bargain_pair_escalation_minutes", 0)
                    if esc_minutes > 0 and wait_minutes >= esc_minutes:
                        escalation = 0.05
                        self.status.add_log(
                            f" ⏫ R{unpaired.round} 配對加價 +$0.05 (等待 {wait_minutes:.0f} 分鐘)"
                        )
                except Exception:
                    pass

                dyn_min, dyn_max = self._compute_dynamic_price_bounds(
                    market,
                    base_min=self.BARGAIN_MIN_PRICE,
                    base_max=self.BARGAIN_PAIR_THRESHOLD
                )

                if unpaired.side == "UP":
                    held_price = unpaired.buy_price
                    if held_price <= 0:
                        continue
                    target_price = self.BARGAIN_PAIR_THRESHOLD - held_price + escalation
                    if down_ask >= dyn_min and down_ask <= min(target_price + 1e-4, dyn_max):
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
                    held_price = unpaired.buy_price
                    if held_price <= 0:
                        continue
                    target_price = self.BARGAIN_PAIR_THRESHOLD - held_price + escalation
                    if up_ask >= dyn_min and up_ask <= min(target_price + 1e-4, dyn_max):
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
                if self._bargain_trades_remaining(market.slug) <= 0:
                    continue
                next_round = stack["round"] + 1
                if next_round > self.config.bargain_max_rounds:
                    continue  # 已達堆疊上限

                window_limit = self.status.dynamic_bargain_window_seconds or self.config.bargain_open_time_window_seconds
                if market.time_remaining_seconds is not None and market.time_remaining_seconds > window_limit:
                    continue

                price_ceiling = stack["last_buy_price"]

                # 第一輪用 price_threshold 作為天花板
                if stack["round"] == 0:
                    price_ceiling = self.BARGAIN_PRICE_THRESHOLD

                dyn_min, dyn_max = self._compute_dynamic_price_bounds(
                    market,
                    base_min=self.BARGAIN_MIN_PRICE,
                    base_max=price_ceiling,
                )

                near_entry_margin = 0.02
                nearest_entry_ask = min(up_ask, down_ask)
                should_log_near_entry = nearest_entry_ask <= (dyn_max + near_entry_margin)
                if should_log_near_entry and self.status.scan_count % 3 == 0:
                    self.status.add_log(
                        f"🔎 [撿便宜檢查] {market.slug} | bounds {dyn_min:.4f}-{dyn_max:.4f} | asks UP={up_ask:.4f} DOWN={down_ask:.4f} | round={next_round} ceiling={price_ceiling:.4f}"
                    )

                candidates = []
                if (up_ask >= dyn_min and up_ask < dyn_max):
                    candidates.append(("UP", up_ask, market.up_token_id, market.down_token_id))
                if (down_ask >= dyn_min and down_ask < dyn_max):
                    candidates.append(("DOWN", down_ask, market.down_token_id, market.up_token_id))

                if should_log_near_entry and self.status.scan_count % 3 == 0:
                    if candidates:
                        candidate_summary = ", ".join(f"{candidate_side}@{candidate_price:.4f}" for candidate_side, candidate_price, _, _ in candidates)
                        self.status.add_log(
                            f"✅ [撿便宜候選] {market.slug} | {candidate_summary}"
                        )
                    else:
                        self.status.add_log(
                            f"🚫 [撿便宜候選] {market.slug} | 無 side 落在有效區間 {dyn_min:.4f}-{dyn_max:.4f}"
                        )

                if candidates:
                    # R1 開倉: 套用偏好方向（或依速度趨勢動態偏好），仍須滿足閾值與天花板
                    bias = self.config.bargain_first_buy_bias.upper()
                    if bias == "AUTO":
                        trend_bias = self.status.velocity_trend
                        if trend_bias in ("up", "down"):
                            bias = trend_bias.upper()
                    if next_round == 1 and bias in ("UP", "DOWN"):
                        biased = [c for c in candidates if c[0] == bias]
                        if biased:
                            candidates = biased
                            if should_log_near_entry and self.status.scan_count % 3 == 0:
                                self.status.add_log(
                                    f"🎯 [撿便宜偏好] {market.slug} | 採用偏好側 {bias}"
                                )
                        else:
                            self.status.add_log(
                                f"🏷️ [撿便宜R1] {market.slug} 偏好側 {bias} 未達條件，改買另一側 {candidates[0][0]}"
                            )
                    # 買最便宜的那側（或偏好側）
                    candidates.sort(key=lambda c: c[1])
                    side, ask, token_id, comp_id = candidates[0]
                    if should_log_near_entry and self.status.scan_count % 3 == 0:
                        self.status.add_log(
                            f"🛒 [撿便宜準備下單] {market.slug} | 選擇 {side} @ {ask:.4f}"
                        )
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

        if self._is_market_plummet_blocked(getattr(market, "slug", "")):
            self.status.add_log(f"⛔ [撿便宜] 略過 {market.slug} {side} | 市場已因急跌護欄封鎖")
            return None

        # 即時檢查: 非配對開倉時，若其他市場有未配對持倉則跳過（防止跨市場重複開倉）
        if not is_pairing:
            blocking_holding = self._find_blocking_unpaired_bargain_holding(market.slug)
            if blocking_holding:
                self.status.add_log(
                    f"🏷️ [撿便宜] 跳過 {market.slug} {side} — 其他市場仍有未配對持倉 {blocking_holding.market_slug} {blocking_holding.side} @ {blocking_holding.buy_price:.4f}"
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
                    self._flush_merger_logs()
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
            market_time_remaining = getattr(holding.market, "time_remaining_seconds", None)
            if market_time_remaining is not None and market_time_remaining <= 0:
                holding.pending_exit_order_id = None
                holding.pending_exit_reason = None
                holding.pending_exit_trade_id = None
                holding.status = "stopped_out"
                self.status.add_log(
                    f"⌛ [到期清理] {holding.market_slug} {holding.side} 已到期，從持倉列表移除"
                )
                continue

            price_info = await self.get_prices(holding.market)
            if not price_info:
                continue

            if holding.side == "UP":
                current_price = price_info.up_best_bid if price_info.up_best_bid > 0 else (price_info.up_best_ask if price_info.up_best_ask > 0 else price_info.up_price)
            else:
                current_price = price_info.down_best_bid if price_info.down_best_bid > 0 else (price_info.down_best_ask if price_info.down_best_ask > 0 else price_info.down_price)

            if holding.buy_price > 0:
                now_datetime = datetime.now(timezone.utc)
                now_iso = now_datetime.isoformat()
                plummet_window_seconds = max(1, int(getattr(self.config, "bargain_plummet_window_seconds", 15) or 15))

                window_start_datetime = None
                raw_window_start_timestamp = getattr(holding, "plummet_window_start_ts", None)
                if raw_window_start_timestamp:
                    try:
                        parsed_window_start_datetime = datetime.fromisoformat(str(raw_window_start_timestamp))
                        if parsed_window_start_datetime.tzinfo is None:
                            parsed_window_start_datetime = parsed_window_start_datetime.replace(tzinfo=timezone.utc)
                        window_start_datetime = parsed_window_start_datetime
                    except Exception:
                        window_start_datetime = None

                if (
                    window_start_datetime is None
                    or (now_datetime - window_start_datetime).total_seconds() >= plummet_window_seconds
                ):
                    holding.plummet_window_start_ts = now_iso
                    holding.plummet_high_price = float(current_price)
                else:
                    holding.plummet_high_price = max(float(holding.plummet_high_price or 0.0), float(current_price))

                plummet_exit_pct = float(getattr(self.config, "bargain_plummet_exit_pct", 0.0) or 0.0)
                if plummet_exit_pct > 0:
                    plummet_trigger_seconds = max(0, int(getattr(self.config, "bargain_plummet_trigger_seconds", 0) or 0))
                    if plummet_trigger_seconds > 0:
                        market_time_remaining = getattr(holding.market, "time_remaining_seconds", None)
                        if market_time_remaining is None or float(market_time_remaining) > plummet_trigger_seconds:
                            holding.plummet_last_price = current_price
                            holding.plummet_last_ts = now_iso
                            continue

                    reference_high_price = max(float(holding.plummet_high_price or 0.0), float(holding.buy_price or 0.0))
                    if reference_high_price <= 0:
                        holding.plummet_last_price = current_price
                        holding.plummet_last_ts = now_iso
                        continue

                    drop_pct = (reference_high_price - current_price) / reference_high_price * 100
                    if drop_pct >= plummet_exit_pct:
                        self._mark_market_plummet_blocked(
                            holding.market_slug,
                            f"{holding.side} 自高點 {reference_high_price:.4f} 跌 {drop_pct:.1f}% ≥ {plummet_exit_pct:.1f}%"
                        )
                        self.status.add_log(
                            f"⚡ [急跌護欄] {holding.market_slug} {holding.side} 自高點 {reference_high_price:.4f} 跌 {drop_pct:.1f}% ≥ {plummet_exit_pct:.1f}% → 立刻平倉"
                        )
                        if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                            self._clear_stale_blocking_pending_exit(holding, "急跌護欄")
                        if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                            pending_exit_label = holding.pending_exit_order_id[:12] if holding.pending_exit_order_id else str(holding.pending_exit_reason or "settlement_pending")
                            self.status.add_log(f"⚡ [急跌護欄] 已有待成交退出狀態 {pending_exit_label}，略過重複掛單")
                            continue
                        unwind_result = {"success": True, "pending": False, "order_type": None, "response": None}
                        if self.config.dry_run:
                            holding.status = "stopped_out"
                        else:
                            try:
                                clob_client = self._get_clob_client()
                                holding_bid_levels = price_info.up_bids if holding.side == "UP" else price_info.down_bids
                                unwind_result = await self._try_unwind_position(
                                    clob_client, holding.token_id, holding.shares,
                                    current_price, "Plummet guard", holding_bid_levels
                                )
                                if unwind_result.get("success"):
                                    holding.status = "stopped_out"
                                    self._clear_holding_pending_exit(holding)
                                elif unwind_result.get("pending"):
                                    pending_response = unwind_result.get("response") or {}
                                    holding.pending_exit_order_id = str(
                                        pending_response.get("orderID")
                                        or pending_response.get("orderId")
                                        or pending_response.get("id")
                                        or pending_response.get("order_id")
                                        or ""
                                    )
                                    holding.pending_exit_reason = "plummet"
                                    record = TradeRecord(
                                        timestamp=datetime.now(timezone.utc).isoformat(),
                                        market_slug=holding.market_slug,
                                        up_price=price_info.up_price,
                                        down_price=price_info.down_price,
                                        total_cost=current_price,
                                        order_size=holding.shares,
                                        expected_profit=0,
                                        profit_pct=0,
                                        status="pending",
                                        details=f"⚡ 急跌護欄 {holding.side} 已掛出 GTC，待成交",
                                    )
                                    record.pending_unwind_result = dict(unwind_result)
                                    record.pending_unwind_token_id = holding.token_id
                                    record.pending_unwind_side_label = holding.side
                                    record.pending_unwind_shares = unwind_result.get("shares", holding.shares)
                                    record.pending_unwind_buy_price = holding.buy_price
                                    record.pending_unwind_sell_price = unwind_result.get("sell_price", current_price)
                                    record.pending_unwind_holding_timestamp = holding.timestamp
                                    record.pending_unwind_holding_reason = "plummet"
                                    trade_id = trade_db.record_trade(
                                        timestamp=record.timestamp,
                                        market_slug=record.market_slug,
                                        trade_type="bargain_plummet",
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
                                    self.status.trade_history.append(record)
                                    holding.pending_exit_trade_id = trade_id
                                    self._register_pending_unwind_trade(
                                        record,
                                        trade_id,
                                        holding.market,
                                        holding.token_id,
                                        holding.side,
                                        holding.shares,
                                        holding.buy_price,
                                        unwind_result,
                                    )
                                    self.status.add_log("⚡ [急跌護欄] 已掛出 GTC，待成交後才算完成")
                                else:
                                    self._clear_holding_pending_exit(holding)
                                    if any("等待結算中" in log_message for log_message in unwind_result.get("log_messages", [])):
                                        self.status.add_log("⚡ [急跌護欄] 尚無可賣餘額，等待結算後重試")
                                    self.status.add_log("⚡ [急跌護欄失敗] 賣單未成交")
                            except Exception as e:
                                unwind_result = {"success": False, "pending": False, "order_type": None, "response": None}
                                self._clear_holding_pending_exit(holding)
                                self.status.add_log(f"⚡ [急跌護欄異常] {str(e)[:120]}")
                        if self.config.dry_run or unwind_result.get("success"):
                            pnl = (current_price - holding.buy_price) * holding.shares
                            record = TradeRecord(
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                market_slug=holding.market_slug,
                                up_price=price_info.up_price,
                                down_price=price_info.down_price,
                                total_cost=current_price,
                                order_size=holding.shares,
                                expected_profit=pnl,
                                profit_pct=(pnl / (holding.buy_price * holding.shares) * 100) if holding.buy_price > 0 else 0,
                                status="executed" if (unwind_result.get("success") and not self.config.dry_run) else "simulated",
                                details=f"⚡ 急跌護欄 {holding.side} 自高點 {reference_high_price:.4f} 跌 {drop_pct:.1f}%",
                            )
                            self.status.trade_history.append(record)
                            self.status.total_trades += 1
                            self.status.increment_trades_for_market(holding.market_slug)
                            self.status.total_profit += record.expected_profit
                            try:
                                trade_db.record_trade(
                                    timestamp=record.timestamp,
                                    market_slug=record.market_slug,
                                    trade_type="bargain_plummet",
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
                        continue

                holding.plummet_last_price = current_price
                holding.plummet_last_ts = now_iso

                secondary_exit_profit_pct = float(getattr(self.config, "bargain_secondary_exit_profit_pct", 0.0) or 0.0)
                if secondary_exit_profit_pct > 0:
                    profit_pct_now = (current_price - holding.buy_price) / holding.buy_price * 100
                    if profit_pct_now >= secondary_exit_profit_pct:
                        self.status.add_log(
                            f"🎯 [二次出場] {holding.market_slug} {holding.side} 利潤 {profit_pct_now:.2f}% ≥ {secondary_exit_profit_pct:.2f}% → 嘗試直接賣出"
                        )
                        if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                            self._clear_stale_blocking_pending_exit(holding, "二次出場")
                        if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                            pending_exit_label = holding.pending_exit_order_id[:12] if holding.pending_exit_order_id else str(holding.pending_exit_reason or "settlement_pending")
                            self.status.add_log(f"🎯 [二次出場] 已有待成交退出狀態 {pending_exit_label}，略過重複掛單")
                            continue
                        unwind_result = {"success": True, "pending": False, "order_type": None, "response": None}
                        if self.config.dry_run:
                            holding.status = "paired"
                            holding.paired_with = "tp-sniper"
                        else:
                            try:
                                clob_client = self._get_clob_client()
                                holding_bid_levels = price_info.up_bids if holding.side == "UP" else price_info.down_bids
                                unwind_result = await self._try_unwind_position(
                                    clob_client, holding.token_id, holding.shares,
                                    current_price, "TP sniper", holding_bid_levels
                                )
                                if unwind_result.get("success"):
                                    holding.status = "paired"
                                    holding.paired_with = "tp-sniper"
                                    self._clear_holding_pending_exit(holding)
                                elif unwind_result.get("pending"):
                                    pending_response = unwind_result.get("response") or {}
                                    holding.pending_exit_order_id = str(
                                        pending_response.get("orderID")
                                        or pending_response.get("orderId")
                                        or pending_response.get("id")
                                        or pending_response.get("order_id")
                                        or ""
                                    )
                                    holding.pending_exit_reason = "tp-sniper"
                                    record = TradeRecord(
                                        timestamp=datetime.now(timezone.utc).isoformat(),
                                        market_slug=holding.market_slug,
                                        up_price=price_info.up_price,
                                        down_price=price_info.down_price,
                                        total_cost=current_price,
                                        order_size=holding.shares,
                                        expected_profit=0,
                                        profit_pct=0,
                                        status="pending",
                                        details=f"🎯 二次出場 {holding.side} 已掛出 GTC，待成交",
                                    )
                                    record.pending_unwind_result = dict(unwind_result)
                                    record.pending_unwind_token_id = holding.token_id
                                    record.pending_unwind_side_label = holding.side
                                    record.pending_unwind_shares = unwind_result.get("shares", holding.shares)
                                    record.pending_unwind_buy_price = holding.buy_price
                                    record.pending_unwind_sell_price = unwind_result.get("sell_price", current_price)
                                    record.pending_unwind_holding_timestamp = holding.timestamp
                                    record.pending_unwind_holding_reason = "tp-sniper"
                                    trade_id = trade_db.record_trade(
                                        timestamp=record.timestamp,
                                        market_slug=record.market_slug,
                                        trade_type="bargain_tp",
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
                                    self.status.trade_history.append(record)
                                    holding.pending_exit_trade_id = trade_id
                                    self._register_pending_unwind_trade(
                                        record,
                                        trade_id,
                                        holding.market,
                                        holding.token_id,
                                        holding.side,
                                        holding.shares,
                                        holding.buy_price,
                                        unwind_result,
                                    )
                                    self.status.add_log("🎯 [二次出場] 已掛出 GTC，待成交後才算完成")
                                else:
                                    self._clear_holding_pending_exit(holding)
                                    self.status.add_log("🎯 [二次出場失敗] 賣單未成交")
                            except Exception as e:
                                unwind_result = {"success": False, "pending": False, "order_type": None, "response": None}
                                self._clear_holding_pending_exit(holding)
                                self.status.add_log(f"🎯 [二次出場異常] {str(e)[:120]}")
                        if self.config.dry_run or unwind_result.get("success"):
                            pnl = (current_price - holding.buy_price) * holding.shares
                            record = TradeRecord(
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                market_slug=holding.market_slug,
                                up_price=price_info.up_price,
                                down_price=price_info.down_price,
                                total_cost=current_price,
                                order_size=holding.shares,
                                expected_profit=pnl,
                                profit_pct=(pnl / (holding.buy_price * holding.shares) * 100) if holding.buy_price > 0 else 0,
                                status="executed" if (unwind_result.get("success") and not self.config.dry_run) else "simulated",
                                details=f"🎯 二次出場 {holding.side} 利潤 {profit_pct_now:.2f}%",
                            )
                            self.status.trade_history.append(record)
                            self.status.total_trades += 1
                            self.status.increment_trades_for_market(holding.market_slug)
                            self.status.total_profit += record.expected_profit
                            try:
                                trade_db.record_trade(
                                    timestamp=record.timestamp,
                                    market_slug=record.market_slug,
                                    trade_type="bargain_tp",
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
                        continue

            if holding.round <= self.config.bargain_stop_loss_immune_rounds:
                if self.status.scan_count % 10 == 0:
                    self.status.add_log(
                        f"🛡️ [R{holding.round}] {holding.side} 免止損 (≤R{self.config.bargain_stop_loss_immune_rounds}) | "
                        f"買入: {holding.buy_price:.4f} 現價: {current_price:.4f} | 等待配對"
                    )
                continue

            price_drop = holding.buy_price - current_price
            if price_drop >= self.BARGAIN_STOP_LOSS_CENTS:
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
                MIN_MARKET_TIME_FOR_DEFER = defer_seconds + 5 * 60

                if holding_age < defer_seconds and market_remaining > MIN_MARKET_TIME_FOR_DEFER:
                    defer_remaining = int(defer_seconds - holding_age)
                    self.status.add_log(
                        f"⏳ [R{holding.round}] {holding.side} 跌 {price_drop:.4f} 達止損線，"
                        f"但持倉僅 {int(holding_age)}s，延遲 {defer_remaining}s 後再止損"
                    )
                    continue

                if self.config.dry_run:
                    self.status.add_log(
                        f"🛑 [模擬止損] 賣出 {holding.shares:.1f} 股 {holding.side} @ ~{current_price:.4f}"
                    )
                    holding.status = "stopped_out"
                else:
                    try:
                        if holding.pending_exit_order_id or holding.pending_exit_reason or holding.pending_exit_trade_id:
                            self._clear_stale_blocking_pending_exit(holding, "止損")
                        clob_client = self._get_clob_client()
                        holding_bid_levels = price_info.up_bids if holding.side == "UP" else price_info.down_bids
                        unwind_result = await self._try_unwind_position(
                            clob_client, holding.token_id, holding.shares,
                            current_price, f"止損R{holding.round}-{holding.side}", holding_bid_levels
                        )
                        unwind_success = bool(isinstance(unwind_result, dict) and unwind_result.get("success"))
                        unwind_pending = bool(isinstance(unwind_result, dict) and unwind_result.get("pending"))
                        if unwind_success:
                            holding.status = "stopped_out"
                            self._clear_holding_pending_exit(holding)
                        elif unwind_pending:
                            holding.status = "stopped_out"
                            if isinstance(unwind_result, dict):
                                pending_order_id = str((unwind_result.get("response") or {}).get("orderID") or (unwind_result.get("response") or {}).get("orderId") or (unwind_result.get("response") or {}).get("id") or (unwind_result.get("response") or {}).get("order_id") or "")
                                if pending_order_id:
                                    holding.pending_exit_order_id = pending_order_id
                                    holding.pending_exit_reason = "stop_loss"
                            self.status.add_log(f"🛑 [止損成功] {holding.side} 已賣出")
                        else:
                            self._clear_holding_pending_exit(holding)
                            if any("等待結算中" in log_message for log_message in unwind_result.get("log_messages", [])):
                                self.status.add_log(f"🛑 [止損] {holding.side} 尚無可賣餘額，等待結算後重試")
                            self.status.add_log(f"🛑 [止損失敗] {holding.side} 需手動處理!")
                    except Exception as e:
                        self.status.add_log(f"🛑 [止損異常] {str(e)[:120]}")

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

                try:
                    recorded_trade_id = trade_db.record_trade(
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
                    if not self.config.dry_run and 'unwind_result' in locals() and isinstance(unwind_result, dict) and (unwind_result.get("success") or unwind_result.get("pending")):
                        record.pending_unwind_shares = holding.shares
                        record.pending_unwind_buy_price = holding.buy_price
                        record.pending_unwind_sell_price = current_price
                        record.pending_unwind_holding_timestamp = holding.timestamp
                        record.pending_unwind_holding_reason = "stop_loss"
                        holding.pending_exit_trade_id = int(recorded_trade_id or 0) if recorded_trade_id is not None else None
                        self._register_pending_unwind_trade(record, int(recorded_trade_id or 0), holding.market, holding.token_id, holding.side, holding.shares, holding.buy_price, unwind_result)
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
            self._ensure_clob_client()
            self.status.add_log("✅ CLOB API 憑證已驗證/註冊")
        except Exception as e:
            self.status.add_log(f"⚠️ CLOB 啟動連線失敗: {e}")
        self._approvals_ok = self.ensure_approvals()

    def ensure_approvals(self) -> bool:
        """確保 EOA 錢包已對 Polymarket 合約設定 USDC 和 CTF ERC-1155 授權。dry_run 時跳過。"""
        if self.config.dry_run:
            return True
        if self.config.signature_type in (1, 2):
            self.status.add_log("ℹ️ sig_type=1/2 (托管帳戶)，跳過自動授權 — Polymarket 帳戶由平台管理授權，無需手動設定")
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
                return False
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
        """執行最小測試單（$1 買入後立即平倉）驗證錢包連線。dry_run 時跳過。"""
        if self.config.dry_run:
            self.status.add_log("🧪 dry_run 模式，跳過連線測試")
            return
        if not getattr(self, '_approvals_ok', True):
            self.status.add_log("⚠️ 授權未完成，跳過連線測試以避免遺留持倉")
            return
        import hashlib
        import trade_db
        _wallet_key = hashlib.sha256(
            f"{self.config.private_key}:{self.config.funder_address}".encode()
        ).hexdigest()[:16]
        _kv_key = f"conn_tested:{_wallet_key}"
        if trade_db.kv_get(_kv_key) == "ok":
            self.status.add_log("✅ 此錢包已通過連線測試，跳過重複測試")
            return
        self.status.add_log("🔌 開始連線測試（$1 測試單）...")
        clob = self._ensure_clob_client()
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
        shares = math.floor(shares)
        if shares < 1:
            self.status.add_log("⚠️ 測試買入股數不足 1，無法平倉")
            return
        self.status.add_log(f"✅ 測試買入成功 {shares} 股 @ {buy_price:.4f}，等待結算後平倉...")
        await asyncio.sleep(8)
        unwind_result = await self._try_unwind_position(clob, token_id, shares, buy_price, f"[測試]{side}")
        if unwind_result.get("success") or unwind_result.get("pending"):
            trade_db.kv_set(_kv_key, "ok")
            self.status.add_log("✅ 連線測試完成：買入+平倉成功，錢包連線正常")
        else:
            self.status.add_log("⚠️ 連線測試：買入成功但平倉失敗，請手動檢查持倉")
