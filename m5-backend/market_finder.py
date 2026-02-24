"""
市場搜尋器 - 負責找到 Polymarket 上的 5 分鐘加密貨幣市場
"""
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from config import BotConfig


class MarketInfo:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.id = raw.get("id", "")
        self.question = raw.get("question", "")
        self.slug = raw.get("slug", "")
        self.end_date = raw.get("endDate", "")
        self.active = raw.get("active", False)
        self.closed = raw.get("closed", False)
        self.accepting_orders = raw.get("acceptingOrders", False)
        self.outcomes = raw.get("outcomes", "")
        self.outcome_prices = raw.get("outcomePrices", "")
        self.condition_id = raw.get("conditionId", "")
        self.clob_token_ids = raw.get("clobTokenIds", "")
        self.volume = raw.get("volume", "0")
        self.liquidity = raw.get("liquidity", "0")

    @property
    def token_ids(self) -> List[str]:
        if isinstance(self.clob_token_ids, str):
            import json
            try:
                return json.loads(self.clob_token_ids)
            except:
                return self.clob_token_ids.strip("[]").replace('"', '').split(",")
        return self.clob_token_ids or []

    @property
    def up_token_id(self) -> Optional[str]:
        ids = self.token_ids
        return ids[0] if len(ids) >= 2 else None

    @property
    def down_token_id(self) -> Optional[str]:
        ids = self.token_ids
        return ids[1] if len(ids) >= 2 else None

    @property
    def end_datetime(self) -> Optional[datetime]:
        if self.end_date:
            try:
                return datetime.fromisoformat(self.end_date.replace("Z", "+00:00"))
            except:
                return None
        return None

    @property
    def time_remaining_seconds(self) -> float:
        end = self.end_datetime
        if end:
            return (end - datetime.now(timezone.utc)).total_seconds()
        return 0

    @property
    def time_remaining_display(self) -> str:
        secs = self.time_remaining_seconds
        if secs <= 0:
            return "已結束"
        hours = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        if hours > 0:
            return f"{hours}時{mins}分"
        return f"{mins}分"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "slug": self.slug,
            "end_date": self.end_date,
            "active": self.active,
            "closed": self.closed,
            "accepting_orders": self.accepting_orders,
            "condition_id": self.condition_id,
            "up_token_id": self.up_token_id,
            "down_token_id": self.down_token_id,
            "time_remaining_seconds": self.time_remaining_seconds,
            "time_remaining_display": self.time_remaining_display,
            "volume": self.volume,
            "liquidity": self.liquidity,
        }


class MarketFinder:
    def __init__(self, config: BotConfig):
        self.config = config
        self.gamma_host = config.GAMMA_HOST

    def _filter_by_window(self, markets: List[MarketInfo]) -> List[MarketInfo]:
        """Filter markets to those ending within min/max time window."""
        out = []
        for m in markets:
            t = m.time_remaining_seconds
            if (
                t >= self.config.min_time_remaining_seconds
                and t <= self.config.max_time_remaining_seconds
                and m.up_token_id
                and m.down_token_id
            ):
                out.append(m)
        return out

    def _slug_for_timestamp(self, crypto: str, ts: int) -> str:
        """Construct 5m slug: btc-updown-5m-<ts>"""
        return f"{crypto.lower()}-updown-5m-{ts}"

    async def _fetch_markets(self, crypto: str) -> List[MarketInfo]:
        markets: List[MarketInfo] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                # Build a small window of candidate slugs around now (±3 buckets)
                now = int(datetime.now(timezone.utc).timestamp())
                bucket = now - (now % 300)
                candidate_ts = [bucket + 300 * offset for offset in range(-1, 4)]

                for ts in candidate_ts:
                    slug = self._slug_for_timestamp(crypto, ts)
                    resp = await client.get(
                        f"{self.gamma_host}/markets",
                        params={"slug": slug},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data if isinstance(data, list) else [data]
                        for m in items:
                            if not isinstance(m, dict) or not m.get("id"):
                                continue
                            market = MarketInfo(m)
                            if market.active and not market.closed:
                                markets.append(market)
            except Exception as e:
                print(f"[搜尋] crypto={crypto} 錯誤: {e}")
        return markets

    async def find_all_crypto_markets(self) -> List[MarketInfo]:
        """搜尋所有配置的加密貨幣，限定於短期(5m)窗口"""
        all_markets = []
        seen_ids = set()

        for crypto in self.config.crypto_symbols:
            crypto = crypto.strip().lower()
            markets = await self._fetch_markets(crypto)
            for m in self._filter_by_window(markets):
                if m.id not in seen_ids:
                    all_markets.append(m)
                    seen_ids.add(m.id)

        # 按剩餘時間排序，最近結束的排前面（過濾掉已結束的）
        all_markets = [m for m in all_markets if m.time_remaining_seconds > 0]
        all_markets.sort(key=lambda m: m.time_remaining_seconds)

        return all_markets

    async def find_active_tradeable_market(self, crypto: str = "btc") -> Optional[MarketInfo]:
        """找到最適合交易的活躍市場（剩餘時間足夠的）"""
        markets = await self.find_all_crypto_markets()
        valid = [m for m in markets if m.time_remaining_seconds >= self.config.min_time_remaining_seconds]
        if not valid:
            return None
        # 取結束時間最接近的市場
        valid.sort(key=lambda m: m.time_remaining_seconds)
        return valid[0]
