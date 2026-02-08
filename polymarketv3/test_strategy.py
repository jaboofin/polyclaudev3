#!/usr/bin/env python3
"""
Pytest suite for strategy.py — validates every strategy with synthetic data.
Mocks py_clob_client so tests run without the real package installed.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class FakeClobClient:
    def __init__(self, *a, **kw):
        pass

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

    def get_midpoint(self, token_id):
        return "0.50"

    def get_price(self, *a, **kw):
        return "0.50"

    def create_or_derive_api_creds(self):
        return MagicMock()

    def set_api_creds(self, c):
        pass


@pytest.fixture(autouse=True)
def mock_external_modules(monkeypatch):
    mock_clob = types.ModuleType("py_clob_client")
    mock_client = types.ModuleType("py_clob_client.client")
    mock_types = types.ModuleType("py_clob_client.clob_types")

    mock_client.ClobClient = FakeClobClient
    mock_clob.client = mock_client
    mock_types.OrderArgs = MagicMock
    mock_types.OrderType = MagicMock
    mock_clob.clob_types = mock_types

    monkeypatch.setitem(sys.modules, "py_clob_client", mock_clob)
    monkeypatch.setitem(sys.modules, "py_clob_client.client", mock_client)
    monkeypatch.setitem(sys.modules, "py_clob_client.clob_types", mock_types)
    monkeypatch.setitem(sys.modules, "websockets", MagicMock())

    yield


@pytest.fixture
def strategy_modules(tmp_path, monkeypatch):
    db_path = tmp_path / "test_strategy.db"
    monkeypatch.setenv("BOT_DB_PATH", str(db_path))

    for suffix in ("", "-wal", "-shm", "-journal"):
        p = f"{db_path}{suffix}"
        if os.path.exists(p):
            os.remove(p)

    repo_root = os.path.dirname(os.path.abspath(__file__))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import persistence as persistence_mod
    importlib.reload(persistence_mod)
    db = persistence_mod.Database(str(db_path))
    persistence_mod.db = db

    import strategy as strategy_mod
    importlib.reload(strategy_mod)
    strategy_mod.db = db

    import market_fetcher as market_fetcher_mod
    importlib.reload(market_fetcher_mod)

    return SimpleNamespace(
        db=db,
        persistence=persistence_mod,
        strategy=strategy_mod,
        market_fetcher=market_fetcher_mod,
    )


@pytest.fixture
def base_time():
    return datetime.now()


def seed_prices(strategy_mod, token_id: str, prices: list[float], base_time: datetime, start_hours_ago: float = 4.0):
    """Insert evenly-spaced price snapshots into the DB using the db object."""
    n = len(prices)
    interval = timedelta(hours=start_hours_ago) / max(n - 1, 1)
    start_time = base_time - timedelta(hours=start_hours_ago)
    for i, price in enumerate(prices):
        ts = (start_time + interval * i).isoformat()
        with strategy_mod.db._cursor() as cur:
            cur.execute(
                "INSERT INTO price_snapshots (token_id, timestamp, price_yes, price_no) VALUES (?,?,?,?)",
                (token_id, ts, price, 1.0 - price),
            )


def make_market(market_fetcher_mod, **overrides):
    """Create a Market with sensible defaults."""
    m_id = overrides.get("id", "mkt_1")
    defaults = dict(
        id=m_id,
        question="Test market?",
        slug="test",
        condition_id="cond_1",
        token_id_yes=f"{m_id}_yes",
        token_id_no=f"{m_id}_no",
        outcomes=["Yes", "No"],
        price_yes=0.50,
        price_no=0.50,
        volume=200000,
        liquidity=50000,
        category="crypto",
        end_date=None,
        description=None,
    )
    defaults.update(overrides)
    return market_fetcher_mod.Market(**defaults)


def test_momentum_clear_uptrend(strategy_modules, base_time):
    m1 = make_market(strategy_modules.market_fetcher, id="m1", price_yes=0.55, price_no=0.45)
    seed_prices(strategy_modules.strategy, "m1_yes", [0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.55], base_time)

    sigs = strategy_modules.strategy.find_momentum_signals(
        [m1], lookback_hours=5, min_snapshots=3, min_move_pct=5.0
    )
    assert sigs, "Expected signal on clear uptrend"
    assert sigs[0].side == "YES"
    assert sigs[0].edge_pct > 0
    assert 0.5 < sigs[0].confidence <= 1.0
    assert "intervals" in sigs[0].reason


def test_momentum_choppy_no_signal(strategy_modules, base_time):
    m2 = make_market(strategy_modules.market_fetcher, id="m2", price_yes=0.50, price_no=0.50)
    seed_prices(strategy_modules.strategy, "m2_yes", [0.50, 0.53, 0.47, 0.53, 0.47, 0.53, 0.47, 0.50], base_time)

    sigs = strategy_modules.strategy.find_momentum_signals(
        [m2], lookback_hours=5, min_snapshots=3, min_move_pct=5.0
    )
    assert sigs == []


def test_momentum_downtrend_no_signal(strategy_modules, base_time):
    m3 = make_market(strategy_modules.market_fetcher, id="m3", price_yes=0.35, price_no=0.65)
    seed_prices(strategy_modules.strategy, "m3_yes", [0.55, 0.52, 0.49, 0.46, 0.43, 0.40, 0.37, 0.35], base_time)

    sigs = strategy_modules.strategy.find_momentum_signals(
        [m3], lookback_hours=5, min_snapshots=3, min_move_pct=5.0
    )
    assert sigs, "Expected signal on downtrend"
    assert sigs[0].side == "NO"


def test_momentum_extreme_price_skipped(strategy_modules, base_time):
    m4 = make_market(strategy_modules.market_fetcher, id="m4", price_yes=0.95, price_no=0.05)
    seed_prices(strategy_modules.strategy, "m4_yes", [0.85, 0.87, 0.89, 0.91, 0.93, 0.95], base_time)

    sigs = strategy_modules.strategy.find_momentum_signals(
        [m4], lookback_hours=5, min_snapshots=3, min_move_pct=5.0
    )
    assert sigs == []


def test_momentum_insufficient_history(strategy_modules, base_time):
    m5 = make_market(strategy_modules.market_fetcher, id="m5", price_yes=0.60, price_no=0.40)
    seed_prices(strategy_modules.strategy, "m5_yes", [0.50, 0.60], base_time)

    sigs = strategy_modules.strategy.find_momentum_signals(
        [m5], lookback_hours=5, min_snapshots=3, min_move_pct=5.0
    )
    assert sigs == []


def test_arbitrage_orderbook_verification(strategy_modules):
    m6 = make_market(
        strategy_modules.market_fetcher,
        id="m6",
        price_yes=0.44,
        price_no=0.51,
        token_id_yes="m6_yes",
        token_id_no="m6_no",
    )

    sigs = strategy_modules.strategy.find_arbitrage_signals([m6], min_profit_pct=1.0)
    assert sigs, "Expected arb when YES_ask + NO_ask < 1.0"
    assert sigs[0].side == "ARB"
    assert sigs[0].edge_pct > 0
    assert sigs[0].confidence >= 0.9
    assert "ask" in sigs[0].reason.lower() or "0.4" in sigs[0].reason


def test_arbitrage_rejects_balanced_market(strategy_modules):
    m7 = make_market(strategy_modules.market_fetcher, id="m7", price_yes=0.55, price_no=0.50)
    sigs = strategy_modules.strategy.find_arbitrage_signals([m7], min_profit_pct=1.0)
    assert sigs == []


def test_mean_reversion_spike_up(strategy_modules, base_time):
    m8 = make_market(strategy_modules.market_fetcher, id="m8", price_yes=0.65, price_no=0.35)
    stable_prices = [0.50 + 0.005 * (i % 3 - 1) for i in range(15)]
    spike_prices = [0.55, 0.60, 0.65]
    seed_prices(strategy_modules.strategy, "m8_yes", stable_prices + spike_prices, base_time, start_hours_ago=13)

    sigs = strategy_modules.strategy.find_mean_reversion_signals(
        [m8], lookback_hours=14, min_snapshots=5, min_spike_pct=10.0, reversion_window_hours=3
    )
    assert sigs, "Expected mean reversion signal"
    assert sigs[0].side == "NO"
    assert 0.4 <= sigs[0].confidence <= 0.7
    assert "reversion" in sigs[0].reason


def test_mean_reversion_spike_down(strategy_modules, base_time):
    m9 = make_market(strategy_modules.market_fetcher, id="m9", price_yes=0.35, price_no=0.65)
    stable_prices = [0.50 + 0.005 * (i % 3 - 1) for i in range(15)]
    drop_prices = [0.45, 0.40, 0.35]
    seed_prices(strategy_modules.strategy, "m9_yes", stable_prices + drop_prices, base_time, start_hours_ago=13)

    sigs = strategy_modules.strategy.find_mean_reversion_signals(
        [m9], lookback_hours=14, min_snapshots=5, min_spike_pct=10.0, reversion_window_hours=3
    )
    assert sigs, "Expected mean reversion signal after drop"
    assert sigs[0].side == "YES"


def test_mean_reversion_no_spike(strategy_modules, base_time):
    m10 = make_market(strategy_modules.market_fetcher, id="m10", price_yes=0.51, price_no=0.49)
    seed_prices(
        strategy_modules.strategy,
        "m10_yes",
        [0.50, 0.51, 0.50, 0.49, 0.50, 0.51, 0.50, 0.51],
        base_time,
        start_hours_ago=13,
    )

    sigs = strategy_modules.strategy.find_mean_reversion_signals(
        [m10], lookback_hours=14, min_snapshots=5, min_spike_pct=10.0
    )
    assert sigs == []


def test_favorites_scoring_filtering(strategy_modules):
    m11a = make_market(strategy_modules.market_fetcher, id="m11a", price_yes=0.78, price_no=0.22, volume=500000)
    m11b = make_market(strategy_modules.market_fetcher, id="m11b", price_yes=0.75, price_no=0.25, volume=10000)
    m11c = make_market(strategy_modules.market_fetcher, id="m11c", price_yes=0.50, price_no=0.50, volume=500000)

    sigs = strategy_modules.strategy.find_favorite_signals([m11a, m11b, m11c], min_volume=100000)
    assert len(sigs) == 1
    assert sigs[0].market.id == "m11a"
    assert sigs[0].confidence < 0.6
    assert "NOT" in sigs[0].reason


def test_favorites_both_sides_eligible(strategy_modules):
    m12 = make_market(strategy_modules.market_fetcher, id="m12", price_yes=0.30, price_no=0.70, volume=200000)
    sigs = strategy_modules.strategy.find_favorite_signals([m12], min_volume=100000)
    assert any(sig.side == "NO" for sig in sigs)


def test_dispatcher_merge_dedup_rank(strategy_modules, base_time):
    m1 = make_market(strategy_modules.market_fetcher, id="m1", price_yes=0.55, price_no=0.45)
    seed_prices(strategy_modules.strategy, "m1_yes", [0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.55], base_time)
    m8 = make_market(strategy_modules.market_fetcher, id="m8", price_yes=0.65, price_no=0.35)
    stable_prices = [0.50 + 0.005 * (i % 3 - 1) for i in range(15)]
    spike_prices = [0.55, 0.60, 0.65]
    seed_prices(strategy_modules.strategy, "m8_yes", stable_prices + spike_prices, base_time, start_hours_ago=13)
    m11a = make_market(strategy_modules.market_fetcher, id="m11a", price_yes=0.78, price_no=0.22, volume=500000)

    all_sigs = strategy_modules.strategy.find_signals(
        [m1, m8, m11a],
        strategies=["momentum", "mean_reversion", "favorites"],
        min_edge_pct=3.0,
        max_results=10,
    )
    assert all_sigs, "Expected signals from multiple strategies"
    if len(all_sigs) >= 2:
        assert all_sigs[0].score >= all_sigs[1].score

    market_side_keys = [f"{sig.market.id}_{sig.side}" for sig in all_sigs]
    assert len(market_side_keys) == len(set(market_side_keys))
    assert len({sig.strategy for sig in all_sigs}) >= 2


def test_dispatcher_unknown_strategy(strategy_modules, base_time):
    m1 = make_market(strategy_modules.market_fetcher, id="m1", price_yes=0.55, price_no=0.45)
    seed_prices(strategy_modules.strategy, "m1_yes", [0.42, 0.44, 0.46, 0.48], base_time)
    sigs = strategy_modules.strategy.find_signals([m1], strategies=["nonexistent_strategy"], min_edge_pct=0)
    assert sigs == []


def test_dispatcher_min_edge_filter(strategy_modules, base_time):
    m1 = make_market(strategy_modules.market_fetcher, id="m1", price_yes=0.55, price_no=0.45)
    seed_prices(strategy_modules.strategy, "m1_yes", [0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.55], base_time)
    m11a = make_market(strategy_modules.market_fetcher, id="m11a", price_yes=0.78, price_no=0.22, volume=500000)

    strict = strategy_modules.strategy.find_signals(
        [m1, m11a], strategies=["momentum", "favorites"], min_edge_pct=50.0
    )
    loose = strategy_modules.strategy.find_signals(
        [m1, m11a], strategies=["momentum", "favorites"], min_edge_pct=1.0
    )
    assert len(strict) <= len(loose)


def test_signal_score_property(strategy_modules):
    sig = strategy_modules.strategy.Signal(
        market=make_market(strategy_modules.market_fetcher, id="m1"),
        side="YES",
        strategy="test",
        edge_pct=20.0,
        confidence=0.8,
        entry_price=0.50,
        reason="test",
    )
    assert abs(sig.score - 16.0) < 0.01


def test_value_sports_no_api_key(strategy_modules):
    m1 = make_market(strategy_modules.market_fetcher, id="m1", price_yes=0.55, price_no=0.45)
    sigs = strategy_modules.strategy.find_value_sports_signals([m1])
    assert sigs == []


def test_strategy_registry(strategy_modules):
    expected = {"momentum", "arbitrage", "value_sports", "mean_reversion", "favorites"}
    registry = set(strategy_modules.strategy.STRATEGY_REGISTRY.keys())
    assert expected.issubset(registry)


def test_autotrader_strategy_mapping(strategy_modules):
    import auto_trader as auto_trader_mod
    importlib.reload(auto_trader_mod)

    for member in auto_trader_mod.AutoStrategy:
        if member != auto_trader_mod.AutoStrategy.MIXED:
            assert member.value in strategy_modules.strategy.STRATEGY_REGISTRY or member.value == "value_sports"
