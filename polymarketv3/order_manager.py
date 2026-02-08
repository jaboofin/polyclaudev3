"""
Order Manager - Automated Take Profit, Stop Loss, and Order Management

Handles:
- Take Profit (TP) orders - auto-sell when price hits target
- Stop Loss (SL) orders - auto-sell to limit losses  
- Trailing Stops - dynamic stop loss that follows price up
- OCO (One-Cancels-Other) orders
- Order monitoring and execution
"""

import time
import threading
from datetime import datetime
from typing import Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum

from config import config
from client_manager import clients
from persistence import db
from trader import Trader, OrderResult
from portfolio import PortfolioManager
from order_tracker import OrderTracker
import logging
logger = logging.getLogger(__name__)


class OrderType(Enum):
    """Types of automated orders."""
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    LIMIT_BUY = "limit_buy"
    LIMIT_SELL = "limit_sell"


class OrderState(Enum):
    """Order states."""
    PENDING = "pending"
    ACTIVE = "active"
    TRIGGERED = "triggered"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class AutoOrder:
    """Automated order with trigger conditions."""
    id: str
    token_id: str
    market_question: str
    order_type: OrderType
    side: str  # "YES" or "NO"
    size: float
    trigger_price: float
    limit_price: Optional[float] = None  # For limit orders
    trailing_percent: Optional[float] = None  # For trailing stops
    highest_price: float = 0.0  # Track for trailing stop
    state: OrderState = OrderState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    triggered_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    execution_price: Optional[float] = None
    linked_order_id: Optional[str] = None  # For OCO orders
    callback: Optional[Callable] = None


@dataclass
class Position:
    """Position with associated orders."""
    token_id: str
    market_question: str
    side: str
    size: float
    entry_price: float
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    trailing_stop_percent: Optional[float] = None


class OrderManager:
    """
    Manages automated orders including Take Profit and Stop Loss.
    
    Usage:
        manager = OrderManager()
        
        # Place a buy with automatic TP/SL
        manager.buy_with_tp_sl(
            token_id="...",
            market_question="Will BTC hit 100k?",
            size=100,
            entry_price=0.45,
            take_profit=0.65,  # Sell at 65%
            stop_loss=0.35     # Cut losses at 35%
        )
        
        # Or add TP/SL to existing position
        manager.set_take_profit(token_id="...", price=0.70, size=50)
        manager.set_stop_loss(token_id="...", price=0.30, size=50)
        
        # Start monitoring
        manager.start_monitoring()
    """
    
    def __init__(self, trader: Optional[Trader] = None):
        self.trader = trader or Trader()
        self.portfolio = self.trader.portfolio
        
        # Order fill tracker — handles the LIVE → FILLED lifecycle
        self.order_tracker = OrderTracker(
            on_fill=self._on_order_fill,
            on_cancel=self._on_order_cancel,
            poll_interval=5,
            stale_timeout_minutes=30,
        )
        
        self.orders: dict[str, AutoOrder] = {}
        self.positions: dict[str, Position] = {}
        
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._order_counter = 0
        
        # Callbacks
        self.on_order_triggered: Optional[Callable] = None
        self.on_order_executed: Optional[Callable] = None
        self.on_order_failed: Optional[Callable] = None

    def _on_order_fill(self, tracked_order, new_fill_size: float, fill_price: float):
        """Called by OrderTracker when a fill is confirmed on the exchange."""
        if str(tracked_order.order_side).upper() == "SELL":
            # Reduce position only when sell fills are confirmed
            realized = self.portfolio.close_position(
                token_id=tracked_order.token_id,
                side=tracked_order.side,
                size=new_fill_size,
                exit_price=fill_price,
            )
            logger.info(
                f"✅ Sell fill confirmed: -{new_fill_size:.2f} {tracked_order.side} "
                f"@ {fill_price:.4f} | Realized: ${realized:.2f}"
            )
            return

        self.portfolio.add_position(
            token_id=tracked_order.token_id,
            market_question=tracked_order.market_question,
            side=tracked_order.side,
            size=new_fill_size,
            entry_price=fill_price,
        )
        # Record position for TP/SL logic (only after fill confirmed)
        self.positions[tracked_order.token_id] = Position(
            token_id=tracked_order.token_id,
            market_question=tracked_order.market_question,
            side=tracked_order.side,
            size=new_fill_size,
            entry_price=fill_price,
        )
        logger.info(
            f"💼 Position updated: +{new_fill_size:.2f} {tracked_order.side} "
            f"@ {fill_price:.4f} ({tracked_order.market_question[:35]}...)"
        )

    def _on_order_cancel(self, tracked_order):
        """Called by OrderTracker when an order is cancelled/expired unfilled."""
        if tracked_order.filled_size > 0:
            logger.info(
                f"⚠️ Order {tracked_order.order_id} cancelled with partial fill "
                f"({tracked_order.filled_size:.2f}/{tracked_order.size:.2f})"
            )
        else:
            logger.info(f"📭 Order {tracked_order.order_id} cancelled/expired — no position created")
    
    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        self._order_counter += 1
        return f"AUTO_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self._order_counter}"
    
    # ==================== BUY METHODS ====================
    
    def buy(
        self,
        token_id: str,
        market_question: str,
        size: float,
        price: float,
        side: str = "YES",
        strategy: str = None
    ) -> OrderResult:
        """
        Place a buy order and track it for fills.
        
        Position is NOT created immediately. The OrderTracker will poll
        the exchange for fill confirmation and update the portfolio only
        when (and if) the order fills.
        
        Args:
            token_id: Token ID to buy
            market_question: Market question text
            size: Number of shares
            price: Limit price
            side: "YES" or "NO"
            strategy: Strategy name for trade logging
        
        Returns:
            OrderResult
        """
        result = self.trader.buy(
            token_id=token_id,
            price=price,
            size=size,
            market_question=market_question
        )
        
        if result.success and result.order_id:
            # Track the order — portfolio will be updated on confirmed fill
            self.order_tracker.track_order(
                order_id=result.order_id,
                token_id=token_id,
                market_question=market_question,
                side=side,
                order_side="BUY",
                size=size,
                limit_price=price,
                strategy=strategy,
            )
            logger.info(f"📋 Order {result.order_id} placed — awaiting fill confirmation")
        
        return result
    
    def buy_with_tp_sl(
        self,
        token_id: str,
        market_question: str,
        size: float,
        entry_price: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        trailing_stop_percent: Optional[float] = None,
        side: str = "YES"
    ) -> dict:
        """
        Buy with automatic Take Profit and/or Stop Loss.
        
        Args:
            token_id: Token ID
            market_question: Market question
            size: Number of shares to buy
            entry_price: Entry price
            take_profit: Take profit price (e.g., 0.70 for 70¢)
            stop_loss: Stop loss price (e.g., 0.30 for 30¢)
            trailing_stop_percent: Trailing stop as % (e.g., 0.10 for 10%)
            side: "YES" or "NO"
        
        Returns:
            Dict with order IDs and results
        """
        result = {
            "success": False,
            "buy_result": None,
            "take_profit_id": None,
            "stop_loss_id": None,
            "trailing_stop_id": None
        }
        
        # Place buy order
        buy_result = self.buy(token_id, market_question, size, entry_price, side)
        result["buy_result"] = buy_result
        
        if not buy_result.success:
            logger.error(f"❌ Buy failed: {buy_result.error}")
            return result
        
        result["success"] = True
        
        # Set Take Profit
        if take_profit:
            tp_id = self.set_take_profit(
                token_id=token_id,
                price=take_profit,
                size=size,
                market_question=market_question,
                side=side
            )
            result["take_profit_id"] = tp_id
        
        # Set Stop Loss
        if stop_loss:
            sl_id = self.set_stop_loss(
                token_id=token_id,
                price=stop_loss,
                size=size,
                market_question=market_question,
                side=side
            )
            result["stop_loss_id"] = sl_id
        
        # Set Trailing Stop
        if trailing_stop_percent:
            ts_id = self.set_trailing_stop(
                token_id=token_id,
                trail_percent=trailing_stop_percent,
                size=size,
                current_price=entry_price,
                market_question=market_question,
                side=side
            )
            result["trailing_stop_id"] = ts_id
        
        # Update position
        if token_id in self.positions:
            pos = self.positions[token_id]
            pos.take_profit_price = take_profit
            pos.stop_loss_price = stop_loss
            pos.trailing_stop_percent = trailing_stop_percent
        
        return result
    
    def market_buy_with_tp_sl(
        self,
        token_id: str,
        market_question: str,
        size: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        side: str = "YES"
    ) -> dict:
        """
        Market buy with automatic TP/SL.
        Uses best available price for entry.
        """
        # Get current best price
        try:
            book = clients.read.get_order_book(token_id)
            if book and book.asks:
                entry_price = float(book.asks[0].price) * 1.01  # 1% slippage
            else:
                return {"success": False, "error": "No liquidity"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        
        return self.buy_with_tp_sl(
            token_id=token_id,
            market_question=market_question,
            size=size,
            entry_price=entry_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            side=side
        )
    
    # ==================== SELL METHODS ====================
    
    def sell(
        self,
        token_id: str,
        size: float,
        price: float,
        market_question: str = "",
        side: str = "YES",
        strategy: str | None = None,
    ) -> OrderResult:
        """Place a limit sell order and track fills before updating portfolio."""

        # Idempotency for SELLs (do not block on kill switch)
        intent_id = self._make_intent_id(
            token_id=token_id,
            side=side,
            order_side="SELL",
            size=size,
            price=price,
            strategy=strategy,
        )
        if db.get_order_intent(intent_id) is not None:
            return OrderResult(success=False, error="Duplicate sell intent (idempotency guard)")
        db.save_order_intent(intent_id, token_id, side, "SELL", price, size, strategy)

        result = self.trader.sell(token_id, price, size)
        if result.success and result.order_id:
            self.order_tracker.track_order(
                order_id=result.order_id,
                token_id=token_id,
                market_question=market_question or "",
                side=side,
                order_side="SELL",
                size=size,
                limit_price=price,
                strategy=strategy,
            )
            logger.info(f"📋 Sell order {result.order_id} placed — awaiting fill confirmation")
        return result
    
    def market_sell(
        self,
        token_id: str,
        size: float,
        market_question: str = "",
        side: str = "YES",
        strategy: str | None = None,
    ) -> OrderResult:
        """Market sell (implemented as aggressive limit) and track fills."""
        result = self.trader.market_sell(token_id, size)
        if result.success and result.order_id:
            self.order_tracker.track_order(
                order_id=result.order_id,
                token_id=token_id,
                market_question=market_question or "",
                side=side,
                order_side="SELL",
                size=size,
                limit_price=result.average_price if result.average_price else 0.0,
                strategy=strategy,
            )
        return result
    
    # ==================== TAKE PROFIT ====================
    
    def set_take_profit(
        self,
        token_id: str,
        price: float,
        size: float,
        market_question: str = "",
        side: str = "YES"
    ) -> str:
        """
        Set a Take Profit order.
        Automatically sells when price reaches target.
        
        Args:
            token_id: Token ID
            price: Target price to sell at
            size: Number of shares to sell
            market_question: Market question
            side: "YES" or "NO"
        
        Returns:
            Order ID
        """
        order_id = self._generate_order_id()
        
        order = AutoOrder(
            id=order_id,
            token_id=token_id,
            market_question=market_question,
            order_type=OrderType.TAKE_PROFIT,
            side=side,
            size=size,
            trigger_price=price,
            state=OrderState.ACTIVE
        )
        
        self.orders[order_id] = order
        
        logger.info(f"📈 Take Profit set: Sell {size} {side} @ ${price:.4f}")
        logger.info(f"   Order ID: {order_id}")
        
        return order_id
    
    # ==================== STOP LOSS ====================
    
    def set_stop_loss(
        self,
        token_id: str,
        price: float,
        size: float,
        market_question: str = "",
        side: str = "YES"
    ) -> str:
        """
        Set a Stop Loss order.
        Automatically sells when price drops to limit losses.
        
        Args:
            token_id: Token ID  
            price: Stop price (sell if price drops to this)
            size: Number of shares to sell
            market_question: Market question
            side: "YES" or "NO"
        
        Returns:
            Order ID
        """
        order_id = self._generate_order_id()
        
        order = AutoOrder(
            id=order_id,
            token_id=token_id,
            market_question=market_question,
            order_type=OrderType.STOP_LOSS,
            side=side,
            size=size,
            trigger_price=price,
            state=OrderState.ACTIVE
        )
        
        self.orders[order_id] = order
        
        logger.info(f"🛑 Stop Loss set: Sell {size} {side} if price <= ${price:.4f}")
        logger.info(f"   Order ID: {order_id}")
        
        return order_id
    
    # ==================== TRAILING STOP ====================
    
    def set_trailing_stop(
        self,
        token_id: str,
        trail_percent: float,
        size: float,
        current_price: float,
        market_question: str = "",
        side: str = "YES"
    ) -> str:
        """
        Set a Trailing Stop order.
        Stop price moves up with price, locks in profits.
        
        Args:
            token_id: Token ID
            trail_percent: Trail distance as decimal (0.10 = 10%)
            size: Number of shares
            current_price: Current market price
            market_question: Market question
            side: "YES" or "NO"
        
        Returns:
            Order ID
        """
        order_id = self._generate_order_id()
        
        # Calculate initial stop price
        stop_price = current_price * (1 - trail_percent)
        
        order = AutoOrder(
            id=order_id,
            token_id=token_id,
            market_question=market_question,
            order_type=OrderType.TRAILING_STOP,
            side=side,
            size=size,
            trigger_price=stop_price,
            trailing_percent=trail_percent,
            highest_price=current_price,
            state=OrderState.ACTIVE
        )
        
        self.orders[order_id] = order
        
        logger.info(f"📉 Trailing Stop set: {trail_percent*100:.1f}% trail")
        logger.info(f"   Current: ${current_price:.4f} → Stop: ${stop_price:.4f}")
        logger.info(f"   Order ID: {order_id}")
        
        return order_id
    
    # ==================== OCO (ONE-CANCELS-OTHER) ====================
    
    def set_oco(
        self,
        token_id: str,
        size: float,
        take_profit_price: float,
        stop_loss_price: float,
        market_question: str = "",
        side: str = "YES"
    ) -> tuple[str, str]:
        """
        Set OCO (One-Cancels-Other) order pair.
        When one triggers, the other is cancelled.
        
        Returns:
            Tuple of (take_profit_id, stop_loss_id)
        """
        tp_id = self.set_take_profit(token_id, take_profit_price, size, market_question, side)
        sl_id = self.set_stop_loss(token_id, stop_loss_price, size, market_question, side)
        
        # Link orders
        self.orders[tp_id].linked_order_id = sl_id
        self.orders[sl_id].linked_order_id = tp_id
        
        logger.info(f"🔗 OCO pair created: TP {tp_id} <-> SL {sl_id}")
        
        return tp_id, sl_id
    
    # ==================== ORDER MANAGEMENT ====================
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an automated order."""
        if order_id not in self.orders:
            logger.warning(f"⚠️ Order {order_id} not found")
            return False
        
        order = self.orders[order_id]
        
        if order.state in [OrderState.EXECUTED, OrderState.CANCELLED]:
            logger.warning(f"⚠️ Order {order_id} already {order.state.value}")
            return False
        
        order.state = OrderState.CANCELLED
        logger.error(f"❌ Cancelled order {order_id}")
        
        return True
    
    def cancel_all_orders(self, token_id: Optional[str] = None) -> int:
        """Cancel all orders, optionally for specific token."""
        cancelled = 0
        
        for order_id, order in self.orders.items():
            if token_id and order.token_id != token_id:
                continue
            
            if order.state == OrderState.ACTIVE:
                order.state = OrderState.CANCELLED
                cancelled += 1
        
        logger.error(f"❌ Cancelled {cancelled} orders")
        return cancelled
    
    def get_active_orders(self, token_id: Optional[str] = None) -> list[AutoOrder]:
        """Get all active orders."""
        orders = [
            o for o in self.orders.values()
            if o.state == OrderState.ACTIVE
        ]
        
        if token_id:
            orders = [o for o in orders if o.token_id == token_id]
        
        return orders
    
    # ==================== MONITORING ====================
    
    def _get_current_price(self, token_id: str) -> Optional[float]:
        """Get current midpoint price for a token."""
        try:
            midpoint = clients.read.get_midpoint(token_id)
            return float(midpoint) if midpoint else None
        except Exception:
            return None
    
    def _check_order(self, order: AutoOrder, current_price: float) -> bool:
        """
        Check if order should trigger.
        Returns True if triggered.
        """
        if order.state != OrderState.ACTIVE:
            return False
        
        triggered = False
        
        if order.order_type == OrderType.TAKE_PROFIT:
            # Trigger when price >= target
            if current_price >= order.trigger_price:
                triggered = True
                logger.info(f"🎯 Take Profit TRIGGERED @ ${current_price:.4f}")
        
        elif order.order_type == OrderType.STOP_LOSS:
            # Trigger when price <= stop
            if current_price <= order.trigger_price:
                triggered = True
                logger.info(f"🛑 Stop Loss TRIGGERED @ ${current_price:.4f}")
        
        elif order.order_type == OrderType.TRAILING_STOP:
            # Update highest price
            if current_price > order.highest_price:
                order.highest_price = current_price
                # Update stop price
                new_stop = current_price * (1 - order.trailing_percent)
                if new_stop > order.trigger_price:
                    order.trigger_price = new_stop
                    logger.info(f"📈 Trailing stop moved: ${order.trigger_price:.4f}")
            
            # Check if triggered
            if current_price <= order.trigger_price:
                triggered = True
                logger.info(f"📉 Trailing Stop TRIGGERED @ ${current_price:.4f}")
        
        return triggered
    
    def _execute_order(self, order: AutoOrder, current_price: float):
        """Execute a triggered order."""
        order.state = OrderState.TRIGGERED
        order.triggered_at = datetime.now()
        
        # Callback
        if self.on_order_triggered:
            self.on_order_triggered(order)
        
        logger.info(f"⚡ Executing {order.order_type.value} order {order.id}...")
        
        # Place sell order
        result = self.trader.market_sell(order.token_id, order.size)
        
        if result.success:
            order.state = OrderState.EXECUTED
            order.executed_at = datetime.now()
            order.execution_price = current_price
            
            logger.info(f"✅ Order executed: Sold {order.size} @ ~${current_price:.4f}")
            
            # Cancel linked OCO order
            if order.linked_order_id and order.linked_order_id in self.orders:
                linked = self.orders[order.linked_order_id]
                linked.state = OrderState.CANCELLED
                logger.info(f"🔗 Cancelled linked order {order.linked_order_id}")
            
            if self.on_order_executed:
                self.on_order_executed(order)
        else:
            order.state = OrderState.FAILED
            logger.error(f"❌ Order failed: {result.error}")
            
            if self.on_order_failed:
                self.on_order_failed(order, result.error)
    
    def _monitor_loop(self, interval: int = 10):
        """Main monitoring loop."""
        logger.info(f"🔄 Order monitor started (checking every {interval}s)")
        
        while self._monitoring:
            active_orders = self.get_active_orders()
            
            if not active_orders:
                time.sleep(interval)
                continue
            
            # Group by token
            tokens = set(o.token_id for o in active_orders)
            
            for token_id in tokens:
                current_price = self._get_current_price(token_id)
                
                if current_price is None:
                    continue
                
                # Check orders for this token
                for order in active_orders:
                    if order.token_id != token_id:
                        continue
                    
                    if self._check_order(order, current_price):
                        self._execute_order(order, current_price)
            
            time.sleep(interval)
        
        logger.info("⏹️ Order monitor stopped")
    
    def start_monitoring(self, interval: int = 10):
        """
        Start monitoring orders in background thread.
        Also starts the OrderTracker for fill polling.
        
        Args:
            interval: Seconds between price checks
        """
        if self._monitoring:
            logger.warning("⚠️ Already monitoring")
            return
        
        # Start the fill tracker first
        self.order_tracker.start()
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop monitoring."""
        self._monitoring = False
        self.order_tracker.stop()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
    
    # ==================== STATUS ====================
    
    def print_status(self):
        """Print current orders and positions."""
        logger.info('============================================================')
        logger.info("📋 ORDER MANAGER STATUS")
        logger.info('============================================================')
        
        active = self.get_active_orders()
        
        if active:
            logger.info(f"\n🔔 Active Orders ({len(active)}):")
            logger.info('============================================================')
            
            for order in active:
                type_emoji = {
                    OrderType.TAKE_PROFIT: "📈",
                    OrderType.STOP_LOSS: "🛑", 
                    OrderType.TRAILING_STOP: "📉",
                }.get(order.order_type, "📌")
                
                logger.info(f"\n{type_emoji} {order.order_type.value.upper()}")
                logger.info(f"   Market: {order.market_question[:40]}...")
                logger.info(f"   Size: {order.size} {order.side}")
                logger.info(f"   Trigger: ${order.trigger_price:.4f}")
                
                if order.order_type == OrderType.TRAILING_STOP:
                    logger.info(f"   Highest: ${order.highest_price:.4f}")
                    logger.info(f"   Trail: {order.trailing_percent*100:.1f}%")
                
                logger.info(f"   ID: {order.id}")
        else:
            logger.info("\n📭 No active orders")
        
        # Pending fills
        pending = self.order_tracker.pending_count
        if pending:
            logger.info(f"\n⏳ Pending fills: {pending} order(s) awaiting confirmation")
            for tracked in self.order_tracker.get_tracked_orders():
                if not tracked.is_terminal:
                    fill_pct = (tracked.filled_size / tracked.size * 100) if tracked.size > 0 else 0
                    logger.info(f"   • {tracked.order_id}: {tracked.order_side} {tracked.size:.1f} {tracked.side} "
                          f"@ {tracked.limit_price:.4f} [{tracked.status}] ({fill_pct:.0f}% filled)")
        
        # Positions
        if self.positions:
            logger.info(f"\n💼 Tracked Positions ({len(self.positions)}):")
            logger.info('============================================================')
            
            for token_id, pos in self.positions.items():
                logger.info(f"\n• {pos.market_question[:40]}...")
                logger.info(f"  {pos.size} {pos.side} @ ${pos.entry_price:.4f}")
                if pos.take_profit_price:
                    logger.info(f"  TP: ${pos.take_profit_price:.4f}")
                if pos.stop_loss_price:
                    logger.info(f"  SL: ${pos.stop_loss_price:.4f}")
        
        logger.info('============================================================')


# ==================== CONVENIENCE FUNCTIONS ====================

def quick_buy_with_tp_sl(
    token_id: str,
    question: str,
    size: float,
    entry: float,
    tp: float,
    sl: float
) -> dict:
    """Quick function to buy with TP/SL."""
    manager = OrderManager()
    return manager.buy_with_tp_sl(
        token_id=token_id,
        market_question=question,
        size=size,
        entry_price=entry,
        take_profit=tp,
        stop_loss=sl
    )


# Demo
if __name__ == "__main__":
    logger.info('============================================================')
    logger.info("🤖 ORDER MANAGER DEMO")
    logger.info('============================================================')
    
    manager = OrderManager()
    
    # Demo: Set up orders for a hypothetical position
    demo_token = "demo_btc_100k"
    demo_question = "Will Bitcoin reach $100k by 2025?"
    
    logger.info("\n📝 Setting up demo orders...\n")
    
    # Simulate a position
    manager.positions[demo_token] = Position(
        token_id=demo_token,
        market_question=demo_question,
        side="YES",
        size=100,
        entry_price=0.45
    )
    
    # Set Take Profit
    tp_id = manager.set_take_profit(
        token_id=demo_token,
        price=0.70,
        size=100,
        market_question=demo_question
    )
    
    # Set Stop Loss
    sl_id = manager.set_stop_loss(
        token_id=demo_token,
        price=0.35,
        size=100,
        market_question=demo_question
    )
    
    # Set Trailing Stop (separate example)
    ts_id = manager.set_trailing_stop(
        token_id=demo_token,
        trail_percent=0.15,  # 15% trail
        size=50,
        current_price=0.50,
        market_question=demo_question
    )
    
    # Print status
    manager.print_status()
    
    logger.info("\n💡 To start live monitoring, call:")
    logger.info("   manager.start_monitoring(interval=10)")
