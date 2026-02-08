"""
Probability Models for Polymarket Trading Bot

Each model produces independent probability estimates for markets.
The auto_trader compares these estimates against market prices to
find real edge, replacing the old placeholder heuristics.

Available models:
    - ManualModel: User-supplied probability estimates
    - OddsApiModel: Bookmaker consensus from the-odds-api.com (sports)
    - MomentumModel: Real momentum detection from price history

Usage:
    from models import ManualModel, OddsApiModel, MomentumModel

    # Stack multiple models — each covers different market types
    manual = ManualModel()
    manual.set_estimate("some-market", fair_yes=0.72, reason="My research")

    odds = OddsApiModel()  # Needs ODDS_API_KEY in .env

    momentum = MomentumModel(db=db)  # Needs persistence layer running
"""

from models.base import ProbabilityModel, ProbabilityEstimate
from models.manual import ManualModel
from models.odds_api import OddsApiModel
from models.momentum import MomentumModel

__all__ = [
    "ProbabilityModel",
    "ProbabilityEstimate",
    "ManualModel",
    "OddsApiModel",
    "MomentumModel",
]
