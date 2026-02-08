"""
Order Tracker — monitors placed orders and updates portfolio on confirmed fills.

The Problem (before this module):
    trader.buy() → post_order() succeeds → portfolio.add_position() immediately
    But post_order() success only means the order is LIVE on the book, not FILLED.
    Result: phantom positions at incorrect prices.

The Fix (this module):
    trader.buy() → post_order() succeeds → order_tracker.track(order_id, ...)
    order_tracker polls get_order(order_id) periodically:
        - LIVE: do nothing, keep polling
        - MATCHED/FILLED: call portfolio.add_position() with actual fill price
        - PARTIALLY_FILLED: add the filled portion, keep tracking remainder
        - CANCELLED/EXPIRED: clean up, no position created

Usage:
    tracker = OrderTracker(portfolio)
    tracker.start()  # starts background polling thread

    # When placing an order:
    response = client.post_order(order)
    order_id = response.get("orderID")
    tracker.track_order(order_id, token_id, side, size, limit_price, ...)

    # The tracker handles the rest — portfolio is updated automatically
    # when (and only when) fills are confirmed.
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Callable
from dataclasses import dataclass, field

from client_manager import clients
from persistence import db
import logging
logger = logging.getLogger(__name__)


@dataclass
class TrackedOrder:
    """An order being tracked for fills."""

    order_id: str
    token_id: str
    market_question: str
    side: str  # YES or NO
    order_side: str  # BUY or SELL
    size: float  # Total requested size
    limit_price: float
    strategy: Optional[str] = None

    # Fill tracking
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    status: str = "LIVE"  # LIVE, MATCHED, PARTIALLY_FILLED, CANCELLED, EXPIRED

    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    last_checked: Optional[datetime] = None
    stale_after: timedelta = field(default_factory=lambda: timedelta(minutes=30))

    @property
    def remaining_size(self) -> float:
        return max(self.size - self.filled_size, 0)

    @property
    def is_fully_filled(self) -> bool:
        return self.filled_size >= self.size * 0.999  # float tolerance

    @property
    def is_terminal(self) -> bool:
        """Order is in a final state and no longer needs tracking."""
        return self.status in ("MATCHED", "CANCELLED", "EXPIRED") or self.is_fully_filled

    @property
    def is_stale(self) -> bool:
        """Order has been live too long without filling."""
        return datetime.now() - self.created_at > self.stale_after


class OrderTracker:
    """
    Tracks placed orders and fires callbacks when fills are confirmed.

    The tracker polls the CLOB API to check order status. When a fill is
    detected, it calls the on_fill callback (which should update the
    portfolio and record the trade). This decouples order placement from
    position tracking.

    The tracker persists its state to the pending_orders table in SQLite
    so it survives bot restarts.
    """

    def __init__(
        self,
        on_fill: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
        poll_interval: int = 5,
        stale_timeout_seconds: int = 1800,
    ):
        """
        Args:
            on_fill: Called when a fill is detected.
                     Signature: on_fill(order: TrackedOrder, new_fill_size: float, fill_price: float)
            on_cancel: Called when an order is cancelled/expired.
                       Signature: on_cancel(order: TrackedOrder)
            poll_interval: Seconds between API polls
            stale_timeout_seconds: Cancel (and attempt to cancel on-exchange) for orders older than this
        """
        self.on_fill = on_fill
        self.on_cancel = on_cancel
        self.poll_interval = poll_interval
        self.stale_timeout = timedelta(seconds=stale_timeout_seconds)

        self._orders: dict[str, TrackedOrder] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Load any pending orders from database (crash recovery)
        self._load_from_db()

    # ── Public API ────────────────────────────────────────────

    def track_order(
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
        """
        Start tracking a newly placed order.

        Call this right after post_order() succeeds (NOT portfolio.add_position()).
        """
        order = TrackedOrder(
            order_id=order_id,
            token_id=token_id,
            market_question=market_question,
            side=side,
            order_side=order_side,
            size=size,
            limit_price=limit_price,
            strategy=strategy,
            stale_after=self.stale_timeout,
        )

        with self._lock:
            self._orders[order_id] = order

        # Persist to database
        db.save_pending_order(
            order_id=order_id,
            token_id=token_id,
            market_question=market_question,
            side=side,
            order_side=order_side,
            size=size,
            limit_price=limit_price,
            strategy=strategy,
        )

        logger.info(f"📋 Tracking order {order_id}: {order_side} {size:.1f} {side} @ {limit_price:.4f}")

    def get_tracked_orders(self) -> list[TrackedOrder]:
        """Get all currently tracked orders."""
        with self._lock:
            return list(self._orders.values())

    def get_order(self, order_id: str) -> Optional[TrackedOrder]:
        """Get a specific tracked order."""
        with self._lock:
            return self._orders.get(order_id)

    @property
    def pending_count(self) -> int:
        """Number of orders still being tracked."""
        with self._lock:
            return sum(1 for o in self._orders.values() if not o.is_terminal)

    def cancel_tracking(self, order_id: str):
        """Stop tracking an order (does NOT cancel the order on the exchange)."""
        with self._lock:
            if order_id in self._orders:
                self._orders[order_id].status = "CANCELLED"
        db.update_pending_order(order_id, "CANCELLED")

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Start background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"🔄 Order tracker started (polling every {self.poll_interval}s)")

    def stop(self):
        """Stop background polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("⏹️ Order tracker stopped")

    def poll_once(self):
        """Run one poll cycle (useful for testing or manual triggering)."""
        self._check_all_orders()

    # ── Internal ──────────────────────────────────────────────

    def _load_from_db(self):
        """Load pending orders from database (crash recovery)."""
        rows = db.get_pending_orders()
        for row in rows:
            order = TrackedOrder(
                order_id=row["order_id"],
                token_id=row["token_id"],
                market_question=row["market_question"],
                side=row["side"],
                order_side=row["order_side"],
                size=row["size"],
                limit_price=row["limit_price"],
                filled_size=row.get("filled_size", 0),
                avg_fill_price=row.get("avg_fill_price", 0),
                status=row.get("status", "LIVE"),
                strategy=row.get("strategy"),
                created_at=datetime.fromisoformat(row["created_at"]),
                stale_after=self.stale_timeout,
            )
            self._orders[order.order_id] = order

        if rows:
            logger.info(f"📋 Recovered {len(rows)} pending orders from database")

    def _poll_loop(self):
        """Background thread: poll all tracked orders."""
        while self._running:
            try:
                self._check_all_orders()
            except Exception as e:
                logger.warning(f"⚠️ Order tracker error: {e}")
            time.sleep(self.poll_interval)

    def _check_all_orders(self):
        """Check status of all non-terminal orders."""
        with self._lock:
            active = [
                o for o in self._orders.values()
                if not o.is_terminal
            ]

        if not active or not clients.has_auth:
            return

        for order in active:
            try:
                self._check_order(order)
            except Exception as e:
                logger.warning(f"⚠️ Failed to check order {order.order_id}: {e}")

    def _check_order(self, order: TrackedOrder):
        """Poll the CLOB API for a single order's fill status."""
        # Check for stale orders
        if order.is_stale:
            # Stale LIVE order: attempt to cancel on-exchange, then stop tracking
            logger.info(f"⏰ Order {order.order_id} stale after {self.stale_timeout} — attempting cancel")
            cancelled = False
            try:
                if clients.has_auth:
                    # ClobClient uses cancel(order_id)
                    resp = clients.auth.cancel(order.order_id)
                    # Best-effort: some versions return {"success": True}
                    if isinstance(resp, dict):
                        cancelled = bool(resp.get("success", True))
                    else:
                        cancelled = True
            except Exception:
                cancelled = False

            order.status = "CANCELLED" if cancelled else "EXPIRED"
            db.update_pending_order(order.order_id, order.status, order.filled_size, order.avg_fill_price)
            if self.on_cancel:
                self.on_cancel(order)
            return

        try:
            api_order = clients.auth.get_order(order.order_id)
        except Exception as e:
            # API error — skip this cycle, don't change state
            return

        if not api_order:
            return

        order.last_checked = datetime.now()

        # Extract fill info from API response
        # The CLOB API returns different fields depending on version.
        # Common fields: status, size_matched, price, original_size
        api_status = str(api_order.get("status", "")).upper()
        size_matched = float(api_order.get("size_matched", 0))
        # The API may return associate_trades with actual fill prices
        trades = api_order.get("associate_trades", []) or []

        # Compute average fill price from trades if available
        if trades:
            total_value = sum(
                float(t.get("size", 0)) * float(t.get("price", 0))
                for t in trades
            )
            total_size = sum(float(t.get("size", 0)) for t in trades)
            fill_price = total_value / total_size if total_size > 0 else order.limit_price
            size_matched = max(size_matched, total_size)
        else:
            fill_price = float(api_order.get("price", order.limit_price))

        # Detect new fills since last check
        prev_filled = order.filled_size
        new_fill = size_matched - prev_filled

        if new_fill > 0.001:  # Meaningful new fill
            order.filled_size = size_matched

            # Weighted average fill price
            if order.avg_fill_price > 0 and prev_filled > 0:
                order.avg_fill_price = (
                    (prev_filled * order.avg_fill_price + new_fill * fill_price)
                    / size_matched
                )
            else:
                order.avg_fill_price = fill_price

            logger.info(
                f"✅ Fill detected: {order.order_id} — "
                f"{new_fill:.2f} @ {fill_price:.4f} "
                f"(total: {size_matched:.2f}/{order.size:.2f})"
            )

            # Fire callback
            if self.on_fill:
                self.on_fill(order, new_fill, fill_price)

        # Update terminal status
        if api_status in ("MATCHED", "FILLED") or order.is_fully_filled:
            order.status = "MATCHED"
            db.update_pending_order(
                order.order_id, "MATCHED",
                order.filled_size, order.avg_fill_price
            )
            logger.info(f"🏁 Order {order.order_id} fully filled at avg {order.avg_fill_price:.4f}")

        elif api_status == "CANCELLED":
            order.status = "CANCELLED"
            db.update_pending_order(
                order.order_id, "CANCELLED",
                order.filled_size, order.avg_fill_price
            )
            if order.filled_size > 0:
                logger.warning(f"⚠️ Order {order.order_id} cancelled with partial fill: {order.filled_size:.2f}/{order.size:.2f}")
            else:
                logger.error(f"❌ Order {order.order_id} cancelled (unfilled)")
            if self.on_cancel:
                self.on_cancel(order)

        else:
            # Still live — update DB with latest fill info
            status = "PARTIALLY_FILLED" if order.filled_size > 0 else "LIVE"
            order.status = status
            db.update_pending_order(
                order.order_id, status,
                order.filled_size, order.avg_fill_price
            )
