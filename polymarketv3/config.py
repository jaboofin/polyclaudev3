"""
Configuration settings for the Polymarket Trading Bot.
Loads environment variables and defines trading parameters.

Live-safety additions (no paper mode):
- Kill switch (blocks NEW entries)
- Spread guard (skip wide books)
- Order TTL (stale LIVE orders can be cancelled)
- Startup cancel-all (optional)
- Circuit breakers (daily loss, drawdown, consecutive errors)

All settings are controlled via environment variables.
"""

import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional

# Load environment variables from .env file
load_dotenv()


@dataclass
class TradingConfig:
    """Trading-related configuration."""
    max_trade_size: float = 100.0  # Maximum USDC per trade
    max_total_exposure: float = 1000.0  # Maximum total position value
    min_market_liquidity: float = 5000.0  # Minimum liquidity to trade
    default_slippage: float = 0.01  # 1% slippage tolerance


@dataclass
class LiveSafetyConfig:
    """Live-only safety controls (no paper mode)."""
    kill_switch: bool = False  # If True: block NEW entries (SELL allowed)
    max_spread_bps: float = 150.0  # Skip entries if spread too wide
    order_ttl_seconds: int = 120  # Cancel stale LIVE orders after this TTL
    cancel_all_on_startup: bool = False  # Cancel all open orders when bot starts
    max_daily_loss_usd: float = 0.0  # 0 disables
    max_drawdown_pct: float = 0.0  # 0 disables
    max_consecutive_errors: int = 10
    intent_ttl_seconds: int = 300  # Idempotency window for order intents
    trade_sync_lookback_days: int = 7  # Used by optional sync_from_exchange()


@dataclass
class AlertConfig:
    """Alert and notification configuration."""
    price_change_threshold: float = 0.05  # Alert on 5%+ price change
    volume_spike_threshold: float = 2.0  # Alert on 2x volume spike
    discord_webhook: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None


@dataclass
class ArbitrageConfig:
    """Arbitrage detection configuration."""
    min_profit_threshold: float = 0.02  # Minimum 2% profit
    max_execution_time: int = 30  # Seconds to execute before aborting
    include_fees: bool = True  # Account for trading fees


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


class Config:
    """
    Main configuration class for the Polymarket bot.

    Usage:
        config = Config()
        print(config.clob_host)
        print(config.trading.max_trade_size)
    """

    # API Endpoints
    GAMMA_API_HOST = "https://gamma-api.polymarket.com"
    CLOB_HOST = "https://clob.polymarket.com"
    WS_HOST = "wss://ws-subscriptions-clob.polymarket.com"
    CHAIN_ID = 137  # Polygon mainnet

    # Category Tag IDs (from Polymarket)
    # These are used to filter markets by category
    CATEGORY_TAGS = {
        "crypto": 21,  # Crypto category
        "sports": None,  # Sports uses /sports endpoint instead
    }

    # Sports leagues (fetched dynamically, but here are common ones)
    SPORTS_LEAGUES = {
        "nba": "NBA",
        "nfl": "NFL",
        "mlb": "MLB",
        "nhl": "NHL",
        "soccer": "Soccer",
        "mma": "MMA",
        "tennis": "Tennis",
    }

    def __init__(self):
        """Initialize configuration from environment variables."""
        # Wallet credentials
        self.private_key = os.getenv("PRIVATE_KEY", "")
        self.funder_address = os.getenv("FUNDER_ADDRESS", "")
        self.signature_type = int(os.getenv("SIGNATURE_TYPE", "1"))

        # Trading configuration
        self.trading = TradingConfig(
            max_trade_size=float(os.getenv("MAX_TRADE_SIZE", "100")),
            max_total_exposure=float(os.getenv("MAX_TOTAL_EXPOSURE", "1000")),
            min_market_liquidity=float(os.getenv("MIN_MARKET_LIQUIDITY", "5000")),
            default_slippage=float(os.getenv("DEFAULT_SLIPPAGE", "0.01")),
        )

        # Live safety configuration
        self.safety = LiveSafetyConfig(
            kill_switch=_env_bool("KILL_SWITCH", False),
            max_spread_bps=float(os.getenv("MAX_SPREAD_BPS", "150")),
            order_ttl_seconds=int(float(os.getenv("ORDER_TTL_SECONDS", "120"))),
            cancel_all_on_startup=_env_bool("CANCEL_ALL_ON_STARTUP", False),
            max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "0")),
            max_drawdown_pct=float(os.getenv("MAX_DRAWDOWN_PCT", "0")),
            max_consecutive_errors=int(float(os.getenv("MAX_CONSECUTIVE_ERRORS", "10"))),
            intent_ttl_seconds=int(float(os.getenv("INTENT_TTL_SECONDS", "300"))),
            trade_sync_lookback_days=int(float(os.getenv("TRADE_SYNC_LOOKBACK_DAYS", "7"))),
        )

        # Alert configuration
        self.alerts = AlertConfig(
            discord_webhook=os.getenv("DISCORD_WEBHOOK_URL"),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        )

        # Arbitrage configuration
        self.arbitrage = ArbitrageConfig()

    @property
    def has_credentials(self) -> bool:
        """Check if trading credentials are configured."""
        return bool(self.private_key and self.funder_address)

    @property
    def clob_host(self) -> str:
        """Get CLOB API host."""
        return self.CLOB_HOST

    @property
    def gamma_host(self) -> str:
        """Get Gamma API host."""
        return self.GAMMA_API_HOST

    @property
    def is_killed(self) -> bool:
        """True if kill switch is enabled."""
        return bool(self.safety.kill_switch)

    def validate(self) -> list[str]:
        """
        Validate configuration and return list of issues.

        Returns:
            List of validation error messages (empty if valid)
        """
        issues = []

        if not self.private_key:
            issues.append("PRIVATE_KEY not set - trading disabled")

        if not self.funder_address:
            issues.append("FUNDER_ADDRESS not set - trading disabled")

        if self.trading.max_trade_size <= 0:
            issues.append("MAX_TRADE_SIZE must be positive")

        if self.trading.max_total_exposure < self.trading.max_trade_size:
            issues.append("MAX_TOTAL_EXPOSURE should be >= MAX_TRADE_SIZE")

        if self.safety.order_ttl_seconds < 10:
            issues.append("ORDER_TTL_SECONDS too low (<10)")

        if self.safety.max_spread_bps <= 0:
            issues.append("MAX_SPREAD_BPS must be positive")

        return issues


# Singleton instance
config = Config()
