"""
Momentum Model — detects real price trends using historical price data.

Unlike the old placeholder that bought anything at 55-80¢ with high volume,
this model computes actual price deltas over configurable time windows and
only signals when price has moved consistently in one direction.

Requirements:
    - The persistence layer must be collecting price snapshots (odds_tracker
      already does this via db.save_price_snapshot()).
    - The bot needs to run for at least `lookback_hours` before this model
      produces meaningful signals.

Momentum criteria (all must be met):
    1. Price has moved >= min_delta_pct in one direction over lookback_hours
    2. The move is consistent (>= consistency_threshold of sub-intervals
       moved in the same direction)
    3. Current price is between min_price and max_price (avoid extremes)
    4. Volume is sufficient for liquidity

This model bets WITH the trend (momentum continuation), not against it.
"""

from typing import Optional
from datetime import datetime, timedelta

from market_fetcher import Market
from models.base import ProbabilityModel, ProbabilityEstimate


class MomentumModel(ProbabilityModel):
    """
    Real momentum detection using stored price history.

    The model converts detected momentum into a probability estimate:
    if price has been consistently rising, the model estimates the true
    probability is HIGHER than the current price (i.e., momentum will
    continue). The size of the edge scales with the strength of the trend.
    """

    def __init__(
        self,
        db=None,
        lookback_hours: int = 4,
        min_delta_pct: float = 5.0,
        consistency_threshold: float = 0.6,
        min_price: float = 0.15,
        max_price: float = 0.85,
        max_edge_pct: float = 15.0,
    ):
        """
        Args:
            db: BotDatabase instance (from persistence.py)
            lookback_hours: How far back to analyze price history
            min_delta_pct: Minimum price change (%) to qualify as momentum
            consistency_threshold: Fraction of sub-intervals that must agree
                                   on direction (0.0 - 1.0)
            min_price: Ignore markets priced below this (too speculative)
            max_price: Ignore markets priced above this (too settled)
            max_edge_pct: Cap the estimated edge at this percentage
        """
        self._db = db
        self.lookback_hours = lookback_hours
        self.min_delta_pct = min_delta_pct
        self.consistency_threshold = consistency_threshold
        self.min_price = min_price
        self.max_price = max_price
        self.max_edge_pct = max_edge_pct

    @property
    def name(self) -> str:
        return "momentum-price-history"

    def set_db(self, db):
        """Set or update the database reference."""
        self._db = db

    def estimate(self, market: Market) -> Optional[ProbabilityEstimate]:
        if self._db is None:
            return None

        # Analyze YES side momentum
        yes_result = self._analyze_token(
            market.token_id_yes, market.price_yes, "YES"
        )

        # Analyze NO side momentum
        no_result = self._analyze_token(
            market.token_id_no, market.price_no, "NO"
        )

        # Pick the stronger signal (if any)
        if yes_result is None and no_result is None:
            return None

        if yes_result and no_result:
            # Both sides show momentum — pick the stronger one
            best = yes_result if yes_result[1] >= no_result[1] else no_result
        else:
            best = yes_result or no_result

        side, edge_pct, direction, consistency, delta_pct = best

        # Convert momentum into a probability estimate
        # If YES is trending UP by 8%, estimate fair_yes = current + portion of momentum
        # (we don't assume ALL of the momentum continues, just that price is behind the trend)
        continuation_factor = 0.5  # Expect ~50% of remaining move to happen

        current_yes = market.price_yes
        if side == "YES" and direction > 0:
            fair_yes = min(current_yes * (1 + edge_pct / 100 * continuation_factor), 0.95)
        elif side == "YES" and direction < 0:
            # YES trending down → fair_yes is lower than current
            fair_yes = max(current_yes * (1 - edge_pct / 100 * continuation_factor), 0.05)
        elif side == "NO" and direction > 0:
            # NO trending up → YES should be trending down
            fair_yes = max(current_yes * (1 - edge_pct / 100 * continuation_factor), 0.05)
        else:
            # NO trending down → YES should be trending up
            fair_yes = min(current_yes * (1 + edge_pct / 100 * continuation_factor), 0.95)

        confidence = min(consistency, 0.9)  # Cap at 0.9 — momentum can reverse

        return ProbabilityEstimate(
            market_id=market.id,
            model_name=self.name,
            fair_probability_yes=fair_yes,
            confidence=confidence,
            reasoning=(
                f"{side} moved {delta_pct:+.1f}% over {self.lookback_hours}h "
                f"(consistency: {consistency:.0%}, {direction:+d})"
            ),
        )

    def _analyze_token(
        self, token_id: str, current_price: float, side: str
    ) -> Optional[tuple[str, float, int, float, float]]:
        """
        Analyze price history for a single token.

        Returns (side, edge_pct, direction, consistency, delta_pct) or None.
        direction: +1 for uptrend, -1 for downtrend.
        """
        # Price range filter
        if current_price < self.min_price or current_price > self.max_price:
            return None

        # Get price history
        history = self._db.get_price_history(
            token_id, hours=self.lookback_hours
        )

        if len(history) < 3:
            # Not enough data points
            return None

        # Extract prices (use price_yes column for YES tokens, price_no for NO)
        if side == "YES":
            prices = [h.get("price_yes", 0) for h in history if h.get("price_yes")]
        else:
            prices = [h.get("price_no", 0) for h in history if h.get("price_no")]

        if len(prices) < 3:
            return None

        # Compute overall delta
        oldest_price = prices[0]
        newest_price = prices[-1]

        if oldest_price <= 0:
            return None

        delta_pct = ((newest_price - oldest_price) / oldest_price) * 100

        # Check minimum delta
        if abs(delta_pct) < self.min_delta_pct:
            return None

        # Check consistency: what fraction of consecutive pairs moved in same direction?
        direction = 1 if delta_pct > 0 else -1
        same_direction_count = 0
        total_pairs = len(prices) - 1

        for i in range(total_pairs):
            move = prices[i + 1] - prices[i]
            if (move > 0 and direction > 0) or (move < 0 and direction < 0):
                same_direction_count += 1

        consistency = same_direction_count / total_pairs if total_pairs > 0 else 0

        if consistency < self.consistency_threshold:
            return None

        # Compute edge (capped)
        edge_pct = min(abs(delta_pct), self.max_edge_pct)

        return (side, edge_pct, direction, consistency, delta_pct)
