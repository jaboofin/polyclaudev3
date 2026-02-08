"""
Persistence - SQLite storage for portfolio, trades, price history, and bot state.

PROBLEM THIS SOLVES:
Previously, all state lived in memory. A crash lost everything:
- Open positions (unknown after restart)
- Trade history (can't calculate real P&L)
- Price snapshots (no historical analysis)
- Bot configuration (had to re-enter)

NOW: Everything auto-saves to a local SQLite database. The bot can crash,
restart, and pick up exactly where it left off.

Usage:
    from persistence import db

    # Positions
    db.save_position(position)
    positions = db.load_positions()

    # Trades
    db.record_trade(trade)
    history = db.get_trade_history(limit=100)

    # Price snapshots
    db.save_price_snapshot(token_id, price_yes, price_no, best_bid, best_ask)
    history = db.get_price_history(token_id, hours=24)

    # Key-value store for bot state
    db.set_state("last_scan_time", "2025-02-07T12:00:00")
    val = db.get_state("last_scan_time")
"""

import os
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager
import logging
logger = logging.getLogger(__name__)


# Default DB path - can be overridden via env var
DB_PATH = os.getenv("BOT_DB_PATH", os.path.join(os.path.dirname(__file__), "bot_data.db"))


class Database:
    """
    SQLite persistence layer for the Polymarket bot.

    Thread-safe: uses a connection-per-thread pattern via threading.local().
    All writes are auto-committed.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._init_schema()

    # ── Connection management ─────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    @contextmanager
    def _cursor(self):
        """Context manager that provides a cursor and auto-commits."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self):
        """Create tables if they don't exist."""
        with self._cursor() as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    id          TEXT PRIMARY KEY,   -- "{token_id}_{side}"
                    token_id    TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    side        TEXT NOT NULL,       -- "YES" or "NO"
                    size        REAL NOT NULL,
                    avg_entry_price REAL NOT NULL,
                    current_price   REAL DEFAULT 0,
                    opened_at   TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    token_id    TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    side        TEXT NOT NULL,
                    action      TEXT NOT NULL,       -- "BUY" or "SELL"
                    size        REAL NOT NULL,
                    price       REAL NOT NULL,
                    fee         REAL DEFAULT 0,
                    order_id    TEXT,
                    strategy    TEXT
                );

                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id    TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    price_yes   REAL NOT NULL,
                    price_no    REAL NOT NULL,
                    best_bid    REAL,
                    best_ask    REAL
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auto_orders (
                    id          TEXT PRIMARY KEY,
                    token_id    TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    order_type  TEXT NOT NULL,
                    side        TEXT NOT NULL,
                    size        REAL NOT NULL,
                    trigger_price REAL NOT NULL,
                    limit_price REAL,
                    trailing_percent REAL,
                    highest_price REAL DEFAULT 0,
                    state       TEXT DEFAULT 'pending',
                    created_at  TEXT NOT NULL,
                    triggered_at TEXT,
                    executed_at TEXT
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_trades_token
                    ON trades(token_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp
                    ON trades(timestamp);
                CREATE INDEX IF NOT EXISTS idx_snapshots_token_time
                    ON price_snapshots(token_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_auto_orders_state
                    ON auto_orders(state);

                CREATE TABLE IF NOT EXISTS pending_orders (
                    order_id    TEXT PRIMARY KEY,
                    token_id    TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    side        TEXT NOT NULL,       -- "YES" or "NO"
                    order_side  TEXT NOT NULL,       -- "BUY" or "SELL"
                    size        REAL NOT NULL,
                    limit_price REAL NOT NULL,
                    status      TEXT DEFAULT 'LIVE', -- LIVE, FILLED, PARTIALLY_FILLED, CANCELLED, EXPIRED
                    filled_size REAL DEFAULT 0,
                    avg_fill_price REAL DEFAULT 0,
                    strategy    TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pending_orders_status
                    ON pending_orders(status);

                CREATE TABLE IF NOT EXISTS order_intents (
                    intent_id   TEXT PRIMARY KEY,
                    token_id    TEXT NOT NULL,
                    side        TEXT NOT NULL,      -- YES/NO/ARB
                    order_side  TEXT NOT NULL,      -- BUY/SELL
                    limit_price REAL,
                    size        REAL,
                    strategy    TEXT,
                    created_at  TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_order_intents_token
                    ON order_intents(token_id, created_at);
            """)

    # ── Positions ─────────────────────────────────────────────

    def save_position(
        self,
        token_id: str,
        side: str,
        market_question: str,
        size: float,
        avg_entry_price: float,
        current_price: float = 0.0,
    ):
        """Insert or update a position."""
        key = f"{token_id}_{side}"
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO positions (id, token_id, market_question, side, size,
                                       avg_entry_price, current_price, opened_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    size = excluded.size,
                    avg_entry_price = excluded.avg_entry_price,
                    current_price = excluded.current_price,
                    updated_at = excluded.updated_at
            """, (key, token_id, market_question, side, size,
                  avg_entry_price, current_price, now, now))

    def remove_position(self, token_id: str, side: str):
        """Delete a closed position."""
        key = f"{token_id}_{side}"
        with self._cursor() as cur:
            cur.execute("DELETE FROM positions WHERE id = ?", (key,))

    def load_positions(self) -> list[dict]:
        """Load all open positions."""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM positions ORDER BY opened_at")
            return [dict(row) for row in cur.fetchall()]

    def update_position_price(self, token_id: str, side: str, current_price: float):
        """Update just the current price of a position."""
        key = f"{token_id}_{side}"
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE positions SET current_price = ?, updated_at = ? WHERE id = ?",
                (current_price, now, key),
            )

    # ── Trades ────────────────────────────────────────────────

    def record_trade(
        self,
        token_id: str,
        market_question: str,
        side: str,
        action: str,
        size: float,
        price: float,
        fee: float = 0.0,
        order_id: Optional[str] = None,
        strategy: Optional[str] = None,
    ):
        """Record a completed trade."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO trades (timestamp, token_id, market_question, side,
                                    action, size, price, fee, order_id, strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (now, token_id, market_question, side, action,
                  size, price, fee, order_id, strategy))

    def get_trade_history(
        self,
        limit: int = 100,
        token_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        """Get trade history with optional filters."""
        query = "SELECT * FROM trades WHERE 1=1"
        params = []

        if token_id:
            query += " AND token_id = ?"
            params.append(token_id)
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._cursor() as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_realized_pnl(self) -> float:
        """Calculate total realized P&L from trade history."""
        with self._cursor() as cur:
            # Sum up: sells are proceeds, buys are costs
            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN action = 'SELL' THEN size * price - fee ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN action = 'BUY' THEN size * price + fee ELSE 0 END), 0)
                    AS realized_pnl
                FROM trades
                WHERE token_id IN (
                    SELECT DISTINCT token_id FROM trades WHERE action = 'SELL'
                )
            """)
            row = cur.fetchone()
            return row["realized_pnl"] if row else 0.0

    def get_trade_stats(self) -> dict:
        """Get aggregate trading statistics."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as total FROM trades")
            total = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) as buys FROM trades WHERE action = 'BUY'")
            buys = cur.fetchone()["buys"]

            cur.execute("SELECT COUNT(*) as sells FROM trades WHERE action = 'SELL'")
            sells = cur.fetchone()["sells"]

            cur.execute("""
                SELECT COUNT(*) as wins FROM trades t1
                WHERE action = 'SELL'
                AND price > (
                    SELECT MAX(price) FROM trades t2
                    WHERE t2.token_id = t1.token_id
                    AND t2.side = t1.side
                    AND t2.action = 'BUY'
                    AND t2.timestamp < t1.timestamp
                )
            """)
            wins = cur.fetchone()["wins"]

            return {
                "total_trades": total,
                "buys": buys,
                "sells": sells,
                "wins": wins,
                "losses": sells - wins,
                "win_rate": (wins / sells * 100) if sells > 0 else 0.0,
            }

    # ── Price Snapshots ───────────────────────────────────────

    def save_price_snapshot(
        self,
        token_id: str,
        price_yes: float,
        price_no: float,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
    ):
        """Save a price observation."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO price_snapshots (token_id, timestamp, price_yes,
                                             price_no, best_bid, best_ask)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (token_id, now, price_yes, price_no, best_bid, best_ask))

    def get_price_history(
        self,
        token_id: str,
        hours: int = 24,
        limit: int = 1000,
    ) -> list[dict]:
        """Get price history for a token within the last N hours."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._cursor() as cur:
            cur.execute("""
                SELECT * FROM price_snapshots
                WHERE token_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (token_id, since, limit))
            return [dict(row) for row in cur.fetchall()]

    def cleanup_old_snapshots(self, days: int = 7):
        """Delete price snapshots older than N days to manage DB size."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._cursor() as cur:
            cur.execute("DELETE FROM price_snapshots WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            if deleted:
                logger.info(f"🗑️ Cleaned up {deleted} old price snapshots")

    # ── Pending Order Tracking ─────────────────────────────────

    def save_pending_order(
        self,
        order_id: str,
        token_id: str,
        market_question: str,
        side: str,
        order_side: str,
        size: float,
        limit_price: float,
        strategy: Optional[str] = None,
    ):
        """Record a newly placed order as LIVE (not yet filled)."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO pending_orders
                (order_id, token_id, market_question, side, order_side,
                 size, limit_price, status, filled_size, avg_fill_price,
                 strategy, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'LIVE', 0, 0, ?, ?, ?)
            """, (order_id, token_id, market_question, side, order_side,
                  size, limit_price, strategy, now, now))

    def update_pending_order(
        self,
        order_id: str,
        status: str,
        filled_size: float = 0,
        avg_fill_price: float = 0,
    ):
        """Update a pending order's fill status."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                UPDATE pending_orders
                SET status = ?, filled_size = ?, avg_fill_price = ?,
                    updated_at = ?
                WHERE order_id = ?
            """, (status, filled_size, avg_fill_price, now, order_id))

    def get_pending_orders(self, status: Optional[str] = None) -> list[dict]:
        """Get pending orders, optionally filtered by status."""
        with self._cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM pending_orders WHERE status = ? ORDER BY created_at",
                    (status,)
                )
            else:
                cur.execute(
                    "SELECT * FROM pending_orders WHERE status IN ('LIVE', 'PARTIALLY_FILLED') ORDER BY created_at"
                )
            return [dict(row) for row in cur.fetchall()]

    def get_pending_order(self, order_id: str) -> Optional[dict]:
        """Get a single pending order by ID."""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM pending_orders WHERE order_id = ?", (order_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def remove_pending_order(self, order_id: str):
        """Remove a pending order (after it's fully processed)."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM pending_orders WHERE order_id = ?", (order_id,))
    def has_live_order_for_token(self, token_id: str) -> bool:
        """Return True if there is any LIVE/PARTIALLY_FILLED pending order for token_id."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) as n
                   FROM pending_orders
                   WHERE token_id = ?
                     AND status IN ('LIVE','PARTIALLY_FILLED')""",
                (token_id,),
            )
            return int(cur.fetchone()["n"]) > 0

    # ── Order Intents (idempotency) ──────────────────────────

    def save_order_intent(
        self,
        intent_id: str,
        token_id: str,
        side: str,
        order_side: str,
        limit_price: float | None,
        size: float | None,
        strategy: str | None,
    ):
        """Record an order intent to prevent duplicate submissions."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute(
                """INSERT OR REPLACE INTO order_intents
                   (intent_id, token_id, side, order_side, limit_price, size, strategy, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (intent_id, token_id, side, order_side, limit_price, size, strategy, now),
            )

    def get_order_intent(self, intent_id: str) -> Optional[dict]:
        """Get an order intent by id."""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM order_intents WHERE intent_id = ?", (intent_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_order_intent(self, intent_id: str):
        """Delete an order intent."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM order_intents WHERE intent_id = ?", (intent_id,))

    def cleanup_old_order_intents(self, older_than_seconds: int = 3600) -> int:
        """Remove order intents older than N seconds."""
        cutoff = (datetime.now() - timedelta(seconds=older_than_seconds)).isoformat()
        with self._cursor() as cur:
            cur.execute("DELETE FROM order_intents WHERE created_at < ?", (cutoff,))
            return cur.rowcount



    # ── Auto Orders (TP/SL/Trailing) ─────────────────────────

    def save_auto_order(
        self,
        order_id: str,
        token_id: str,
        market_question: str,
        order_type: str,
        side: str,
        size: float,
        trigger_price: float,
        limit_price: Optional[float] = None,
        trailing_percent: Optional[float] = None,
    ):
        """Save an automated order (take profit, stop loss, etc.)."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO auto_orders (id, token_id, market_question, order_type,
                                         side, size, trigger_price, limit_price,
                                         trailing_percent, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state,
                    trigger_price = excluded.trigger_price
            """, (order_id, token_id, market_question, order_type, side,
                  size, trigger_price, limit_price, trailing_percent, now))

    def update_auto_order_state(self, order_id: str, state: str):
        """Update the state of an auto order."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            time_col = ""
            if state == "triggered":
                time_col = ", triggered_at = ?"
            elif state in ("executed", "failed"):
                time_col = ", executed_at = ?"

            if time_col:
                cur.execute(
                    f"UPDATE auto_orders SET state = ?{time_col} WHERE id = ?",
                    (state, now, order_id),
                )
            else:
                cur.execute(
                    "UPDATE auto_orders SET state = ? WHERE id = ?",
                    (state, order_id),
                )

    def get_active_auto_orders(self) -> list[dict]:
        """Get all pending/active auto orders."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM auto_orders WHERE state IN ('pending', 'active') ORDER BY created_at"
            )
            return [dict(row) for row in cur.fetchall()]

    def update_trailing_stop_price(self, order_id: str, highest_price: float):
        """Update the highest tracked price for a trailing stop."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE auto_orders SET highest_price = ? WHERE id = ?",
                (highest_price, order_id),
            )

    # ── Key-Value State ───────────────────────────────────────

    def set_state(self, key: str, value: str):
        """Store a key-value pair (for bot state, config, etc.)."""
        now = datetime.now().isoformat()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO bot_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, value, now))

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a stored value by key."""
        with self._cursor() as cur:
            cur.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_state_json(self, key: str, value):
        """Store a JSON-serializable value."""
        self.set_state(key, json.dumps(value))

    def get_state_json(self, key: str, default=None):
        """Retrieve a JSON value."""
        raw = self.get_state(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    # ── Diagnostics ───────────────────────────────────────────

    def stats(self) -> dict:
        """Get database statistics."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM positions")
            positions = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM trades")
            trades = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM price_snapshots")
            snapshots = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM auto_orders WHERE state IN ('pending','active')")
            active_orders = cur.fetchone()["n"]

        return {
            "db_path": self.db_path,
            "positions": positions,
            "trades": trades,
            "price_snapshots": snapshots,
            "active_auto_orders": active_orders,
        }

    def close(self):
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ── Singleton instance ────────────────────────────────────────
# Import this everywhere: from persistence import db
db = Database()
