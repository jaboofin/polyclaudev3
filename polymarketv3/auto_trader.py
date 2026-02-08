"""
Auto Trader - Fully Automated Polymarket Trading Bot

This bot does EVERYTHING automatically:
- Scans markets for opportunities
- Picks bets based on strategy
- Places trades automatically
- Sets Take Profit & Stop Loss
- Monitors and sells automatically

Just run it and let it work!
"""

import os
import time
import random
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from config import config
from market_fetcher import MarketFetcher, Market
from order_manager import OrderManager
from client_manager import clients
from portfolio import PortfolioManager
from odds_tracker import OddsTracker
from persistence import db
from arbitrage import ArbitrageDetector
from models import ManualModel, OddsApiModel, MomentumModel, ProbabilityEstimate
import logging
logger = logging.getLogger(__name__)


class AutoStrategy(Enum):
    """Auto-picking strategies."""
    VALUE = "value"              # Bet when model disagrees with market price
    MOMENTUM = "momentum"        # Bet on real price trends (requires history)
    ARBITRAGE = "arbitrage"      # Guaranteed profit via orderbook mispricing
    FAVORITES = "favorites"      # High probability bets (>65%) — no edge model
    UNDERDOGS = "underdogs"      # Low odds, high reward (20-40%) — no edge model
    MIXED = "mixed"              # Arbitrage + value (requires at least one model)


@dataclass
class AutoTradeConfig:
    """Configuration for auto trading."""
    # Money management
    bankroll: float = 50.0              # Total bankroll
    max_bet_size: float = 10.0          # Max per trade
    max_open_positions: int = 5         # Max simultaneous bets
    reserve_percent: float = 20.0       # Keep 20% as reserve
    
    # Strategy
    strategy: AutoStrategy = AutoStrategy.MIXED
    categories: list = None             # ["crypto", "sports"] or None for all
    
    # Entry criteria
    min_volume: float = 50000           # Min market volume
    min_liquidity: float = 10000        # Min liquidity
    min_edge: float = 10.0              # Min expected edge %
    
    # ⏰ TIME-BASED FILTERING
    # For SPORTS: same-day bets are GREAT (bet on tonight's game!)
    # For CRYPTO: same-day can work (24/7 volatile)
    min_hours_to_resolution: int = 2    # At least 2 hours to resolution (time to exit if needed)
    max_days_to_resolution: int = 7     # Don't bet on markets ending > 7 days (capital locked)
    
    # Category-specific overrides
    sports_allow_same_day: bool = True  # Sports: allow same-day games ✅
    crypto_allow_same_day: bool = True  # Crypto: allow same-day markets ✅
    sports_max_days: int = 3            # Sports: max 3 days out (games are scheduled)
    crypto_max_days: int = 7            # Crypto: max 7 days out
    
    prefer_ending_soon: bool = True     # Prioritize markets ending soonest
    
    # Exit criteria (Take Profit / Stop Loss)
    take_profit_percent: float = 30.0   # Take profit at +30%
    stop_loss_percent: float = 15.0     # Stop loss at -15%
    trailing_stop_percent: float = None # Optional trailing stop
    
    # Timing
    scan_interval: int = 300            # Scan every 5 minutes
    max_hold_hours: int = 48            # Max hold time before force sell
    
    def __post_init__(self):
        if self.categories is None:
            self.categories = ["crypto", "sports"]


@dataclass 
class AutoBet:
    """Tracked auto bet."""
    id: str
    market: Market
    side: str
    size: float
    entry_price: float
    entry_time: datetime
    take_profit: float
    stop_loss: float
    strategy: str
    status: str = "open"  # open, won, lost, sold


class AutoTrader:
    """
    Fully automated trading bot.
    
    Usage:
        bot = AutoTrader(bankroll=50)
        bot.run()  # Runs forever, trading automatically
    
    Or customize:
        config = AutoTradeConfig(
            bankroll=50,
            max_bet_size=10,
            strategy=AutoStrategy.VALUE,
            take_profit_percent=40,
            stop_loss_percent=20
        )
        bot = AutoTrader(config=config)
        bot.run()
    """
    
    def __init__(
        self,
        bankroll: float = None,
        config: AutoTradeConfig = None,
        models: list = None,
    ):
        """Initialize the auto trader.
        
        Args:
            bankroll: Starting bankroll amount.
            config: Full AutoTradeConfig instance.
            models: List of ProbabilityModel instances. If not provided,
                    initializes default models (OddsApi + Momentum).
                    Use ManualModel to add your own probability estimates.
        """
        if config:
            self.config = config
        else:
            self.config = AutoTradeConfig(bankroll=bankroll or 50.0)
        
        self.fetcher = MarketFetcher()
        self.order_manager = OrderManager()
        self.portfolio = PortfolioManager()
        self.tracker = OddsTracker()
        self.arb_detector = ArbitrageDetector()
        
        # Initialize probability models
        self._init_models(models)
        
        self.active_bets: dict[str, AutoBet] = {}
        self.bet_history: list[AutoBet] = []
        self.total_pnl: float = 0.0
        self._running: bool = False
        self._bet_counter: int = 0
        
        logger.info('============================================================')
        logger.info("🤖 AUTO TRADER INITIALIZED")
        logger.info('============================================================')
        logger.info(f"💰 Bankroll: ${self.config.bankroll}")
        logger.info(f"📊 Strategy: {self.config.strategy.value}")
        logger.info(f"🎯 Max bet: ${self.config.max_bet_size}")
        logger.info(f"📈 Take Profit: +{self.config.take_profit_percent}%")
        logger.info(f"📉 Stop Loss: -{self.config.stop_loss_percent}%")
        logger.info(f"🏷️ Categories: {', '.join(self.config.categories)}")
        logger.info(f"⏰ Same-day bets: ✅ ENABLED (min {self.config.min_hours_to_resolution}h buffer)")
        logger.info(f"⏰ Sports max: {self.config.sports_max_days} days | Crypto max: {self.config.crypto_max_days} days")
        logger.info(f"⏱️ Force sell after: {self.config.max_hold_hours}h")
        
        # Show active models
        model_names = [m.name for m in self._models]
        if model_names:
            logger.info(f"🧠 Models: {', '.join(model_names)}")
        else:
            logger.warning("⚠️  No probability models active — value/momentum strategies disabled")
            if self.config.strategy in (AutoStrategy.VALUE, AutoStrategy.MOMENTUM, AutoStrategy.MIXED):
                logger.info("   → Falling back to ARBITRAGE only")
        logger.info("="*60 + "\n")


    # ==================== LIVE SAFETY ====================

    def _startup_safety(self):
        """Best-effort startup safety actions for LIVE trading."""
        # Cancel all open orders if requested
        if config.safety.cancel_all_on_startup and clients.has_auth:
            try:
                resp = clients.auth.cancel_all()
                logger.info(f"🧹 Startup: cancel_all() executed ({resp})")
            except Exception as e:
                logger.warning(f"⚠️ Startup cancel_all failed: {e}")

        # Clean up old idempotency intents
        try:
            db.cleanup_old_order_intents(older_than_seconds=max(config.safety.intent_ttl_seconds * 10, 600))
        except Exception:
            pass

    def _get_orderbook_spread_bps(self, token_id: str) -> Optional[float]:
        """Compute best bid/ask spread in bps using CLOB orderbook."""
        try:
            book = clients.read.get_order_book(token_id)
            if not book:
                return None
            bids = book.get("bids") or book.get("buy") or []
            asks = book.get("asks") or book.get("sell") or []
            if not bids or not asks:
                return None

            def _best(levels, want_max=True):
                best = None
                for lvl in levels:
                    try:
                        p = float(lvl.get("price") if isinstance(lvl, dict) else lvl[0])
                    except Exception:
                        continue
                    if best is None:
                        best = p
                    else:
                        best = max(best, p) if want_max else min(best, p)
                return best

            bid = _best(bids, want_max=True)
            ask = _best(asks, want_max=False)
            if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
                return None
            mid = (bid + ask) / 2.0
            spread_bps = (ask - bid) / mid * 10000.0
            return spread_bps
        except Exception:
            return None

    def _circuit_breaker_check(self) -> Optional[str]:
        """Return a reason string if trading should be halted."""
        # Daily loss (realized PnL delta) — uses local portfolio realized_pnl
        today = datetime.now().date().isoformat()
        day_key = db.get_state("pnl_day", "")
        if day_key != today:
            db.set_state("pnl_day", today)
            db.set_state("realized_pnl_day_start", str(self.portfolio.realized_pnl))

        try:
            day_start = float(db.get_state("realized_pnl_day_start", str(self.portfolio.realized_pnl)))
        except Exception:
            day_start = self.portfolio.realized_pnl

        daily_realized = self.portfolio.realized_pnl - day_start
        if config.safety.max_daily_loss_usd and daily_realized <= -abs(config.safety.max_daily_loss_usd):
            return f"MAX_DAILY_LOSS_USD triggered (daily_realized={daily_realized:.2f})"

        # Drawdown based on (cash_start + realized + unrealized)
        try:
            cash_start = float(db.get_state("cash_start_usd", str(self.config.bankroll)))
        except Exception:
            cash_start = self.config.bankroll

        # Update mark-to-market before evaluating drawdown
        self.portfolio.update_prices()
        equity = cash_start + self.portfolio.realized_pnl + self.portfolio.get_total_unrealized_pnl()
        dd_pct = 0.0
        if cash_start > 0:
            dd_pct = (cash_start - equity) / cash_start * 100.0

        if config.safety.max_drawdown_pct and dd_pct >= abs(config.safety.max_drawdown_pct):
            return f"MAX_DRAWDOWN_PCT triggered (drawdown={dd_pct:.2f}%)"

        return None

    def _init_models(self, models: list = None):
        """Initialize probability models."""
        if models is not None:
            self._models = models
        else:
            self._models = []
            
            # Load manual estimates from file if configured
            estimates_file = os.getenv("MANUAL_ESTIMATES_FILE", "")
            if estimates_file:
                manual = ManualModel.from_file(estimates_file)
                if manual._estimates:
                    self._models.append(manual)
                    logger.info(f"✅ Manual model loaded ({len(manual._estimates)} estimates from {estimates_file})")
            
            # Auto-initialize available models
            odds_model = OddsApiModel()
            if odds_model.available:
                self._models.append(odds_model)
                logger.info("✅ OddsAPI model loaded (sports bookmaker consensus)")
            
            momentum_model = MomentumModel(db=db)
            self._models.append(momentum_model)
            logger.info("✅ Momentum model loaded (price history analysis)")
        
        # Separate models by type for routing
        self._value_models = [m for m in self._models if not isinstance(m, MomentumModel)]
        self._momentum_models = [m for m in self._models if isinstance(m, MomentumModel)]
    
    # ==================== SCANNING ====================
    
    def _hours_until_resolution(self, market: Market) -> float:
        """Calculate hours until market resolves."""
        try:
            if hasattr(market, 'end_date') and market.end_date:
                end_date = datetime.fromisoformat(market.end_date.replace('Z', '+00:00'))
                delta = end_date - datetime.now(end_date.tzinfo)
                hours = delta.total_seconds() / 3600
                return max(0, hours)
            # If no end date, assume it's far away
            return 9999
        except:
            return 9999
    
    def _days_until_resolution(self, market: Market) -> float:
        """Calculate days until market resolves."""
        return self._hours_until_resolution(market) / 24
    
    def _get_market_category(self, market: Market) -> str:
        """Determine if market is sports or crypto."""
        question = market.question.lower()
        
        # Sports keywords
        sports_keywords = ['win', 'championship', 'super bowl', 'nba', 'nfl', 'mlb', 
                          'nhl', 'world series', 'playoffs', 'finals', 'game', 
                          'match', 'vs', 'score', 'premier league', 'uefa', 'fifa']
        
        # Crypto keywords  
        crypto_keywords = ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'solana',
                          'sol', 'xrp', 'doge', 'price', 'token', 'coin', 'defi']
        
        for kw in sports_keywords:
            if kw in question:
                return "sports"
        
        for kw in crypto_keywords:
            if kw in question:
                return "crypto"
        
        return "other"
    
    def scan_markets(self) -> list[Market]:
        """Scan for markets matching our criteria (with smart time filters)."""
        all_markets = []
        
        if "crypto" in self.config.categories:
            crypto = self.fetcher.get_crypto_markets(limit=50)
            all_markets.extend(crypto)
        
        if "sports" in self.config.categories:
            sports = self.fetcher.get_sports_markets(limit=50)
            all_markets.extend(sports)
        
        # Filter by volume, liquidity, AND resolution time
        filtered = []
        for m in all_markets:
            # Basic filters
            if m.volume < self.config.min_volume:
                continue
            if m.liquidity < self.config.min_liquidity:
                continue
            
            # ⏰ Smart time-based filters (category-aware)
            hours_left = self._hours_until_resolution(m)
            days_left = hours_left / 24
            category = self._get_market_category(m)
            
            # Minimum time check (need at least 2h to react)
            if hours_left < self.config.min_hours_to_resolution:
                continue
            
            # Category-specific max days
            if category == "sports":
                # Sports: allow same-day, but max 3 days out (games are scheduled)
                if not self.config.sports_allow_same_day and days_left < 1:
                    continue
                if days_left > self.config.sports_max_days:
                    continue
            elif category == "crypto":
                # Crypto: allow same-day, max 7 days out
                if not self.config.crypto_allow_same_day and days_left < 1:
                    continue
                if days_left > self.config.crypto_max_days:
                    continue
            else:
                # Other: use default max
                if days_left > self.config.max_days_to_resolution:
                    continue
            
            filtered.append(m)
        
        # Sort by hours to resolution (prefer markets ending soon)
        if self.config.prefer_ending_soon:
            filtered.sort(key=lambda m: self._hours_until_resolution(m))
        
        return filtered
    
    # ==================== STRATEGY LOGIC ====================
    
    def find_value_bets(self, markets: list[Market]) -> list[tuple[Market, str, float]]:
        """
        Find value bets — markets where our probability models disagree
        with the market price by more than min_edge%.
        
        Each model produces an independent probability estimate. We take the
        highest-confidence estimate and compare it to the market price.
        
        Returns list of (market, side, edge_percent).
        """
        if not self._value_models:
            return []
        
        opportunities = []
        
        # Batch-estimate where possible (saves API calls)
        all_estimates: dict[str, ProbabilityEstimate] = {}
        for model in self._value_models:
            try:
                batch = model.batch_estimate(markets)
                for market_id, est in batch.items():
                    # Keep the highest-confidence estimate per market
                    existing = all_estimates.get(market_id)
                    if existing is None or est.confidence > existing.confidence:
                        all_estimates[market_id] = est
            except Exception as e:
                logger.error(f"   Warning: {model.name} failed: {e}")
        
        for market in markets:
            est = all_estimates.get(market.id)
            if est is None:
                continue
            
            # Check YES side edge
            yes_edge = est.edge_vs_market(market.price_yes, "YES")
            if yes_edge >= self.config.min_edge and est.confidence >= 0.5:
                opportunities.append((market, "YES", yes_edge))
            
            # Check NO side edge
            no_edge = est.edge_vs_market(market.price_no, "NO")
            if no_edge >= self.config.min_edge and est.confidence >= 0.5:
                opportunities.append((market, "NO", no_edge))
        
        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x[2], reverse=True)
        return opportunities
    
    def find_momentum_bets(self, markets: list[Market]) -> list[tuple[Market, str, float]]:
        """
        Find momentum bets — markets with consistent price trends detected
        from actual price history stored in the database.
        
        Requires the persistence layer to have been collecting price snapshots
        (odds_tracker does this automatically). Returns nothing if no history
        is available yet.
        
        Returns list of (market, side, edge_percent).
        """
        if not self._momentum_models:
            return []
        
        opportunities = []
        
        for market in markets:
            for model in self._momentum_models:
                try:
                    est = model.estimate(market)
                    if est is None:
                        continue
                    
                    # Determine which side the momentum favors
                    yes_edge = est.edge_vs_market(market.price_yes, "YES")
                    no_edge = est.edge_vs_market(market.price_no, "NO")
                    
                    # Pick the side with positive edge
                    if yes_edge >= self.config.min_edge and yes_edge >= no_edge:
                        opportunities.append((market, "YES", yes_edge))
                    elif no_edge >= self.config.min_edge:
                        opportunities.append((market, "NO", no_edge))
                        
                except Exception as e:
                    logger.error(f"   Warning: momentum analysis failed for {market.question[:30]}...: {e}")
        
        opportunities.sort(key=lambda x: x[2], reverse=True)
        return opportunities
    
    def find_arbitrage_bets(self, markets: list[Market]) -> list[tuple[Market, str, float]]:
        """
        Find arbitrage — when YES + NO < $1.00 on the LIVE orderbook.
        
        IMPORTANT: Uses fresh orderbook data (best ask prices) instead of
        stale Gamma API prices. Gamma prices can be minutes old and will
        produce phantom arbitrage signals. The ArbitrageDetector queries
        the CLOB orderbook directly for each market.
        
        Returns list of (market, "ARB", profit_percent).
        """
        opportunities = []
        
        for market in markets:
            try:
                opp = self.arb_detector.check_market(market)
                if opp and opp.opportunity_type == "underpriced":
                    profit_pct = opp.profit_percent
                    if profit_pct >= 2.0:  # At least 2% profit
                        opportunities.append((market, "ARB", profit_pct))
            except Exception as e:
                # Don't spam errors for every market
                pass
        
        opportunities.sort(key=lambda x: x[2], reverse=True)
        return opportunities
    
    def find_favorite_bets(self, markets: list[Market]) -> list[tuple[Market, str, float]]:
        """
        Find favorites — high probability outcomes (>65%).
        
        ⚠️ WARNING: This strategy does NOT detect real edge. Betting on
        favorites is only profitable if the market systematically underprices
        likely outcomes. In efficient markets, a 70¢ contract has ~70% chance
        of paying $1 — no free money. Use this only as a filter combined
        with a real probability model, or accept that you're essentially
        gambling on the favorite with a small house edge against you.
        """
        opportunities = []
        
        for market in markets:
            # High probability YES
            if market.price_yes >= 0.65 and market.price_yes <= 0.85:
                # Edge = probability of profit
                edge = market.price_yes * 100 - 50
                opportunities.append((market, "YES", edge))
            
            # High probability NO
            if market.price_no >= 0.65 and market.price_no <= 0.85:
                edge = market.price_no * 100 - 50
                opportunities.append((market, "NO", edge))
        
        opportunities.sort(key=lambda x: x[2], reverse=True)
        return opportunities
    
    def find_underdog_bets(self, markets: list[Market]) -> list[tuple[Market, str, float]]:
        """
        Find underdogs — low probability, high reward.
        
        ⚠️ WARNING: This strategy does NOT detect real edge. Buying cheap
        contracts feels exciting but is only profitable if your model says
        the true probability is higher than the market price. A 25¢ contract
        pays 4:1 but only wins ~25% of the time in an efficient market.
        Use with a real probability model for best results.
        """
        opportunities = []
        
        for market in markets:
            # Underdog YES (20-40%)
            if market.price_yes >= 0.20 and market.price_yes <= 0.40:
                # Potential return if wins
                potential_return = (1 / market.price_yes - 1) * 100
                if potential_return >= 100:  # 2x or more potential
                    opportunities.append((market, "YES", potential_return))
            
            # Underdog NO
            if market.price_no >= 0.20 and market.price_no <= 0.40:
                potential_return = (1 / market.price_no - 1) * 100
                if potential_return >= 100:
                    opportunities.append((market, "NO", potential_return))
        
        opportunities.sort(key=lambda x: x[2], reverse=True)
        return opportunities
    
    def find_opportunities(self, markets: list[Market]) -> list[tuple[Market, str, float, str]]:
        """
        Find all opportunities based on strategy.
        Returns list of (market, side, score, strategy_name)
        """
        opportunities = []
        
        if self.config.strategy == AutoStrategy.VALUE:
            for m, s, e in self.find_value_bets(markets):
                opportunities.append((m, s, e, "value"))
        
        elif self.config.strategy == AutoStrategy.MOMENTUM:
            for m, s, e in self.find_momentum_bets(markets):
                opportunities.append((m, s, e, "momentum"))
        
        elif self.config.strategy == AutoStrategy.ARBITRAGE:
            for m, s, e in self.find_arbitrage_bets(markets):
                opportunities.append((m, s, e, "arbitrage"))
        
        elif self.config.strategy == AutoStrategy.FAVORITES:
            for m, s, e in self.find_favorite_bets(markets):
                opportunities.append((m, s, e, "favorites"))
        
        elif self.config.strategy == AutoStrategy.UNDERDOGS:
            for m, s, e in self.find_underdog_bets(markets):
                opportunities.append((m, s, e, "underdogs"))
        
        elif self.config.strategy == AutoStrategy.MIXED:
            # MIXED = arbitrage (always safe) + value bets (if models available)
            # Does NOT include favorites/underdogs (no real edge detection)
            for m, s, e in self.find_arbitrage_bets(markets)[:3]:
                opportunities.append((m, s, e, "arbitrage"))
            for m, s, e in self.find_value_bets(markets)[:3]:
                opportunities.append((m, s, e, "value"))
            for m, s, e in self.find_momentum_bets(markets)[:2]:
                opportunities.append((m, s, e, "momentum"))
        
        # Remove duplicates (same market)
        seen = set()
        unique = []
        for opp in opportunities:
            if opp[0].id not in seen:
                seen.add(opp[0].id)
                unique.append(opp)
        
        return unique
    
    # ==================== BETTING ====================
    
    def calculate_bet_size(self) -> float:
        """Calculate appropriate bet size based on bankroll."""
        # Available = bankroll - reserve - open positions
        reserve = self.config.bankroll * (self.config.reserve_percent / 100)
        open_value = sum(b.size * b.entry_price for b in self.active_bets.values())
        available = self.config.bankroll - reserve - open_value
        
        # Bet size = min of max_bet_size and 25% of available
        bet_size = min(self.config.max_bet_size, available * 0.25)
        
        return max(bet_size, 0)
    
    def can_place_bet(self) -> bool:
        """Check if we can place another bet."""
        if len(self.active_bets) >= self.config.max_open_positions:
            return False
        
        bet_size = self.calculate_bet_size()
        if bet_size < 5:  # Minimum $5 bet
            return False
        
        return True
    
    def place_auto_bet(
        self,
        market: Market,
        side: str,
        strategy_name: str
    ) -> Optional[AutoBet]:
        """Place an automated bet."""
        if not self.can_place_bet():
            return None
        
        bet_size = self.calculate_bet_size()
        entry_price = market.price_yes if side == "YES" else market.price_no
        
        # Calculate TP/SL prices
        tp_price = min(entry_price * (1 + self.config.take_profit_percent / 100), 0.99)
        sl_price = max(entry_price * (1 - self.config.stop_loss_percent / 100), 0.01)
        
        # Generate bet ID
        self._bet_counter += 1
        bet_id = f"AUTO_{datetime.now().strftime('%m%d_%H%M')}_{self._bet_counter}"
        
        # Time info
        hours_left = self._hours_until_resolution(market)
        if hours_left < 24:
            time_str = f"{hours_left:.1f} hours"
        else:
            time_str = f"{hours_left/24:.1f} days"
        
        logger.info(f"\n🎲 PLACING AUTO BET")
        logger.info(f"   Market: {market.question[:50]}...")
        logger.info(f"   Side: {side}")
        logger.info(f"   Size: ${bet_size:.2f}")
        logger.info(f"   Entry: {entry_price*100:.1f}¢")
        logger.info(f"   TP: {tp_price*100:.1f}¢ (+{self.config.take_profit_percent}%)")
        logger.info(f"   SL: {sl_price*100:.1f}¢ (-{self.config.stop_loss_percent}%)")
        logger.info(f"   Strategy: {strategy_name}")
        logger.info(f"   ⏰ Resolves in: {time_str}")
        
        # Handle arbitrage (buy both sides)
        if side == "ARB":
            # Split bet between YES and NO
            half_size = bet_size / 2
            token_yes = market.token_id_yes
            token_no = market.token_id_no
            
            result_yes = self.order_manager.buy(
                token_id=token_yes,
                market_question=market.question,
                size=half_size / market.price_yes,
                price=market.price_yes,
                side="YES"
            )
            
            result_no = self.order_manager.buy(
                token_id=token_no,
                market_question=market.question,
                size=half_size / market.price_no,
                price=market.price_no,
                side="NO"
            )
            
            if result_yes.success and result_no.success:
                logger.info(f"   ✅ Arbitrage placed!")
            else:
                logger.error(f"   ❌ Arbitrage failed")
                return None
        else:
            # Regular bet
            token_id = market.token_id_yes if side == "YES" else market.token_id_no
            
            result = self.order_manager.buy_with_tp_sl(
                token_id=token_id,
                market_question=market.question,
                size=bet_size / entry_price,
                entry_price=entry_price,
                take_profit=tp_price,
                stop_loss=sl_price,
                trailing_stop_percent=self.config.trailing_stop_percent,
                side=side
            )
            
            if not result["success"]:
                logger.error(f"   ❌ Bet failed")
                return None
            
            logger.info(f"   ✅ Bet placed!")
        
        # Track the bet
        auto_bet = AutoBet(
            id=bet_id,
            market=market,
            side=side,
            size=bet_size,
            entry_price=entry_price,
            entry_time=datetime.now(),
            take_profit=tp_price,
            stop_loss=sl_price,
            strategy=strategy_name
        )
        
        self.active_bets[bet_id] = auto_bet
        return auto_bet
    
    # ==================== MONITORING ====================
    
    def check_positions(self):
        """Check all open positions and update status."""
        for bet_id, bet in list(self.active_bets.items()):
            # Get current price
            try:
                token_id = bet.market.token_id_yes if bet.side == "YES" else bet.market.token_id_no
                current_price = self.tracker.fetch_price(token_id)
                
                if current_price is None:
                    continue
                
                # Check if TP/SL hit (order_manager handles this, but we track)
                pnl_pct = ((current_price - bet.entry_price) / bet.entry_price) * 100
                
                # Check max hold time
                hold_time = datetime.now() - bet.entry_time
                if hold_time > timedelta(hours=self.config.max_hold_hours):
                    logger.info(f"\n⏰ Max hold time reached for {bet.id}")
                    self._close_position(bet, current_price, "timeout")
                
            except Exception as e:
                logger.error(f"Error checking {bet_id}: {e}")
    
    def _close_position(self, bet: AutoBet, price: float, reason: str):
        """Close a position."""
        pnl = (price - bet.entry_price) * (bet.size / bet.entry_price)
        pnl_pct = ((price - bet.entry_price) / bet.entry_price) * 100
        
        logger.info(f"\n📤 CLOSING POSITION")
        logger.info(f"   {bet.market.question[:40]}...")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   Entry: {bet.entry_price*100:.1f}¢ → Exit: {price*100:.1f}¢")
        logger.info(f"   P&L: ${pnl:.2f} ({pnl_pct:+.1f}%)")
        
        # Update stats
        self.total_pnl += pnl
        bet.status = "won" if pnl > 0 else "lost"
        self.bet_history.append(bet)
        del self.active_bets[bet.id]
    
    # ==================== MAIN LOOP ====================
    
    def run_once(self):
        """Run one cycle of scanning and betting."""
        logger.info(f"\n🔍 Scanning markets... ({datetime.now().strftime('%H:%M:%S')})")
        
        # Scan markets
        markets = self.scan_markets()
        logger.info(f"   Found {len(markets)} markets")
        
        # Find opportunities
        opportunities = self.find_opportunities(markets)
        logger.info(f"   Found {len(opportunities)} opportunities")
        
        # Live-safety: circuit breakers
        breaker_reason = self._circuit_breaker_check()
        if breaker_reason:
            logger.error(f"🛑 CIRCUIT BREAKER: {breaker_reason} — halting new entries")
            # Block new entries; still manage existing positions
            config.safety.kill_switch = True

        # Place bets on best opportunities
        bets_placed = 0
        for market, side, score, strategy in opportunities:
            if not self.can_place_bet():
                break
            
            # Skip if we already have a bet on this market
            if any(b.market.id == market.id for b in self.active_bets.values()):
                continue
            
            # Kill switch blocks NEW entries (still monitors/prints positions)
            if config.safety.kill_switch:
                continue

            # Spread guard (uses token orderbook)
            token_id = market.token_id_yes if side == "YES" else market.token_id_no
            spread_bps = self._get_orderbook_spread_bps(token_id)
            if spread_bps is not None and spread_bps > config.safety.max_spread_bps:
                logger.info(
                    f"⛔ Skip (spread {spread_bps:.0f} bps > {config.safety.max_spread_bps:.0f}): "
                    f"{market.question[:50]}..."
                )
                continue

            bet = self.place_auto_bet(market, side, strategy)
            if bet:
                bets_placed += 1
            
            # Limit bets per cycle
            if bets_placed >= 2:
                break
        
        # Check existing positions
        self.check_positions()
        
        # Print summary
        self.print_status()
    
    def run(self, cycles: int = None):
        """
        Run the auto trader.
        
        Args:
            cycles: Number of cycles to run (None = forever)
        """
        self._running = True
        cycle = 0
        
        # Startup live-safety actions
        if not db.get_state("cash_start_usd", ""):
            db.set_state("cash_start_usd", str(self.config.bankroll))
        self._startup_safety()

        # Start order fill tracking (polls for confirmed fills)
        self.order_manager.order_tracker.start()
        
        logger.info("\n🚀 AUTO TRADER STARTED")
        logger.info(f"   Scanning every {self.config.scan_interval} seconds")
        logger.info("   Press Ctrl+C to stop\n")
        
        try:
            while self._running:
                self.run_once()
                cycle += 1
                
                if cycles and cycle >= cycles:
                    break
                
                # Wait for next scan
                logger.info(f"\n💤 Waiting {self.config.scan_interval}s until next scan...")
                time.sleep(self.config.scan_interval)
        
        except KeyboardInterrupt:
            logger.info("\n\n⏹️ Stopping auto trader...")
        
        finally:
            self._running = False
            self.order_manager.order_tracker.stop()
            self.print_final_report()
    
    def stop(self):
        """Stop the auto trader."""
        self._running = False
    
    def run_scan_only(self, cycles: int = None):
        """
        Run in SCAN MODE - shows opportunities without placing bets.
        Safe way to preview what the bot would do.
        
        Args:
            cycles: Number of cycles to run (None = forever)
        """
        self._running = True
        cycle = 0
        
        logger.info('============================================================')
        logger.info("👁️ SCAN MODE - Watching Only (NO bets placed)")
        logger.info('============================================================')
        logger.info(f"💰 Simulated Bankroll: ${self.config.bankroll}")
        logger.info(f"📊 Strategy: {self.config.strategy.value}")
        logger.info(f"🎯 Would bet up to: ${self.config.max_bet_size:.2f}")
        logger.info(f"📈 Take Profit: +{self.config.take_profit_percent}%")
        logger.info(f"📉 Stop Loss: -{self.config.stop_loss_percent}%")
        logger.info(f"⏰ Same-day bets: ✅ ENABLED")
        logger.info(f"⏰ Sports max: {self.config.sports_max_days}d | Crypto max: {self.config.crypto_max_days}d")
        logger.info('============================================================')
        logger.info("\n   Press Ctrl+C to stop\n")
        
        try:
            while self._running:
                logger.info(f"\n🔍 Scanning markets... ({datetime.now().strftime('%H:%M:%S')})")
                
                # Scan markets
                markets = self.scan_markets()
                logger.info(f"   Found {len(markets)} markets matching criteria")
                
                # Count by category
                sports_today = [m for m in markets if self._get_market_category(m) == "sports" and self._hours_until_resolution(m) < 24]
                crypto_today = [m for m in markets if self._get_market_category(m) == "crypto" and self._hours_until_resolution(m) < 24]
                
                if sports_today:
                    logger.info(f"   🏀 Tonight's games: {len(sports_today)}")
                if crypto_today:
                    logger.info(f"   💰 Same-day crypto: {len(crypto_today)}")
                
                # Find opportunities
                opportunities = self.find_opportunities(markets)
                
                if opportunities:
                    logger.info(f"\n📊 OPPORTUNITIES FOUND ({len(opportunities)}):")
                    logger.info('============================================================')
                    
                    for i, (market, side, score, strategy) in enumerate(opportunities[:5], 1):
                        hours = self._hours_until_resolution(market)
                        if hours < 24:
                            time_str = f"{hours:.1f}h"
                        else:
                            time_str = f"{hours/24:.1f}d"
                        
                        price = market.price_yes if side == "YES" else market.price_no
                        
                        # Show what we WOULD bet
                        logger.info(f"\n  {i}. {market.question[:45]}...")
                        logger.info(f"     📍 {side} @ {price*100:.0f}¢ | ⏰ {time_str} | 🎯 {strategy}")
                        logger.info(f"     📊 Score: {score:.1f} | Volume: ${market.volume:,.0f}")
                        
                        if side == "ARB":
                            profit = (1 - market.price_yes - market.price_no) * 100
                            logger.info(f"     💰 Arb profit: +{profit:.1f}%")
                        else:
                            tp = price * (1 + self.config.take_profit_percent / 100)
                            sl = price * (1 - self.config.stop_loss_percent / 100)
                            logger.info(f"     🎯 TP: {tp*100:.0f}¢ | 🛑 SL: {sl*100:.0f}¢")
                    
                    # Show what bot would do
                    bet_size = self.calculate_bet_size()
                    if self.can_place_bet():
                        logger.info(f"\n   ✅ Would place bet: ${bet_size:.2f} on #{1}")
                    else:
                        logger.warning(f"\n   ⚠️ Would NOT bet (position limit or low funds)")
                else:
                    logger.info(f"\n   😴 No opportunities found this scan")
                
                # Summary
                logger.info(f"\n" + "-"*55)
                logger.info(f"📊 SCAN SUMMARY")
                logger.info(f"   Markets scanned: {len(markets)}")
                logger.info(f"   Opportunities: {len(opportunities)}")
                logger.info(f"   Same-day sports: {len(sports_today)}")
                logger.info(f"   Same-day crypto: {len(crypto_today)}")
                logger.info('============================================================')
                
                cycle += 1
                if cycles and cycle >= cycles:
                    break
                
                # Wait for next scan
                logger.info(f"\n💤 Next scan in {self.config.scan_interval}s...")
                time.sleep(self.config.scan_interval)
        
        except KeyboardInterrupt:
            logger.info("\n\n⏹️ Scan mode stopped")
        
        finally:
            self._running = False
            logger.info('============================================================')
            logger.info("👁️ SCAN MODE ENDED")
            logger.info('============================================================')
            logger.info("To trade for real, run:")
            logger.info("  python auto_trader.py")
            logger.info("  Select: balanced, scalper, or sports")
            logger.info('============================================================')
    
    # ==================== REPORTING ====================
    
    def print_status(self):
        """Print current status."""
        logger.info('============================================================')
        logger.info("📊 STATUS")
        logger.info('============================================================')
        
        bet_size = self.calculate_bet_size()
        logger.info(f"💰 Available for betting: ${bet_size:.2f}")
        logger.info(f"📈 Total P&L: ${self.total_pnl:.2f}")
        logger.info(f"🎲 Active bets: {len(self.active_bets)}/{self.config.max_open_positions}")
        
        pending = self.order_manager.order_tracker.pending_count
        if pending:
            logger.info(f"⏳ Pending fills: {pending} order(s) awaiting confirmation")
        
        if self.active_bets:
            logger.info("\n🎯 Open Positions:")
            for bet in self.active_bets.values():
                logger.info(f"   • {bet.side} {bet.market.question[:35]}...")
                logger.info(f"     Entry: {bet.entry_price*100:.0f}¢ | Size: ${bet.size:.0f} | {bet.strategy}")
        
        logger.info('============================================================')
    
    def print_final_report(self):
        """Print final trading report."""
        logger.info('============================================================')
        logger.info("📋 FINAL REPORT")
        logger.info('============================================================')
        
        total_bets = len(self.bet_history) + len(self.active_bets)
        wins = sum(1 for b in self.bet_history if b.status == "won")
        losses = sum(1 for b in self.bet_history if b.status == "lost")
        
        logger.info(f"💰 Starting Bankroll: ${self.config.bankroll:.2f}")
        logger.info(f"📈 Total P&L: ${self.total_pnl:.2f}")
        logger.info(f"💵 Final Bankroll: ${self.config.bankroll + self.total_pnl:.2f}")
        logger.info(f"\n🎲 Total Bets: {total_bets}")
        logger.info(f"   ✅ Wins: {wins}")
        logger.error(f"   ❌ Losses: {losses}")
        logger.info(f"   🔄 Open: {len(self.active_bets)}")
        
        if wins + losses > 0:
            win_rate = wins / (wins + losses) * 100
            logger.info(f"   📊 Win Rate: {win_rate:.1f}%")
        
        logger.info('============================================================')


# ==================== PRESET CONFIGS ====================

def conservative_config(bankroll: float = 50) -> AutoTradeConfig:
    """Conservative auto trading config - quick turnover."""
    return AutoTradeConfig(
        bankroll=bankroll,
        max_bet_size=bankroll * 0.15,       # 15% max per bet
        max_open_positions=3,
        reserve_percent=30,
        strategy=AutoStrategy.FAVORITES,
        min_volume=100000,
        min_edge=15,
        take_profit_percent=25,
        stop_loss_percent=10,
        # ⏰ Time settings
        min_hours_to_resolution=3,          # At least 3h to resolution
        sports_allow_same_day=True,         # ✅ Bet on tonight's games
        crypto_allow_same_day=True,         # ✅ Same-day crypto OK
        sports_max_days=2,                  # Sports: max 2 days out
        crypto_max_days=5,                  # Crypto: max 5 days out
        max_hold_hours=24,                  # Force sell after 24h
    )


def balanced_config(bankroll: float = 50) -> AutoTradeConfig:
    """Balanced config — arbitrage + model-backed value bets + momentum."""
    return AutoTradeConfig(
        bankroll=bankroll,
        max_bet_size=bankroll * 0.20,       # 20% max per bet
        max_open_positions=4,
        reserve_percent=20,
        strategy=AutoStrategy.MIXED,
        min_volume=50000,
        min_edge=10,
        take_profit_percent=35,
        stop_loss_percent=15,
        # ⏰ Time settings
        min_hours_to_resolution=2,          # At least 2h to resolution
        sports_allow_same_day=True,         # ✅ Bet on tonight's games
        crypto_allow_same_day=True,         # ✅ Same-day crypto OK
        sports_max_days=3,                  # Sports: max 3 days out
        crypto_max_days=7,                  # Crypto: max 7 days out
        max_hold_hours=36,                  # Force sell after 36h
    )


def aggressive_config(bankroll: float = 50) -> AutoTradeConfig:
    """Aggressive auto trading config - longer holds allowed."""
    return AutoTradeConfig(
        bankroll=bankroll,
        max_bet_size=bankroll * 0.25,       # 25% max per bet
        max_open_positions=5,
        reserve_percent=15,
        strategy=AutoStrategy.UNDERDOGS,
        min_volume=25000,
        min_edge=5,
        take_profit_percent=75,
        stop_loss_percent=25,
        # ⏰ Time settings
        min_hours_to_resolution=2,          # At least 2h to resolution
        sports_allow_same_day=True,         # ✅ Bet on tonight's games
        crypto_allow_same_day=True,         # ✅ Same-day crypto OK
        sports_max_days=5,                  # Sports: max 5 days out
        crypto_max_days=14,                 # Crypto: max 14 days out
        max_hold_hours=48,                  # Force sell after 48h
    )


def scalper_config(bankroll: float = 50) -> AutoTradeConfig:
    """Scalper config — quick trades using real momentum detection from price history.
    Requires price snapshots to have been collected for at least lookback_hours."""
    return AutoTradeConfig(
        bankroll=bankroll,
        max_bet_size=bankroll * 0.25,       # 25% max per bet
        max_open_positions=3,
        reserve_percent=25,
        strategy=AutoStrategy.MOMENTUM,
        min_volume=75000,                   # Higher volume for quick exits
        min_edge=8,
        take_profit_percent=15,             # Small quick profits
        stop_loss_percent=8,                # Tight stops
        # ⏰ Time settings - QUICK TRADES
        min_hours_to_resolution=2,          # At least 2h (time to exit)
        sports_allow_same_day=True,         # ✅ Tonight's games are BEST
        crypto_allow_same_day=True,         # ✅ Same-day crypto
        sports_max_days=1,                  # Sports: today or tomorrow only
        crypto_max_days=2,                  # Crypto: max 2 days
        prefer_ending_soon=True,
        max_hold_hours=12,                  # Force sell after 12h
    )


def sports_tonight_config(bankroll: float = 50) -> AutoTradeConfig:
    """Sports config - optimized for betting on tonight's games."""
    return AutoTradeConfig(
        bankroll=bankroll,
        max_bet_size=bankroll * 0.20,       # 20% max per bet
        max_open_positions=4,
        reserve_percent=20,
        strategy=AutoStrategy.MIXED,
        categories=["sports"],              # Sports only
        min_volume=50000,
        min_edge=10,
        take_profit_percent=30,
        stop_loss_percent=15,
        # ⏰ Sports-specific
        min_hours_to_resolution=1,          # Games starting in 1h+ OK
        sports_allow_same_day=True,         # ✅ Tonight's games
        sports_max_days=2,                  # Today or tomorrow
        prefer_ending_soon=True,            # Prioritize tonight
        max_hold_hours=24,
    )


# ==================== QUICK START ====================

def start_auto_trader(
    bankroll: float = 50,
    risk_level: str = "balanced"
):
    """
    Quick start for auto trader.
    
    Args:
        bankroll: Your starting bankroll
        risk_level: "conservative", "balanced", "aggressive", "scalper", or "sports"
    """
    if risk_level == "conservative":
        config = conservative_config(bankroll)
    elif risk_level == "aggressive":
        config = aggressive_config(bankroll)
    elif risk_level == "scalper":
        config = scalper_config(bankroll)
    elif risk_level == "sports":
        config = sports_tonight_config(bankroll)
    else:
        config = balanced_config(bankroll)
    
    bot = AutoTrader(config=config)
    bot.run()


# Demo mode
if __name__ == "__main__":
    from bot_logging import setup_logging
    setup_logging()
    logger.info('============================================================')
    logger.info("🤖 POLYMARKET AUTO TRADER")
    logger.info('============================================================')
    logger.info("\nModes:")
    logger.info("  1. scan      - Watch only, NO betting (safe preview)")
    logger.info("  2. conservative")
    logger.info("  3. balanced  - Recommended")
    logger.info("  4. aggressive")
    logger.info("  5. scalper   - Quick same-day trades")
    logger.info("  6. sports    - Tonight's games only 🏀")
    logger.info("")
    
    try:
        bankroll = float(input("Enter bankroll amount [$50]: ") or "50")
        mode = input("Select mode [scan]: ").lower() or "scan"
        
        if mode == "scan":
            logger.info(f"\n👁️ SCAN MODE - Watching only, NO bets placed")
            logger.info("   Press Ctrl+C to stop\n")
            
            # Run in scan mode
            config = balanced_config(bankroll)
            bot = AutoTrader(config=config)
            bot.run_scan_only()
        else:
            logger.info(f"\n🚀 Starting with ${bankroll} on {mode} mode...")
            logger.info("   Same-day sports & crypto: ✅ ENABLED")
            logger.info("   Press Ctrl+C to stop\n")
            
            start_auto_trader(bankroll=bankroll, risk_level=mode)
    
    except KeyboardInterrupt:
        logger.info("\n👋 Goodbye!")
    except ValueError:
        logger.info("Invalid input. Using scan mode...")
        config = balanced_config(50)
        bot = AutoTrader(config=config)
        bot.run_scan_only()
