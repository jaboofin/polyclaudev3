"""
Portfolio Manager - Track positions, P&L, and exposure.
"""

import json
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from config import config
from client_manager import clients
from persistence import db
from market_fetcher import Market, MarketFetcher
import logging
logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a position in a market."""
    token_id: str
    market_question: str
    side: str  # "YES" or "NO"
    size: float  # Number of shares
    avg_entry_price: float
    current_price: float = 0.0
    
    @property
    def cost_basis(self) -> float:
        """Total cost of position."""
        return self.size * self.avg_entry_price
    
    @property
    def current_value(self) -> float:
        """Current value of position."""
        return self.size * self.current_price
    
    @property
    def unrealized_pnl(self) -> float:
        """Unrealized profit/loss."""
        return self.current_value - self.cost_basis
    
    @property
    def unrealized_pnl_percent(self) -> float:
        """Unrealized P&L as percentage."""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100
    
    @property
    def potential_payout(self) -> float:
        """Potential payout if position wins (YES resolves to $1)."""
        return self.size * 1.0


@dataclass
class Trade:
    """Represents a completed trade."""
    timestamp: datetime
    token_id: str
    market_question: str
    side: str
    action: str  # "BUY" or "SELL"
    size: float
    price: float
    fee: float = 0.0
    
    @property
    def total_cost(self) -> float:
        """Total cost including fees."""
        if self.action == "BUY":
            return (self.size * self.price) + self.fee
        else:
            return (self.size * self.price) - self.fee


@dataclass
class PortfolioStats:
    """Portfolio statistics."""
    total_positions: int
    total_value: float
    total_cost_basis: float
    total_unrealized_pnl: float
    total_realized_pnl: float
    win_rate: float
    largest_position: Optional[str]
    exposure_by_category: dict = field(default_factory=dict)


class PortfolioManager:
    """
    Manage portfolio positions and track performance.
    
    Usage:
        portfolio = PortfolioManager()
        
        # Load existing positions from Polymarket
        portfolio.sync_from_chain()
        
        # View positions
        portfolio.print_summary()
        
        # Get stats
        stats = portfolio.get_stats()
    """
    
    def __init__(self):
        self.positions: dict[str, Position] = {}
        self.trade_history: list[Trade] = []
        self.realized_pnl: float = 0.0
        self._market_fetcher = MarketFetcher()
        
        # Load saved state from database
        self._load_from_db()
    
    def _load_from_db(self):
        """Load positions and realized P&L from the database."""
        saved_positions = db.load_positions()
        for p in saved_positions:
            key = p["id"]
            self.positions[key] = Position(
                token_id=p["token_id"],
                market_question=p["market_question"],
                side=p["side"],
                size=p["size"],
                avg_entry_price=p["avg_entry_price"],
                current_price=p.get("current_price", 0),
            )
        
        # Load realized P&L from trade history
        self.realized_pnl = float(db.get_state("realized_pnl", "0"))
        
        if saved_positions:
            logger.info(f"📂 Loaded {len(saved_positions)} positions from database")
    
    def _persist_position(self, key: str):
        """Save a single position to the database."""
        if key in self.positions:
            pos = self.positions[key]
            db.save_position(
                token_id=pos.token_id,
                side=pos.side,
                market_question=pos.market_question,
                size=pos.size,
                avg_entry_price=pos.avg_entry_price,
                current_price=pos.current_price,
            )
    
    def _persist_realized_pnl(self):
        """Save realized P&L to the database."""
        db.set_state("realized_pnl", str(self.realized_pnl))
    
    def add_position(
        self,
        token_id: str,
        market_question: str,
        side: str,
        size: float,
        entry_price: float
    ):
        """
        Add or update a position.
        
        Args:
            token_id: Market token ID
            market_question: Market question text
            side: "YES" or "NO"
            size: Number of shares
            entry_price: Average entry price
        """
        key = f"{token_id}_{side}"
        
        if key in self.positions:
            # Update existing position (average in)
            existing = self.positions[key]
            total_size = existing.size + size
            total_cost = (existing.size * existing.avg_entry_price) + (size * entry_price)
            avg_price = total_cost / total_size if total_size > 0 else 0
            
            existing.size = total_size
            existing.avg_entry_price = avg_price
        else:
            # New position
            self.positions[key] = Position(
                token_id=token_id,
                market_question=market_question,
                side=side,
                size=size,
                avg_entry_price=entry_price
            )
        
        # Persist position to database
        self._persist_position(key)
        
        # Record trade in both memory and database
        self.trade_history.append(Trade(
            timestamp=datetime.now(),
            token_id=token_id,
            market_question=market_question,
            side=side,
            action="BUY",
            size=size,
            price=entry_price
        ))
        db.record_trade(
            token_id=token_id,
            market_question=market_question,
            side=side,
            action="BUY",
            size=size,
            price=entry_price,
        )
    
    def close_position(
        self,
        token_id: str,
        side: str,
        size: float,
        exit_price: float
    ) -> float:
        """
        Close (fully or partially) a position.
        
        Returns:
            Realized P&L from the close
        """
        key = f"{token_id}_{side}"
        
        if key not in self.positions:
            logger.warning(f"⚠️ No position found for {key}")
            return 0.0
        
        position = self.positions[key]
        
        if size > position.size:
            size = position.size
        
        # Calculate realized P&L
        cost_basis = size * position.avg_entry_price
        proceeds = size * exit_price
        realized = proceeds - cost_basis
        
        self.realized_pnl += realized
        
        # Update position
        position.size -= size
        
        if position.size <= 0:
            del self.positions[key]
            db.remove_position(token_id, side)
        else:
            self._persist_position(key)
        
        # Persist realized P&L
        self._persist_realized_pnl()
        
        # Record trade in both memory and database
        self.trade_history.append(Trade(
            timestamp=datetime.now(),
            token_id=token_id,
            market_question=position.market_question,
            side=side,
            action="SELL",
            size=size,
            price=exit_price
        ))
        db.record_trade(
            token_id=token_id,
            market_question=position.market_question,
            side=side,
            action="SELL",
            size=size,
            price=exit_price,
        )
        
        return realized
    
    def update_prices(self):
        """Update current prices for all positions."""
        for position in self.positions.values():
            try:
                price_data = clients.read.get_price(position.token_id, side="SELL")
                if price_data:
                    position.current_price = float(price_data)
                    db.update_position_price(
                        position.token_id, position.side, position.current_price
                    )
            except Exception as e:
                logger.error(f"Error updating price for {position.token_id[:20]}...: {e}")
    
    def get_total_value(self) -> float:
        """Get total portfolio value."""
        return sum(p.current_value for p in self.positions.values())
    
    def get_total_exposure(self) -> float:
        """Get total exposure (cost basis)."""
        return sum(p.cost_basis for p in self.positions.values())
    
    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L."""
        return sum(p.unrealized_pnl for p in self.positions.values())
    
    def get_stats(self) -> PortfolioStats:
        """Get portfolio statistics."""
        # Calculate exposure by category
        exposure_by_cat: dict[str, float] = {}
        largest_pos = None
        largest_value = 0
        
        for pos in self.positions.values():
            # Categorize (simplified - you could enhance this)
            cat = "crypto" if "btc" in pos.market_question.lower() or "eth" in pos.market_question.lower() else "sports"
            exposure_by_cat[cat] = exposure_by_cat.get(cat, 0) + pos.current_value
            
            if pos.current_value > largest_value:
                largest_value = pos.current_value
                largest_pos = pos.market_question
        
        # Calculate win rate from closed trades
        winning_trades = 0
        total_closed = 0
        
        for i, trade in enumerate(self.trade_history):
            if trade.action == "SELL":
                total_closed += 1
                # Find corresponding buy
                for prev_trade in self.trade_history[:i]:
                    if prev_trade.token_id == trade.token_id and prev_trade.action == "BUY":
                        if trade.price > prev_trade.price:
                            winning_trades += 1
                        break
        
        win_rate = (winning_trades / total_closed * 100) if total_closed > 0 else 0
        
        return PortfolioStats(
            total_positions=len(self.positions),
            total_value=self.get_total_value(),
            total_cost_basis=self.get_total_exposure(),
            total_unrealized_pnl=self.get_total_unrealized_pnl(),
            total_realized_pnl=self.realized_pnl,
            win_rate=win_rate,
            largest_position=largest_pos,
            exposure_by_category=exposure_by_cat
        )
    
    def check_risk_limits(self) -> list[str]:
        """
        Check if any risk limits are exceeded.
        
        Returns:
            List of warning messages
        """
        warnings = []
        
        total_exposure = self.get_total_exposure()
        if total_exposure > config.trading.max_total_exposure:
            warnings.append(
                f"⚠️ Total exposure (${total_exposure:.2f}) exceeds limit "
                f"(${config.trading.max_total_exposure:.2f})"
            )
        
        for pos in self.positions.values():
            if pos.cost_basis > config.trading.max_trade_size * 2:
                warnings.append(
                    f"⚠️ Large position in {pos.market_question[:30]}... "
                    f"(${pos.cost_basis:.2f})"
                )
        
        return warnings
    
    def print_summary(self):
        """Print portfolio summary to console."""
        self.update_prices()
        stats = self.get_stats()
        
        logger.info("=" * 60)
        logger.info("💼 PORTFOLIO SUMMARY")
        logger.info("=" * 60)
        
        logger.info(f"\n📊 Overview:")
        logger.info(f"   Positions: {stats.total_positions}")
        logger.info(f"   Total Value: ${stats.total_value:,.2f}")
        logger.info(f"   Cost Basis: ${stats.total_cost_basis:,.2f}")
        
        pnl_emoji = "📈" if stats.total_unrealized_pnl >= 0 else "📉"
        logger.info(f"\n{pnl_emoji} P&L:")
        logger.info(f"   Unrealized: ${stats.total_unrealized_pnl:,.2f}")
        logger.info(f"   Realized: ${stats.total_realized_pnl:,.2f}")
        logger.info(f"   Win Rate: {stats.win_rate:.1f}%")
        
        if stats.exposure_by_category:
            logger.info(f"\n📁 Exposure by Category:")
            for cat, exp in stats.exposure_by_category.items():
                logger.info(f"   {cat}: ${exp:,.2f}")
        
        if self.positions:
            logger.info(f"\n📋 Positions:")
            logger.info("-" * 60)
            
            for key, pos in self.positions.items():
                pnl_pct = pos.unrealized_pnl_percent
                pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                
                logger.info(f"\n{pnl_emoji} {pos.market_question[:45]}...")
                logger.info(f"   Side: {pos.side} | Size: {pos.size:.2f}")
                logger.info(f"   Entry: ${pos.avg_entry_price:.4f} → Current: ${pos.current_price:.4f}")
                logger.info(f"   P&L: ${pos.unrealized_pnl:.2f} ({pnl_pct:+.1f}%)")
        
        # Risk warnings
        warnings = self.check_risk_limits()
        if warnings:
            logger.warning(f"\n⚠️ Risk Warnings:")
            for w in warnings:
                logger.info(f"   {w}")
        
        logger.info("=" * 60)
    
    def export_to_json(self, filepath: str):
        """Export portfolio to JSON file."""
        data = {
            "exported_at": datetime.now().isoformat(),
            "positions": [
                {
                    "token_id": p.token_id,
                    "market_question": p.market_question,
                    "side": p.side,
                    "size": p.size,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "unrealized_pnl": p.unrealized_pnl
                }
                for p in self.positions.values()
            ],
            "trade_history": [
                {
                    "timestamp": t.timestamp.isoformat(),
                    "token_id": t.token_id,
                    "side": t.side,
                    "action": t.action,
                    "size": t.size,
                    "price": t.price
                }
                for t in self.trade_history
            ],
            "realized_pnl": self.realized_pnl
        }
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"📁 Portfolio exported to {filepath}")
    
    def import_from_json(self, filepath: str):
        """Import portfolio from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)
        
        self.positions = {}
        for p in data.get("positions", []):
            key = f"{p['token_id']}_{p['side']}"
            self.positions[key] = Position(
                token_id=p["token_id"],
                market_question=p["market_question"],
                side=p["side"],
                size=p["size"],
                avg_entry_price=p["avg_entry_price"],
                current_price=p.get("current_price", 0)
            )
        
        self.trade_history = []
        for t in data.get("trade_history", []):
            self.trade_history.append(Trade(
                timestamp=datetime.fromisoformat(t["timestamp"]),
                token_id=t["token_id"],
                market_question=t.get("market_question", ""),
                side=t["side"],
                action=t["action"],
                size=t["size"],
                price=t["price"]
            ))
        
        self.realized_pnl = data.get("realized_pnl", 0)
        
        logger.info(f"📁 Portfolio imported from {filepath}")
        logger.info(f"   Loaded {len(self.positions)} positions")


# Quick demo
if __name__ == "__main__":
    portfolio = PortfolioManager()
    
    # Add some demo positions
    portfolio.add_position(
        token_id="demo_btc_100k",
        market_question="Will Bitcoin reach $100k by end of 2025?",
        side="YES",
        size=100,
        entry_price=0.45
    )
    
    portfolio.add_position(
        token_id="demo_nba_finals",
        market_question="Will Lakers win NBA Finals 2025?",
        side="YES",
        size=50,
        entry_price=0.15
    )
    
    # Simulate price changes
    portfolio.positions["demo_btc_100k_YES"].current_price = 0.52
    portfolio.positions["demo_nba_finals_YES"].current_price = 0.18
    
    # Print summary
    portfolio.print_summary()
