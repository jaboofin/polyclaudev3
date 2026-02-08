"""
Polymarket Dashboard - Full Trading Interface

Features:
- 3 Trading Modes (Interactive, Simple, Advanced)
- Strategy Presets (Conservative, Balanced, Aggressive, Trailing)
- Live market data
- Portfolio tracking
- Arbitrage scanner
"""

import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import time
import json

# Page config
st.set_page_config(
    page_title="Polymarket Bot Dashboard",
    page_icon="🎰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Constants
GAMMA_API = "https://gamma-api.polymarket.com"

# Strategy Presets
STRATEGIES = {
    "🛡️ Conservative": {
        "description": "Low risk, small gains, tight stop loss",
        "take_profit": 30,
        "stop_loss": 10,
        "trailing_stop": None,
        "risk_level": "Low",
        "color": "green"
    },
    "⚖️ Balanced": {
        "description": "Medium risk, good risk/reward ratio",
        "take_profit": 50,
        "stop_loss": 25,
        "trailing_stop": None,
        "risk_level": "Medium",
        "color": "blue"
    },
    "🔥 Aggressive": {
        "description": "High risk, big gains or big losses",
        "take_profit": 100,
        "stop_loss": 50,
        "trailing_stop": None,
        "risk_level": "High",
        "color": "red"
    },
    "📈 Trailing Stop": {
        "description": "Lock in profits as price rises",
        "take_profit": None,
        "stop_loss": None,
        "trailing_stop": 20,
        "risk_level": "Medium",
        "color": "purple"
    },
    "🎯 Custom": {
        "description": "Set your own parameters",
        "take_profit": 50,
        "stop_loss": 25,
        "trailing_stop": None,
        "risk_level": "Custom",
        "color": "gray"
    }
}

# Code Examples for 3 Options
CODE_EXAMPLES = {
    "🟢 Interactive (Easiest)": '''# Just run this in terminal:
python easy_trade.py

# Then type commands:
>>> crypto                    # See crypto markets
>>> find bitcoin              # Search markets
>>> buy 1 50                  # Buy $50 of market #1
>>> positions                 # See your bets
>>> start                     # Turn on auto TP/SL
''',
    
    "🟡 Simple Script": '''from easy_trade import EasyTrader

trader = EasyTrader()

# Find markets
markets = trader.find_markets("bitcoin")

# Buy with automatic protection
trader.buy(
    market=markets[0],
    amount=50,                # Bet $50
    take_profit_percent=50,   # Auto-sell if up 50%
    stop_loss_percent=25      # Auto-sell if down 25%
)

# Bot watches and sells for you
trader.start_monitoring()
''',
    
    "🔴 Full Control": '''from order_manager import OrderManager

manager = OrderManager()

# Buy with all options
manager.buy_with_tp_sl(
    token_id="your_token_id",
    market_question="Will BTC hit 100k?",
    size=100,
    entry_price=0.45,
    take_profit=0.70,           # Sell at 70¢
    stop_loss=0.30,             # Stop at 30¢
    trailing_stop_percent=0.15  # 15% trailing stop
)

# Start monitoring
manager.start_monitoring()
'''
}


@st.cache_data(ttl=60)
def fetch_markets(limit=50):
    """Fetch markets from Polymarket API."""
    try:
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume",
            "ascending": "false"
        }
        
        response = requests.get(f"{GAMMA_API}/events", params=params, timeout=10)
        events = response.json()
        
        markets = []
        for event in events:
            for market in event.get("markets", []):
                try:
                    prices = eval(market.get("outcomePrices", "[]"))
                    outcomes = eval(market.get("outcomes", '["Yes", "No"]'))
                    token_ids = market.get("clobTokenIds", [])
                    tags = event.get("tags", [{}])
                    category = tags[0].get("label", "Other") if tags else "Other"
                    
                    if len(token_ids) >= 2 and len(prices) >= 2:
                        markets.append({
                            "id": market.get("id"),
                            "question": market.get("question", ""),
                            "category": category,
                            "price_yes": float(prices[0]),
                            "price_no": float(prices[1]),
                            "volume": float(market.get("volume") or 0),
                            "liquidity": float(market.get("liquidity") or 0),
                            "token_id_yes": token_ids[0],
                            "token_id_no": token_ids[1],
                        })
                except Exception:
                    continue
        
        return markets
    except Exception as e:
        st.error(f"Error fetching markets: {e}")
        return []


def calculate_trade_preview(entry_price, amount, strategy):
    """Calculate trade preview based on strategy."""
    size = amount / entry_price
    
    results = {
        "size": size,
        "entry_price": entry_price,
        "cost": amount,
    }
    
    if strategy["take_profit"]:
        tp_price = entry_price * (1 + strategy["take_profit"] / 100)
        tp_price = min(tp_price, 0.99)
        tp_profit = (tp_price - entry_price) * size
        results["tp_price"] = tp_price
        results["tp_profit"] = tp_profit
    
    if strategy["stop_loss"]:
        sl_price = entry_price * (1 - strategy["stop_loss"] / 100)
        sl_price = max(sl_price, 0.01)
        sl_loss = (entry_price - sl_price) * size
        results["sl_price"] = sl_price
        results["sl_loss"] = sl_loss
    
    if strategy["trailing_stop"]:
        trail_price = entry_price * (1 - strategy["trailing_stop"] / 100)
        results["trail_percent"] = strategy["trailing_stop"]
        results["trail_price"] = trail_price
    
    return results


def main():
    # ==================== SIDEBAR ====================
    with st.sidebar:
        st.title("🎰 Polymarket Bot")
        st.caption("Sports & Crypto Trading")
        
        st.markdown("---")
        
        # Mode Selection
        st.subheader("📱 Dashboard Mode")
        mode = st.radio(
            "Select view:",
            ["🏠 Home", "💹 Trade", "📊 Markets", "🔍 Arbitrage", "💼 Portfolio", "📖 How to Use"],
            label_visibility="collapsed"
        )
        
        st.markdown("---")
        
        # Quick Stats
        markets = fetch_markets()
        crypto_count = len([m for m in markets if "crypto" in m["category"].lower()])
        sports_keywords = ["nba", "nfl", "mlb", "nhl", "soccer", "sport"]
        sports_count = len([m for m in markets if any(k in m["category"].lower() for k in sports_keywords)])
        
        st.metric("Total Markets", len(markets))
        col1, col2 = st.columns(2)
        col1.metric("🪙 Crypto", crypto_count)
        col2.metric("🏀 Sports", sports_count)
        
        st.markdown("---")
        
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()
        
        st.caption(f"Updated: {datetime.now().strftime('%H:%M:%S')}")
    
    # ==================== MAIN CONTENT ====================
    
    markets = fetch_markets()
    sports_keywords = ["nba", "nfl", "mlb", "nhl", "soccer", "sport"]
    
    # ---------- HOME ----------
    if mode == "🏠 Home":
        st.title("🎰 Polymarket Trading Dashboard")
        st.caption("Automated trading for Sports & Crypto prediction markets")
        
        # Quick Stats Row
        col1, col2, col3, col4 = st.columns(4)
        
        total_volume = sum(m["volume"] for m in markets)
        arb_count = len([m for m in markets if m["price_yes"] + m["price_no"] < 0.99])
        
        col1.metric("📈 Markets", len(markets))
        col2.metric("💰 Volume (24h)", f"${total_volume/1e6:.1f}M")
        col3.metric("⚡ Arb Opps", arb_count)
        col4.metric("🎯 Avg Price", f"{sum(m['price_yes'] for m in markets)/len(markets)*100:.0f}¢" if markets else "N/A")
        
        st.markdown("---")
        
        # Top Markets
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("🪙 Top Crypto Markets")
            crypto = [m for m in markets if "crypto" in m["category"].lower()][:5]
            for m in crypto:
                with st.container():
                    st.markdown(f"**{m['question'][:50]}...**")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("YES", f"{m['price_yes']*100:.0f}¢", label_visibility="collapsed")
                    c2.metric("NO", f"{m['price_no']*100:.0f}¢", label_visibility="collapsed")
                    c3.caption(f"Vol: ${m['volume']:,.0f}")
                    st.markdown("---")
        
        with col2:
            st.subheader("🏀 Top Sports Markets")
            sports = [m for m in markets if any(k in m["category"].lower() for k in sports_keywords)][:5]
            for m in sports:
                with st.container():
                    st.markdown(f"**{m['question'][:50]}...**")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("YES", f"{m['price_yes']*100:.0f}¢", label_visibility="collapsed")
                    c2.metric("NO", f"{m['price_no']*100:.0f}¢", label_visibility="collapsed")
                    c3.caption(f"Vol: ${m['volume']:,.0f}")
                    st.markdown("---")
    
    # ---------- TRADE ----------
    elif mode == "💹 Trade":
        st.title("💹 Trade")
        
        # Strategy Selection
        st.subheader("1️⃣ Choose Strategy")
        
        strategy_cols = st.columns(5)
        selected_strategy = None
        
        for i, (name, strat) in enumerate(STRATEGIES.items()):
            with strategy_cols[i]:
                if st.button(name, use_container_width=True):
                    st.session_state.selected_strategy = name
        
        # Get selected strategy
        if "selected_strategy" not in st.session_state:
            st.session_state.selected_strategy = "⚖️ Balanced"
        
        selected_strategy = st.session_state.selected_strategy
        strategy = STRATEGIES[selected_strategy].copy()
        
        # Show strategy details
        st.info(f"**{selected_strategy}**: {strategy['description']} | Risk: {strategy['risk_level']}")
        
        # Custom parameters if Custom selected
        if selected_strategy == "🎯 Custom":
            col1, col2, col3 = st.columns(3)
            with col1:
                custom_tp = st.number_input("Take Profit %", 0, 200, 50)
                strategy["take_profit"] = custom_tp
            with col2:
                custom_sl = st.number_input("Stop Loss %", 0, 100, 25)
                strategy["stop_loss"] = custom_sl
            with col3:
                custom_trail = st.number_input("Trailing Stop %", 0, 50, 0)
                strategy["trailing_stop"] = custom_trail if custom_trail > 0 else None
        
        st.markdown("---")
        
        # Market Selection
        st.subheader("2️⃣ Select Market")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            search = st.text_input("🔍 Search markets", placeholder="bitcoin, lakers, etc.")
        with col2:
            category_filter = st.selectbox("Category", ["All", "Crypto", "Sports"])
        
        # Filter markets
        filtered = markets
        if search:
            filtered = [m for m in filtered if search.lower() in m["question"].lower()]
        if category_filter == "Crypto":
            filtered = [m for m in filtered if "crypto" in m["category"].lower()]
        elif category_filter == "Sports":
            filtered = [m for m in filtered if any(k in m["category"].lower() for k in sports_keywords)]
        
        # Market selector
        if filtered:
            market_options = {f"{m['question'][:60]}... ({m['price_yes']*100:.0f}¢)": m for m in filtered[:20]}
            selected_market_name = st.selectbox("Select market:", list(market_options.keys()))
            selected_market = market_options[selected_market_name]
            
            # Show market details
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("YES Price", f"{selected_market['price_yes']*100:.1f}¢")
            col2.metric("NO Price", f"{selected_market['price_no']*100:.1f}¢")
            col3.metric("Volume", f"${selected_market['volume']:,.0f}")
            col4.metric("Liquidity", f"${selected_market['liquidity']:,.0f}")
        else:
            st.warning("No markets found")
            selected_market = None
        
        st.markdown("---")
        
        # Trade Parameters
        if selected_market:
            st.subheader("3️⃣ Place Order")
            
            col1, col2 = st.columns(2)
            
            with col1:
                side = st.radio("Side", ["YES", "NO"], horizontal=True)
                amount = st.number_input("Amount ($)", min_value=1.0, max_value=1000.0, value=50.0, step=10.0)
            
            with col2:
                entry_price = selected_market["price_yes"] if side == "YES" else selected_market["price_no"]
                preview = calculate_trade_preview(entry_price, amount, strategy)
                
                st.markdown("**Order Preview:**")
                st.write(f"• Size: **{preview['size']:.2f}** shares")
                st.write(f"• Entry: **{entry_price*100:.1f}¢**")
                st.write(f"• Cost: **${preview['cost']:.2f}**")
                
                if "tp_price" in preview:
                    st.write(f"• Take Profit: **{preview['tp_price']*100:.1f}¢** (+${preview['tp_profit']:.2f})")
                if "sl_price" in preview:
                    st.write(f"• Stop Loss: **{preview['sl_price']*100:.1f}¢** (-${preview['sl_loss']:.2f})")
                if "trail_percent" in preview:
                    st.write(f"• Trailing Stop: **{preview['trail_percent']}%** (currently {preview['trail_price']*100:.1f}¢)")
            
            st.markdown("---")
            
            # Generate Code
            st.subheader("4️⃣ Execute")
            
            # Generate the code for this trade
            code = f'''from easy_trade import EasyTrader

trader = EasyTrader()
markets = trader.find_markets("{selected_market['question'][:30]}")

trader.buy(
    market=markets[0],
    amount={amount},
    side="{side}",'''
            
            if strategy["take_profit"]:
                code += f'''
    take_profit_percent={strategy["take_profit"]},'''
            if strategy["stop_loss"]:
                code += f'''
    stop_loss_percent={strategy["stop_loss"]},'''
            if strategy["trailing_stop"]:
                code += f'''
    trailing_stop_percent={strategy["trailing_stop"]},'''
            
            code += '''
)

trader.start_monitoring()'''
            
            st.code(code, language="python")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("📋 Copy Code", use_container_width=True):
                    st.success("Code copied! Paste in your Python file.")
            with col2:
                if st.button("🚀 Execute Trade", use_container_width=True, type="primary"):
                    st.warning("⚠️ Connect your wallet in .env to execute trades")
    
    # ---------- MARKETS ----------
    elif mode == "📊 Markets":
        st.title("📊 All Markets")
        
        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            search = st.text_input("Search", placeholder="Search markets...")
        with col2:
            cat_filter = st.selectbox("Category", ["All", "Crypto", "Sports", "Other"])
        with col3:
            sort_by = st.selectbox("Sort by", ["Volume", "Price (High)", "Price (Low)", "Liquidity"])
        
        # Filter
        filtered = markets
        if search:
            filtered = [m for m in filtered if search.lower() in m["question"].lower()]
        if cat_filter == "Crypto":
            filtered = [m for m in filtered if "crypto" in m["category"].lower()]
        elif cat_filter == "Sports":
            filtered = [m for m in filtered if any(k in m["category"].lower() for k in sports_keywords)]
        
        # Sort
        if sort_by == "Volume":
            filtered.sort(key=lambda x: x["volume"], reverse=True)
        elif sort_by == "Price (High)":
            filtered.sort(key=lambda x: x["price_yes"], reverse=True)
        elif sort_by == "Price (Low)":
            filtered.sort(key=lambda x: x["price_yes"])
        elif sort_by == "Liquidity":
            filtered.sort(key=lambda x: x["liquidity"], reverse=True)
        
        # Display
        st.caption(f"Showing {len(filtered)} markets")
        
        for m in filtered[:30]:
            with st.container():
                col1, col2, col3, col4 = st.columns([4, 1, 1, 1])
                with col1:
                    st.markdown(f"**{m['question'][:70]}**")
                    st.caption(f"Category: {m['category']}")
                with col2:
                    st.metric("YES", f"{m['price_yes']*100:.0f}¢")
                with col3:
                    st.metric("NO", f"{m['price_no']*100:.0f}¢")
                with col4:
                    st.caption(f"Vol: ${m['volume']:,.0f}")
                st.markdown("---")
    
    # ---------- ARBITRAGE ----------
    elif mode == "🔍 Arbitrage":
        st.title("🔍 Arbitrage Finder")
        st.caption("Find markets where YES + NO < $1.00 for guaranteed profit")
        
        # Find opportunities
        opportunities = []
        for m in markets:
            combined = m["price_yes"] + m["price_no"]
            if combined < 0.99:
                profit = 1 - combined
                opportunities.append({
                    **m,
                    "combined": combined,
                    "profit_pct": profit * 100,
                    "profit_per_100": profit * 100
                })
        
        opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
        
        if opportunities:
            st.success(f"Found {len(opportunities)} arbitrage opportunities!")
            
            for opp in opportunities[:10]:
                with st.expander(f"💰 +{opp['profit_pct']:.2f}% - {opp['question'][:50]}..."):
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("YES", f"{opp['price_yes']*100:.1f}¢")
                    col2.metric("NO", f"{opp['price_no']*100:.1f}¢")
                    col3.metric("Combined", f"{opp['combined']*100:.1f}¢")
                    col4.metric("Profit/$100", f"+${opp['profit_per_100']:.2f}")
                    
                    st.info(f"💡 **Strategy:** Buy {50/opp['price_yes']:.0f} YES @ {opp['price_yes']*100:.1f}¢ + {50/opp['price_no']:.0f} NO @ {opp['price_no']*100:.1f}¢ = Guaranteed ${opp['profit_per_100']:.2f} profit per $100")
        else:
            st.info("No arbitrage opportunities found right now. Check back later!")
    
    # ---------- PORTFOLIO ----------
    elif mode == "💼 Portfolio":
        st.title("💼 Portfolio")
        
        st.info("📌 This is a demo portfolio. Connect your wallet to see real positions.")
        
        # Demo positions
        positions = [
            {"market": "Bitcoin $100k by 2025?", "side": "YES", "size": 100, "entry": 0.45, "current": 0.52},
            {"market": "Lakers NBA Finals?", "side": "YES", "size": 50, "entry": 0.15, "current": 0.22},
            {"market": "ETH above $5k?", "side": "NO", "size": 75, "entry": 0.60, "current": 0.55},
        ]
        
        # Calculate totals
        total_value = sum(p["size"] * p["current"] for p in positions)
        total_cost = sum(p["size"] * p["entry"] for p in positions)
        total_pnl = total_value - total_cost
        
        # Summary
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Value", f"${total_value:.2f}")
        col2.metric("Total Cost", f"${total_cost:.2f}")
        col3.metric("Unrealized P&L", f"${total_pnl:.2f}", f"{total_pnl/total_cost*100:.1f}%")
        col4.metric("Positions", len(positions))
        
        st.markdown("---")
        
        # Positions table
        for pos in positions:
            pnl = (pos["current"] - pos["entry"]) * pos["size"]
            pnl_pct = (pos["current"] - pos["entry"]) / pos["entry"] * 100
            
            with st.container():
                col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 1, 1])
                col1.markdown(f"**{pos['market']}**")
                col2.write(f"{pos['side']} x{pos['size']}")
                col3.write(f"Entry: {pos['entry']*100:.0f}¢")
                col4.write(f"Now: {pos['current']*100:.0f}¢")
                col5.markdown(f"**{'🟢' if pnl >= 0 else '🔴'} ${pnl:.2f}** ({pnl_pct:+.1f}%)")
                st.markdown("---")
    
    # ---------- HOW TO USE ----------
    elif mode == "📖 How to Use":
        st.title("📖 How to Use This Bot")
        
        st.markdown("---")
        
        # 3 Options Toggle
        st.subheader("🎮 Choose Your Mode")
        
        option = st.radio(
            "Select how you want to use the bot:",
            list(CODE_EXAMPLES.keys()),
            horizontal=True
        )
        
        # Show description
        if option == "🟢 Interactive (Easiest)":
            st.success("**Best for:** Total beginners. Just type commands!")
        elif option == "🟡 Simple Script":
            st.info("**Best for:** Set it and forget it. Write a few lines of Python.")
        else:
            st.warning("**Best for:** Advanced users. Full customization.")
        
        # Show code
        st.code(CODE_EXAMPLES[option], language="python")
        
        st.markdown("---")
        
        # Strategy Comparison
        st.subheader("📈 Strategy Comparison")
        
        strategy_data = []
        for name, strat in STRATEGIES.items():
            if name != "🎯 Custom":
                strategy_data.append({
                    "Strategy": name,
                    "Take Profit": f"+{strat['take_profit']}%" if strat['take_profit'] else "-",
                    "Stop Loss": f"-{strat['stop_loss']}%" if strat['stop_loss'] else "-",
                    "Trailing Stop": f"{strat['trailing_stop']}%" if strat['trailing_stop'] else "-",
                    "Risk Level": strat['risk_level'],
                    "Description": strat['description']
                })
        
        df = pd.DataFrame(strategy_data)
        st.table(df)
        
        st.markdown("---")
        
        # Quick Start
        st.subheader("🚀 Quick Start")
        
        st.markdown("""
        1. **Install:** `pip install -r requirements.txt`
        2. **Configure:** Copy `.env.example` to `.env` and add your keys
        3. **Run:** `python easy_trade.py` or `streamlit run dashboard.py`
        4. **Trade:** Find a market, pick a strategy, execute!
        """)
        
        st.markdown("---")
        
        # Order Types
        st.subheader("💡 Order Types Explained")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("""
            **📈 Take Profit**
            - Automatically sells when price goes UP
            - Locks in your gains
            - Example: Buy at 50¢, TP at 70¢ = +40% profit
            
            **🛑 Stop Loss**
            - Automatically sells when price goes DOWN
            - Limits your losses
            - Example: Buy at 50¢, SL at 35¢ = -30% max loss
            """)
        
        with col2:
            st.markdown("""
            **📉 Trailing Stop**
            - Stop price moves UP with the market
            - Locks in profits as price rises
            - Example: 15% trail - if price hits 80¢, stop moves to 68¢
            
            **🔗 OCO (One-Cancels-Other)**
            - When Take Profit hits, Stop Loss cancels
            - And vice versa
            - Prevents double-execution
            """)


if __name__ == "__main__":
    main()
