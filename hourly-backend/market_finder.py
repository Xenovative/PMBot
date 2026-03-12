"""
市場搜尋器 - 負責找到 Polymarket 上的每小時加密貨幣 Up or Down 市場
"""
import re
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from config import BotConfig


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        normalized_text = str(value).strip().replace(",", "")
        if not normalized_text:
            return None
        return float(normalized_text)
    except (TypeError, ValueError):
        return None


def _extract_first_price_hint(text: str) -> Optional[float]:
    normalized_text = str(text or "")
    if not normalized_text:
        return None
    price_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", normalized_text)
    if price_match:
        return _safe_float(price_match.group(1))
    plain_match = re.search(r"\b([0-9]{3,}(?:,[0-9]{3})*(?:\.[0-9]+)?)\b", normalized_text)
    if plain_match:
        return _safe_float(plain_match.group(1))
    return None

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
        self.underlying_symbol = raw.get("underlyingSymbol", "")
        self.underlying_price = raw.get("underlyingPrice")
        self.underlying_price_source = raw.get("underlyingPriceSource")
        self.reference_price = self._resolve_reference_price(raw)
        self.reference_source = self._resolve_reference_source(raw, self.reference_price)
        self.reference_price_source = raw.get("referencePriceSource")

    def _resolve_reference_price(self, raw: Dict[str, Any]) -> Optional[float]:
        candidate_keys = [
            "referencePrice",
            "reference_price",
            "strikePrice",
            "strike_price",
            "threshold",
            "targetPrice",
            "target_price",
            "initialValue",
            "initial_value",
            "openPrice",
            "open_price",
            "price",
        ]
        for candidate_key in candidate_keys:
            candidate_value = _safe_float(raw.get(candidate_key))
            if candidate_value is not None and candidate_value > 0:
                return candidate_value
        nested_keys = ["metadata", "extraData", "events"]
        for nested_key in nested_keys:
            nested_value = raw.get(nested_key)
            if isinstance(nested_value, dict):
                for candidate_key in candidate_keys:
                    candidate_value = _safe_float(nested_value.get(candidate_key))
                    if candidate_value is not None and candidate_value > 0:
                        return candidate_value
        question_price = _extract_first_price_hint(self.question)
        if question_price is not None and question_price > 0:
            return question_price
        description_price = _extract_first_price_hint(str(raw.get("description", "") or ""))
        if description_price is not None and description_price > 0:
            return description_price
        return None

    def _resolve_reference_source(self, raw: Dict[str, Any], reference_price: Optional[float]) -> Optional[str]:
        if reference_price is None:
            return None
        candidate_keys = [
            "referencePrice",
            "reference_price",
            "strikePrice",
            "strike_price",
            "threshold",
            "targetPrice",
            "target_price",
            "initialValue",
            "initial_value",
            "openPrice",
            "open_price",
            "price",
        ]
        for candidate_key in candidate_keys:
            candidate_value = _safe_float(raw.get(candidate_key))
            if candidate_value is not None and candidate_value == reference_price:
                return candidate_key
        if _extract_first_price_hint(self.question) == reference_price:
            return "question"
        if _extract_first_price_hint(str(raw.get("description", "") or "")) == reference_price:
            return "description"
        return "derived"

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
            "underlying_symbol": self.underlying_symbol,
            "underlying_price": self.underlying_price,
            "underlying_price_source": self.underlying_price_source,
            "reference_price": self.reference_price,
            "reference_source": self.reference_source,
            "reference_price_source": self.reference_price_source,
        }


class MarketFinder:
    def __init__(self, config: BotConfig):
        self.config = config
        self.gamma_host = config.GAMMA_HOST

    def _generate_hourly_slugs(self, crypto: str) -> List[str]:
        """
        生成每小時 Up or Down 市場的 slug。
        根據 Polymarket 實際樣式，例如:
          bitcoin-up-or-down-march-3-11pm-et
          ethereum-up-or-down-march-3-11pm-et
        同時保留舊有的 at/to 變體以增加匹配率。
        """
        from zoneinfo import ZoneInfo

        crypto_name = CRYPTO_NAME_MAP.get(crypto.lower(), crypto.lower())
        now_et = datetime.now(ZoneInfo("America/New_York"))
        slugs: List[str] = []

        for hour_offset in range(0, 6):  # cover current + next 5 hours
            target = now_et + timedelta(hours=hour_offset)
            month_name = MONTH_NAMES[target.month]
            day = target.day
            minute = target.minute
            hour_24 = target.hour
            hour_12 = hour_24 % 12 or 12
            ampm = "am" if hour_24 < 12 else "pm"
            next_hour_24 = (hour_24 + 1) % 24
            next_hour_12 = next_hour_24 % 12 or 12
            next_ampm = "am" if next_hour_24 < 12 else "pm"

            # Primary: bitcoin-up-or-down-march-3-11pm-et
            slugs.append(f"{crypto_name}-up-or-down-{month_name}-{day}-{hour_12}{ampm}-et")
            # Minute variant if Polymarket includes minutes (rare): 3-30pm-et
            if minute:
                slugs.append(f"{crypto_name}-up-or-down-{month_name}-{day}-{hour_12}-{minute:02d}{ampm}-et")

            # Legacy patterns kept for safety
            slugs.append(f"{crypto_name}-up-or-down-on-{month_name}-{day}-at-{hour_12}{ampm}")
            slugs.append(f"{crypto_name}-up-or-down-on-{month_name}-{day}-{hour_12}{ampm}-to-{next_hour_12}{next_ampm}")
            slugs.append(f"{crypto_name}-up-or-down-in-1-hour-on-{month_name}-{day}-at-{hour_12}{ampm}")
            slugs.append(f"{crypto_name}-up-or-down-on-{month_name}-{day:02d}-at-{hour_12}{ampm}")
            slugs.append(f"{crypto_name}-up-or-down-on-{month_name}-{day}-{hour_24:02d}00-utc")

        return slugs

    async def _search_gamma_keyword(self, crypto: str) -> List[MarketInfo]:
        """使用關鍵字搜尋每小時市場（備用方案）"""
        crypto_name = CRYPTO_NAME_MAP.get(crypto.lower(), crypto.lower())
        markets = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.gamma_host}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": 100,
                        "keyword": f"{crypto_name} up or down",
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("data", [])
                    for m in items:
                        slug = m.get("slug", "")
                        question = m.get("question", "").lower()
                        # Filter for hourly-like markets: contain "hour" or "1h" or time patterns
                        if ("hour" in slug or "1h" in slug or
                                "hour" in question or "1h" in question or
                                ("am" in slug and "pm" in slug) or
                                ("-am" in slug or "-pm" in slug)):
                            if crypto_name in slug or crypto.lower() in question:
                                market = MarketInfo(m)
                                if market.active and not market.closed:
                                    markets.append(market)
        except Exception as e:
            print(f"[搜尋] keyword search 錯誤: {e}")
        return markets

    async def find_markets_by_slug(self, crypto: str = "btc") -> List[MarketInfo]:
        """透過 slug 精確匹配搜尋每小時 Up or Down 市場"""
        markets = []
        slugs = self._generate_hourly_slugs(crypto)
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
        slugs = self._generate_hourly_slugs(crypto)
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
        """搜尋所有配置的加密貨幣的每小時 Up or Down 市場"""
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
