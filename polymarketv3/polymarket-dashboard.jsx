import React, { useState, useEffect, useRef } from 'react';
import { Settings, HelpCircle, Wallet, TrendingUp, TrendingDown, DollarSign, Zap, Search, Play, Square, Activity, CheckCircle, XCircle, List, BarChart3 } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

const STRATEGIES = {
  "conservative": { name: "Conservative", emoji: "🛡️", tp: 25, sl: 10, maxBet: 15, desc: "Safe, tight stops" },
  "balanced": { name: "Balanced", emoji: "⚖️", tp: 35, sl: 15, maxBet: 20, desc: "Mixed strategies" },
  "aggressive": { name: "Aggressive", emoji: "🔥", tp: 75, sl: 25, maxBet: 25, desc: "Underdogs, high risk" },
  "scalper": { name: "Scalper", emoji: "⚡", tp: 15, sl: 8, maxBet: 25, desc: "Quick same-day" },
  "sports": { name: "Sports", emoji: "🏀", tp: 30, sl: 15, maxBet: 20, desc: "Tonight's games" },
};

const CODE_EXAMPLES = {
  interactive: `python auto_trader.py
Enter bankroll [$50]: 50
Select risk level: balanced
🚀 Bot running...`,
  simple: `from auto_trader import start_auto_trader
start_auto_trader(bankroll=50, risk_level="balanced")`,
  advanced: `from auto_trader import AutoTrader, AutoTradeConfig
config = AutoTradeConfig(bankroll=50, take_profit_percent=35)
bot = AutoTrader(config=config)
bot.run()`
};

const DEMO_MARKETS = [
  { id: 1, question: "Lakers vs Celtics - Lakers win tonight?", category: "Sports", priceYes: 0.45, priceNo: 0.55, volume: 850000, change24h: 3.2, endsIn: "4h" },
  { id: 2, question: "Will Bitcoin reach $100,000 by end of 2025?", category: "Crypto", priceYes: 0.62, priceNo: 0.38, volume: 2500000, change24h: 5.2, endsIn: "3d" },
  { id: 3, question: "Chiefs vs Ravens - Chiefs win Sunday?", category: "Sports", priceYes: 0.58, priceNo: 0.42, volume: 1200000, change24h: -1.5, endsIn: "2d" },
  { id: 4, question: "Will Ethereum hit $5,000 by Friday?", category: "Crypto", priceYes: 0.45, priceNo: 0.55, volume: 1800000, change24h: -2.1, endsIn: "3d" },
  { id: 5, question: "Warriors vs Suns - Warriors win tonight?", category: "Sports", priceYes: 0.52, priceNo: 0.48, volume: 620000, change24h: 8.5, endsIn: "6h" },
  { id: 6, question: "Yankees win World Series 2025?", category: "Sports", priceYes: 0.18, priceNo: 0.82, volume: 650000, change24h: 1.2, endsIn: "5d" },
];

const ARB_OPPORTUNITIES = [
  { id: 101, question: "Fed rate cut in March 2025?", priceYes: 0.42, priceNo: 0.55, volume: 320000 },
  { id: 102, question: "Tesla stock above $400 by April?", priceYes: 0.35, priceNo: 0.62, volume: 450000 },
  { id: 103, question: "Apple announces AI device Q1?", priceYes: 0.28, priceNo: 0.68, volume: 280000 },
];

const INITIAL_POSITIONS = [
  { id: 1, market: "Lakers vs Celtics", side: "YES", size: 10, entry: 0.42, current: 0.45, time: "2h ago", endsIn: "4h" },
  { id: 2, market: "BTC to $100k", side: "YES", size: 8, entry: 0.58, current: 0.62, time: "5h ago", endsIn: "3d" },
];

const CLOSED_POSITIONS = [
  { id: 101, market: "Warriors vs Suns", side: "YES", size: 10, entry: 0.48, exit: 0.65, pnl: 3.54, time: "12h ago", status: "won" },
  { id: 102, market: "Chiefs vs Ravens", side: "NO", size: 8, entry: 0.45, exit: 0.38, pnl: -1.5, time: "1d ago", status: "lost" },
  { id: 103, market: "ETH above $4k", side: "YES", size: 12, entry: 0.30, exit: 0.42, pnl: 4.8, time: "2d ago", status: "won" },
];

const INITIAL_LOGS = [
  { time: "18:32:15", type: "info", msg: "🔍 Scanning markets..." },
  { time: "18:32:16", type: "info", msg: "Found 45 markets, 12 same-day games 🏀" },
  { time: "18:32:17", type: "success", msg: "🔄 Found 3 arbitrage opportunities!" },
  { time: "18:32:18", type: "success", msg: "✅ Bet: Lakers vs Celtics YES @ 42¢ (4h)" },
  { time: "18:40:18", type: "success", msg: "✅ Bet: BTC $100k YES @ 58¢ (3d)" },
];

export default function Dashboard() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("All");
  const [showSettings, setShowSettings] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [strategy, setStrategy] = useState("balanced");
  const [codeMode, setCodeMode] = useState("interactive");
  const [selectedMarket, setSelectedMarket] = useState(DEMO_MARKETS[0]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState("open");
  const [marketTab, setMarketTab] = useState("markets");
  const [openPositions, setOpenPositions] = useState(INITIAL_POSITIONS);
  const [closedPositions] = useState(CLOSED_POSITIONS);
  const [logs, setLogs] = useState(INITIAL_LOGS);
  const [bankroll, setBankroll] = useState(50);
  const logsEndRef = useRef(null);

  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [logs]);
  
  useEffect(() => {
    if (!isRunning) return;
    const interval = setInterval(() => {
      const now = new Date().toLocaleTimeString('en-US', { hour12: false });
      const msgs = [
        { type: "info", msg: "🔍 Scanning markets..." },
        { type: "info", msg: `Found ${30 + Math.floor(Math.random()*15)} markets` },
        { type: "success", msg: `📈 Position +${(Math.random()*5).toFixed(1)}%` },
        { type: "success", msg: `🔄 Found ${ARB_OPPORTUNITIES.length} arb opps` },
      ];
      const m = msgs[Math.floor(Math.random() * msgs.length)];
      setLogs(prev => [...prev.slice(-50), { time: now, ...m }]);
      setOpenPositions(prev => prev.map(p => ({
        ...p, current: Math.max(0.05, Math.min(0.95, p.current + (Math.random() - 0.48) * 0.02))
      })));
    }, 3000);
    return () => clearInterval(interval);
  }, [isRunning]);

  const filtered = DEMO_MARKETS.filter(m => m.question.toLowerCase().includes(search.toLowerCase()) && (category === "All" || m.category === category));
  const openPnL = openPositions.reduce((s, p) => s + (p.current - p.entry) * p.size, 0);
  const closedPnL = closedPositions.reduce((s, p) => s + p.pnl, 0);
  const totalPnL = openPnL + closedPnL;
  const currentBankroll = bankroll + totalPnL;
  const wins = closedPositions.filter(p => p.status === "won").length;
  const losses = closedPositions.filter(p => p.status === "lost").length;
  const winRate = wins + losses > 0 ? (wins / (wins + losses) * 100) : 0;
  const pieData = openPositions.map((p, i) => ({ name: p.market, value: p.size * p.current, color: ["#f7931a", "#552583", "#627eea", "#14f195"][i % 4] }));
  const priceHistory = Array.from({ length: 12 }, (_, i) => ({ time: `${i * 2}:00`, price: selectedMarket.priceYes + (Math.sin(i * 0.5) * 0.08) }));
  const arbWithProfit = ARB_OPPORTUNITIES.map(a => ({ ...a, combined: a.priceYes + a.priceNo, profit: (1 - a.priceYes - a.priceNo) * 100 })).sort((a, b) => b.profit - a.profit);

  const handleStartStop = () => {
    const now = new Date().toLocaleTimeString('en-US', { hour12: false });
    setLogs(prev => [...prev, { time: now, type: isRunning ? "info" : "success", msg: isRunning ? "⏹️ Bot stopped" : `🚀 Started (${STRATEGIES[strategy].name})` }]);
    setIsRunning(!isRunning);
  };

  const s = STRATEGIES[strategy];

  return (
    <div className="bg-slate-900 text-white rounded-xl p-4 min-h-[700px]">
      {/* Header */}
      <div className="flex justify-between items-center mb-4">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-xl font-bold">🤖 Polymarket Auto Trader</h1>
            <p className="text-slate-400 text-xs">Fully Automated</p>
          </div>
          <button onClick={handleStartStop} className={`px-4 py-2 rounded-lg font-medium flex items-center gap-2 ${isRunning ? 'bg-red-600 hover:bg-red-500' : 'bg-emerald-600 hover:bg-emerald-500'}`}>
            {isRunning ? <Square size={16} /> : <Play size={16} />}
            {isRunning ? 'Stop' : 'Start'}
          </button>
          {isRunning && <div className="flex items-center gap-2 text-emerald-400 text-sm"><div className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse"></div>Running</div>}
        </div>
        <div className="flex gap-2">
          <button onClick={() => { setShowSettings(!showSettings); setShowHelp(false); }} className={`p-2 rounded-lg flex items-center gap-1 text-sm ${showSettings ? 'bg-blue-600' : 'bg-slate-800 hover:bg-slate-700'}`}>
            <Settings size={16} /><span>Strategy</span>
          </button>
          <button onClick={() => { setShowHelp(!showHelp); setShowSettings(false); }} className={`p-2 rounded-lg flex items-center gap-1 text-sm ${showHelp ? 'bg-blue-600' : 'bg-slate-800 hover:bg-slate-700'}`}>
            <HelpCircle size={16} /><span>Help</span>
          </button>
        </div>
      </div>

      {showSettings && (
        <div className="bg-slate-800 rounded-xl p-4 mb-4 border border-slate-700">
          <div className="flex justify-between items-center mb-3">
            <h3 className="font-semibold text-sm">⚙️ Strategy Settings</h3>
            <button onClick={() => setShowSettings(false)} className="text-slate-400 hover:text-white">×</button>
          </div>
          <div className="grid grid-cols-5 gap-2 mb-4">
            {Object.entries(STRATEGIES).map(([key, st]) => (
              <button key={key} onClick={() => setStrategy(key)} className={`p-3 rounded-lg text-center border-2 ${strategy === key ? 'border-blue-500 bg-blue-500/20' : 'border-slate-700 bg-slate-700/50'}`}>
                <div className="text-2xl mb-1">{st.emoji}</div>
                <div className="text-sm font-medium">{st.name}</div>
                <div className="text-xs text-slate-400">{st.desc}</div>
                <div className="text-xs mt-1"><span className="text-emerald-400">+{st.tp}%</span> <span className="text-red-400">-{st.sl}%</span></div>
              </button>
            ))}
          </div>
          <div className="flex items-center gap-4 p-3 bg-slate-700/50 rounded-lg">
            <div className="flex items-center gap-2"><DollarSign size={16} /><span className="text-sm">Bankroll:</span>
              <input type="number" value={bankroll} onChange={(e) => setBankroll(parseFloat(e.target.value) || 0)} className="w-20 px-2 py-1 bg-slate-600 rounded text-sm" />
            </div>
            <span className="text-sm text-slate-400">Max bet: <span className="text-white">${(bankroll * s.maxBet / 100).toFixed(2)}</span></span>
          </div>
        </div>
      )}

      {showHelp && (
        <div className="bg-slate-800 rounded-xl p-4 mb-4 border border-slate-700">
          <div className="flex justify-between items-center mb-3">
            <h3 className="font-semibold text-sm">📖 How to Use</h3>
            <button onClick={() => setShowHelp(false)} className="text-slate-400 hover:text-white">×</button>
          </div>
          <div className="flex gap-2 mb-3">
            {[{ id: 'interactive', l: '🟢 Terminal', c: 'bg-emerald-600' }, { id: 'simple', l: '🟡 Simple', c: 'bg-yellow-600' }, { id: 'advanced', l: '🔴 Advanced', c: 'bg-red-600' }].map(o => (
              <button key={o.id} onClick={() => setCodeMode(o.id)} className={`flex-1 py-2 rounded-lg text-sm font-medium ${codeMode === o.id ? o.c : 'bg-slate-700'}`}>{o.l}</button>
            ))}
          </div>
          <pre className="bg-slate-900 rounded-lg p-3 text-xs text-slate-300 whitespace-pre-wrap">{CODE_EXAMPLES[codeMode]}</pre>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-6 gap-3 mb-4">
        <div className="bg-slate-800 rounded-xl p-3">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1"><DollarSign size={14} />Bankroll</div>
          <div className="text-xl font-bold">${currentBankroll.toFixed(2)}</div>
        </div>
        <div className="bg-slate-800 rounded-xl p-3">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">{totalPnL >= 0 ? <TrendingUp size={14} className="text-emerald-400" /> : <TrendingDown size={14} className="text-red-400" />}Total P&L</div>
          <div className={`text-xl font-bold ${totalPnL >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{totalPnL >= 0 ? '+' : ''}${totalPnL.toFixed(2)}</div>
        </div>
        <div className="bg-slate-800 rounded-xl p-3">
          <div className="text-slate-400 text-xs mb-1">Open P&L</div>
          <div className={`text-xl font-bold ${openPnL >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{openPnL >= 0 ? '+' : ''}${openPnL.toFixed(2)}</div>
        </div>
        <div className="bg-slate-800 rounded-xl p-3">
          <div className="text-slate-400 text-xs mb-1">Win Rate</div>
          <div className="text-xl font-bold">{winRate.toFixed(0)}%</div>
        </div>
        <div className="bg-slate-800 rounded-xl p-3">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1"><Zap size={14} className="text-yellow-400" />Arbitrage</div>
          <div className="text-xl font-bold text-purple-400">{ARB_OPPORTUNITIES.length}</div>
        </div>
        <div className="bg-slate-800 rounded-xl p-3">
          <div className="text-slate-400 text-xs mb-1">Strategy</div>
          <div className="text-xl font-bold">{s.emoji}</div>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2 space-y-4">
          {/* Markets / Arbitrage Tabs */}
          <div className="bg-slate-800 rounded-xl p-4">
            <div className="flex justify-between items-center mb-3">
              <div className="flex gap-2">
                <button onClick={() => setMarketTab("markets")} className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium ${marketTab === "markets" ? 'bg-blue-600' : 'bg-slate-700 hover:bg-slate-600'}`}>
                  <BarChart3 size={14} /> Markets
                </button>
                <button onClick={() => setMarketTab("arbitrage")} className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium ${marketTab === "arbitrage" ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'}`}>
                  <Zap size={14} /> Arbitrage
                  <span className="bg-purple-500 px-1.5 rounded text-xs">{ARB_OPPORTUNITIES.length}</span>
                </button>
              </div>
              {marketTab === "markets" && (
                <div className="flex gap-1">{["All", "Crypto", "Sports"].map(c => (
                  <button key={c} onClick={() => setCategory(c)} className={`px-2 py-1 rounded text-xs ${category === c ? 'bg-blue-600' : 'bg-slate-700'}`}>{c}</button>
                ))}</div>
              )}
            </div>

            {marketTab === "markets" && (
              <>
                <div className="relative mb-3"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input type="text" placeholder="Search..." value={search} onChange={e => setSearch(e.target.value)} className="w-full pl-9 pr-3 py-2 bg-slate-700 rounded-lg text-sm" />
                </div>
                <div className="space-y-2 max-h-32 overflow-y-auto">
                  {filtered.map((m, i) => (
                    <div key={i} onClick={() => setSelectedMarket(m)} className={`flex justify-between items-center p-2 rounded-lg cursor-pointer ${selectedMarket?.id === m.id ? 'bg-blue-600/20 border border-blue-500' : 'bg-slate-700/50 hover:bg-slate-700'}`}>
                      <div className="flex-1 min-w-0"><div className="text-sm truncate">{m.question}</div>
                        <div className="text-xs text-slate-500">⏰ {m.endsIn || '7d'}</div>
                      </div>
                      <div className="flex gap-3 ml-2">
                        <span className="text-emerald-400 font-bold text-sm">{(m.priceYes * 100).toFixed(0)}¢</span>
                        <span className="text-red-400 font-bold text-sm">{(m.priceNo * 100).toFixed(0)}¢</span>
                        <span className={`text-xs ${m.change24h >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{m.change24h >= 0 ? '↑' : '↓'}{Math.abs(m.change24h).toFixed(1)}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}

            {marketTab === "arbitrage" && (
              <>
                <div className="bg-purple-900/30 border border-purple-700 rounded-lg p-2 mb-3 text-sm">
                  <span className="text-purple-400">💡 Arbitrage:</span> When YES + NO &lt; $1.00, buy both = guaranteed profit!
                </div>
                <div className="space-y-2 max-h-40 overflow-y-auto">
                  {arbWithProfit.map((a, i) => (
                    <div key={i} className="p-3 bg-slate-700/50 rounded-lg border border-purple-800/50">
                      <div className="flex justify-between items-start mb-2">
                        <div className="text-sm font-medium truncate flex-1">{a.question}</div>
                        <div className="text-emerald-400 font-bold text-lg ml-2">+{a.profit.toFixed(1)}%</div>
                      </div>
                      <div className="flex justify-between items-center text-xs">
                        <div className="flex gap-3">
                          <span>YES: <span className="text-emerald-400 font-medium">{(a.priceYes * 100).toFixed(0)}¢</span></span>
                          <span>NO: <span className="text-red-400 font-medium">{(a.priceNo * 100).toFixed(0)}¢</span></span>
                          <span>= <span className="text-white font-medium">{(a.combined * 100).toFixed(0)}¢</span></span>
                        </div>
                        <span className="text-slate-400">Vol: ${(a.volume / 1000).toFixed(0)}K</span>
                      </div>
                      <div className="mt-2 p-2 bg-emerald-900/30 rounded text-xs text-emerald-300">
                        💰 $100 bet → ${(100 + a.profit).toFixed(2)} back = ${a.profit.toFixed(2)} profit
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Chart */}
          <div className="bg-slate-800 rounded-xl p-4">
            <h3 className="font-semibold text-sm mb-2 truncate">{selectedMarket.question}</h3>
            <div className="h-24">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={priceHistory}>
                  <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/><stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/></linearGradient></defs>
                  <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} tickFormatter={v => `${(v*100).toFixed(0)}¢`} />
                  <Tooltip contentStyle={{ background: '#1e293b', border: 'none', borderRadius: 8 }} formatter={(v) => [`${(v*100).toFixed(1)}¢`]} />
                  <Area type="monotone" dataKey="price" stroke="#3b82f6" fill="url(#g)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Tabs: Open, Closed, Logs */}
          <div className="bg-slate-800 rounded-xl p-4">
            <div className="flex gap-2 mb-3">
              {[{ id: 'open', label: 'Open', icon: Activity, count: openPositions.length }, { id: 'closed', label: 'Closed', icon: CheckCircle, count: closedPositions.length }, { id: 'logs', label: 'Logs', icon: List, count: logs.length }].map(t => (
                <button key={t.id} onClick={() => setActiveTab(t.id)} className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm ${activeTab === t.id ? 'bg-blue-600' : 'bg-slate-700 hover:bg-slate-600'}`}>
                  <t.icon size={14} />{t.label}<span className="bg-slate-600 px-1.5 rounded text-xs">{t.count}</span>
                </button>
              ))}
            </div>

            {activeTab === 'open' && (
              <div className="space-y-2 max-h-32 overflow-y-auto">
                {openPositions.length === 0 ? <div className="text-slate-500 text-center py-4">No open positions</div> : openPositions.map((p, i) => {
                  const pnl = (p.current - p.entry) * p.size;
                  const pnlPct = ((p.current - p.entry) / p.entry) * 100;
                  return (
                    <div key={i} className="flex justify-between items-center p-2 bg-slate-700/50 rounded-lg">
                      <div className="flex-1"><div className="text-sm font-medium">{p.market}</div><div className="text-xs text-slate-400">{p.side} × ${p.size} @ {(p.entry*100).toFixed(0)}¢ • ⏰ {p.endsIn || '?'}</div></div>
                      <div className="text-right"><div className="text-sm">Now: {(p.current*100).toFixed(0)}¢</div><div className={`text-sm font-bold ${pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(0)}%)</div></div>
                    </div>
                  );
                })}
              </div>
            )}

            {activeTab === 'closed' && (
              <div className="space-y-2 max-h-32 overflow-y-auto">
                {closedPositions.map((p, i) => (
                  <div key={i} className="flex justify-between items-center p-2 bg-slate-700/50 rounded-lg">
                    <div className="flex items-center gap-2">
                      {p.status === 'won' ? <CheckCircle size={16} className="text-emerald-400" /> : <XCircle size={16} className="text-red-400" />}
                      <div><div className="text-sm font-medium">{p.market}</div><div className="text-xs text-slate-400">{p.side} × ${p.size} • {(p.entry*100).toFixed(0)}¢ → {(p.exit*100).toFixed(0)}¢ • {p.time}</div></div>
                    </div>
                    <div className={`text-sm font-bold ${p.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{p.pnl >= 0 ? '+' : ''}${p.pnl.toFixed(2)}</div>
                  </div>
                ))}
              </div>
            )}

            {activeTab === 'logs' && (
              <div className="space-y-1 max-h-32 overflow-y-auto font-mono text-xs">
                {logs.map((l, i) => (
                  <div key={i} className={l.type === 'success' ? 'text-emerald-400' : 'text-slate-400'}>
                    <span className="text-slate-500">[{l.time}]</span> {l.msg}
                  </div>
                ))}
                <div ref={logsEndRef} />
              </div>
            )}
          </div>
        </div>

        {/* Right Column */}
        <div className="space-y-4">
          <div className="bg-slate-800 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3"><Wallet size={16} /><h3 className="font-semibold text-sm">Portfolio</h3></div>
            {openPositions.length > 0 ? (
              <>
                <div className="h-24"><ResponsiveContainer width="100%" height="100%">
                  <PieChart><Pie data={pieData} cx="50%" cy="50%" innerRadius={25} outerRadius={40} dataKey="value">{pieData.map((e, i) => <Cell key={i} fill={e.color} />)}</Pie></PieChart>
                </ResponsiveContainer></div>
                <div className="space-y-1 mt-2">{openPositions.map((p, i) => {
                  const pnl = (p.current - p.entry) * p.size;
                  return <div key={i} className="flex justify-between text-xs"><span className="truncate max-w-24">{p.market}</span><span className={pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}</span></div>;
                })}</div>
              </>
            ) : <div className="text-slate-500 text-center py-6 text-sm">No positions</div>}
          </div>

          <div className="bg-slate-800 rounded-xl p-4">
            <h3 className="font-semibold text-sm mb-3">📊 Active Strategy</h3>
            <div className="text-center mb-3"><div className="text-4xl mb-1">{s.emoji}</div><div className="font-medium">{s.name}</div><div className="text-xs text-slate-400">{s.desc}</div></div>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="bg-slate-700/50 rounded-lg p-2 text-center"><div className="text-emerald-400 font-bold">+{s.tp}%</div><div className="text-xs text-slate-400">Take Profit</div></div>
              <div className="bg-slate-700/50 rounded-lg p-2 text-center"><div className="text-red-400 font-bold">-{s.sl}%</div><div className="text-xs text-slate-400">Stop Loss</div></div>
            </div>
          </div>

          <div className="bg-slate-800 rounded-xl p-4">
            <h3 className="font-semibold text-sm mb-3">📈 Performance</h3>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-slate-400">Total Trades</span><span>{openPositions.length + closedPositions.length}</span></div>
              <div className="flex justify-between"><span className="text-slate-400">Wins / Losses</span><span><span className="text-emerald-400">{wins}</span> / <span className="text-red-400">{losses}</span></span></div>
              <div className="flex justify-between"><span className="text-slate-400">Win Rate</span><span>{winRate.toFixed(0)}%</span></div>
              <div className="flex justify-between"><span className="text-slate-400">Realized P&L</span><span className={closedPnL >= 0 ? 'text-emerald-400' : 'text-red-400'}>{closedPnL >= 0 ? '+' : ''}${closedPnL.toFixed(2)}</span></div>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 flex justify-between text-xs text-slate-500">
        <span>{s.emoji} {s.name} | TP +{s.tp}% | SL -{s.sl}% | Max ${(bankroll * s.maxBet / 100).toFixed(0)}</span>
        <span>{isRunning ? '🟢 Running' : '⚪ Stopped'}</span>
      </div>
    </div>
  );
}
