"""
Easy Trade - Simple interface for trading with Take Profit and Stop Loss.

This is the beginner-friendly interface for the bot.
All the complexity is hidden - just call simple functions!
"""

from typing import Optional
from dataclasses import dataclass

from config import config
from market_fetcher import MarketFetcher, Market
from order_manager import OrderManager
from portfolio import PortfolioManager


@dataclass
class TradeResult:
    """Result of a trade operation."""
    success: bool
    message: str
    position_size: float = 0
    entry_price: float = 0
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    order_ids: dict = None


class EasyTrader:
    """
    Simple trading interface for beginners.
    
    Example:
        trader = EasyTrader()
        
        # Find a market
        markets = trader.find_markets("bitcoin")
        
        # Buy with automatic protection
        result = trader.buy(
            market=markets[0],
            amount=50,                    # $50 worth
            take_profit_percent=50,       # Sell if up 50%
            stop_loss_percent=20          # Sell if down 20%
        )
        
        # Check status
        trader.show_positions()
    """
    
    def __init__(self):
        """Initialize the easy trader."""
        self.fetcher = MarketFetcher()
        self.manager = OrderManager()
        self.portfolio = PortfolioManager()
        
        # Check credentials
        if not config.has_credentials:
            print("⚠️  Trading credentials not configured!")
            print("    Copy .env.example to .env and add your keys.")
            print("    You can still browse markets in read-only mode.\n")
    
    # ==================== FIND MARKETS ====================
    
    def find_markets(self, search: str = "", category: str = "all") -> list[Market]:
        """
        Find markets by search term.
        
        Args:
            search: Search term (e.g., "bitcoin", "lakers")
            category: "crypto", "sports", or "all"
        
        Returns:
            List of matching markets
        """
        print(f"🔍 Searching for '{search}'...")
        
        if category == "crypto":
            markets = self.fetcher.get_crypto_markets()
        elif category == "sports":
            markets = self.fetcher.get_sports_markets()
        else:
            markets = self.fetcher.get_all_target_markets()
        
        if search:
            markets = [
                m for m in markets
                if search.lower() in m.question.lower()
            ]
        
        # Sort by volume
        markets.sort(key=lambda m: m.volume, reverse=True)
        
        print(f"✅ Found {len(markets)} markets\n")
        
        # Show top results
        for i, m in enumerate(markets[:5], 1):
            print(f"{i}. {m.question[:60]}...")
            print(f"   YES: {m.price_yes*100:.0f}¢ | NO: {m.price_no*100:.0f}¢ | Vol: ${m.volume:,.0f}")
            print()
        
        return markets
    
    def get_crypto_markets(self, limit: int = 20) -> list[Market]:
        """Get top crypto markets by volume."""
        return self.fetcher.get_crypto_markets(limit=limit)
    
    def get_sports_markets(self, limit: int = 20) -> list[Market]:
        """Get top sports markets by volume."""
        return self.fetcher.get_sports_markets(limit=limit)
    
    # ==================== BUY ====================
    
    def buy(
        self,
        market: Market,
        amount: float,
        side: str = "YES",
        take_profit_percent: Optional[float] = None,
        stop_loss_percent: Optional[float] = None,
        trailing_stop_percent: Optional[float] = None
    ) -> TradeResult:
        """
        Buy shares in a market with optional TP/SL.
        
        Args:
            market: Market object to trade
            amount: Amount in USD to spend
            side: "YES" or "NO"
            take_profit_percent: Sell when up X% (e.g., 50 = sell at 50% profit)
            stop_loss_percent: Sell when down X% (e.g., 20 = sell at 20% loss)
            trailing_stop_percent: Trail stop by X% (e.g., 15 = 15% trailing stop)
        
        Returns:
            TradeResult with details
        """
        if not config.has_credentials:
            return TradeResult(
                success=False,
                message="Trading credentials not configured. Add keys to .env file."
            )
        
        # Get current price
        if side == "YES":
            token_id = market.token_id_yes
            entry_price = market.price_yes
        else:
            token_id = market.token_id_no
            entry_price = market.price_no
        
        # Calculate size
        size = amount / entry_price
        
        # Calculate TP/SL prices
        tp_price = None
        sl_price = None
        
        if take_profit_percent:
            tp_price = entry_price * (1 + take_profit_percent / 100)
            tp_price = min(tp_price, 0.99)  # Cap at 99¢
        
        if stop_loss_percent:
            sl_price = entry_price * (1 - stop_loss_percent / 100)
            sl_price = max(sl_price, 0.01)  # Floor at 1¢
        
        ts_pct = trailing_stop_percent / 100 if trailing_stop_percent else None
        
        print(f"\n📊 PLACING ORDER")
        print(f"   Market: {market.question[:50]}...")
        print(f"   Side: {side}")
        print(f"   Amount: ${amount:.2f} ({size:.2f} shares @ {entry_price*100:.1f}¢)")
        
        if tp_price:
            print(f"   Take Profit: {tp_price*100:.1f}¢ (+{take_profit_percent}%)")
        if sl_price:
            print(f"   Stop Loss: {sl_price*100:.1f}¢ (-{stop_loss_percent}%)")
        if ts_pct:
            print(f"   Trailing Stop: {trailing_stop_percent}%")
        print()
        
        # Execute
        result = self.manager.buy_with_tp_sl(
            token_id=token_id,
            market_question=market.question,
            size=size,
            entry_price=entry_price,
            take_profit=tp_price,
            stop_loss=sl_price,
            trailing_stop_percent=ts_pct,
            side=side
        )
        
        if result["success"]:
            return TradeResult(
                success=True,
                message=f"✅ Bought {size:.2f} {side} shares",
                position_size=size,
                entry_price=entry_price,
                take_profit=tp_price,
                stop_loss=sl_price,
                order_ids=result
            )
        else:
            return TradeResult(
                success=False,
                message=f"❌ Order failed: {result.get('buy_result', {}).error}"
            )
    
    def quick_buy(
        self,
        market: Market,
        amount: float = 50,
        side: str = "YES"
    ) -> TradeResult:
        """
        Quick buy with default 50% TP and 25% SL.
        """
        return self.buy(
            market=market,
            amount=amount,
            side=side,
            take_profit_percent=50,
            stop_loss_percent=25
        )
    
    # ==================== SELL ====================
    
    def sell(
        self,
        market: Market,
        size: float,
        side: str = "YES"
    ) -> TradeResult:
        """
        Sell shares at market price.
        
        Args:
            market: Market to sell
            size: Number of shares to sell
            side: "YES" or "NO"
        """
        if not config.has_credentials:
            return TradeResult(
                success=False,
                message="Trading credentials not configured"
            )
        
        token_id = market.token_id_yes if side == "YES" else market.token_id_no
        
        result = self.manager.market_sell(token_id, size)
        
        if result.success:
            # Cancel any TP/SL orders for this position
            self.manager.cancel_all_orders(token_id)
            
            return TradeResult(
                success=True,
                message=f"✅ Sold {size:.2f} {side} shares"
            )
        else:
            return TradeResult(
                success=False,
                message=f"❌ Sell failed: {result.error}"
            )
    
    def sell_all(self, market: Market, side: str = "YES") -> TradeResult:
        """Sell entire position in a market."""
        token_id = market.token_id_yes if side == "YES" else market.token_id_no
        
        # Get position size from portfolio
        key = f"{token_id}_{side}"
        if key not in self.portfolio.positions:
            return TradeResult(
                success=False,
                message="No position found"
            )
        
        position = self.portfolio.positions[key]
        return self.sell(market, position.size, side)
    
    # ==================== ORDERS ====================
    
    def set_take_profit(
        self,
        market: Market,
        price: float,
        size: float,
        side: str = "YES"
    ) -> str:
        """
        Set a take profit order on existing position.
        
        Args:
            market: Market
            price: Price to sell at (e.g., 0.70 for 70¢)
            size: Shares to sell
            side: "YES" or "NO"
        
        Returns:
            Order ID
        """
        token_id = market.token_id_yes if side == "YES" else market.token_id_no
        return self.manager.set_take_profit(
            token_id=token_id,
            price=price,
            size=size,
            market_question=market.question,
            side=side
        )
    
    def set_stop_loss(
        self,
        market: Market,
        price: float,
        size: float,
        side: str = "YES"
    ) -> str:
        """
        Set a stop loss order.
        
        Args:
            market: Market
            price: Price to trigger stop (e.g., 0.30 for 30¢)
            size: Shares to sell
            side: "YES" or "NO"
        
        Returns:
            Order ID
        """
        token_id = market.token_id_yes if side == "YES" else market.token_id_no
        return self.manager.set_stop_loss(
            token_id=token_id,
            price=price,
            size=size,
            market_question=market.question,
            side=side
        )
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        return self.manager.cancel_order(order_id)
    
    def cancel_all_orders(self) -> int:
        """Cancel all pending orders."""
        return self.manager.cancel_all_orders()
    
    # ==================== STATUS ====================
    
    def show_positions(self):
        """Show current positions and P&L."""
        self.portfolio.print_summary()
    
    def show_orders(self):
        """Show active orders (TP/SL)."""
        self.manager.print_status()
    
    def start_monitoring(self):
        """Start monitoring orders (runs in background)."""
        print("🔄 Starting order monitor...")
        print("   TP/SL orders will execute automatically when triggered.")
        print("   Press Ctrl+C to stop.\n")
        self.manager.start_monitoring(interval=10)
    
    def stop_monitoring(self):
        """Stop monitoring."""
        self.manager.stop_monitoring()


# ==================== INTERACTIVE MODE ====================

def interactive_mode():
    """Run interactive trading session."""
    trader = EasyTrader()
    
    print("\n" + "="*60)
    print("🎰 POLYMARKET EASY TRADER")
    print("="*60)
    print("\nCommands:")
    print("  find <term>     - Search markets")
    print("  crypto          - Show crypto markets")
    print("  sports          - Show sports markets")
    print("  buy <n> <amt>   - Buy market #n for $amt")
    print("  sell <n>        - Sell position in market #n")
    print("  positions       - Show your positions")
    print("  orders          - Show active orders")
    print("  start           - Start order monitoring")
    print("  quit            - Exit")
    print("="*60 + "\n")
    
    markets = []
    
    while True:
        try:
            cmd = input(">>> ").strip().lower().split()
            
            if not cmd:
                continue
            
            action = cmd[0]
            
            if action == "quit" or action == "exit":
                print("👋 Goodbye!")
                break
            
            elif action == "find":
                term = " ".join(cmd[1:]) if len(cmd) > 1 else ""
                markets = trader.find_markets(term)
            
            elif action == "crypto":
                markets = trader.get_crypto_markets()
                for i, m in enumerate(markets[:10], 1):
                    print(f"{i}. {m.question[:50]}... ({m.price_yes*100:.0f}¢)")
            
            elif action == "sports":
                markets = trader.get_sports_markets()
                for i, m in enumerate(markets[:10], 1):
                    print(f"{i}. {m.question[:50]}... ({m.price_yes*100:.0f}¢)")
            
            elif action == "buy":
                if len(cmd) < 3:
                    print("Usage: buy <market#> <amount>")
                    print("Example: buy 1 50  (buy $50 of market #1)")
                    continue
                
                idx = int(cmd[1]) - 1
                amt = float(cmd[2])
                
                if idx < 0 or idx >= len(markets):
                    print("Invalid market number")
                    continue
                
                result = trader.buy(
                    market=markets[idx],
                    amount=amt,
                    take_profit_percent=50,
                    stop_loss_percent=25
                )
                print(result.message)
            
            elif action == "positions":
                trader.show_positions()
            
            elif action == "orders":
                trader.show_orders()
            
            elif action == "start":
                trader.start_monitoring()
            
            elif action == "help":
                print("Commands: find, crypto, sports, buy, sell, positions, orders, start, quit")
            
            else:
                print(f"Unknown command: {action}")
                print("Type 'help' for commands")
        
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")


# Quick start
if __name__ == "__main__":
    interactive_mode()
