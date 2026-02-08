"""
Odds Tracker - Monitor price changes in real-time.
Supports alerts and historical tracking.
"""

import time
import json
import asyncio
import websockets
from datetime import datetime
from typing import Optional, Callable
from dataclasses import dataclass, field
from collections import defaultdict

from config import config
from client_manager import clients
from persistence import db
from market_fetcher import Market, MarketFetcher
import logging
logger = logging.getLogger(__name__)


@dataclass
class PricePoint:
    """A single price observation."""
    timestamp: datetime
    price_yes: float
    price_no: float
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    
    @property
    def midpoint(self) -> float:
        """Calculate midpoint price."""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return self.price_yes


@dataclass
class PriceHistory:
    """Price history for a market."""
    token_id: str
    market_question: str
    prices: list[PricePoint] = field(default_factory=list)
    
    def add_price(self, price_point: PricePoint):
        """Add a new price observation."""
        self.prices.append(price_point)
        # Keep last 1000 observations to manage memory
        if len(self.prices) > 1000:
            self.prices = self.prices[-1000:]
    
    @property
    def current_price(self) -> Optional[float]:
        """Get most recent price."""
        return self.prices[-1].price_yes if self.prices else None
    
    @property
    def price_change_1h(self) -> Optional[float]:
        """Calculate 1-hour price change."""
        if len(self.prices) < 2:
            return None
        
        now = datetime.now()
        hour_ago = None
        
        for p in reversed(self.prices):
            if (now - p.timestamp).total_seconds() >= 3600:
                hour_ago = p
                break
        
        if hour_ago and self.current_price:
            return self.current_price - hour_ago.price_yes
        return None
    
    @property
    def price_change_percent_1h(self) -> Optional[float]:
        """Calculate 1-hour price change as percentage."""
        change = self.price_change_1h
        if change is None or len(self.prices) < 2:
            return None
        
        # Find price from ~1 hour ago
        for p in reversed(self.prices[:-1]):
            if (datetime.now() - p.timestamp).total_seconds() >= 3600:
                if p.price_yes > 0:
                    return (change / p.price_yes) * 100
                break
        return None


@dataclass
class Alert:
    """Represents a price alert."""
    market_id: str
    condition: str  # "above", "below", "change"
    threshold: float
    triggered: bool = False
    callback: Optional[Callable] = None


class OddsTracker:
    """
    Track odds/prices for multiple markets.
    
    Usage:
        tracker = OddsTracker()
        
        # Add markets to track
        tracker.add_market(token_id="...", question="Will BTC hit 100k?")
        
        # Set up alerts
        tracker.add_alert(
            token_id="...",
            condition="above",
            threshold=0.75,
            callback=lambda m, p: logger.info(f"Alert! {m} hit {p}")
        )
        
        # Start tracking (polling mode)
        tracker.start_polling(interval=60)
        
        # Or use WebSocket for real-time
        await tracker.start_websocket()
    """
    
    def __init__(self):
        self.tracked_markets: dict[str, PriceHistory] = {}
        self.alerts: list[Alert] = []
        self._running = False
    
    def add_market(self, token_id: str, question: str = ""):
        """Add a market to track."""
        if token_id not in self.tracked_markets:
            self.tracked_markets[token_id] = PriceHistory(
                token_id=token_id,
                market_question=question
            )
            logger.info(f"📊 Now tracking: {question or token_id[:20]}...")
    
    def add_markets(self, markets: list[Market]):
        """Add multiple markets to track."""
        for market in markets:
            self.add_market(market.token_id_yes, market.question)
    
    def remove_market(self, token_id: str):
        """Stop tracking a market."""
        if token_id in self.tracked_markets:
            del self.tracked_markets[token_id]
    
    def add_alert(
        self,
        token_id: str,
        condition: str,
        threshold: float,
        callback: Optional[Callable] = None
    ):
        """
        Add a price alert.
        
        Args:
            token_id: Market token ID
            condition: "above", "below", or "change"
            threshold: Price threshold (or change % for "change")
            callback: Function to call when triggered (receives market, price)
        """
        self.alerts.append(Alert(
            market_id=token_id,
            condition=condition,
            threshold=threshold,
            callback=callback
        ))
    
    def _check_alerts(self, token_id: str, price: float, old_price: Optional[float]):
        """Check and trigger any alerts for this market."""
        for alert in self.alerts:
            if alert.market_id != token_id or alert.triggered:
                continue
            
            triggered = False
            
            if alert.condition == "above" and price >= alert.threshold:
                triggered = True
            elif alert.condition == "below" and price <= alert.threshold:
                triggered = True
            elif alert.condition == "change" and old_price:
                change = abs(price - old_price) / old_price
                if change >= alert.threshold:
                    triggered = True
            
            if triggered:
                alert.triggered = True
                history = self.tracked_markets.get(token_id)
                market_name = history.market_question if history else token_id
                
                logger.info(f"🚨 ALERT: {market_name}")
                logger.info(f"   Condition: {alert.condition} {alert.threshold}")
                logger.info(f"   Current price: {price:.4f}")
                
                if alert.callback:
                    alert.callback(market_name, price)
    
    def fetch_price(self, token_id: str) -> Optional[PricePoint]:
        """Fetch current price for a token."""
        try:
            # Get midpoint
            midpoint = clients.read.get_midpoint(token_id)
            
            # Get orderbook for bid/ask
            book = clients.read.get_order_book(token_id)
            
            best_bid = None
            best_ask = None
            
            if book and book.bids:
                best_bid = float(book.bids[0].price)
            if book and book.asks:
                best_ask = float(book.asks[0].price)
            
            price_yes = float(midpoint) if midpoint else 0.5
            
            # Persist snapshot to database
            db.save_price_snapshot(
                token_id=token_id,
                price_yes=price_yes,
                price_no=1.0 - price_yes,
                best_bid=best_bid,
                best_ask=best_ask,
            )
            
            return PricePoint(
                timestamp=datetime.now(),
                price_yes=price_yes,
                price_no=1.0 - price_yes,
                best_bid=best_bid,
                best_ask=best_ask
            )
        except Exception as e:
            logger.error(f"Error fetching price for {token_id[:20]}...: {e}")
            return None
    
    def update_prices(self):
        """Update prices for all tracked markets."""
        for token_id, history in self.tracked_markets.items():
            old_price = history.current_price
            price_point = self.fetch_price(token_id)
            
            if price_point:
                history.add_price(price_point)
                self._check_alerts(token_id, price_point.price_yes, old_price)
    
    def start_polling(self, interval: int = 60):
        """
        Start polling for price updates.
        
        Args:
            interval: Seconds between updates
        """
        self._running = True
        logger.info(f"🔄 Starting price tracker (polling every {interval}s)")
        logger.info(f"   Tracking {len(self.tracked_markets)} markets")
        logger.info('============================================================')
        
        try:
            while self._running:
                self.update_prices()
                self._print_status()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("\n⏹️ Tracker stopped")
            self._running = False
    
    def _print_status(self):
        """Print current tracking status."""
        logger.info(f"\n📊 Price Update @ {datetime.now().strftime('%H:%M:%S')}")
        logger.info('============================================================')
        
        for token_id, history in self.tracked_markets.items():
            if history.prices:
                latest = history.prices[-1]
                change_1h = history.price_change_percent_1h
                change_str = f"{change_1h:+.1f}%" if change_1h else "N/A"
                
                # Color code based on change
                emoji = "📈" if change_1h and change_1h > 0 else "📉" if change_1h and change_1h < 0 else "➖"
                
                logger.info(f"{emoji} {history.market_question[:40]}...")
                logger.info(f"   YES: ${latest.price_yes:.4f} | 1h: {change_str}")
    
    async def start_websocket(self):
        """
        Start WebSocket connection for real-time updates.
        More efficient than polling for many markets.
        """
        ws_url = f"{config.WS_HOST}/ws/market"
        
        logger.info(f"🔌 Connecting to WebSocket...")
        logger.info(f"   Tracking {len(self.tracked_markets)} markets")
        
        async with websockets.connect(ws_url) as ws:
            # Subscribe to markets
            token_ids = list(self.tracked_markets.keys())
            
            subscribe_msg = {
                "type": "market",
                "assets_ids": token_ids
            }
            
            await ws.send(json.dumps(subscribe_msg))
            logger.info("✅ Subscribed to market updates")
            
            self._running = True
            
            try:
                async for message in ws:
                    if not self._running:
                        break
                    
                    data = json.loads(message)
                    await self._handle_ws_message(data)
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("⚠️ WebSocket connection closed")
            except KeyboardInterrupt:
                logger.info("\n⏹️ Tracker stopped")
    
    async def _handle_ws_message(self, data: dict):
        """Handle incoming WebSocket message."""
        msg_type = data.get("type", "")
        
        if msg_type == "book":
            # Orderbook update
            token_id = data.get("asset_id")
            if token_id in self.tracked_markets:
                history = self.tracked_markets[token_id]
                old_price = history.current_price
                
                # Parse book data
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                
                best_bid = float(bids[0]["price"]) if bids else None
                best_ask = float(asks[0]["price"]) if asks else None
                
                if best_bid and best_ask:
                    midpoint = (best_bid + best_ask) / 2
                    
                    price_point = PricePoint(
                        timestamp=datetime.now(),
                        price_yes=midpoint,
                        price_no=1.0 - midpoint,
                        best_bid=best_bid,
                        best_ask=best_ask
                    )
                    
                    history.add_price(price_point)
                    self._check_alerts(token_id, midpoint, old_price)
    
    def stop(self):
        """Stop the tracker."""
        self._running = False
    
    def get_history(self, token_id: str) -> Optional[PriceHistory]:
        """Get price history for a market."""
        return self.tracked_markets.get(token_id)
    
    def export_history(self, filepath: str):
        """Export price history to JSON file."""
        export_data = {}
        
        for token_id, history in self.tracked_markets.items():
            export_data[token_id] = {
                "question": history.market_question,
                "prices": [
                    {
                        "timestamp": p.timestamp.isoformat(),
                        "price_yes": p.price_yes,
                        "price_no": p.price_no,
                        "best_bid": p.best_bid,
                        "best_ask": p.best_ask
                    }
                    for p in history.prices
                ]
            }
        
        with open(filepath, "w") as f:
            json.dump(export_data, f, indent=2)
        
        logger.info(f"📁 Exported history to {filepath}")


def default_alert_callback(market: str, price: float):
    """Default alert callback - prints to console."""
    logger.info(f"🚨 PRICE ALERT: {market} @ {price:.4f}")


# Quick test
if __name__ == "__main__":
    from market_fetcher import MarketFetcher
    
    # Fetch some markets
    fetcher = MarketFetcher()
    markets = fetcher.get_crypto_markets(limit=5)
    
    if markets:
        tracker = OddsTracker()
        
        # Add first 3 markets
        for m in markets[:3]:
            tracker.add_market(m.token_id_yes, m.question)
            
            # Add alert for 10% price change
            tracker.add_alert(
                m.token_id_yes,
                condition="change",
                threshold=0.10,
                callback=default_alert_callback
            )
        
        # Start polling
        logger.info("\nStarting tracker (Ctrl+C to stop)...\n")
        tracker.start_polling(interval=30)
