"""
市場搜尋器 - 負責找到 Polymarket 上的 15 分鐘加密貨幣市場
"""
import httpx
import time
import math
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
        mins = int(secs // 60)
        remaining_secs = int(secs % 60)
        return f"{mins}分{remaining_secs}秒"

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

    def _generate_slug_timestamps(self) -> List[int]:
        """
        生成可能的 15 分鐘市場時間戳。
        Polymarket 15 分鐘市場的 slug 格式: {crypto}-updown-15m-{end_timestamp}
        end_timestamp 是該 15 分鐘窗口的結束時間（UTC），對齊到 15 分鐘邊界。
        """
        now = datetime.now(timezone.utc)
        # 對齊到當前 15 分鐘邊界
        minutes_rounded = (now.minute // 15) * 15
        current_boundary = now.replace(minute=minutes_rounded, second=0, microsecond=0)

        timestamps = []
        # 嘗試前後多個時間窗口
        for offset_minutes in [-15, 0, 15, 30, 45, 60]:
            boundary = current_boundary + timedelta(minutes=offset_minutes)
            timestamps.append(int(boundary.timestamp()))

        return timestamps

    async def find_markets_by_slug(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 slug 模式搜尋 15 分鐘市場"""
        markets = []
        timestamps = self._generate_slug_timestamps()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for ts in timestamps:
                slug = f"{crypto}-updown-15m-{ts}"
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
                    print(f"[搜尋] slug={slug} 錯誤: {e}")

        return markets

    async def find_markets_by_search(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過搜尋 API 查找 15 分鐘市場"""
        markets = []
        search_terms = [
            f"{crypto.upper()} Up or Down",
            f"{crypto}-updown-15m",
        ]

        async with httpx.AsyncClient(timeout=15.0) as client:
            for term in search_terms:
                try:
                    resp = await client.get(
                        f"{self.gamma_host}/markets",
                        params={
                            "active": "true",
                            "closed": "false",
                            "limit": 50,
                            "slug_contains": f"{crypto}-updown-15m",
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            for m in data:
                                market = MarketInfo(m)
                                slug = market.slug or ""
                                if (
                                    f"{crypto}-updown-15m" in slug
                                    and market.active
                                    and not market.closed
                                ):
                                    markets.append(market)
                except Exception as e:
                    print(f"[搜尋] term={term} 錯誤: {e}")

        return markets

    async def find_markets_by_events(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 events API 查找 15 分鐘市場（第三種方法）"""
        markets = []
        slug_pattern = f"{crypto}-updown-15m"

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                # 嘗試透過 events 端點搜尋
                resp = await client.get(
                    f"{self.gamma_host}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": 20,
                        "slug": slug_pattern,
                    }
                )
                if resp.status_code == 200:
                    events = resp.json()
                    if isinstance(events, list):
                        for event in events:
                            event_markets = event.get("markets", [])
                            if isinstance(event_markets, list):
                                for m in event_markets:
                                    market = MarketInfo(m)
                                    if market.active and not market.closed:
                                        markets.append(market)
            except Exception as e:
                print(f"[搜尋] events 搜尋錯誤: {e}")

            # 也嘗試用 tag 搜尋 crypto 類別
            try:
                resp = await client.get(
                    f"{self.gamma_host}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": 50,
                        "tag": "crypto",
                    }
                )
                if resp.status_code == 200:
                    events = resp.json()
                    if isinstance(events, list):
                        for event in events:
                            event_slug = event.get("slug", "")
                            if "updown-15m" in event_slug:
                                event_markets = event.get("markets", [])
                                if isinstance(event_markets, list):
                                    for m in event_markets:
                                        market = MarketInfo(m)
                                        if market.active and not market.closed:
                                            markets.append(market)
            except Exception as e:
                print(f"[搜尋] tag 搜尋錯誤: {e}")

        return markets

    async def find_all_crypto_markets(self) -> List[MarketInfo]:
        """搜尋所有配置的加密貨幣的 15 分鐘市場"""
        all_markets = []
        seen_ids = set()

        for crypto in self.config.crypto_symbols:
            crypto = crypto.strip().lower()

            # 方法 1: 透過 slug 精確匹配
            slug_markets = await self.find_markets_by_slug(crypto)
            for m in slug_markets:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    all_markets.append(m)

            # 方法 2: 透過搜尋 API (slug_contains)
            search_markets = await self.find_markets_by_search(crypto)
            for m in search_markets:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    all_markets.append(m)

            # 方法 3: 透過 events API
            event_markets = await self.find_markets_by_events(crypto)
            for m in event_markets:
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
            markets = await self.find_markets_by_search(crypto)

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
