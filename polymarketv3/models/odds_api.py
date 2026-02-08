"""
Sports Odds Model — uses the-odds-api.com for independent probability estimates.

Compares bookmaker consensus odds against Polymarket prices to find real edge.
When the bookmaker market implies 65% but Polymarket prices at 55¢, that's
a potential +18% edge worth investigating.

Setup:
    1. Get a free API key at https://the-odds-api.com (500 requests/month free)
    2. Set ODDS_API_KEY in .env

The model:
    - Fetches odds for upcoming events from major bookmakers
    - Converts American/decimal odds to implied probability
    - Averages across bookmakers for a consensus estimate
    - Matches Polymarket markets by fuzzy keyword matching on team/event names
    - Only returns estimates when it finds a confident match
"""

import os
import re
import time
import requests
from typing import Optional
from dataclasses import dataclass

from market_fetcher import Market
from models.base import ProbabilityModel, ProbabilityEstimate


# Sports the-odds-api supports (subset most relevant to Polymarket)
SPORT_KEYS = [
    "basketball_nba",
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "baseball_mlb",
    "icehockey_nhl",
    "mma_mixed_martial_arts",
    "soccer_epl",
    "soccer_usa_mls",
    "basketball_ncaab",
]

# Cache TTL: don't re-fetch within this window
CACHE_TTL_SECONDS = 300  # 5 minutes


@dataclass
class BookmakerOdds:
    """Odds from a single bookmaker for one outcome."""

    bookmaker: str
    outcome_name: str
    implied_probability: float


class OddsApiModel(ProbabilityModel):
    """
    Probability model using the-odds-api.com bookmaker consensus.

    Only applies to sports markets. Returns None for non-sports markets.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("ODDS_API_KEY", "")
        self._base_url = "https://api.the-odds-api.com/v4/sports"
        self._cache: dict[str, tuple[float, list[dict]]] = {}  # sport_key → (timestamp, data)
        self._session = requests.Session()

    @property
    def name(self) -> str:
        return "odds-api-bookmaker-consensus"

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def estimate(self, market: Market) -> Optional[ProbabilityEstimate]:
        if not self.available:
            return None

        # Only handle sports
        if not self._looks_like_sports(market):
            return None

        # Try to match against bookmaker odds
        match = self._find_matching_odds(market)
        if match is None:
            return None

        team_name, consensus_prob, n_books, reasoning = match

        # Determine which side this probability applies to
        # If the matched team appears in the YES outcome description, it's YES prob
        side = self._determine_side(market, team_name)
        if side == "YES":
            fair_yes = consensus_prob
        else:
            fair_yes = 1.0 - consensus_prob

        # Confidence scales with number of bookmakers agreeing
        confidence = min(n_books / 8, 1.0)  # 8+ books = full confidence

        return ProbabilityEstimate(
            market_id=market.id,
            model_name=self.name,
            fair_probability_yes=fair_yes,
            confidence=confidence,
            reasoning=reasoning,
        )

    def batch_estimate(
        self, markets: list[Market]
    ) -> dict[str, ProbabilityEstimate]:
        """Pre-fetch all sports odds, then estimate each market."""
        if not self.available:
            return {}

        # Pre-warm cache for all sport keys
        for sport_key in SPORT_KEYS:
            self._fetch_odds(sport_key)

        # Now estimate each market against the cached data
        results = {}
        for market in markets:
            est = self.estimate(market)
            if est is not None:
                results[market.id] = est
        return results

    # ── Internal Methods ────────────────────────────────────

    def _looks_like_sports(self, market: Market) -> bool:
        """Quick check if market looks sports-related."""
        q = market.question.lower()
        keywords = [
            "win", "beat", "defeat", "nba", "nfl", "mlb", "nhl", "mma",
            "ufc", "fight", "championship", "super bowl", "world series",
            "playoffs", "finals", "game", "match", "vs", "premier league",
            "serie a", "la liga", "champions league", "epl",
        ]
        return any(kw in q for kw in keywords)

    def _fetch_odds(self, sport_key: str) -> list[dict]:
        """Fetch odds for a sport, with caching."""
        now = time.time()

        if sport_key in self._cache:
            cached_time, cached_data = self._cache[sport_key]
            if now - cached_time < CACHE_TTL_SECONDS:
                return cached_data

        try:
            resp = self._session.get(
                f"{self._base_url}/{sport_key}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": "us,eu",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._cache[sport_key] = (now, data)
                return data
            elif resp.status_code == 401:
                # Bad API key — disable future calls
                self._api_key = ""
                return []
            else:
                return self._cache.get(sport_key, (0, []))[1]
        except Exception:
            return self._cache.get(sport_key, (0, []))[1]

    def _find_matching_odds(
        self, market: Market
    ) -> Optional[tuple[str, float, int, str]]:
        """
        Try to match a Polymarket market against bookmaker events.

        Returns (team_name, consensus_probability, n_bookmakers, reasoning)
        or None if no match found.
        """
        q = market.question.lower()

        for sport_key in SPORT_KEYS:
            events = self._fetch_odds(sport_key)

            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")

                # Check if either team appears in the market question
                home_match = self._fuzzy_team_match(home, q)
                away_match = self._fuzzy_team_match(away, q)

                if not (home_match or away_match):
                    continue

                # Found a match — compute consensus probability
                target_team = home if home_match else away
                probs = self._extract_consensus(event, target_team)

                if probs:
                    avg_prob = sum(probs) / len(probs)
                    reasoning = (
                        f"{len(probs)} bookmakers avg {avg_prob:.1%} for "
                        f"{target_team} ({sport_key})"
                    )
                    return (target_team, avg_prob, len(probs), reasoning)

        return None

    def _fuzzy_team_match(self, team_name: str, question: str) -> bool:
        """Check if a team name appears in the market question."""
        if not team_name:
            return False

        team_lower = team_name.lower()

        # Direct substring match
        if team_lower in question:
            return True

        # Try last word (e.g., "Lakers" from "Los Angeles Lakers")
        parts = team_lower.split()
        if len(parts) > 1 and parts[-1] in question:
            return True

        # Try without city (e.g., "Lakers" from "LA Lakers")
        # Common abbreviations
        abbrevs = {
            "los angeles lakers": ["lakers", "la lakers"],
            "golden state warriors": ["warriors", "gsw"],
            "new york knicks": ["knicks", "ny knicks"],
            "boston celtics": ["celtics"],
            "miami heat": ["heat"],
            "dallas mavericks": ["mavericks", "mavs"],
            "denver nuggets": ["nuggets"],
            "milwaukee bucks": ["bucks"],
            "phoenix suns": ["suns"],
            "philadelphia 76ers": ["76ers", "sixers"],
            "new york yankees": ["yankees"],
            "los angeles dodgers": ["dodgers"],
            "kansas city chiefs": ["chiefs"],
            "san francisco 49ers": ["49ers", "niners"],
            "new england patriots": ["patriots", "pats"],
            "green bay packers": ["packers"],
        }

        for full_name, aliases in abbrevs.items():
            if team_lower == full_name or team_lower in aliases:
                return any(a in question for a in aliases) or full_name in question

        return False

    def _extract_consensus(
        self, event: dict, target_team: str
    ) -> list[float]:
        """
        Extract implied probabilities for target_team from all bookmakers.
        Returns list of probabilities (one per bookmaker).
        """
        probs = []
        target_lower = target_team.lower()

        for bookmaker in event.get("bookmakers", []):
            for market_data in bookmaker.get("markets", []):
                if market_data.get("key") != "h2h":
                    continue

                for outcome in market_data.get("outcomes", []):
                    outcome_name = outcome.get("name", "").lower()
                    decimal_odds = outcome.get("price", 0)

                    if decimal_odds <= 1.0:
                        continue

                    # Match outcome to our target team
                    if (
                        target_lower in outcome_name
                        or outcome_name in target_lower
                        or self._fuzzy_team_match(outcome.get("name", ""), target_lower)
                    ):
                        implied_prob = 1.0 / decimal_odds
                        probs.append(implied_prob)

        return probs

    def _determine_side(self, market: Market, team_name: str) -> str:
        """Determine if the matched team corresponds to YES or NO."""
        q = market.question.lower()
        team_lower = team_name.lower()

        # Common patterns:
        # "Will [team] win?" → team = YES
        # "Will [team] beat [other]?" → team = YES
        # "[Team A] vs [Team B]: Who will win?" → depends on phrasing

        # If question starts with "Will [team]" → YES
        if f"will {team_lower}" in q or q.startswith(team_lower):
            return "YES"

        # If the question has "win" or "beat" near the team name
        # Simple heuristic: if team appears before "win"/"beat", it's YES
        team_pos = q.find(team_lower)
        win_pos = max(q.find("win"), q.find("beat"), q.find("defeat"))

        if team_pos >= 0 and win_pos >= 0 and team_pos < win_pos:
            return "YES"

        # Default: assume team name in question = YES side
        return "YES"
