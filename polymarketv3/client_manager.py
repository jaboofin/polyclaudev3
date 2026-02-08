"""
Client Manager - Shared ClobClient instances for the entire bot.

PROBLEM THIS SOLVES:
Previously, every module (trader, portfolio, odds_tracker, arbitrage, order_manager)
created its own ClobClient and independently called create_or_derive_api_creds().
This caused:
- Redundant credential derivations (slow, rate-limit risk)
- No connection reuse
- Inconsistent auth state across modules

NOW: All modules import from here. One read-only client, one authenticated client.
Both are wrapped in a rate limiter to prevent API bans.

Usage:
    from client_manager import clients

    # Read-only operations (orderbook, prices, midpoints)
    book = clients.read.get_order_book(token_id)

    # Authenticated operations (trading, orders)
    if clients.has_auth:
        result = clients.auth.post_order(order)
"""

import os
import time
import threading
from typing import Optional
from py_clob_client.client import ClobClient

from config import config
import logging
logger = logging.getLogger(__name__)


# ── Rate Limiter ──────────────────────────────────────────────

# Default: 10 requests/second. Override via env var.
DEFAULT_RATE_LIMIT = float(os.getenv("API_RATE_LIMIT", "10"))


class RateLimitedClient:
    """
    Thread-safe rate-limiting wrapper around ClobClient.

    Intercepts all method calls and enforces a minimum interval between
    API requests. This prevents hammering the Polymarket API when scanning
    many markets (arbitrage, price tracking) and avoids throttling/bans.

    All attribute access is proxied to the underlying ClobClient, so this
    is a transparent drop-in replacement. Only methods that actually make
    HTTP requests are rate-limited; attribute access and local methods
    pass through instantly.
    """

    # Methods known to make HTTP requests to the CLOB/Gamma API
    _RATE_LIMITED_METHODS = frozenset({
        "get_order_book", "get_midpoint", "get_price", "get_last_trade_price",
        "get_order", "get_orders", "get_trades",
        "post_order", "cancel", "cancel_all", "cancel_orders",
        "create_order", "create_or_derive_api_creds",
        "get_markets", "get_market",
    })

    def __init__(self, client: ClobClient, calls_per_second: float = DEFAULT_RATE_LIMIT):
        """
        Args:
            client: The underlying ClobClient to wrap.
            calls_per_second: Maximum API calls per second (across all threads).
        """
        self._client = client
        self._min_interval = 1.0 / calls_per_second if calls_per_second > 0 else 0
        self._last_call_time = 0.0
        self._lock = threading.Lock()
        self._call_count = 0

    def _wait(self):
        """Block until enough time has passed since the last API call."""
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call_time = time.monotonic()
            self._call_count += 1

    @property
    def api_call_count(self) -> int:
        """Total number of rate-limited API calls made through this wrapper."""
        return self._call_count

    def __getattr__(self, name):
        """Proxy attribute access to the underlying client, with rate limiting for API methods."""
        attr = getattr(self._client, name)

        if name in self._RATE_LIMITED_METHODS and callable(attr):
            def rate_limited_call(*args, **kwargs):
                self._wait()
                return attr(*args, **kwargs)
            return rate_limited_call

        return attr


class ClientManager:
    """
    Singleton manager for shared ClobClient instances.

    Provides two clients:
    - read: unauthenticated, for public data (orderbook, prices, midpoints)
    - auth: authenticated, for trading (place/cancel orders, get positions)

    Thread-safe lazy initialization.
    """

    def __init__(self):
        self._read_client: Optional[ClobClient] = None
        self._auth_client: Optional[ClobClient] = None
        self._lock = threading.Lock()
        self._auth_initialized = False
        self._auth_error: Optional[str] = None

    # ── Read-only client ──────────────────────────────────────

    @property
    def read(self) -> RateLimitedClient:
        """
        Get the shared read-only (unauthenticated) ClobClient.
        Used for: get_order_book, get_midpoint, get_price, etc.
        Created lazily on first access. Rate-limited automatically.
        """
        if self._read_client is None:
            with self._lock:
                if self._read_client is None:
                    raw_client = ClobClient(config.clob_host)
                    self._read_client = RateLimitedClient(raw_client)
        return self._read_client

    # ── Authenticated client ──────────────────────────────────

    @property
    def auth(self) -> Optional[RateLimitedClient]:
        """
        Get the shared authenticated ClobClient.
        Used for: create_order, post_order, cancel, get_orders, etc.
        Returns None if credentials aren't configured or auth failed.
        Rate-limited automatically.
        """
        if not config.has_credentials:
            return None

        if not self._auth_initialized:
            with self._lock:
                if not self._auth_initialized:
                    self._init_auth_client()
        return self._auth_client

    @property
    def has_auth(self) -> bool:
        """Check if authenticated client is available and ready."""
        return self.auth is not None

    @property
    def auth_error(self) -> Optional[str]:
        """Get the error message if auth initialization failed."""
        return self._auth_error

    def _init_auth_client(self):
        """Initialize the authenticated client. Called once."""
        try:
            client = ClobClient(
                config.clob_host,
                key=config.private_key,
                chain_id=config.CHAIN_ID,
                signature_type=config.signature_type,
                funder=config.funder_address,
            )
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)

            self._auth_client = RateLimitedClient(client)
            self._auth_error = None
            logger.info("✅ Authenticated trading client initialized")

        except Exception as e:
            self._auth_client = None
            self._auth_error = str(e)
            logger.error(f"❌ Failed to initialize authenticated client: {e}")

        finally:
            self._auth_initialized = True

    # ── Lifecycle ─────────────────────────────────────────────

    def reset(self):
        """
        Reset all clients. Useful for reconnecting after errors
        or when credentials change.
        """
        with self._lock:
            self._read_client = None
            self._auth_client = None
            self._auth_initialized = False
            self._auth_error = None

    def status(self) -> dict:
        """Get status summary for diagnostics/dashboard."""
        read_calls = self._read_client.api_call_count if self._read_client else 0
        auth_calls = self._auth_client.api_call_count if self._auth_client else 0
        return {
            "read_client": "ready" if self._read_client else "not initialized",
            "auth_client": (
                "ready" if self._auth_client
                else f"failed: {self._auth_error}" if self._auth_error
                else "no credentials"
            ),
            "has_credentials": config.has_credentials,
            "rate_limit": f"{DEFAULT_RATE_LIMIT} req/s",
            "api_calls": {"read": read_calls, "auth": auth_calls},
        }


# ── Singleton instance ────────────────────────────────────────
# Import this everywhere: from client_manager import clients
clients = ClientManager()
