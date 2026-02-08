"""
Arbitrage Detector - Find mispriced markets for risk-free profits.
"""

from dataclasses import dataclass
from typing import Optional

from config import config
from client_manager import clients
from market_fetcher import Market, MarketFetcher
import logging
logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """Represents an arbitrage opportunity."""
    market_question: str
    token_id_yes: str
    token_id_no: str
    price_yes: float
    price_no: float
    combined_price: float
    profit_per_dollar: float
    estimated_profit_100: float  # Profit on $100 investment
    opportunity_type: str  # "underpriced" or "overpriced"
    
    @property
    def profit_percent(self) -> float:
        """Profit as percentage."""
        return self.profit_per_dollar * 100


class ArbitrageDetector:
    """
    Detect arbitrage opportunities in prediction markets.
    
    Types of arbitrage:
    
    1. **Underpriced Pair (YES + NO < $1.00)**
       - Buy both YES and NO
       - Guaranteed $1.00 payout regardless of outcome
       - Profit = $1.00 - (YES price + NO price)
    
    2. **Overpriced Pair (YES + NO > $1.00)**
       - Sell both YES and NO
       - Requires existing positions or market making capability
       - Profit = (YES price + NO price) - $1.00
    
    3. **Cross-Market Arbitrage**
       - Same event on different platforms
       - Buy cheap, sell expensive
    
    Usage:
        detector = ArbitrageDetector()
        
        # Scan for opportunities
        opportunities = detector.scan_markets(markets)
        
        # Filter by minimum profit
        good_opps = detector.filter_opportunities(
            opportunities, 
            min_profit_percent=2.0
        )
    """
    
    def __init__(self):
        self._fetcher = MarketFetcher()
    
    def check_market(self, market: Market) -> Optional[ArbitrageOpportunity]:
        """
        Check a single market for arbitrage opportunity.
        
        Args:
            market: Market to check
        
        Returns:
            ArbitrageOpportunity if found, None otherwise
        """
        try:
            # Get fresh prices from orderbook
            yes_price = self._get_best_price(market.token_id_yes, "BUY")
            no_price = self._get_best_price(market.token_id_no, "BUY")
            
            if yes_price is None or no_price is None:
                return None
            
            combined = yes_price + no_price
            
            # Check for underpriced (buy both sides for <$1)
            if combined < 1.0 - config.arbitrage.min_profit_threshold:
                profit = 1.0 - combined
                
                # Account for fees if configured
                if config.arbitrage.include_fees:
                    # Polymarket has no trading fees currently, but include for safety
                    fee_estimate = 0.001  # 0.1% estimate
                    profit -= (combined * fee_estimate * 2)  # Buy both sides
                
                if profit > config.arbitrage.min_profit_threshold:
                    return ArbitrageOpportunity(
                        market_question=market.question,
                        token_id_yes=market.token_id_yes,
                        token_id_no=market.token_id_no,
                        price_yes=yes_price,
                        price_no=no_price,
                        combined_price=combined,
                        profit_per_dollar=profit,
                        estimated_profit_100=profit * 100,
                        opportunity_type="underpriced"
                    )
            
            # Check for overpriced (sell both sides for >$1)
            # This requires existing positions or market making
            yes_sell = self._get_best_price(market.token_id_yes, "SELL")
            no_sell = self._get_best_price(market.token_id_no, "SELL")
            
            if yes_sell and no_sell:
                combined_sell = yes_sell + no_sell
                
                if combined_sell > 1.0 + config.arbitrage.min_profit_threshold:
                    profit = combined_sell - 1.0
                    
                    if config.arbitrage.include_fees:
                        fee_estimate = 0.001
                        profit -= (combined_sell * fee_estimate * 2)
                    
                    if profit > config.arbitrage.min_profit_threshold:
                        return ArbitrageOpportunity(
                            market_question=market.question,
                            token_id_yes=market.token_id_yes,
                            token_id_no=market.token_id_no,
                            price_yes=yes_sell,
                            price_no=no_sell,
                            combined_price=combined_sell,
                            profit_per_dollar=profit,
                            estimated_profit_100=profit * 100,
                            opportunity_type="overpriced"
                        )
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking market {market.question[:30]}...: {e}")
            return None
    
    def _get_best_price(self, token_id: str, side: str) -> Optional[float]:
        """Get best available price for a token."""
        try:
            book = clients.read.get_order_book(token_id)
            
            if side == "BUY":
                # To buy, we look at asks (what sellers are offering)
                if book and book.asks:
                    return float(book.asks[0].price)
            else:
                # To sell, we look at bids (what buyers are offering)
                if book and book.bids:
                    return float(book.bids[0].price)
            
            return None
        except Exception:
            return None
    
    def scan_markets(self, markets: list[Market]) -> list[ArbitrageOpportunity]:
        """
        Scan multiple markets for arbitrage opportunities.
        
        Args:
            markets: List of markets to scan
        
        Returns:
            List of arbitrage opportunities found
        """
        opportunities = []
        
        logger.info(f"🔍 Scanning {len(markets)} markets for arbitrage...")
        
        for i, market in enumerate(markets):
            if (i + 1) % 10 == 0:
                logger.info(f"   Scanned {i + 1}/{len(markets)} markets...")
            
            opp = self.check_market(market)
            if opp:
                opportunities.append(opp)
        
        # Sort by profit
        opportunities.sort(key=lambda x: x.profit_per_dollar, reverse=True)
        
        logger.info(f"✅ Found {len(opportunities)} opportunities")
        
        return opportunities
    
    def filter_opportunities(
        self,
        opportunities: list[ArbitrageOpportunity],
        min_profit_percent: float = 2.0,
        max_results: int = 10
    ) -> list[ArbitrageOpportunity]:
        """
        Filter opportunities by criteria.
        
        Args:
            opportunities: List to filter
            min_profit_percent: Minimum profit percentage
            max_results: Maximum results to return
        
        Returns:
            Filtered list of opportunities
        """
        filtered = [
            opp for opp in opportunities
            if opp.profit_percent >= min_profit_percent
        ]
        
        return filtered[:max_results]
    
    def print_opportunities(self, opportunities: list[ArbitrageOpportunity]):
        """Print opportunities in a nice format."""
        if not opportunities:
            logger.info("\n😔 No arbitrage opportunities found")
            return
        
        logger.info("=" * 60)
        logger.info("💰 ARBITRAGE OPPORTUNITIES")
        logger.info("=" * 60)
        
        for i, opp in enumerate(opportunities, 1):
            emoji = "🟢" if opp.profit_percent >= 3 else "🟡"
            
            logger.info(f"\n{emoji} #{i}: {opp.market_question[:50]}...")
            logger.info(f"   Type: {opp.opportunity_type}")
            logger.info(f"   YES: ${opp.price_yes:.4f} | NO: ${opp.price_no:.4f}")
            logger.info(f"   Combined: ${opp.combined_price:.4f}")
            logger.info(f"   💵 Profit: {opp.profit_percent:.2f}% (${opp.estimated_profit_100:.2f} per $100)")
            
            if opp.opportunity_type == "underpriced":
                logger.info(f"   📝 Strategy: Buy both YES and NO")
            else:
                logger.info(f"   📝 Strategy: Sell both YES and NO (requires positions)")
        
        logger.info("=" * 60)
        logger.warning("⚠️  Note: Execute quickly - arbitrage disappears fast!")
        logger.info("=" * 60)
    
    def continuous_scan(
        self,
        categories: list[str] = ["crypto", "sports"],
        interval: int = 60,
        callback=None
    ):
        """
        Continuously scan for arbitrage opportunities.
        
        Args:
            categories: Market categories to scan
            interval: Seconds between scans
            callback: Function to call when opportunity found
        """
        import time
        
        logger.info(f"🔄 Starting continuous arbitrage scanner")
        logger.info(f"   Categories: {', '.join(categories)}")
        logger.info(f"   Interval: {interval}s")
        logger.info("-" * 50)
        
        while True:
            try:
                # Fetch fresh markets
                markets = self._fetcher.get_all_target_markets(
                    min_liquidity=config.trading.min_market_liquidity
                )
                
                # Scan for opportunities
                opportunities = self.scan_markets(markets)
                
                # Filter good ones
                good_opps = self.filter_opportunities(
                    opportunities,
                    min_profit_percent=config.arbitrage.min_profit_threshold * 100
                )
                
                if good_opps:
                    self.print_opportunities(good_opps)
                    
                    if callback:
                        for opp in good_opps:
                            callback(opp)
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("\n⏹️ Scanner stopped")
                break
            except Exception as e:
                logger.error(f"Error in scan: {e}")
                time.sleep(interval)


def execute_arbitrage(
    opportunity: ArbitrageOpportunity,
    amount_usd: float = 100.0
) -> dict:
    """
    Execute an arbitrage trade (requires authenticated client).
    
    Args:
        opportunity: The arbitrage opportunity
        amount_usd: Amount in USD to invest
    
    Returns:
        Dict with execution results
    """
    if not config.has_credentials:
        return {
            "success": False,
            "error": "No credentials configured - cannot execute trades"
        }
    
    if opportunity.opportunity_type != "underpriced":
        return {
            "success": False,
            "error": "Only underpriced arbitrage is supported for auto-execution"
        }
    
    # Calculate sizes
    # For underpriced arb: buy equal $ amounts of YES and NO
    yes_size = (amount_usd / 2) / opportunity.price_yes
    no_size = (amount_usd / 2) / opportunity.price_no
    
    logger.info(f"\n🚀 Executing arbitrage:")
    logger.info(f"   Buy {yes_size:.2f} YES @ ${opportunity.price_yes:.4f}")
    logger.info(f"   Buy {no_size:.2f} NO @ ${opportunity.price_no:.4f}")
    logger.info(f"   Expected profit: ${opportunity.profit_per_dollar * amount_usd:.2f}")
    
    # Note: Actual execution would use the Trader class
    # This is just a placeholder showing the structure
    
    return {
        "success": True,
        "message": "Trade execution would happen here",
        "yes_size": yes_size,
        "no_size": no_size,
        "expected_profit": opportunity.profit_per_dollar * amount_usd
    }


# Quick test
if __name__ == "__main__":
    detector = ArbitrageDetector()
    fetcher = MarketFetcher()
    
    # Get crypto markets (usually have more arb opportunities)
    logger.info("Fetching markets...")
    markets = fetcher.get_crypto_markets(limit=50)
    
    if markets:
        # Scan for opportunities
        opportunities = detector.scan_markets(markets)
        
        # Show results
        detector.print_opportunities(opportunities[:5])
    else:
        logger.info("No markets found to scan")
