import os
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List

load_dotenv()


class BotConfig(BaseModel):
    private_key: str = os.getenv("PRIVATE_KEY", "")
    funder_address: str = os.getenv("FUNDER_ADDRESS", "")
    target_pair_cost: float = float(os.getenv("TARGET_PAIR_COST", "0.99"))
    order_size: float = float(os.getenv("ORDER_SIZE", "50"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    signature_type: int = int(os.getenv("SIGNATURE_TYPE", "0"))
    min_time_remaining_seconds: int = int(os.getenv("MIN_TIME_REMAINING_SECONDS", "3600"))
    max_trades_per_market: int = int(os.getenv("MAX_TRADES_PER_MARKET", "10"))
    trade_cooldown_seconds: int = int(os.getenv("TRADE_COOLDOWN_SECONDS", "300"))
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

    CLOB_HOST: str = "https://clob.polymarket.com"
    GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    CHAIN_ID: int = 137


def get_config() -> BotConfig:
    return BotConfig()
