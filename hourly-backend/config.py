import os
import re
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator
from typing import List

load_dotenv()


def validate_private_key(v: str) -> str:
    """Accept empty (dry-run / sig_type=0) or a 64-hex-char key with optional 0x prefix."""
    v = v.strip()
    if not v:
        return v
    raw = v[2:] if v.startswith(("0x", "0X")) else v
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        raise ValueError(
            f"PRIVATE_KEY must be a 32-byte hex string (64 hex chars, optional 0x prefix). "
            f"Got {len(raw)} hex chars — did you paste a wallet address instead?"
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
    private_key: str = os.getenv("PRIVATE_KEY", "")
    funder_address: str = os.getenv("FUNDER_ADDRESS", "")
    target_pair_cost: float = float(os.getenv("TARGET_PAIR_COST", "0.99"))
    order_size: float = float(os.getenv("ORDER_SIZE", "50"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    signature_type: int = int(os.getenv("SIGNATURE_TYPE", "0"))
    min_time_remaining_seconds: int = int(os.getenv("MIN_TIME_REMAINING_SECONDS", "600"))
    max_trades_per_market: int = int(os.getenv("MAX_TRADES_PER_MARKET", "10"))
    trade_cooldown_seconds: int = int(os.getenv("TRADE_COOLDOWN_SECONDS", "120"))
    max_position_imbalance: int = int(os.getenv("MAX_POSITION_IMBALANCE", "5"))
    min_liquidity: float = float(os.getenv("MIN_LIQUIDITY", "50"))
    crypto_symbols: List[str] = os.getenv("CRYPTO_SYMBOLS", "btc,eth,sol").split(",")

    # Bargain Hunter Settings
    bargain_enabled: bool = os.getenv("BARGAIN_ENABLED", "true").lower() == "true"
    bargain_price_threshold: float = float(os.getenv("BARGAIN_PRICE_THRESHOLD", "0.49"))
    bargain_pair_threshold: float = float(os.getenv("BARGAIN_PAIR_THRESHOLD", "0.99"))
    bargain_stop_loss_cents: float = float(os.getenv("BARGAIN_STOP_LOSS_CENTS", "0.02"))
    bargain_min_price: float = float(os.getenv("BARGAIN_MIN_PRICE", "0.10"))
    bargain_max_rounds: int = int(os.getenv("BARGAIN_MAX_ROUNDS", "56"))
    bargain_stop_loss_defer_minutes: int = int(os.getenv("BARGAIN_STOP_LOSS_DEFER_MINUTES", "10"))
    bargain_stop_loss_cooldown_minutes: int = int(os.getenv("BARGAIN_STOP_LOSS_COOLDOWN_MINUTES", "10"))
    bargain_stop_loss_immune_rounds: int = int(os.getenv("BARGAIN_STOP_LOSS_IMMUNE_ROUNDS", "3"))
    bargain_first_buy_bias: str = os.getenv("BARGAIN_FIRST_BUY_BIAS", "AUTO")  # "UP", "DOWN", or "AUTO"
    # Escalation window now in minutes (was hours)
    bargain_pair_escalation_minutes: int = int(os.getenv("BARGAIN_PAIR_ESCALATION_MINUTES", "15"))
    # Late liquidation threshold (seconds before expiry to force sell holdings)
    late_liquidation_seconds: int = int(os.getenv("LATE_LIQUIDATION_SECONDS", "90"))

    @field_validator("private_key", mode="before")
    @classmethod
    def _validate_private_key(cls, v):
        return validate_private_key(str(v) if v is not None else "")

    @field_validator("funder_address", mode="before")
    @classmethod
    def _validate_funder_address(cls, v):
        return validate_funder_address(str(v) if v is not None else "")

    CLOB_HOST: str = "https://clob.polymarket.com"
    GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    CHAIN_ID: int = 137
    POLYGON_RPC_URL: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")


def get_config() -> BotConfig:
    return BotConfig()
