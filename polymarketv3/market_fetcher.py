"""
Market Fetcher - Retrieves markets from Polymarket's Gamma API.
Filters for Sports and Crypto categories only.
"""

import requests
from typing import Optional
from dataclasses import dataclass
from config import config
import logging
logger = logging.getLogger(__name__)


@dataclass
class Market:
    """Represents a Polymarket market."""
    id: str
    question: str
    slug: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    outcomes: list[str]
    price_yes: float
    price_no: float
    volume: float
    liquidity: float
    category: str
    end_date: Optional[str] = None
    description: Optional[str] = None
    
    @property
    def spread(self) -> float:
        """Calculate bid-ask spread."""
        return abs(1.0 - self.price_yes - self.price_no)
    
    @property
    def implied_probability(self) -> float:
        """Get implied probability of YES outcome."""
        return self.price_yes


@dataclass 
class Event:
    """Represents a Polymarket event (can contain multiple markets)."""
    id: str
    title: str
    slug: str
    category: str
    markets: list[Market]
    volume: float
    liquidity: float
    active: bool
    closed: bool


class MarketFetcher:
    """
    Fetches and filters markets from Polymarket's Gamma API.
    
    Usage:
        fetcher = MarketFetcher()
        
        # Get all crypto markets
        crypto = fetcher.get_crypto_markets()
        
        # Get all sports markets
        sports = fetcher.get_sports_markets()
        
        # Get both categories
        all_markets = fetcher.get_all_target_markets()
    """
    
    def __init__(self):
        self.gamma_host = config.gamma_host
        self.session = requests.Session()
        self._sports_metadata = None
    
    def _request(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make a GET request to the Gamma API."""
        url = f"{self.gamma_host}{endpoint}"
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def get_tags(self) -> list[dict]:
        """Get all available market tags/categories."""
        return self._request("/tags")
    
    def get_sports_metadata(self) -> list[dict]:
        """
        Get sports metadata including leagues and series IDs.
        Caches result for efficiency.
        """
        if self._sports_metadata is None:
            self._sports_metadata = self._request("/sports")
        return self._sports_metadata
    
    def _parse_market(self, market_data: dict, category: str) -> Optional[Market]:
        """Parse raw market data into Market object."""
        try:
            # Parse token IDs from clobTokenIds field
            clob_token_ids = market_data.get("clobTokenIds", [])
            if len(clob_token_ids) < 2:
                return None
            
            # Parse prices
            outcome_prices = market_data.get("outcomePrices", "[]")
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)
            
            price_yes = float(outcome_prices[0]) if outcome_prices else 0.5
            price_no = float(outcome_prices[1]) if len(outcome_prices) > 1 else 1 - price_yes
            
            # Parse outcomes
            outcomes = market_data.get("outcomes", '["Yes", "No"]')
            if isinstance(outcomes, str):
                import json
                outcomes = json.loads(outcomes)
            
            return Market(
                id=str(market_data.get("id", "")),
                question=market_data.get("question", ""),
                slug=market_data.get("slug", ""),
                condition_id=market_data.get("conditionId", ""),
                token_id_yes=clob_token_ids[0],
                token_id_no=clob_token_ids[1],
                outcomes=outcomes,
                price_yes=price_yes,
                price_no=price_no,
                volume=float(market_data.get("volume", 0) or 0),
                liquidity=float(market_data.get("liquidity", 0) or 0),
                category=category,
                end_date=market_data.get("endDate"),
                description=market_data.get("description"),
            )
        except Exception as e:
            logger.error(f"Error parsing market: {e}")
            return None
    
    def _parse_event(self, event_data: dict, category: str) -> Optional[Event]:
        """Parse raw event data into Event object."""
        try:
            markets = []
            for m in event_data.get("markets", []):
                market = self._parse_market(m, category)
                if market:
                    markets.append(market)
            
            return Event(
                id=str(event_data.get("id", "")),
                title=event_data.get("title", ""),
                slug=event_data.get("slug", ""),
                category=category,
                markets=markets,
                volume=float(event_data.get("volume", 0) or 0),
                liquidity=float(event_data.get("liquidity", 0) or 0),
                active=event_data.get("active", False),
                closed=event_data.get("closed", False),
            )
        except Exception as e:
            logger.error(f"Error parsing event: {e}")
            return None
    
    def get_crypto_markets(
        self,
        limit: int = 100,
        min_liquidity: Optional[float] = None,
        active_only: bool = True
    ) -> list[Market]:
        """
        Fetch all crypto-related markets.
        
        Args:
            limit: Maximum number of events to fetch
            min_liquidity: Minimum liquidity filter (USDC)
            active_only: Only return active, non-closed markets
        
        Returns:
            List of Market objects
        """
        params = {
            "tag_id": config.CATEGORY_TAGS["crypto"],
            "limit": limit,
            "order": "volume",
            "ascending": "false",
        }
        
        if active_only:
            params["active"] = "true"
            params["closed"] = "false"
        
        events_data = self._request("/events", params)
        
        markets = []
        for event_data in events_data:
            event = self._parse_event(event_data, "crypto")
            if event:
                for market in event.markets:
                    if min_liquidity is None or market.liquidity >= min_liquidity:
                        markets.append(market)
        
        return markets
    
    def get_sports_markets(
        self,
        league: Optional[str] = None,
        limit: int = 100,
        min_liquidity: Optional[float] = None,
        active_only: bool = True
    ) -> list[Market]:
        """
        Fetch all sports-related markets.
        
        Args:
            league: Specific league to filter (e.g., "NBA", "NFL")
            limit: Maximum number of events to fetch
            min_liquidity: Minimum liquidity filter (USDC)
            active_only: Only return active, non-closed markets
        
        Returns:
            List of Market objects
        """
        # Get sports metadata to find series IDs
        sports_meta = self.get_sports_metadata()
        
        markets = []
        
        # Iterate through sports leagues
        for sport in sports_meta:
            sport_name = sport.get("label", "")
            
            # Filter by specific league if requested
            if league and league.lower() not in sport_name.lower():
                continue
            
            # Get series (leagues) for this sport
            for series in sport.get("series", []):
                series_id = series.get("id")
                if not series_id:
                    continue
                
                params = {
                    "series_id": series_id,
                    "limit": limit,
                    "order": "startTime",
                    "ascending": "true",
                }
                
                if active_only:
                    params["active"] = "true"
                    params["closed"] = "false"
                
                try:
                    events_data = self._request("/events", params)
                    
                    for event_data in events_data:
                        event = self._parse_event(event_data, f"sports:{sport_name}")
                        if event:
                            for market in event.markets:
                                if min_liquidity is None or market.liquidity >= min_liquidity:
                                    markets.append(market)
                except Exception as e:
                    logger.error(f"Error fetching series {series_id}: {e}")
                    continue
        
        return markets
    
    def get_all_target_markets(
        self,
        min_liquidity: Optional[float] = None,
        active_only: bool = True
    ) -> list[Market]:
        """
        Fetch all markets in target categories (sports + crypto).
        
        Args:
            min_liquidity: Minimum liquidity filter (USDC)
            active_only: Only return active, non-closed markets
        
        Returns:
            List of Market objects from all target categories
        """
        all_markets = []
        
        # Fetch crypto markets
        logger.info("Fetching crypto markets...")
        crypto = self.get_crypto_markets(
            min_liquidity=min_liquidity,
            active_only=active_only
        )
        all_markets.extend(crypto)
        logger.info(f"  Found {len(crypto)} crypto markets")
        
        # Fetch sports markets
        logger.info("Fetching sports markets...")
        sports = self.get_sports_markets(
            min_liquidity=min_liquidity,
            active_only=active_only
        )
        all_markets.extend(sports)
        logger.info(f"  Found {len(sports)} sports markets")
        
        return all_markets
    
    def get_market_by_slug(self, slug: str) -> Optional[Market]:
        """
        Fetch a specific market by its slug.
        
        Args:
            slug: Market slug (from URL)
        
        Returns:
            Market object or None if not found
        """
        try:
            data = self._request(f"/markets/{slug}")
            return self._parse_market(data, "unknown")
        except Exception:
            return None
    
    def search_markets(self, query: str, limit: int = 20) -> list[Market]:
        """
        Search markets by query string.
        
        Args:
            query: Search query
            limit: Maximum results
        
        Returns:
            List of matching Market objects
        """
        params = {
            "q": query,
            "limit": limit,
        }
        
        results = self._request("/search", params)
        markets = []
        
        for item in results:
            if item.get("type") == "market":
                market = self._parse_market(item, "search")
                if market:
                    markets.append(market)
        
        return markets


# Convenience function for quick testing
def scan_markets():
    """Quick scan of available markets."""
    fetcher = MarketFetcher()
    
    logger.info('============================================================')
    logger.info("📊 POLYMARKET MARKET SCANNER")
    logger.info('============================================================')
    
    markets = fetcher.get_all_target_markets(
        min_liquidity=config.trading.min_market_liquidity
    )
    
    logger.info(f"\nTotal markets found: {len(markets)}")
    logger.info('============================================================')
    
    # Sort by volume
    markets.sort(key=lambda m: m.volume, reverse=True)
    
    # Show top 10
    logger.info("Top 10 markets by volume:\n")
    for i, market in enumerate(markets[:10], 1):
        logger.info(f"{i}. [{market.category}] {market.question[:50]}...")
        logger.info(f"   YES: ${market.price_yes:.2f} | NO: ${market.price_no:.2f}")
        logger.info(f"   Volume: ${market.volume:,.0f} | Liquidity: ${market.liquidity:,.0f}")
        logger.info()


if __name__ == "__main__":
    scan_markets()
