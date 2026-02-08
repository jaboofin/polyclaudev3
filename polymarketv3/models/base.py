"""
Base Probability Model Interface

All probability models must implement this interface. A probability model
takes a market and returns an independent estimate of the true probability,
which is then compared to the market price to determine edge.

If a model cannot estimate a market (e.g., a sports model looking at a
crypto market), it returns None.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from market_fetcher import Market


@dataclass
class ProbabilityEstimate:
    """An independent probability estimate for a market outcome."""

    market_id: str
    model_name: str
    fair_probability_yes: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0 — how confident the model is
    reasoning: str  # Short explanation for logging

    @property
    def fair_probability_no(self) -> float:
        return 1.0 - self.fair_probability_yes

    def edge_vs_market(self, market_price: float, side: str) -> float:
        """
        Compute edge as a percentage.

        edge > 0 means we believe the market is mispriced in our favor.
        Example: our model says 60% but market is priced at 50¢ → +20% edge.
        """
        if side == "YES":
            fair = self.fair_probability_yes
        else:
            fair = self.fair_probability_no

        if market_price <= 0:
            return 0.0

        return ((fair - market_price) / market_price) * 100

    def expected_value(self, market_price: float, side: str) -> float:
        """
        Expected value per dollar wagered.

        EV > 0 means profitable in the long run.
        Example: fair prob 60%, price 50¢ → EV = 0.60/0.50 - 1 = +0.20
        """
        if side == "YES":
            fair = self.fair_probability_yes
        else:
            fair = self.fair_probability_no

        if market_price <= 0:
            return 0.0

        return (fair / market_price) - 1.0


class ProbabilityModel(ABC):
    """
    Abstract base class for probability estimation models.

    Subclasses must implement:
        - name: human-readable model name
        - estimate(market) → Optional[ProbabilityEstimate]
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this model (used in logs and trade records)."""
        ...

    @abstractmethod
    def estimate(self, market: Market) -> Optional[ProbabilityEstimate]:
        """
        Produce an independent probability estimate for a market.

        Returns None if the model cannot evaluate this market
        (e.g., it's outside the model's domain).
        """
        ...

    def batch_estimate(
        self, markets: list[Market]
    ) -> dict[str, ProbabilityEstimate]:
        """
        Estimate probabilities for multiple markets.
        Returns dict mapping market_id → estimate (skipping None results).

        Override for models that can batch API calls efficiently.
        """
        results = {}
        for market in markets:
            est = self.estimate(market)
            if est is not None:
                results[market.id] = est
        return results
