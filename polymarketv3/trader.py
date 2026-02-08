"""
Trader - Execute trades on Polymarket.
Handles order creation, submission, and management.
"""

from typing import Optional
from dataclasses import dataclass
from enum import Enum
from py_clob_client.clob_types import OrderArgs, OrderType

from config import config
from client_manager import clients
from portfolio import PortfolioManager
import logging
logger = logging.getLogger(__name__)


class Side(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "pending"
    LIVE = "live"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class OrderResult:
    """Result of an order operation."""
    success: bool
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    average_price: float = 0.0
    error: Optional[str] = None


class Trader:
    """
    Execute trades on Polymarket.
    
    IMPORTANT: Trading requires:
    1. Valid private key
    2. Funder address (proxy wallet)
    3. USDC balance on Polymarket
    
    Usage:
        trader = Trader()
        
        # Place a limit order
        result = trader.buy(
            token_id="...",
            price=0.45,
            size=100
        )
        
        # Place a market order (uses best available price)
        result = trader.market_buy(
            token_id="...",
            size=100
        )
        
        # Cancel an order
        trader.cancel_order(order_id="...")
    """
    
    def __init__(self, portfolio: Optional[PortfolioManager] = None):
        """
        Initialize trader.
        
        Args:
            portfolio: Optional portfolio manager to track positions
        """
        self.portfolio = portfolio or PortfolioManager()
        
        if not clients.has_auth:
            if config.has_credentials:
                logger.warning(f"⚠️ Auth client failed: {clients.auth_error}")
            else:
                logger.warning("⚠️ No credentials configured - trading disabled")
                logger.info("   Set PRIVATE_KEY and FUNDER_ADDRESS in .env")
    
    @property
    def client(self) -> Optional[object]:
        """Get the shared authenticated client."""
        return clients.auth
    
    @property
    def is_ready(self) -> bool:
        """Check if trader is ready to execute."""
        return clients.has_auth
    
    def _validate_trade(self, size: float, price: float) -> Optional[str]:
        """
        Validate trade parameters.
        
        Returns error message if invalid, None if valid.
        """
        if size <= 0:
            return "Size must be positive"
        
        if price <= 0 or price >= 1:
            return "Price must be between 0 and 1"
        
        trade_value = size * price
        
        if trade_value > config.trading.max_trade_size:
            return f"Trade value (${trade_value:.2f}) exceeds max (${config.trading.max_trade_size:.2f})"
        
        # Check total exposure
        current_exposure = self.portfolio.get_total_exposure()
        if current_exposure + trade_value > config.trading.max_total_exposure:
            return f"Would exceed max exposure (${config.trading.max_total_exposure:.2f})"
        
        return None
    
    def buy(
        self,
        token_id: str,
        price: float,
        size: float,
        market_question: str = ""
    ) -> OrderResult:
        """
        Place a limit buy order.
        
        Args:
            token_id: Token ID to buy
            price: Limit price (0.01 to 0.99)
            size: Number of shares to buy
            market_question: Market question (for tracking)
        
        Returns:
            OrderResult with status and details
        """
        if not self.is_ready:
            return OrderResult(
                success=False,
                error="Trader not initialized - check credentials"
            )
        
        # Validate
        error = self._validate_trade(size, price)
        if error:
            return OrderResult(success=False, error=error)
        
        try:
            # Create order
            order = self.client.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side="BUY"
                )
            )
            
            # Submit order
            response = self.client.post_order(order)
            
            if response and response.get("success"):
                order_id = response.get("orderID", "")
                
                # NOTE: We do NOT call portfolio.add_position() here!
                # The order is LIVE on the book, not FILLED.
                # OrderTracker will poll for fill confirmation and
                # update the portfolio only when the fill is confirmed.
                
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    status=OrderStatus.LIVE,
                    filled_size=0,
                    average_price=price
                )
            else:
                return OrderResult(
                    success=False,
                    error=response.get("errorMsg", "Unknown error")
                )
                
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def sell(
        self,
        token_id: str,
        price: float,
        size: float
    ) -> OrderResult:
        """
        Place a limit sell order.
        
        Args:
            token_id: Token ID to sell
            price: Limit price
            size: Number of shares to sell
        
        Returns:
            OrderResult with status
        """
        if not self.is_ready:
            return OrderResult(
                success=False,
                error="Trader not initialized"
            )
        
        try:
            order = self.client.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side="SELL"
                )
            )
            
            response = self.client.post_order(order)
            
            if response and response.get("success"):
                return OrderResult(
                    success=True,
                    order_id=response.get("orderID", ""),
                    status=OrderStatus.LIVE
                )
            else:
                return OrderResult(
                    success=False,
                    error=response.get("errorMsg", "Unknown error")
                )
                
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def market_buy(
        self,
        token_id: str,
        size: float,
        max_slippage: float = 0.02
    ) -> OrderResult:
        """
        Place a market buy order.
        
        Market orders are executed by placing a limit order at a price
        that ensures immediate fill.
        
        Args:
            token_id: Token ID to buy
            size: Number of shares
            max_slippage: Maximum acceptable slippage (default 2%)
        
        Returns:
            OrderResult
        """
        if not self.is_ready:
            return OrderResult(
                success=False,
                error="Trader not initialized"
            )
        
        try:
            # Get current best ask
            book = self.client.get_order_book(token_id)
            
            if not book or not book.asks:
                return OrderResult(
                    success=False,
                    error="No liquidity available"
                )
            
            best_ask = float(book.asks[0].price)
            
            # Add slippage buffer
            max_price = min(best_ask * (1 + max_slippage), 0.99)
            
            return self.buy(token_id, max_price, size)
            
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def market_sell(
        self,
        token_id: str,
        size: float,
        max_slippage: float = 0.02
    ) -> OrderResult:
        """
        Place a market sell order.
        
        Args:
            token_id: Token ID to sell
            size: Number of shares
            max_slippage: Maximum acceptable slippage
        
        Returns:
            OrderResult
        """
        if not self.is_ready:
            return OrderResult(
                success=False,
                error="Trader not initialized"
            )
        
        try:
            book = self.client.get_order_book(token_id)
            
            if not book or not book.bids:
                return OrderResult(
                    success=False,
                    error="No liquidity available"
                )
            
            best_bid = float(book.bids[0].price)
            min_price = max(best_bid * (1 - max_slippage), 0.01)
            
            return self.sell(token_id, min_price, size)
            
        except Exception as e:
            return OrderResult(success=False, error=str(e))
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            True if cancelled successfully
        """
        if not self.is_ready:
            logger.error("❌ Trader not initialized")
            return False
        
        try:
            response = self.client.cancel(order_id)
            return response.get("success", False)
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False
    
    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.
        
        Returns:
            Number of orders cancelled
        """
        if not self.is_ready:
            return 0
        
        try:
            response = self.client.cancel_all()
            return response.get("canceled", 0)
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
            return 0
    
    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        if not self.is_ready:
            return []
        
        try:
            return self.client.get_orders() or []
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    def get_trades(self, limit: int = 100) -> list[dict]:
        """Get recent trades."""
        if not self.is_ready:
            return []
        
        try:
            return self.client.get_trades(limit=limit) or []
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return []


class StrategyExecutor:
    """
    Execute trading strategies.
    
    Built-in strategies:
    - Value betting: Buy when market is mispriced vs your estimate
    - Momentum: Follow price trends
    - Mean reversion: Bet on prices returning to average
    """
    
    def __init__(self, trader: Trader):
        self.trader = trader
    
    def value_bet(
        self,
        token_id: str,
        market_question: str,
        your_probability: float,
        market_price: float,
        edge_threshold: float = 0.10,
        size: float = 50
    ) -> Optional[OrderResult]:
        """
        Execute a value bet when you think the market is wrong.
        
        Args:
            token_id: Token ID
            market_question: Market question
            your_probability: Your estimated probability (0-1)
            market_price: Current market price
            edge_threshold: Minimum edge to bet (default 10%)
            size: Bet size in shares
        
        Returns:
            OrderResult if bet placed, None if no edge
        """
        edge = your_probability - market_price
        
        if edge >= edge_threshold:
            logger.info(f"📊 Value bet opportunity:")
            logger.info(f"   Market: {market_question[:50]}...")
            logger.info(f"   Your estimate: {your_probability:.2%}")
            logger.info(f"   Market price: {market_price:.2%}")
            logger.info(f"   Edge: {edge:.2%}")
            
            return self.trader.buy(
                token_id=token_id,
                price=market_price,
                size=size,
                market_question=market_question
            )
        
        return None
    
    def momentum_trade(
        self,
        token_id: str,
        market_question: str,
        price_change_1h: float,
        current_price: float,
        momentum_threshold: float = 0.05,
        size: float = 25
    ) -> Optional[OrderResult]:
        """
        Trade based on price momentum.
        
        Args:
            token_id: Token ID
            market_question: Market question
            price_change_1h: 1-hour price change (absolute)
            current_price: Current price
            momentum_threshold: Minimum price change to trade (default 5%)
            size: Trade size
        
        Returns:
            OrderResult if trade placed
        """
        if abs(price_change_1h) < momentum_threshold:
            return None
        
        if price_change_1h > 0:
            # Upward momentum - buy
            logger.info(f"📈 Momentum buy: {market_question[:40]}...")
            logger.info(f"   1h change: +{price_change_1h:.2%}")
            
            return self.trader.buy(
                token_id=token_id,
                price=current_price,
                size=size,
                market_question=market_question
            )
        else:
            # Downward momentum - could short (sell) if you have position
            logger.info(f"📉 Downward momentum detected: {market_question[:40]}...")
            return None


# Demo
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🤖 POLYMARKET TRADER")
    logger.info("=" * 60)
    
    trader = Trader()
    
    if trader.is_ready:
        logger.info("\n✅ Trader is ready!")
        logger.info("\nOpen orders:")
        orders = trader.get_open_orders()
        if orders:
            for order in orders[:5]:
                logger.info(f"  - {order}")
        else:
            logger.info("  No open orders")
    else:
        logger.warning("\n⚠️ Trader not ready - configure credentials in .env")
        logger.info("\nTo enable trading:")
        logger.info("1. Copy .env.example to .env")
        logger.info("2. Add your PRIVATE_KEY")
        logger.info("3. Add your FUNDER_ADDRESS")
