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

You can also launch the live AutoTrader via the CLI:
```bash
python main.py --mode trade
```

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

All previously identified issues are resolved. Summary of what changed:

### ✅ Priority 1: Real edge models (DONE)
- Added a pluggable `models/` framework (manual, odds API, momentum) for probability estimates.
- Value bets now choose the highest-confidence model per market and require edge > `min_edge%`.
- Momentum uses real price-history deltas; arbitrage uses the live CLOB orderbook.
- MIXED only combines arbitrage + model value + momentum; favorites/underdogs remain but warn they lack edge detection.

### ✅ Priority 2: Order lifecycle tracking (DONE)
- Added `order_tracker.py` to track orders through fills (including partial fills).
- Positions are only created on confirmed fills; pending orders are persisted in SQLite.
- `order_manager.py` and `auto_trader.py` now start/stop the tracker automatically.

### ✅ Priority 3: API rate limiting (DONE)
- Added a `RateLimitedClient` wrapper in `client_manager.py` for all API calls.
- Thread-safe global limit (default 10 rps, configurable via `API_RATE_LIMIT`).

### ✅ Priority 4: Proper logging (DONE)
- Introduced `bot_logging.py` with console + rotating file logs.
- Core modules now use `logging`; CLI-style files keep `print()` for readability.

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
