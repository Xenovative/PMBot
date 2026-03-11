"""
市場搜尋器 - 負責找到 Polymarket 上的 5 分鐘加密貨幣市場
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
        self.reference_price = self._resolve_reference_price(raw)
        self.reference_source = self._resolve_reference_source(raw, self.reference_price)

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
            "reference_price": self.reference_price,
            "reference_source": self.reference_source,
        }


class MarketFinder:
    def __init__(self, config: BotConfig):
        self.config = config
        self.gamma_host = config.GAMMA_HOST

    async def _fetch_underlying_prices(self, symbols: List[str]) -> Dict[str, float]:
        normalized_symbols = [str(symbol or "").strip().lower() for symbol in symbols if str(symbol or "").strip()]
        if not normalized_symbols:
            return {}
        coingecko_ids = {
            "btc": "bitcoin",
            "eth": "ethereum",
            "sol": "solana",
        }
        requested_ids = [coingecko_ids[symbol] for symbol in normalized_symbols if symbol in coingecko_ids]
        if not requested_ids:
            return {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": ",".join(requested_ids),
                        "vs_currencies": "usd",
                    },
                )
                if response.status_code != 200:
                    return {}
                payload = response.json() or {}
        except Exception:
            return {}
        prices_by_symbol: Dict[str, float] = {}
        for symbol in normalized_symbols:
            coingecko_id = coingecko_ids.get(symbol)
            if not coingecko_id:
                continue
            usd_price = ((payload.get(coingecko_id) or {}).get("usd"))
            if usd_price is None:
                continue
            try:
                prices_by_symbol[symbol] = float(usd_price)
            except (TypeError, ValueError):
                continue
        return prices_by_symbol

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
        underlying_prices = await self._fetch_underlying_prices(self.config.crypto_symbols)

        for crypto in self.config.crypto_symbols:
            crypto = crypto.strip().lower()
            markets = await self._fetch_markets(crypto)
            for m in self._filter_by_window(markets):
                if m.id not in seen_ids:
                    m.underlying_symbol = crypto.upper()
                    m.underlying_price = underlying_prices.get(crypto)
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
