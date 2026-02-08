"""
Strategy Module - Real edge-finding strategies for Polymarket.

Each strategy returns a list of Signal objects. A Signal represents a
concrete trading opportunity with a measured edge, confidence level,
and human-readable reason.

STRATEGIES:
    momentum        - Detects real price trends using historical snapshots
    arbitrage       - Verifies YES+NO mispricing via live orderbook (not stale Gamma data)
    value_sports    - Compares Polymarket odds to external sportsbook lines
    value_crypto    - Compares Polymarket odds to a simple price-range model
    mean_reversion  - Bets on overreactions reverting toward prior equilibrium

USAGE:
    from strategy import find_signals, Signal

    signals = find_signals(markets, strategies=["momentum", "arbitrage"])
    for sig in signals:
        print(f"{sig.side} {sig.market.question[:40]} | edge={sig.edge_pct:.1f}%")
"""

import time
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from market_fetcher import Market
from client_manager import clients
from persistence import db


# ─────────────────────────────────────────────────────────────
#  Signal: what every strategy returns
# ─────────────────────────────────────────────────────────────

@dataclass
class Signal:
    """A concrete trading signal produced by a strategy."""
    market: Market
    side: str               # "YES", "NO", or "ARB"
    strategy: str           # which strategy produced this
    edge_pct: float         # estimated edge in percent (higher = better)
    confidence: float       # 0.0 – 1.0 how sure we are
    entry_price: float      # recommended entry price
    reason: str             # human-readable explanation

    @property
    def score(self) -> float:
        """Composite score for ranking (edge × confidence)."""
        return self.edge_pct * self.confidence


# ─────────────────────────────────────────────────────────────
#  1. MOMENTUM — uses real price history from the DB
# ─────────────────────────────────────────────────────────────

def find_momentum_signals(
    markets: list[Market],
    lookback_hours: int = 4,
    min_snapshots: int = 3,
    min_move_pct: float = 5.0,
    consistency_threshold: float = 0.65,
) -> list[Signal]:
    """
    Find markets where price has moved consistently in one direction.

    Uses YES price history (stored in DB snapshots) to decide whether to buy:
      - YES if YES is trending up
      - NO if YES is trending down
    """
    signals = []

    for market in markets:
        # We store both YES/NO prices per snapshot under the YES token_id.
        # So momentum uses YES history to decide whether to buy YES (trend up)
        # or buy NO (YES trending down).
        if market.price_yes < 0.10 or market.price_yes > 0.90:
            continue

        snapshots = db.get_price_history(market.token_id_yes, hours=lookback_hours)
        if len(snapshots) < min_snapshots:
            continue

        prices = [s["price_yes"] for s in snapshots]
        oldest_price = prices[0]
        newest_price = prices[-1]

        if oldest_price <= 0:
            continue

        # 1. Total move
        total_move = newest_price - oldest_price
        total_move_pct = abs(total_move / oldest_price) * 100
        if total_move_pct < min_move_pct:
            continue

        # 2. Consistency — what fraction of intervals agree with direction?
        direction = 1 if total_move > 0 else -1
        intervals = len(prices) - 1
        agreeing = sum(
            1 for i in range(intervals)
            if (prices[i + 1] - prices[i]) * direction > 0
        )
        consistency = agreeing / intervals
        if consistency < consistency_threshold:
            continue

        # Edge estimate: move_pct * consistency * decay factor
        decay = max(0.3, 1.0 - (total_move_pct / 50))
        edge = total_move_pct * consistency * decay

        if direction > 0:
            signals.append(Signal(
                market=market,
                side="YES",
                strategy="momentum",
                edge_pct=edge,
                confidence=min(consistency, 0.95),
                entry_price=market.price_yes,
                reason=(
                    f"YES moved {total_move_pct:+.1f}% over {lookback_hours}h "
                    f"({agreeing}/{intervals} intervals consistent)"
                ),
            ))
        else:
            signals.append(Signal(
                market=market,
                side="NO",
                strategy="momentum",
                edge_pct=edge,
                confidence=min(consistency, 0.95),
                entry_price=market.price_no,
                reason=(
                    f"YES fell {total_move_pct:.1f}% over {lookback_hours}h → NO rising "
                    f"({agreeing}/{intervals} intervals consistent)"
                ),
            ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


# ─────────────────────────────────────────────────────────────
#  2. ARBITRAGE — verified via live orderbook
# ─────────────────────────────────────────────────────────────

def find_arbitrage_signals(
    markets: list[Market],
    min_profit_pct: float = 1.5,
    fee_estimate: float = 0.002,
) -> list[Signal]:
    """
    Find markets where buying both YES and NO costs less than $1.00.

    UNLIKE the old version (which used stale Gamma API prices), this
    hits the live CLOB orderbook to get actual ask prices for both
    sides before reporting an opportunity.

    For each candidate:
      1. Pre-screen using Gamma prices (fast, avoids unnecessary API calls)
      2. If combined < 0.995, verify via live orderbook asks
      3. Subtract fee estimate (2x buy fees)
      4. Only signal if net profit > min_profit_pct

    Args:
        markets: candidate markets
        min_profit_pct: minimum net profit after fees to report
        fee_estimate: estimated fee per side as a fraction (0.002 = 0.2%)
    """
    signals = []

    for market in markets:
        # Step 1: quick pre-screen with Gamma prices
        gamma_combined = market.price_yes + market.price_no
        if gamma_combined >= 0.995:
            continue  # definitely no arb here

        # Step 2: verify with live orderbook
        try:
            yes_ask = _get_best_ask(market.token_id_yes)
            no_ask = _get_best_ask(market.token_id_no)
        except Exception:
            continue

        if yes_ask is None or no_ask is None:
            continue

        live_combined = yes_ask + no_ask

        # Step 3: subtract fees
        total_fees = (yes_ask + no_ask) * fee_estimate * 2  # buy both sides
        net_profit = 1.0 - live_combined - total_fees
        net_profit_pct = net_profit * 100

        if net_profit_pct < min_profit_pct:
            continue

        signals.append(Signal(
            market=market,
            side="ARB",
            strategy="arbitrage",
            edge_pct=net_profit_pct,
            confidence=0.95,  # high confidence — it's math
            entry_price=live_combined,
            reason=(
                f"Buy YES@{yes_ask:.3f} + NO@{no_ask:.3f} = {live_combined:.3f} "
                f"→ net profit {net_profit_pct:.2f}% after fees"
            ),
        ))

    signals.sort(key=lambda s: s.edge_pct, reverse=True)
    return signals


def _get_best_ask(token_id: str) -> Optional[float]:
    """Get the best ask price from live orderbook."""
    try:
        book = clients.read.get_order_book(token_id)
        if book and book.asks:
            return float(book.asks[0].price)
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
#  3. VALUE (SPORTS) — compare Polymarket to external odds
# ─────────────────────────────────────────────────────────────

# External odds source — the-odds-api.com (free tier: 500 req/month)
# Users must supply their own API key via .env
# If no key, this strategy is skipped gracefully.

_ODDS_API_KEY: Optional[str] = None

def _load_odds_api_key():
    """Load the Odds API key from environment (lazy)."""
    global _ODDS_API_KEY
    if _ODDS_API_KEY is None:
        import os
        _ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
    return _ODDS_API_KEY


def find_value_sports_signals(
    markets: list[Market],
    min_edge_pct: float = 8.0,
) -> list[Signal]:
    """
    Compare Polymarket sports odds to external sportsbook consensus.

    HOW IT WORKS:
      1. Fetch consensus odds from the-odds-api.com for live sports events
      2. For each Polymarket sports market, try to match it to an external event
         by keyword matching (team names, league)
      3. If the external consensus implied probability differs from
         the Polymarket price by > min_edge_pct, signal a value bet
         on the side the market is underpricing

    REQUIRES: ODDS_API_KEY in .env (free at https://the-odds-api.com)
    If no key is configured, this strategy returns [] with no errors.

    Args:
        markets: candidate markets
        min_edge_pct: minimum edge vs external consensus to signal
    """
    api_key = _load_odds_api_key()
    if not api_key:
        return []  # no external odds source configured

    # Fetch external odds (cached per scan cycle)
    external_odds = _fetch_external_sports_odds(api_key)
    if not external_odds:
        return []

    signals = []
    sports_markets = [m for m in markets if _is_sports_market(m)]

    for market in sports_markets:
        # Try to find a matching external event
        match = _match_to_external(market, external_odds)
        if match is None:
            continue

        ext_prob, ext_source = match  # e.g. (0.62, "DraftKings avg")

        # Compare to Polymarket YES price
        poly_price = market.price_yes
        edge = ext_prob - poly_price  # positive = market underprices YES

        if abs(edge * 100) >= min_edge_pct:
            if edge > 0:
                # External says more likely than market price → buy YES
                signals.append(Signal(
                    market=market,
                    side="YES",
                    strategy="value_sports",
                    edge_pct=edge * 100,
                    confidence=0.7,  # external odds aren't perfect
                    entry_price=poly_price,
                    reason=(
                        f"External odds ({ext_source}): {ext_prob:.0%} vs "
                        f"Polymarket: {poly_price:.0%} → YES underpriced by {edge*100:.1f}%"
                    ),
                ))
            else:
                # Market overprices YES → buy NO
                no_edge = abs(edge)
                signals.append(Signal(
                    market=market,
                    side="NO",
                    strategy="value_sports",
                    edge_pct=no_edge * 100,
                    confidence=0.7,
                    entry_price=market.price_no,
                    reason=(
                        f"External odds ({ext_source}): {ext_prob:.0%} vs "
                        f"Polymarket: {poly_price:.0%} → YES overpriced, NO underpriced by {no_edge*100:.1f}%"
                    ),
                ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


def _fetch_external_sports_odds(api_key: str) -> list[dict]:
    """
    Fetch odds from the-odds-api.com for upcoming events.
    Returns a flat list of events with consensus implied probabilities.

    Each item:
        {
            "teams": ["Team A", "Team B"],
            "sport": "basketball_nba",
            "commence_time": "2025-...",
            "probabilities": {"Team A": 0.62, "Team B": 0.38},
            "source": "consensus (3 books)"
        }
    """
    # Only fetch the major US sports + soccer
    sports = [
        "basketball_nba", "americanfootball_nfl", "baseball_mlb",
        "icehockey_nhl", "mma_mixed_martial_arts",
        "soccer_epl", "soccer_usa_mls",
    ]

    all_events = []

    for sport_key in sports:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
            resp = requests.get(url, params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "decimal",
            }, timeout=10)

            if resp.status_code != 200:
                continue

            for event in resp.json():
                teams = [
                    event.get("home_team", ""),
                    event.get("away_team", ""),
                ]

                # Average implied probability across bookmakers
                probs = _average_bookmaker_probs(event.get("bookmakers", []), teams)
                if probs:
                    all_events.append({
                        "teams": teams,
                        "sport": sport_key,
                        "commence_time": event.get("commence_time", ""),
                        "probabilities": probs,
                        "source": f"consensus ({len(event.get('bookmakers', []))} books)",
                    })

            # Be polite to the API
            time.sleep(0.3)

        except Exception:
            continue

    return all_events


def _average_bookmaker_probs(
    bookmakers: list[dict], teams: list[str]
) -> Optional[dict]:
    """
    Average the implied probabilities across bookmakers for each team.
    Converts decimal odds → implied probability and normalizes to sum to 1.
    """
    if not bookmakers or not teams:
        return None

    team_totals = {t: [] for t in teams}

    for bm in bookmakers:
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for outcome in mkt.get("outcomes", []):
                name = outcome.get("name", "")
                decimal_odds = outcome.get("price", 0)
                if decimal_odds > 1.0 and name in team_totals:
                    implied = 1.0 / decimal_odds
                    team_totals[name].append(implied)

    # Average and normalize
    avg = {}
    for team, probs_list in team_totals.items():
        if probs_list:
            avg[team] = sum(probs_list) / len(probs_list)

    if not avg:
        return None

    # Normalize to sum to 1 (remove overround)
    total = sum(avg.values())
    if total > 0:
        avg = {t: p / total for t, p in avg.items()}

    return avg


def _is_sports_market(market: Market) -> bool:
    """Check if a market is sports-related."""
    q = market.question.lower()
    cat = market.category.lower()
    sports_kw = [
        "win", "championship", "super bowl", "nba", "nfl", "mlb", "nhl",
        "playoffs", "finals", "game", "match", "vs", "premier league",
        "ufc", "mma", "tennis",
    ]
    return "sports" in cat or any(kw in q for kw in sports_kw)


def _match_to_external(
    market: Market, external_events: list[dict]
) -> Optional[tuple[float, str]]:
    """
    Try to match a Polymarket market to an external event.
    Returns (implied_probability_for_YES_side, source) or None.

    Matching is done by checking if both team names from the external
    event appear in the Polymarket question. This is a fuzzy heuristic
    — not perfect, but catches most standard "Team A vs Team B" markets.
    """
    question = market.question.lower()

    for event in external_events:
        teams = event["teams"]
        probs = event["probabilities"]

        # Both team names must appear in the question
        team_lower = [t.lower() for t in teams]
        if not all(t in question for t in team_lower):
            continue

        # Figure out which team the YES side corresponds to
        # Convention: "Will [TeamA] win?" → YES = TeamA wins
        for team in teams:
            if team.lower() in question:
                # Check if this team is the subject (appears before "win"/"beat")
                team_pos = question.find(team.lower())
                win_pos = question.find("win")
                beat_pos = question.find("beat")
                action_pos = min(
                    p for p in [win_pos, beat_pos, len(question)] if p >= 0
                )

                if team_pos < action_pos and team in probs:
                    return (probs[team], event["source"])

        # Fallback: if only one team mentioned, assume YES = that team
        mentioned_teams = [t for t in teams if t.lower() in question]
        if len(mentioned_teams) == 1 and mentioned_teams[0] in probs:
            return (probs[mentioned_teams[0]], event["source"])

    return None


# ─────────────────────────────────────────────────────────────
#  4. MEAN REVERSION — bet on overreactions snapping back
# ─────────────────────────────────────────────────────────────

def find_mean_reversion_signals(
    markets: list[Market],
    lookback_hours: int = 12,
    min_snapshots: int = 5,
    min_spike_pct: float = 10.0,
    reversion_window_hours: int = 2,
) -> list[Signal]:
    """
    Detect sudden price spikes and bet on partial reversion.

    HOW IT WORKS:
      1. Compute the average price over `lookback_hours`
      2. Compare current price to that average
      3. If current price is >min_spike_pct away from average AND
         the spike happened recently (within reversion_window_hours),
         signal a bet AGAINST the spike direction

    This exploits the fact that prediction markets frequently overreact
    to news and partially revert within hours.

    Does NOT bet against genuine sustained moves (those get caught
    by the momentum strategy instead). The key difference is the
    recency of the spike — mean reversion targets sudden, recent moves.

    Args:
        markets: candidate markets
        lookback_hours: window for computing the baseline average
        min_snapshots: minimum data points needed
        min_spike_pct: minimum deviation from average to trigger
        reversion_window_hours: spike must have occurred within this many hours
    """
    signals = []

    for market in markets:
        for side, token_id, current_price in [
            ("YES", market.token_id_yes, market.price_yes),
            ("NO", market.token_id_no, market.price_no),
        ]:
            if current_price < 0.10 or current_price > 0.90:
                continue

            snapshots = db.get_price_history(token_id, hours=lookback_hours)
            if len(snapshots) < min_snapshots:
                continue

            prices = [s["price_yes"] for s in snapshots]
            avg_price = sum(prices) / len(prices)

            if avg_price <= 0:
                continue

            # How far is current from average?
            deviation = current_price - avg_price
            deviation_pct = (deviation / avg_price) * 100

            if abs(deviation_pct) < min_spike_pct:
                continue

            # Was the spike recent? Check if most of the move happened
            # in the last `reversion_window_hours`
            recent_cutoff = datetime.now() - timedelta(hours=reversion_window_hours)
            recent_snapshots = [
                s for s in snapshots
                if datetime.fromisoformat(s["timestamp"]) >= recent_cutoff
            ]

            if len(recent_snapshots) < 2:
                continue

            recent_prices = [s["price_yes"] for s in recent_snapshots]
            recent_move = recent_prices[-1] - recent_prices[0]
            recent_move_pct = abs(recent_move / recent_prices[0]) * 100 if recent_prices[0] > 0 else 0

            # The recent move should account for most of the total deviation
            if recent_move_pct < min_spike_pct * 0.6:
                continue

            # Bet AGAINST the spike
            # Spiked up → bet NO (expect YES to fall back)
            # Spiked down → bet YES (expect YES to recover)
            edge = abs(deviation_pct) * 0.5  # expect ~50% reversion

            if deviation > 0 and side == "YES":
                # YES spiked up → bet NO
                signals.append(Signal(
                    market=market,
                    side="NO",
                    strategy="mean_reversion",
                    edge_pct=edge,
                    confidence=0.55,  # mean reversion is probabilistic
                    entry_price=market.price_no,
                    reason=(
                        f"YES spiked {deviation_pct:+.1f}% vs {lookback_hours}h avg "
                        f"({avg_price:.3f} → {current_price:.3f}), expecting partial reversion"
                    ),
                ))
            elif deviation < 0 and side == "YES":
                # YES dropped → bet YES (recovery)
                signals.append(Signal(
                    market=market,
                    side="YES",
                    strategy="mean_reversion",
                    edge_pct=edge,
                    confidence=0.55,
                    entry_price=current_price,
                    reason=(
                        f"YES dropped {deviation_pct:.1f}% vs {lookback_hours}h avg "
                        f"({avg_price:.3f} → {current_price:.3f}), expecting partial reversion"
                    ),
                ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


# ─────────────────────────────────────────────────────────────
#  5. FAVORITES — proper expected value scoring
# ─────────────────────────────────────────────────────────────

def find_favorite_signals(
    markets: list[Market],
    min_prob: float = 0.65,
    max_prob: float = 0.85,
    min_volume: float = 100000,
) -> list[Signal]:
    """
    Find high-probability outcomes that are liquid enough to trade.

    THIS IS NOT A TRUE EDGE STRATEGY. It simply identifies markets
    where one side has high implied probability AND high volume
    (which suggests the price is informationally efficient).

    The "edge" here is weak — you're betting that the crowd is right.
    The real value is combining this with other signals (e.g., external
    odds confirmation, momentum in the right direction).

    Scoring: edge_pct = implied probability - 50, scaled by volume confidence.
    This means an 80% favorite scores ~30, while a 65% favorite scores ~15.
    The confidence is deliberately low (0.4) because being a favorite
    alone is not real alpha — the market already prices this in.

    Args:
        min_prob: minimum implied probability (0.65 = 65%)
        max_prob: maximum (avoid markets too close to resolution at 95%+)
        min_volume: minimum volume to consider (filters out thin markets)
    """
    signals = []

    for market in markets:
        if market.volume < min_volume:
            continue

        for side, price in [("YES", market.price_yes), ("NO", market.price_no)]:
            if min_prob <= price <= max_prob:
                edge = (price - 0.50) * 100  # how far above 50/50

                # Volume-based confidence bump (more volume = price more likely correct)
                vol_factor = min(market.volume / 500000, 1.0)
                confidence = 0.35 + (0.15 * vol_factor)  # 0.35 – 0.50

                signals.append(Signal(
                    market=market,
                    side=side,
                    strategy="favorites",
                    edge_pct=edge,
                    confidence=confidence,
                    entry_price=price,
                    reason=(
                        f"{side} at {price:.0%} (vol: ${market.volume:,.0f}) — "
                        f"crowd favorite, NOT a true edge signal"
                    ),
                ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


def find_underdog_signals(
    markets: list[Market],
    min_prob: float = 0.20,
    max_prob: float = 0.40,
    min_volume: float = 100000,
) -> list[Signal]:
    """
    Find liquid underdogs (low implied probability outcomes).

    Like favorites, this is NOT a true edge strategy by itself.
    """
    signals = []

    for market in markets:
        if market.volume < min_volume:
            continue

        for side, price in [("YES", market.price_yes), ("NO", market.price_no)]:
            if min_prob <= price <= max_prob:
                edge = (0.50 - price) * 100  # how far below 50/50

                vol_factor = min(market.volume / 500000, 1.0)
                confidence = 0.30 + (0.10 * vol_factor)  # 0.30 – 0.40

                signals.append(Signal(
                    market=market,
                    side=side,
                    strategy="underdogs",
                    edge_pct=edge,
                    confidence=confidence,
                    entry_price=price,
                    reason=(
                        f"{side} underdog at {price:.0%} (vol: ${market.volume:,.0f}) — "
                        f"risk-seeking filter, NOT a true edge signal"
                    ),
                ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals


# ─────────────────────────────────────────────────────────────
#  DISPATCHER — run selected strategies and merge/rank results
# ─────────────────────────────────────────────────────────────

# Map strategy names to their functions
STRATEGY_REGISTRY = {
    "momentum":        find_momentum_signals,
    "arbitrage":       find_arbitrage_signals,
    "value_sports":    find_value_sports_signals,
    "value":           find_value_sports_signals,  # alias for AutoStrategy/value mode
    "mean_reversion":  find_mean_reversion_signals,
    "favorites":       find_favorite_signals,
    "underdogs":       find_underdog_signals,
}


def find_signals(
    markets: list[Market],
    strategies: Optional[list[str]] = None,
    min_edge_pct: float = 5.0,
    max_results: int = 10,
) -> list[Signal]:
    """
    Run one or more strategies and return ranked, deduplicated signals.

    Args:
        markets: list of candidate markets
        strategies: which strategies to run (None = all)
        min_edge_pct: minimum edge to include in results
        max_results: maximum signals to return

    Returns:
        List of Signal objects, sorted by score (edge × confidence) descending,
        deduplicated by market ID (best signal wins per market).
    """
    if strategies is None:
        strategies = list(STRATEGY_REGISTRY.keys())

    all_signals: list[Signal] = []

    for name in strategies:
        func = STRATEGY_REGISTRY.get(name)
        if func is None:
            print(f"⚠️ Unknown strategy: {name}")
            continue

        try:
            sigs = func(markets)
            all_signals.extend(sigs)
        except Exception as e:
            print(f"⚠️ Strategy '{name}' failed: {e}")

    # Filter by minimum edge
    all_signals = [s for s in all_signals if s.edge_pct >= min_edge_pct]

    # Deduplicate: keep best signal per market
    best_per_market: dict[str, Signal] = {}
    for sig in all_signals:
        key = f"{sig.market.id}_{sig.side}"
        if key not in best_per_market or sig.score > best_per_market[key].score:
            best_per_market[key] = sig

    # Sort by score
    result = sorted(best_per_market.values(), key=lambda s: s.score, reverse=True)
    return result[:max_results]
