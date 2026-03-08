import os
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List

load_dotenv(override=True)


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).split("#")[0].strip())
    except (ValueError, AttributeError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).split("#")[0].strip())
    except (ValueError, AttributeError):
        return default


def _bool(key: str, default: bool) -> bool:
    v = os.getenv(key, str(default)).split("#")[0].strip().lower()
    return v in ("1", "true", "yes")


def _str(key: str, default: str) -> str:
    v = os.getenv(key, default)
    return (v.split("#")[0].strip() if v else default) or default


class BotConfig(BaseModel):
    private_key: str = _str("PRIVATE_KEY", "")
    funder_address: str = _str("FUNDER_ADDRESS", "")
    target_pair_cost: float = _float("TARGET_PAIR_COST", 0.99)
    order_size: float = _float("ORDER_SIZE", 50)
    dry_run: bool = _bool("DRY_RUN", True)
    signature_type: int = _int("SIGNATURE_TYPE", 0)
    min_time_remaining_seconds: int = _int("MIN_TIME_REMAINING_SECONDS", 120)
    max_trades_per_market: int = _int("MAX_TRADES_PER_MARKET", 10)
    trade_cooldown_seconds: int = _int("TRADE_COOLDOWN_SECONDS", 60)
    max_position_imbalance: int = _int("MAX_POSITION_IMBALANCE", 5)
    min_liquidity: float = _float("MIN_LIQUIDITY", 100)
    crypto_symbols: List[str] = _str("CRYPTO_SYMBOLS", "btc,eth,sol").split(",")

    # Bargain Hunter Settings
    bargain_enabled: bool = _bool("BARGAIN_ENABLED", True)
    bargain_price_threshold: float = _float("BARGAIN_PRICE_THRESHOLD", 0.49)
    bargain_pair_threshold: float = _float("BARGAIN_PAIR_THRESHOLD", 0.99)
    bargain_stop_loss_cents: float = _float("BARGAIN_STOP_LOSS_CENTS", 0.02)
    # 15m: allow bargain on all markets by default; set to >0 to limit to future markets only
    bargain_future_min_seconds: int = _int("BARGAIN_FUTURE_MIN_SECONDS", 0)
    bargain_min_price: float = _float("BARGAIN_MIN_PRICE", 0.10)
    bargain_max_rounds: int = _int("BARGAIN_MAX_ROUNDS", 56)
    bargain_stop_loss_defer_minutes: int = _int("BARGAIN_STOP_LOSS_DEFER_MINUTES", 5)
    bargain_stop_loss_cooldown_minutes: int = _int("BARGAIN_STOP_LOSS_COOLDOWN_MINUTES", 5)
    bargain_stop_loss_immune_rounds: int = _int("BARGAIN_STOP_LOSS_IMMUNE_ROUNDS", 3)
    bargain_first_buy_bias: str = _str("BARGAIN_FIRST_BUY_BIAS", "AUTO")  # AUTO|UP|DOWN
    bargain_pair_escalation_minutes: int = _int("BARGAIN_PAIR_ESCALATION_MINUTES", 15)
    # Late liquidation threshold (seconds before expiry to force sell holdings)
    late_liquidation_seconds: int = _int("LATE_LIQUIDATION_SECONDS", 90)

    CLOB_HOST: str = "https://clob.polymarket.com"
    GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    CHAIN_ID: int = 137
    POLYGON_RPC_URL: str = _str("POLYGON_RPC_URL", "https://polygon-rpc.com")
    poly_relayer_enabled: bool = _bool("POLY_RELAYER_ENABLED", False)
    poly_relayer_url: str = _str("POLY_RELAYER_URL", "https://relayer-v2.polymarket.com/")
    poly_builder_api_key: str = _str("POLY_BUILDER_API_KEY", "")
    poly_builder_secret: str = _str("POLY_BUILDER_SECRET", "")
    poly_builder_passphrase: str = _str("POLY_BUILDER_PASSPHRASE", "")
    relayer_helper_command: str = _str("RELAYER_HELPER_COMMAND", "node")
    relayer_helper_script: str = _str("RELAYER_HELPER_SCRIPT", "")


def get_config() -> BotConfig:
    return BotConfig()
