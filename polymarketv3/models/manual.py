"""
Manual Probability Model — user-supplied fair probability overrides.

This is the simplest and often most reliable model: the user does their own
research and tells the bot "I think this market's true probability is X%."
The bot then watches for the market price to diverge from that estimate
and trades when edge exceeds the threshold.

Usage in config or at runtime:

    model = ManualModel()
    model.set_estimate("some-market-id", fair_yes=0.72, reason="My ELO model says 72%")
    model.set_estimate("another-market", fair_yes=0.35, reason="Based on polling data")

Estimates can also be loaded from a JSON file:

    model = ManualModel.from_file("my_estimates.json")

JSON format:
    {
        "estimates": {
            "market-id-or-slug": {
                "fair_yes": 0.72,
                "confidence": 0.8,
                "reason": "ELO model output"
            }
        }
    }
"""

import json
import os
from typing import Optional
from dataclasses import dataclass

from market_fetcher import Market
from models.base import ProbabilityModel, ProbabilityEstimate


@dataclass
class ManualEstimateEntry:
    """A single user-supplied estimate."""
    fair_yes: float
    confidence: float = 0.9  # User presumably knows what they're doing
    reason: str = "User-supplied estimate"


class ManualModel(ProbabilityModel):
    """
    User-supplied probability estimates.

    Use this when you have your own research, model, or conviction
    about a market's true probability. The bot will trade when the
    market price diverges from your estimate by more than min_edge%.
    """

    def __init__(self):
        self._estimates: dict[str, ManualEstimateEntry] = {}

    @property
    def name(self) -> str:
        return "manual-user-estimate"

    def set_estimate(
        self,
        market_id_or_slug: str,
        fair_yes: float,
        confidence: float = 0.9,
        reason: str = "User-supplied estimate",
    ):
        """
        Set a probability estimate for a market.

        Args:
            market_id_or_slug: Market ID or slug to match against
            fair_yes: Your estimate of the true YES probability (0.0 - 1.0)
            confidence: How confident you are (0.0 - 1.0)
            reason: Short note for trade logs
        """
        if not 0.0 < fair_yes < 1.0:
            raise ValueError(f"fair_yes must be between 0 and 1, got {fair_yes}")

        self._estimates[market_id_or_slug.lower()] = ManualEstimateEntry(
            fair_yes=fair_yes,
            confidence=confidence,
            reason=reason,
        )

    def remove_estimate(self, market_id_or_slug: str):
        """Remove an estimate."""
        self._estimates.pop(market_id_or_slug.lower(), None)

    def clear(self):
        """Remove all estimates."""
        self._estimates.clear()

    def estimate(self, market: Market) -> Optional[ProbabilityEstimate]:
        # Try matching by ID, then by slug
        entry = self._estimates.get(market.id.lower())
        if entry is None:
            entry = self._estimates.get(market.slug.lower()) if market.slug else None
        if entry is None:
            # Try partial slug match (user might use a shortened version)
            for key, val in self._estimates.items():
                if key in market.slug.lower() or key in market.question.lower():
                    entry = val
                    break

        if entry is None:
            return None

        return ProbabilityEstimate(
            market_id=market.id,
            model_name=self.name,
            fair_probability_yes=entry.fair_yes,
            confidence=entry.confidence,
            reasoning=entry.reason,
        )

    @classmethod
    def from_file(cls, filepath: str) -> "ManualModel":
        """Load estimates from a JSON file."""
        model = cls()

        if not os.path.exists(filepath):
            return model

        with open(filepath, "r") as f:
            data = json.load(f)

        for market_key, entry_data in data.get("estimates", {}).items():
            model.set_estimate(
                market_id_or_slug=market_key,
                fair_yes=entry_data["fair_yes"],
                confidence=entry_data.get("confidence", 0.9),
                reason=entry_data.get("reason", "Loaded from file"),
            )

        return model

    def save_to_file(self, filepath: str):
        """Save current estimates to a JSON file."""
        data = {
            "estimates": {
                key: {
                    "fair_yes": e.fair_yes,
                    "confidence": e.confidence,
                    "reason": e.reason,
                }
                for key, e in self._estimates.items()
            }
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
