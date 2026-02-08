#!/usr/bin/env python3
"""
Polymarket Sports & Crypto Trading Bot
Main entry point with CLI interface.

Usage:
    python main.py --mode scan      # Scan for markets (read-only)
    python main.py --mode track     # Track odds in real-time
    python main.py --mode portfolio # View portfolio
    python main.py --mode arbitrage # Scan for arbitrage
    python main.py --mode trade     # Run full trading bot
"""

import argparse
import sys
import time
from datetime import datetime

from config import config
from bot_logging import setup_logging
from market_fetcher import MarketFetcher
from odds_tracker import OddsTracker
from portfolio import PortfolioManager
from arbitrage import ArbitrageDetector
from trader import Trader, StrategyExecutor


def print_banner():
    """Print welcome banner."""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   🎰  POLYMARKET TRADING BOT  🎰                             ║
║                                                              ║
║   Categories: Sports 🏀 & Crypto 🪙                          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def mode_scan():
    """Scan and display available markets."""
    print("\n📊 MARKET SCANNER")
    print("="*60)
    
    fetcher = MarketFetcher()
    
    # Get markets
    print("\nFetching markets...")
    markets = fetcher.get_all_target_markets(
        min_liquidity=config.trading.min_market_liquidity
    )
    
    if not markets:
        print("No markets found matching criteria")
        return
    
    # Sort by volume
    markets.sort(key=lambda m: m.volume, reverse=True)
    
    # Display results
    print(f"\n✅ Found {len(markets)} markets\n")
    
    # Crypto markets
    crypto = [m for m in markets if "crypto" in m.category.lower()]
    print(f"🪙 CRYPTO MARKETS ({len(crypto)}):")
    print("-"*60)
    for m in crypto[:10]:
        print(f"  • {m.question[:50]}...")
        print(f"    YES: ${m.price_yes:.2f} | Volume: ${m.volume:,.0f}")
    
    # Sports markets
    sports = [m for m in markets if "sports" in m.category.lower()]
    print(f"\n🏀 SPORTS MARKETS ({len(sports)}):")
    print("-"*60)
    for m in sports[:10]:
        print(f"  • {m.question[:50]}...")
        print(f"    YES: ${m.price_yes:.2f} | Volume: ${m.volume:,.0f}")
    
    print("\n" + "="*60)


def mode_track():
    """Track odds movements in real-time."""
    print("\n📈 ODDS TRACKER")
    print("="*60)
    
    fetcher = MarketFetcher()
    tracker = OddsTracker()
    
    # Fetch top markets
    print("\nFetching markets to track...")
    markets = fetcher.get_all_target_markets(
        min_liquidity=config.trading.min_market_liquidity
    )
    
    if not markets:
        print("No markets found")
        return
    
    # Sort by volume and track top 10
    markets.sort(key=lambda m: m.volume, reverse=True)
    
    for m in markets[:10]:
        tracker.add_market(m.token_id_yes, m.question)
        
        # Add alert for significant price changes
        tracker.add_alert(
            m.token_id_yes,
            condition="change",
            threshold=config.alerts.price_change_threshold
        )
    
    print(f"\n✅ Tracking {len(tracker.tracked_markets)} markets")
    print("Press Ctrl+C to stop\n")
    
    # Start polling
    tracker.start_polling(interval=60)


def mode_portfolio():
    """View and manage portfolio."""
    print("\n💼 PORTFOLIO MANAGER")
    print("="*60)
    
    portfolio = PortfolioManager()
    
    if not config.has_credentials:
        print("\n⚠️ No credentials configured")
        print("Showing demo portfolio...\n")
        
        # Add demo positions
        portfolio.add_position(
            token_id="demo_1",
            market_question="[DEMO] Will Bitcoin reach $100k by 2025?",
            side="YES",
            size=100,
            entry_price=0.45
        )
        portfolio.positions["demo_1_YES"].current_price = 0.52
        
        portfolio.add_position(
            token_id="demo_2",
            market_question="[DEMO] Lakers to win NBA Finals?",
            side="YES",
            size=50,
            entry_price=0.15
        )
        portfolio.positions["demo_2_YES"].current_price = 0.18
    
    portfolio.print_summary()


def mode_arbitrage():
    """Scan for arbitrage opportunities."""
    print("\n💰 ARBITRAGE SCANNER")
    print("="*60)
    
    fetcher = MarketFetcher()
    detector = ArbitrageDetector()
    
    print("\nFetching markets...")
    markets = fetcher.get_all_target_markets(
        min_liquidity=1000  # Lower threshold for arb scanning
    )
    
    if not markets:
        print("No markets found")
        return
    
    # Scan for opportunities
    opportunities = detector.scan_markets(markets)
    
    # Filter by minimum profit
    good_opps = detector.filter_opportunities(
        opportunities,
        min_profit_percent=1.0  # 1% minimum
    )
    
    detector.print_opportunities(good_opps)


def mode_trade():
    """Run the full trading bot."""
    print("\n🤖 TRADING BOT")
    print("="*60)
    
    # Check credentials
    if not config.has_credentials:
        print("\n❌ Trading requires credentials!")
        print("\nTo enable trading:")
        print("1. Copy .env.example to .env")
        print("2. Add your PRIVATE_KEY (from Polymarket)")
        print("3. Add your FUNDER_ADDRESS (proxy wallet)")
        return
    
    # Validate config
    issues = config.validate()
    if issues:
        print("\n⚠️ Configuration issues:")
        for issue in issues:
            print(f"  - {issue}")
    
    # Initialize components
    fetcher = MarketFetcher()
    portfolio = PortfolioManager()
    trader = Trader(portfolio)
    strategy = StrategyExecutor(trader)
    tracker = OddsTracker()
    
    if not trader.is_ready:
        print("\n❌ Failed to initialize trader")
        return
    
    print("\n✅ Trading bot initialized!")
    print(f"   Max trade size: ${config.trading.max_trade_size}")
    print(f"   Max exposure: ${config.trading.max_total_exposure}")
    print(f"   Min liquidity: ${config.trading.min_market_liquidity}")
    
    print("\n🔄 Starting trading loop...")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            # Fetch fresh markets
            markets = fetcher.get_all_target_markets(
                min_liquidity=config.trading.min_market_liquidity
            )
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Analyzing {len(markets)} markets...")
            
            # Update portfolio prices
            portfolio.update_prices()
            
            # Check risk limits
            warnings = portfolio.check_risk_limits()
            if warnings:
                for w in warnings:
                    print(w)
            
            # Look for opportunities
            # (In a real bot, you'd implement your strategy here)
            
            # Simple example: track top movers
            for market in markets[:5]:
                tracker.add_market(market.token_id_yes, market.question)
            
            tracker.update_prices()
            
            # Print summary
            stats = portfolio.get_stats()
            print(f"   Positions: {stats.total_positions}")
            print(f"   Value: ${stats.total_value:,.2f}")
            print(f"   P&L: ${stats.total_unrealized_pnl:,.2f}")
            
            # Wait before next iteration
            time.sleep(60)
            
    except KeyboardInterrupt:
        print("\n\n⏹️ Trading bot stopped")
        portfolio.print_summary()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Polymarket Sports & Crypto Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mode scan        # View available markets
  python main.py --mode track       # Track price movements  
  python main.py --mode portfolio   # View your portfolio
  python main.py --mode arbitrage   # Find arbitrage opportunities
  python main.py --mode trade       # Run full trading bot

For more info, see README.md
        """
    )
    
    parser.add_argument(
        "--mode", "-m",
        choices=["scan", "track", "portfolio", "arbitrage", "trade"],
        default="scan",
        help="Operating mode (default: scan)"
    )
    
    parser.add_argument(
        "--config", "-c",
        help="Path to config file (optional)"
    )
    
    args = parser.parse_args()
    
    # Print banner
    print_banner()
    
    # Print config status
    print(f"Configuration:")
    print(f"  Credentials: {'✅ Configured' if config.has_credentials else '❌ Not configured'}")
    print(f"  Min liquidity: ${config.trading.min_market_liquidity:,.0f}")
    print(f"  Max trade size: ${config.trading.max_trade_size:,.0f}")
    
    # Run selected mode
    modes = {
        "scan": mode_scan,
        "track": mode_track,
        "portfolio": mode_portfolio,
        "arbitrage": mode_arbitrage,
        "trade": mode_trade,
    }
    
    try:
        modes[args.mode]()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    setup_logging()
    main()
