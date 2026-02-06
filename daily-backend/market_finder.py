"""
市場搜尋器 - 負責找到 Polymarket 上的每日加密貨幣 Up or Down 市場
"""
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from config import BotConfig

# 幣種符號 → Polymarket slug 名稱映射
CRYPTO_NAME_MAP = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
}

# 月份名稱映射
MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}


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

    def _generate_daily_slugs(self, crypto: str) -> List[str]:
        """
        生成每日 Up or Down 市場的 slug。
        格式: {crypto_name}-up-or-down-on-{month}-{day}
        嘗試今天、明天、後天的市場。
        """
        crypto_name = CRYPTO_NAME_MAP.get(crypto.lower(), crypto.lower())
        now = datetime.now(timezone.utc)
        slugs = []

        for day_offset in range(0, 3):
            target_date = now + timedelta(days=day_offset)
            month_name = MONTH_NAMES[target_date.month]
            day = target_date.day
            slug = f"{crypto_name}-up-or-down-on-{month_name}-{day}"
            slugs.append(slug)

        return slugs

    async def find_markets_by_slug(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 slug 精確匹配搜尋每日 Up or Down 市場"""
        markets = []
        slugs = self._generate_daily_slugs(crypto)

        async with httpx.AsyncClient(timeout=15.0) as client:
            for slug in slugs:
                try:
                    # 透過 events API 用精確 slug 搜尋
                    resp = await client.get(
                        f"{self.gamma_host}/events",
                        params={"slug": slug, "limit": 1}
                    )
                    if resp.status_code == 200:
                        events = resp.json()
                        if isinstance(events, list):
                            for event in events:
                                event_slug = event.get("slug", "")
                                if event_slug == slug:
                                    event_markets = event.get("markets", [])
                                    if isinstance(event_markets, list):
                                        for m in event_markets:
                                            market = MarketInfo(m)
                                            if market.active and not market.closed:
                                                markets.append(market)
                except Exception as e:
                    print(f"[搜尋] slug={slug} 錯誤: {e}")

        return markets

    async def find_markets_by_direct(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 markets API 直接搜尋"""
        markets = []
        slugs = self._generate_daily_slugs(crypto)

        async with httpx.AsyncClient(timeout=15.0) as client:
            for slug in slugs:
                try:
                    resp = await client.get(
                        f"{self.gamma_host}/markets",
                        params={"slug": slug}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            for m in data:
                                market = MarketInfo(m)
                                if market.active and not market.closed:
                                    markets.append(market)
                        elif isinstance(data, dict) and data.get("id"):
                            market = MarketInfo(data)
                            if market.active and not market.closed:
                                markets.append(market)
                except Exception as e:
                    print(f"[搜尋] direct slug={slug} 錯誤: {e}")

        return markets

    async def find_all_crypto_markets(self) -> List[MarketInfo]:
        """搜尋所有配置的加密貨幣的每日 Up or Down 市場"""
        all_markets = []
        seen_ids = set()

        for crypto in self.config.crypto_symbols:
            crypto = crypto.strip().lower()

            # 方法 1: 透過 events API 精確 slug 匹配
            slug_markets = await self.find_markets_by_slug(crypto)
            for m in slug_markets:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    all_markets.append(m)

            # 方法 2: 透過 markets API 直接搜尋
            direct_markets = await self.find_markets_by_direct(crypto)
            for m in direct_markets:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    all_markets.append(m)

        # 按剩餘時間排序，最近結束的排前面（過濾掉已結束的）
        all_markets = [m for m in all_markets if m.time_remaining_seconds > 0]
        all_markets.sort(key=lambda m: m.time_remaining_seconds)

        return all_markets

    async def find_active_tradeable_market(self, crypto: str = "btc") -> Optional[MarketInfo]:
        """找到最適合交易的活躍市場（剩餘時間足夠的）"""
        markets = await self.find_markets_by_slug(crypto)
        if not markets:
            markets = await self.find_markets_by_direct(crypto)

        for market in markets:
            remaining = market.time_remaining_seconds
            if (
                remaining > self.config.min_time_remaining_seconds
                and market.accepting_orders
                and market.up_token_id
                and market.down_token_id
            ):
                return market

        return None
