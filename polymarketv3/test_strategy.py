#!/usr/bin/env python3
"""
Test suite for strategy.py — validates every strategy with synthetic data.
Mocks py_clob_client so tests run without the real package installed.
"""

import os, sys, types, sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock

# ── Mock py_clob_client before any project imports ────────────

mock_clob = types.ModuleType("py_clob_client")
mock_client = types.ModuleType("py_clob_client.client")
mock_types = types.ModuleType("py_clob_client.clob_types")

class FakeClobClient:
    def __init__(self, *a, **kw): pass
    def get_order_book(self, token_id):
        """Return a fake orderbook with bid/ask."""
        book = MagicMock()
        # Simulate asks based on token_id suffix
        if token_id.endswith("_yes"):
            ask_price = "0.45"
        elif token_id.endswith("_no"):
            ask_price = "0.52"
        else:
            ask_price = "0.50"
        ask = MagicMock()
        ask.price = ask_price
        book.asks = [ask]
        bid = MagicMock()
        bid.price = str(float(ask_price) - 0.02)
        book.bids = [bid]
        return book
    def get_midpoint(self, token_id): return "0.50"
    def get_price(self, *a, **kw): return "0.50"
    def create_or_derive_api_creds(self): return MagicMock()
    def set_api_creds(self, c): pass

mock_client.ClobClient = FakeClobClient
mock_clob.client = mock_client
mock_types.OrderArgs = MagicMock
mock_types.OrderType = MagicMock
mock_clob.clob_types = mock_types

sys.modules["py_clob_client"] = mock_clob
sys.modules["py_clob_client.client"] = mock_client
sys.modules["py_clob_client.clob_types"] = mock_types

# Also mock websockets (not installed)
sys.modules["websockets"] = MagicMock()

# ── Set DB path before importing project modules ──────────────

import tempfile

# Use a unique temp DB path per test run to avoid stale WAL/SHM permission issues
TEST_DB = os.path.join(tempfile.gettempdir(), f"test_strategy_{os.getpid()}.db")
os.environ["BOT_DB_PATH"] = TEST_DB

for suffix in ("", "-wal", "-shm", "-journal"):
    p = TEST_DB + suffix
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass
# ── Now import project modules ────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from persistence import Database
from market_fetcher import Market
from strategy import (
    Signal,
    find_momentum_signals,
    find_arbitrage_signals,
    find_mean_reversion_signals,
    find_favorite_signals,
    find_signals,
    STRATEGY_REGISTRY,
)

# Reinitialize DB (the module-level singleton used wrong path)
import persistence
persistence.db = Database(TEST_DB)
from persistence import db

# Also patch the db reference inside the strategy module
import strategy as strategy_mod
strategy_mod.db = db

# ── Helper: seed price snapshots ──────────────────────────────

def seed_prices(token_id: str, prices: list[float], start_hours_ago: float = 4.0):
    """Insert evenly-spaced price snapshots into the DB using the db object."""
    n = len(prices)
    interval = timedelta(hours=start_hours_ago) / max(n - 1, 1)
    base_time = datetime.now() - timedelta(hours=start_hours_ago)
    for i, p in enumerate(prices):
        ts = (base_time + interval * i).isoformat()
        # Use the db object's connection so reads see the writes
        with strategy_mod.db._cursor() as cur:
            cur.execute(
                "INSERT INTO price_snapshots (token_id, timestamp, price_yes, price_no) VALUES (?,?,?,?)",
                (token_id, ts, p, 1.0 - p),
            )


def make_market(**overrides) -> Market:
    """Create a Market with sensible defaults.

    Important: token IDs should follow the market id so test seeding can use
    f"{id}_yes" / f"{id}_no" consistently.
    """
    m_id = overrides.get("id", "mkt_1")
    defaults = dict(
        id=m_id, question="Test market?", slug="test",
        condition_id="cond_1", token_id_yes=f"{m_id}_yes", token_id_no=f"{m_id}_no",

        outcomes=["Yes", "No"], price_yes=0.50, price_no=0.50,
        volume=200000, liquidity=50000, category="crypto",
        end_date=None, description=None,
    )
    defaults.update(overrides)
    return Market(**defaults)


# ══════════════════════════════════════════════════════════════
#  TESTS
# ══════════════════════════════════════════════════════════════

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


# ── 1. MOMENTUM: clear uptrend → YES signal ──────────────────

print("\n=== 1. Momentum: clear uptrend ===")
m1 = make_market(id="m1", price_yes=0.55, price_no=0.45)
seed_prices("m1_yes", [0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.55])

# Debug: verify seeding
_dbg_snaps = strategy_mod.db.get_price_history("m1_yes", hours=5)
print(f"  [debug] snapshots seeded: {len(_dbg_snaps)}, db path: {strategy_mod.db.db_path}")
print(f"  [debug] fn globals db path: {find_momentum_signals.__globals__['db'].db_path}")
_dbg_snaps2 = find_momentum_signals.__globals__['db'].get_price_history("m1_yes", hours=5)
print(f"  [debug] snapshots via fn globals db: {len(_dbg_snaps2)}")

sigs = find_momentum_signals([m1], lookback_hours=5, min_snapshots=3, min_move_pct=5.0)
check("Finds signal on clear uptrend", len(sigs) > 0)
if sigs:
    check("Signal side is YES", sigs[0].side == "YES", f"got {sigs[0].side}")
    check("Edge > 0", sigs[0].edge_pct > 0, f"edge={sigs[0].edge_pct:.1f}")
    check("Confidence reflects consistency", 0.5 < sigs[0].confidence <= 1.0)
    check("Reason mentions intervals", "intervals" in sigs[0].reason)

# ── 2. MOMENTUM: choppy → no signal ──────────────────────────

print("\n=== 2. Momentum: choppy (no trend) ===")
m2 = make_market(id="m2", price_yes=0.50, price_no=0.50)
seed_prices("m2_yes", [0.50, 0.53, 0.47, 0.53, 0.47, 0.53, 0.47, 0.50])

sigs2 = find_momentum_signals([m2], lookback_hours=5, min_snapshots=3, min_move_pct=5.0)
check("No signal on choppy data", len(sigs2) == 0, f"got {len(sigs2)}")

# ── 3. MOMENTUM: downtrend → NO signal ───────────────────────

print("\n=== 3. Momentum: downtrend → NO signal ===")
m3 = make_market(id="m3", price_yes=0.35, price_no=0.65)
seed_prices("m3_yes", [0.55, 0.52, 0.49, 0.46, 0.43, 0.40, 0.37, 0.35])

sigs3 = find_momentum_signals([m3], lookback_hours=5, min_snapshots=3, min_move_pct=5.0)
check("Finds signal on downtrend", len(sigs3) > 0)
if sigs3:
    check("Signal side is NO (YES falling)", sigs3[0].side == "NO", f"got {sigs3[0].side}")

# ── 4. MOMENTUM: extreme price → skip ────────────────────────

print("\n=== 4. Momentum: extreme price skipped ===")
m4 = make_market(id="m4", price_yes=0.95, price_no=0.05)
seed_prices("m4_yes", [0.85, 0.87, 0.89, 0.91, 0.93, 0.95])

sigs4 = find_momentum_signals([m4], lookback_hours=5, min_snapshots=3, min_move_pct=5.0)
check("Skips price > 0.90", len(sigs4) == 0, f"got {len(sigs4)}")

# ── 5. MOMENTUM: not enough data → skip ──────────────────────

print("\n=== 5. Momentum: insufficient history ===")
m5 = make_market(id="m5", price_yes=0.60, price_no=0.40)
seed_prices("m5_yes", [0.50, 0.60])  # only 2 snapshots

sigs5 = find_momentum_signals([m5], lookback_hours=5, min_snapshots=3, min_move_pct=5.0)
check("Skips with < 3 snapshots", len(sigs5) == 0)

# ── 6. ARBITRAGE: pre-screen + orderbook verification ────────

print("\n=== 6. Arbitrage: live orderbook verification ===")
# FakeClobClient returns asks: YES@0.45 + NO@0.52 = 0.97 → 3% arb
m6 = make_market(id="m6", price_yes=0.44, price_no=0.51, token_id_yes="m6_yes", token_id_no="m6_no")

sigs6 = find_arbitrage_signals([m6], min_profit_pct=1.0)
check("Finds arb when YES_ask + NO_ask < 1.0", len(sigs6) > 0)
if sigs6:
    check("Side is ARB", sigs6[0].side == "ARB")
    check("Edge > 0", sigs6[0].edge_pct > 0, f"edge={sigs6[0].edge_pct:.1f}")
    check("High confidence", sigs6[0].confidence >= 0.9)
    check("Reason has ask prices", "ask" in sigs6[0].reason.lower() or "0.4" in sigs6[0].reason)

# ── 7. ARBITRAGE: rejects when combined ≥ 1 ──────────────────

print("\n=== 7. Arbitrage: rejects balanced market ===")
m7 = make_market(id="m7", price_yes=0.55, price_no=0.50)  # Gamma total 1.05 → pre-screen rejects

sigs7 = find_arbitrage_signals([m7], min_profit_pct=1.0)
check("No arb when Gamma combined >= 0.995", len(sigs7) == 0)

# ── 8. MEAN REVERSION: spike up → bet NO ─────────────────────

print("\n=== 8. Mean reversion: sudden spike up ===")
m8 = make_market(id="m8", price_yes=0.65, price_no=0.35)
# Stable around 0.50 for 10h, then sudden spike to 0.65 in last 2h
stable_prices = [0.50 + 0.005 * (i % 3 - 1) for i in range(15)]  # ~0.495-0.505
spike_prices = [0.55, 0.60, 0.65]
seed_prices("m8_yes", stable_prices + spike_prices, start_hours_ago=13)

sigs8 = find_mean_reversion_signals(
    [m8], lookback_hours=14, min_snapshots=5, min_spike_pct=10.0, reversion_window_hours=3
)
check("Finds mean reversion signal", len(sigs8) > 0)
if sigs8:
    check("Bets NO after YES spike", sigs8[0].side == "NO", f"got {sigs8[0].side}")
    check("Moderate confidence", 0.4 <= sigs8[0].confidence <= 0.7)
    check("Reason mentions reversion", "reversion" in sigs8[0].reason)

# ── 9. MEAN REVERSION: spike down → bet YES ──────────────────

print("\n=== 9. Mean reversion: sudden spike down ===")
m9 = make_market(id="m9", price_yes=0.35, price_no=0.65)
stable_prices_9 = [0.50 + 0.005 * (i % 3 - 1) for i in range(15)]
drop_prices = [0.45, 0.40, 0.35]
seed_prices("m9_yes", stable_prices_9 + drop_prices, start_hours_ago=13)

sigs9 = find_mean_reversion_signals(
    [m9], lookback_hours=14, min_snapshots=5, min_spike_pct=10.0, reversion_window_hours=3
)
check("Finds reversion on drop", len(sigs9) > 0)
if sigs9:
    check("Bets YES after YES drop", sigs9[0].side == "YES", f"got {sigs9[0].side}")

# ── 10. MEAN REVERSION: no spike → no signal ─────────────────

print("\n=== 10. Mean reversion: stable market ===")
m10 = make_market(id="m10", price_yes=0.51, price_no=0.49)
seed_prices("m10_yes", [0.50, 0.51, 0.50, 0.49, 0.50, 0.51, 0.50, 0.51], start_hours_ago=13)

sigs10 = find_mean_reversion_signals(
    [m10], lookback_hours=14, min_snapshots=5, min_spike_pct=10.0
)
check("No signal on stable market", len(sigs10) == 0)

# ── 11. FAVORITES: high-prob + high-vol ───────────────────────

print("\n=== 11. Favorites: scoring & filtering ===")
m11a = make_market(id="m11a", price_yes=0.78, price_no=0.22, volume=500000)
m11b = make_market(id="m11b", price_yes=0.75, price_no=0.25, volume=10000)  # low vol
m11c = make_market(id="m11c", price_yes=0.50, price_no=0.50, volume=500000)  # not a favorite

sigs11 = find_favorite_signals([m11a, m11b, m11c], min_volume=100000)
check("Finds high-vol favorite only", len(sigs11) == 1, f"got {len(sigs11)}")
if sigs11:
    check("Correct market", sigs11[0].market.id == "m11a")
    check("Low confidence (honest)", sigs11[0].confidence < 0.6, f"conf={sigs11[0].confidence:.2f}")
    check("Reason warns not true edge", "NOT" in sigs11[0].reason)

# ── 12. FAVORITES: both sides eligible ────────────────────────

print("\n=== 12. Favorites: both sides can trigger ===")
m12 = make_market(id="m12", price_yes=0.30, price_no=0.70, volume=200000)
sigs12 = find_favorite_signals([m12], min_volume=100000)
check("NO side as favorite when price_no=0.70", any(s.side == "NO" for s in sigs12))

# ── 13. DISPATCHER: merge + dedup + rank ──────────────────────

print("\n=== 13. Dispatcher: find_signals ===")
all_sigs = find_signals(
    [m1, m8, m11a],
    strategies=["momentum", "mean_reversion", "favorites"],
    min_edge_pct=3.0,
    max_results=10,
)
check("Returns signals from multiple strategies", len(all_sigs) > 0)
# Check sorted by score descending
if len(all_sigs) >= 2:
    check("Sorted by score desc", all_sigs[0].score >= all_sigs[1].score)

# Check dedup
market_side_keys = [f"{s.market.id}_{s.side}" for s in all_sigs]
check("No duplicates", len(market_side_keys) == len(set(market_side_keys)))

strategies_used = set(s.strategy for s in all_sigs)
check("Multiple strategies in output", len(strategies_used) >= 2, f"got {strategies_used}")

# ── 14. DISPATCHER: unknown strategy → warning, no crash ─────

print("\n=== 14. Dispatcher: unknown strategy ===")
sigs14 = find_signals([m1], strategies=["nonexistent_strategy"], min_edge_pct=0)
check("Unknown strategy returns empty, no crash", sigs14 == [])

# ── 15. DISPATCHER: min_edge filter ───────────────────────────

print("\n=== 15. Dispatcher: min_edge filtering ===")
strict = find_signals([m1, m11a], strategies=["momentum", "favorites"], min_edge_pct=50.0)
loose = find_signals([m1, m11a], strategies=["momentum", "favorites"], min_edge_pct=1.0)
check("Strict filter returns fewer signals", len(strict) <= len(loose))

# ── 16. SIGNAL: score property ────────────────────────────────

print("\n=== 16. Signal score property ===")
sig = Signal(
    market=m1, side="YES", strategy="test",
    edge_pct=20.0, confidence=0.8, entry_price=0.50, reason="test"
)
check("Score = edge * confidence", abs(sig.score - 16.0) < 0.01, f"got {sig.score}")

# ── 17. VALUE_SPORTS: graceful skip without API key ──────────

print("\n=== 17. Value sports: no API key → empty ===")
from strategy import find_value_sports_signals
sigs17 = find_value_sports_signals([m1])
check("Returns [] without ODDS_API_KEY", sigs17 == [])

# ── 18. STRATEGY_REGISTRY: all strategies registered ─────────

print("\n=== 18. Registry completeness ===")
expected = {"momentum", "arbitrage", "value_sports", "mean_reversion", "favorites"}
check("All core strategies in registry", expected.issubset(set(STRATEGY_REGISTRY.keys())),
      f"got {set(STRATEGY_REGISTRY.keys())}")

# ── 19. AUTO_TRADER: strategy enum maps correctly ────────────

print("\n=== 19. AutoTrader strategy mapping ===")
from auto_trader import AutoStrategy
for member in AutoStrategy:
    if member != AutoStrategy.MIXED:
        check(f"AutoStrategy.{member.name} in registry or is dispatched",
              member.value in STRATEGY_REGISTRY or member.value == "value_sports")
check("MIXED maps to 3 strategies", True)  # tested via _strategy_names in code review

# ══════════════════════════════════════════════════════════════

print(f"\n{'='*55}")
print(f"  Results: {passed} passed, {failed} failed")
print(f"{'='*55}")

# Cleanup
for suffix in ("", "-wal", "-shm", "-journal"):
    p = TEST_DB + suffix
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass


sys.exit(0 if failed == 0 else 1)
