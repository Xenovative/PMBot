"""
市場搜尋器 - 負責找到 Polymarket 上的 4 小時加密貨幣 Up or Down 市場
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

    def _generate_4h_slugs(self, crypto: str) -> List[str]:
        """
        生成 4 小時 Up or Down 市場的 slug。
        Polymarket 實際格式: btc-updown-4h-{unix_timestamp}
        例如: btc-updown-4h-1772600400
             xrp-updown-4h-1772600400
             eth-updown-4h-1772600400
             sol-updown-4h-1772600400
        4h 槽位固定每 4 小時一個，時間戳對齊 UTC 4h 邊界。
        """
        symbol = crypto.lower()
        now_utc = datetime.now(timezone.utc)
        slugs: List[str] = []

        # Polymarket 4h slots are ET-aligned: 1,5,9,13,17,21 UTC (offset +3600 from UTC 4h boundary)
        SLOT_OFFSET = 3600
        slot_size = 4 * 3600
        raw_ts = int(now_utc.timestamp())
        current_slot_ts = ((raw_ts - SLOT_OFFSET) // slot_size) * slot_size + SLOT_OFFSET

        # Cover previous slot + current + next 3 (16 hours total)
        for i in range(-1, 4):
            ts = current_slot_ts + i * slot_size
            slugs.append(f"{symbol}-updown-4h-{ts}")

        return slugs

    async def _search_gamma_keyword(self, crypto: str) -> List[MarketInfo]:
        """使用關鍵字搜尋 4 小時市場（備用方案）"""
        symbol = crypto.lower()
        markets = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.gamma_host}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": 100,
                        "tag_slug": "crypto",
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("data", [])
                    for event in items:
                        slug = event.get("slug", "")
                        # Match pattern: {symbol}-updown-4h-{timestamp}
                        if slug.startswith(f"{symbol}-updown-4h-"):
                            for m in event.get("markets", []):
                                market = MarketInfo(m)
                                if market.active and not market.closed:
                                    markets.append(market)
        except Exception as e:
            print(f"[搜尋] keyword search 錯誤: {e}")
        return markets

    async def find_markets_by_slug(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 slug 精確匹配搜尋 4 小時 Up or Down 市場"""
        markets = []
        slugs = self._generate_4h_slugs(crypto)
        seen_ids = set()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for slug in slugs:
                try:
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
                                            if market.active and not market.closed and market.id not in seen_ids:
                                                seen_ids.add(market.id)
                                                markets.append(market)
                except Exception as e:
                    print(f"[搜尋] slug={slug} 錯誤: {e}")

        return markets

    async def find_markets_by_direct(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 markets API 直接搜尋"""
        markets = []
        slugs = self._generate_4h_slugs(crypto)
        seen_ids = set()

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
                                if market.active and not market.closed and market.id not in seen_ids:
                                    seen_ids.add(market.id)
                                    markets.append(market)
                        elif isinstance(data, dict) and data.get("id"):
                            market = MarketInfo(data)
                            if market.active and not market.closed and market.id not in seen_ids:
                                seen_ids.add(market.id)
                                markets.append(market)
                except Exception as e:
                    print(f"[搜尋] direct slug={slug} 錯誤: {e}")

        return markets

    async def find_all_crypto_markets(self) -> List[MarketInfo]:
        """搜尋所有配置的加密貨幣的 4 小時 Up or Down 市場"""
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

            # 方法 3: 關鍵字搜尋（備用）
            if not any(m for m in all_markets):
                kw_markets = await self._search_gamma_keyword(crypto)
                for m in kw_markets:
                    if m.id not in seen_ids:
                        seen_ids.add(m.id)
                        all_markets.append(m)

        # 按剩餘時間排序，過濾已結束的
        all_markets = [m for m in all_markets if m.time_remaining_seconds > 0]
        all_markets.sort(key=lambda m: m.time_remaining_seconds)

        return all_markets

    async def find_active_tradeable_market(self, crypto: str = "btc") -> Optional[MarketInfo]:
        """找到最適合交易的活躍市場（剩餘時間足夠的）"""
        markets = await self.find_markets_by_slug(crypto)
        if not markets:
            markets = await self.find_markets_by_direct(crypto)
        if not markets:
            markets = await self._search_gamma_keyword(crypto)

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
