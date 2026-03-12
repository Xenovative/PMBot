import os
import re
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


def validate_private_key(v: str) -> str:
    """Accept empty (dry-run / sig_type=0) or a 64-hex-char key with optional 0x prefix."""
    v = v.strip()
    if not v:
        return v
    raw = v[2:] if v.startswith(("0x", "0X")) else v
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        raise ValueError(
            f"PRIVATE_KEY must be a 32-byte hex string (64 hex chars, optional 0x prefix). "
            f"Got {len(raw)} hex chars ??did you paste a wallet address instead?"
        )
    return v


def validate_funder_address(v: str) -> str:
    """Accept empty or a 0x-prefixed 20-byte EVM address (40 hex chars)."""
    v = v.strip()
    if not v:
        return v
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", v):
        raise ValueError(
            f"FUNDER_ADDRESS must be a 0x-prefixed 20-byte EVM address (42 chars total). "
            f"Got: {v!r}"
        )
    return v


class BotConfig(BaseModel):
    private_key: str = _str("PRIVATE_KEY", "")
    funder_address: str = _str("FUNDER_ADDRESS", "")
    target_pair_cost: float = _float("TARGET_PAIR_COST", 0.99)
    order_size: float = _float("ORDER_SIZE", 50)
    dry_run: bool = _bool("DRY_RUN", True)
    signature_type: int = _int("SIGNATURE_TYPE", 0)
    min_time_remaining_seconds: int = _int("MIN_TIME_REMAINING_SECONDS", 600)
    max_trades_per_market: int = _int("MAX_TRADES_PER_MARKET", 10)
    trade_cooldown_seconds: int = _int("TRADE_COOLDOWN_SECONDS", 120)
    max_position_imbalance: int = _int("MAX_POSITION_IMBALANCE", 5)
    min_liquidity: float = _float("MIN_LIQUIDITY", 50)
    crypto_symbols: List[str] = _str("CRYPTO_SYMBOLS", "btc,eth,sol").split(",")

    # Bargain Hunter Settings
    bargain_enabled: bool = _bool("BARGAIN_ENABLED", True)
    bargain_price_threshold: float = _float("BARGAIN_PRICE_THRESHOLD", 0.49)
    bargain_pair_threshold: float = _float("BARGAIN_PAIR_THRESHOLD", 0.99)
    bargain_stop_loss_cents: float = _float("BARGAIN_STOP_LOSS_CENTS", 0.02)
    bargain_min_price: float = _float("BARGAIN_MIN_PRICE", 0.10)
    bargain_max_rounds: int = _int("BARGAIN_MAX_ROUNDS", 56)
    bargain_stop_loss_defer_minutes: int = _int("BARGAIN_STOP_LOSS_DEFER_MINUTES", 10)
    bargain_stop_loss_cooldown_minutes: int = _int("BARGAIN_STOP_LOSS_COOLDOWN_MINUTES", 10)
    bargain_stop_loss_immune_rounds: int = _int("BARGAIN_STOP_LOSS_IMMUNE_ROUNDS", 3)
    bargain_first_buy_bias: str = _str("BARGAIN_FIRST_BUY_BIAS", "AUTO")  # "UP", "DOWN", or "AUTO"
    bargain_plummet_exit_pct: float = _float("BARGAIN_PLUMMET_EXIT_PCT", 20.0)
    bargain_plummet_window_seconds: int = _int("BARGAIN_PLUMMET_WINDOW_SECONDS", 15)
    bargain_plummet_trigger_seconds: int = _int("BARGAIN_PLUMMET_TRIGGER_SECONDS", 0)
    # Escalation window now in minutes (was hours)
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
