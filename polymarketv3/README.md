# 🤖 Polymarket Auto Trading Bot

A **fully automated** Python bot for trading on Polymarket prediction markets. Focused on **Sports** and **Crypto** categories.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🤖 **Full Automation** | Auto-picks bets, places trades, and sells automatically |
| ⏰ **Smart Timeframes** | Only picks markets ending in 1-7 days (configurable) |
| 🔄 **Arbitrage Detection** | Finds guaranteed profit opportunities |
| 📊 **Live Dashboard** | Web dashboard with charts, positions, logs |
| 💰 **Auto Take Profit** | Sells when you hit your target |
| 🛑 **Auto Stop Loss** | Sells to limit losses |
| 📉 **Trailing Stops** | Lock in profits as price rises |
| 💼 **Portfolio Tracking** | Track all positions and P&L |
| 💾 **Crash-Safe Persistence** | All state saved to SQLite — survives restarts |
| 🔌 **Shared Client Architecture** | Single connection pool, no redundant auth |

---

## 🚀 Quick Start

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env with your Polymarket private key
```

### 3. Run Auto Trader
```bash
python auto_trader.py
```

That's it! The bot runs fully automatically.

---

## 📁 Project Structure

```
polymarket-bot/
├── client_manager.py       # 🔌 Shared ClobClient instances (NEW)
├── persistence.py          # 💾 SQLite storage for all state (NEW)
├── models/                 # 🧠 Probability estimation models (NEW)
│   ├── base.py             #    Abstract base class + ProbabilityEstimate
│   ├── odds_api.py         #    Sports bookmaker consensus (the-odds-api.com)
│   ├── manual.py           #    User-supplied probability overrides
│   └── momentum.py         #    Real momentum detection from price history
├── auto_trader.py          # 🤖 Fully automated trading bot
├── order_tracker.py        # 📋 Order fill tracking (LIVE → FILLED) (NEW)
├── bot_logging.py          # 📝 Logging configuration (NEW)
├── easy_trade.py           # Interactive trading mode
├── order_manager.py        # TP/SL/Trailing stop logic
├── trader.py               # Core trading functions
├── dashboard.py            # Streamlit web dashboard
├── polymarket-dashboard.jsx # React dashboard preview
├── market_fetcher.py       # Fetch markets from Gamma API
├── arbitrage.py            # Arbitrage detection
├── portfolio.py            # Position tracking (with persistence)
├── odds_tracker.py         # Price monitoring (with persistence)
├── config.py               # Settings & environment config
├── main.py                 # CLI entry point
├── bot_data.db             # SQLite database (auto-created at runtime)
└── .env.example            # Config template
```

---

## 🏗️ Architecture

### Client Manager (`client_manager.py`)

All modules share a single pair of API clients instead of each creating their own:

```
┌─────────────────────────────────────────────────┐
│              ClientManager (singleton)           │
│                                                  │
│   clients.read   → Unauthenticated ClobClient   │
│                    (orderbook, prices, midpoints) │
│                                                  │
│   clients.auth   → Authenticated ClobClient      │
│                    (place/cancel orders)          │
│                                                  │
│   Used by: trader, portfolio, odds_tracker,      │
│            arbitrage, order_manager              │
└─────────────────────────────────────────────────┘
```

**Why this matters:** Previously, 6 separate `ClobClient` instances each called `create_or_derive_api_creds()` independently. This was slow, risked rate limits, and meant inconsistent auth state. Now there's one read client and one auth client, initialized once, shared everywhere.

### Persistence Layer (`persistence.py`)

All state auto-saves to a local SQLite database:

```
┌──────────────────────────────────────────────────┐
│               SQLite Database                     │
│                                                   │
│   positions        → Open positions + prices      │
│   trades           → Full trade history           │
│   price_snapshots  → Historical price data        │
│   auto_orders      → TP/SL/trailing stop state    │
│   bot_state        → Key-value store (anything)   │
└──────────────────────────────────────────────────┘
```

**Why this matters:** Previously, a crash lost everything — open positions, trade history, P&L. Now the bot can restart and pick up exactly where it left off. Trade history accumulates across sessions for real performance analysis.

---

## 🎮 How It Works

```
┌─────────────────────────────────────────────────────────┐
│  1. 🔍 SCAN     - Scans Polymarket every 5 minutes      │
│  2. ⏰ FILTER   - Only markets ending in 1-7 days       │
│  3. 🎯 PICK     - Finds best opportunities              │
│  4. 💰 BET      - Places bets automatically             │
│  5. 💾 SAVE     - Persists state to SQLite              │
│  6. 📊 MONITOR  - Watches prices 24/7                   │
│  7. 💵 SELL     - Auto-sells at TP/SL targets           │
│  8. 🔄 REPEAT   - Loops forever until you stop          │
└─────────────────────────────────────────────────────────┘
```

---

## ⏰ Time-Based Filtering

| Category | Same-Day | Max Days | Why |
|----------|----------|----------|-----|
| 🏀 **Sports** | ✅ YES | 3 days | Tonight's games are BEST! |
| 💰 **Crypto** | ✅ YES | 7 days | 24/7 volatility |

---

## 📈 Trading Strategies

The dashboard currently ships with these strategy presets:

| Strategy | Take Profit % | Stop Loss % | Trailing Stop % |
|----------|---------------|-------------|-----------------|
| 🛡️ **Conservative** | 15 | 5 | — |
| ⚖️ **Balanced** | 25 | 10 | — |
| 🔥 **Aggressive** | 40 | 20 | — |
| 📈 **Trailing Stop** | — | — | 10 |
| 🎯 **Custom** | user-defined | user-defined | user-defined |

---

## 📊 Dashboard

```bash
streamlit run dashboard.py
```

---

## ⚙️ Configuration

Edit `.env` file:

```env
# Your Polymarket wallet private key
PRIVATE_KEY=your_private_key_here

# Your proxy wallet address
FUNDER_ADDRESS=0xYourAddress

# Trading limits
MAX_TRADE_SIZE=5
MAX_TOTAL_EXPOSURE=25
```

Optional: override the database path:
```env
BOT_DB_PATH=/path/to/custom/bot_data.db
```

Recommended first-run safety caps:
```env
MAX_DAILY_LOSS_USD=25
MAX_DRAWDOWN_PCT=15
```

---

## 🤝 Getting Your Keys

1. Go to [polymarket.com](https://polymarket.com)
2. Click **Settings** → **Export Private Key**
3. Copy your funder wallet address
4. Add both to `.env` file

---

## ✅ Completed Improvements

All identified critical issues have been addressed. Here's what was done:

---

### ✅ Priority 1: Replace Placeholder Strategies with Real Edge Models — DONE

**Status:** Completed. All strategy methods now use real probability models instead of flawed heuristics.

**What changed:**

- **New `models/` directory** with a pluggable probability model framework:
  - `models/base.py` — `ProbabilityModel` abstract base class and `ProbabilityEstimate` dataclass with edge/EV calculations
  - `models/odds_api.py` — `OddsApiModel` fetches bookmaker consensus from [the-odds-api.com](https://the-odds-api.com) and compares against Polymarket prices for sports markets
  - `models/manual.py` — `ManualModel` for user-supplied probability estimates (from code or JSON file)
  - `models/momentum.py` — `MomentumModel` detects real price trends from stored price history via `db.get_price_history()`

- **`find_value_bets()`** now queries all active models, takes the highest-confidence estimate per market, and only bets when model edge > `min_edge%`. No more "high volume = underpriced" nonsense.

- **`find_momentum_bets()`** now uses actual price deltas over configurable time windows with consistency checks (>60% of sub-intervals must agree on direction). Requires price snapshots to accumulate.

- **`find_arbitrage_bets()`** now calls `ArbitrageDetector.check_market()` which queries the live CLOB orderbook (best ask prices), not stale Gamma API prices.

- **`MIXED` strategy** now only combines arbitrage + model-backed value + momentum (no more favorites/underdogs which lack edge detection).

- **`favorites`/`underdogs`** strategies kept but carry docstring warnings that they do NOT detect real edge.

**How to use the models:**

```python
# Option 1: Supply your own probabilities
from models import ManualModel
manual = ManualModel()
manual.set_estimate("market-slug", fair_yes=0.72, reason="My ELO model says 72%")
bot = AutoTrader(config=my_config, models=[manual])

# Option 2: Load from JSON file (set MANUAL_ESTIMATES_FILE in .env)
# File format: {"estimates": {"market-slug": {"fair_yes": 0.72, "reason": "..."}}}

# Option 3: Use sports bookmaker consensus (set ODDS_API_KEY in .env)
# The OddsApiModel loads automatically when the key is present

# Option 4: Momentum (always active, improves as price history accumulates)
# Just run the bot — odds_tracker saves snapshots → momentum model reads them
```

---

### ✅ Priority 2: Fix Order Lifecycle Tracking — DONE

**Status:** Completed. Orders are now tracked from placement through fill confirmation. Positions are only created on confirmed fills.

**What changed:**

- **New `order_tracker.py`** module with `OrderTracker` class:
  - Tracks orders from `LIVE` → `PARTIALLY_FILLED` → `MATCHED` (or `CANCELLED`/`EXPIRED`)
  - Polls `client.get_order(order_id)` in a background thread to detect fills
  - Fires `on_fill` callback with actual fill price and size — this is what updates the portfolio
  - Handles partial fills incrementally (each fill chunk updates the position separately)
  - Persists tracked orders to `pending_orders` table in SQLite → survives bot restarts
  - Stale order timeout (default 30 min) auto-cancels tracking for unfilled orders

- **Updated `trader.py`**: `buy()` no longer calls `portfolio.add_position()` immediately. It returns the order ID, and the caller is responsible for tracking it.

- **Updated `order_manager.py`**:
  - Creates `OrderTracker` with fill/cancel callbacks wired to `portfolio.add_position()`
  - `buy()` method now calls `order_tracker.track_order()` instead of adding phantom positions
  - `start_monitoring()` and `stop_monitoring()` also start/stop the fill tracker
  - Status display shows pending fill count

- **Updated `auto_trader.py`**: `run()` starts and stops the order tracker automatically.

- **Updated `persistence.py`**: New `pending_orders` table with full lifecycle state tracking.

**The old flow (broken):**
```
post_order() success → portfolio.add_position(limit_price)  ← WRONG: assumes instant fill
```

**The new flow (correct):**
```
post_order() success → order_tracker.track(order_id)
                          ↓ (background polling)
                       get_order(order_id) → status check
                          ↓ (on confirmed fill)
                       on_fill callback → portfolio.add_position(actual_fill_price)
```

---

### ✅ Priority 3: Add Rate Limiting on API Calls — DONE

**Status:** Completed. All API calls are now globally rate-limited via a transparent proxy in `client_manager.py`.

**What changed:**

- **`RateLimitedClient`** wrapper in `client_manager.py` intercepts all API methods (`get_order_book`, `get_midpoint`, `post_order`, etc.) and enforces a minimum interval between calls. Non-API attribute access passes through instantly.
- Both `clients.read` and `clients.auth` are automatically wrapped — every module in the codebase gets rate limiting for free with **zero code changes** to consuming modules.
- Thread-safe: uses a lock to ensure the global rate limit is respected even when multiple threads (order tracker, TP/SL monitor, arbitrage scanner) make concurrent calls.
- Default: 10 requests/second. Override via `API_RATE_LIMIT` env var.
- Call counter available via `clients.read.api_call_count` for diagnostics.

---

### ✅ Priority 4: Replace print() with Proper Logging — DONE

**Status:** Completed. All core modules now use Python's `logging` module with file + console output.

**What changed:**

- **New `bot_logging.py`** module:
  - `setup_logging()` configures root logger with console (INFO+) and rotating file handler (DEBUG+)
  - File handler: `bot.log`, 10MB max with 3 backups (all configurable via env vars)
  - Console: `HH:MM:SS [LEVEL] module: message` format
  - File: full timestamp + filename:lineno for debugging
  - Idempotent — safe to call multiple times

- **All 10 core modules converted** from `print()` to `logger.info/warning/error()`:
  - `client_manager.py`, `persistence.py`, `arbitrage.py`, `odds_tracker.py`
  - `order_tracker.py`, `order_manager.py`, `trader.py`, `portfolio.py`
  - `auto_trader.py`, `market_fetcher.py`
  - Each module uses `logger = logging.getLogger(__name__)` for per-module filtering

- **Entry points** (`main.py`, `auto_trader.py` `__main__`) call `setup_logging()` at startup

- **Configurable via `.env`:**
  - `BOT_LOG_FILE` — log file path (default: `bot.log`, empty to disable)
  - `BOT_LOG_LEVEL` — console verbosity (DEBUG/INFO/WARNING/ERROR)
  - `BOT_LOG_MAX_MB` — max file size before rotation (default: 10)
  - `BOT_LOG_BACKUPS` — rotated files to keep (default: 3)

- **CLI/interactive files** (`easy_trade.py`, `main.py`, `dashboard.py`) kept `print()` for user-facing menu output where logging formatting would hurt readability.

---

## 📚 API Reference

| API | URL | Purpose |
|-----|-----|---------|
| Gamma | `gamma-api.polymarket.com` | Market data |
| CLOB | `clob.polymarket.com` | Trading |
| WebSocket | `ws-subscriptions-clob.polymarket.com` | Real-time |

---

## ⚖️ Disclaimer

This bot is for **educational purposes**. Trading prediction markets involves risk. You can lose your entire investment. The current strategy logic is a **starting point** — you should develop and validate your own edge model before trading with real money. Always start with small amounts.

---

Made with ❤️ for the Polymarket community


## LIVE SAFETY (REAL MONEY)

This project is configured for **LIVE** Polymarket CLOB trading (no paper mode).  
You must set `PRIVATE_KEY` and `FUNDER_ADDRESS` in `.env`.

### Recommended safety env vars

- `KILL_SWITCH=1` — blocks **new BUY entries** (SELL still allowed)
- `MAX_SPREAD_BPS=150` — skips entries if best bid/ask spread is too wide
- `ORDER_TTL_SECONDS=120` — cancels stale LIVE orders (best-effort)
- `CANCEL_ALL_ON_STARTUP=1` — cancels *all* open orders when bot starts
- Circuit breakers:
  - `MAX_DAILY_LOSS_USD=50` (0 disables)
  - `MAX_DRAWDOWN_PCT=10` (0 disables)
  - `MAX_CONSECUTIVE_ERRORS=10`

Copy `.env.example` → `.env` and edit.

### What the kill switch does

- **Blocks NEW entries** (no new BUY orders)
- Continues:
  - tracking fills
  - printing dashboard/state
  - allowing SELL exits (manual or TP/SL)
