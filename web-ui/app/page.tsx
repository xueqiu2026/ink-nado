"use client";

import { useState, useEffect, useRef } from "react";
import { Play, Square, Activity, Settings, Terminal, TrendingUp, AlertTriangle, Wallet, Calculator, XCircle, ShieldAlert, ShieldCheck } from "lucide-react";

interface Product { symbol: string; id: number; min_size: string; }
interface Stats {
  pnl: number;
  equity: number;
  health: number;
  liq_price: number;
  active_pos: number;
  volume: number;
  volume_rate_min: number;
  active_orders?: any[];
  trades?: any[];
}

export default function Dashboard() {
  const [status, setStatus] = useState("stopped");
  const [logs, setLogs] = useState<string[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [currentPrice, setCurrentPrice] = useState(0); // Add price state
  const [targetVol, setTargetVol] = useState(1000000); // 1M Target
  const [isPlanning, setIsPlanning] = useState(false);




  const [config, setConfig] = useState({
    ticker: "ETH-PERP", quantity: 0.05, spread: 0.0005, interval: 5, boostMode: false, maxExposure: 200
  });

  const wsRef = useRef<WebSocket | null>(null);



  useEffect(() => {
    fetch("http://127.0.0.1:8000/status").then(res => res.json()).then(data => setStatus(data.status)).catch(console.error);
    fetch("http://127.0.0.1:8000/products").then(res => res.json()).then(data => { if (data.products) setProducts(data.products) }).catch(console.error);
    const ws = new WebSocket("ws://127.0.0.1:8000/ws");
    ws.onmessage = (event) => addLog(event.data);
    return () => ws.close();
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      if (status === 'running') fetch("http://127.0.0.1:8000/stats").then(res => res.json()).then(setStats).catch(console.error);
    }, 2000);
    return () => clearInterval(timer);
  }, [status]);

  useEffect(() => {
    // Fetch price whenever ticker changes
    if (config.ticker) {
      fetch(`http://127.0.0.1:8000/price/${config.ticker}`)
        .then(res => res.json())
        .then(data => setCurrentPrice(data.price || 0))
        .catch(console.error);
    }
  }, [config.ticker]);

  const addLog = (msg: string) => setLogs(prev => [msg, ...prev].slice(0, 100));

  const apiCall = async (endpoint: string, body?: any) => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/${endpoint}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      return await res.json();
    } catch (e) { return { error: String(e) }; }
  };

  const handleStart = async () => {
    console.log("Start Button Clicked");
    addLog("--> 尝试发送启动指令...");
    try {
      const data = await apiCall("start", config);
      console.log("Start Response:", data);
      if (data.status === "started") { setStatus("running"); addLog("--> ✅ 策略已启动"); }
      else addLog(`--> ❌ 启动失败: ${data.error || JSON.stringify(data)}`);
    } catch (err) {
      console.error("HandleStart Error:", err);
      addLog(`--> ❌ 前端错误: ${String(err)}`);
    }
  };

  const handlePanic = async (action: 'stop' | 'close' | 'cancel') => {
    if (action === 'stop') {
      addLog("--> 发送停止指令...");
      await apiCall("stop");
      setStatus("stopped");
      addLog("--> ⏹️ 策略已停止");
    }
    if (action === 'close') {
      addLog("⚠️ PANIC CLOSE Triggered!");
      const res = await apiCall("close_all");
      if (res.status === "closed") addLog(`--> ✅ 全平成功: $${res.size} ${res.side}`);
      else if (res.status === "no_position") addLog("--> ℹ️ 无需平仓: 当前仓位为0");
      else addLog(`--> ❌ 平仓失败: ${res.error || JSON.stringify(res)}`);
    }
    if (action === 'cancel') {
      addLog("⚠️ Cancel All Triggered!");
      const res = await apiCall("cancel_all");
      if (res.status === "cancelled") addLog("--> ✅ 挂单已全部撤销");
      else addLog(`--> ❌ 撤单失败: ${res.error || JSON.stringify(res)}`);
    }
  };

  // Planner Calcs
  const volRate = stats?.volume_rate_min || 0;
  const remaining = Math.max(0, targetVol - (stats?.volume || 0));
  const timeToTarget = volRate > 0 ? remaining / volRate : 999999;
  const estFees = targetVol * 0.0005; // 0.05% est

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-200 font-mono p-6">
      <header className="flex justify-between items-center mb-6 border-b border-neutral-800 pb-4">
        <h1 className="text-2xl font-bold flex items-center gap-2 text-emerald-400"><Activity /> Nado 交易员套件 (专业版)</h1>

        <div className="flex items-center gap-4">
          {/* Global Clock */}
          {/* Legacy Clock Removed */}

          <div className={`px-4 py-1.5 rounded-full text-sm font-bold flex items-center gap-2 ${status === 'running' ? 'bg-emerald-900/50 text-emerald-400 animate-pulse' : 'bg-red-900/30 text-red-500'}`}>
            <div className={`w-2 h-2 rounded-full ${status === 'running' ? 'bg-emerald-400' : 'bg-red-500'}`} />
            {status === 'running' ? '系统运行中' : '系统离线'}
          </div>
        </div>
      </header>

      {/* 1. Risk & Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatsCard label="账户净值" value={`$${stats?.equity?.toFixed(2) || '0.00'}`} subValue={stats?.pnl ? `${stats.pnl > 0 ? '+' : ''}${stats.pnl.toFixed(2)} PnL` : "0.00 PnL"} icon={<Wallet />} />

        {/* Safety Buffer Bar */}
        <div className="bg-neutral-900/50 p-4 rounded-xl border border-neutral-800 col-span-1">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs font-bold text-neutral-500 uppercase">保证金健康度: {stats?.health?.toFixed(2) || 100}</span>
            <span className="text-xs text-red-400">{stats?.active_pos ? `强平: $${stats.liq_price.toFixed(0)}` : "安全"}</span>
          </div>
          <div className="w-full bg-neutral-800 h-2 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-500 ${stats?.health && stats.health < 20 ? 'bg-red-500' : 'bg-emerald-500'}`}
              style={{ width: `${Math.min(stats?.health || 100, 100)}%` }}
            />
          </div>
          <p className="text-[10px] text-neutral-600 mt-2">缓冲 &lt; 20 = 危险区域</p>
        </div>

        <StatsCard label="总成交量" value={`$${stats?.volume?.toFixed(0) || '0'}`} subValue={`${stats?.volume_rate_min?.toFixed(0) || 0}/分`} icon={<TrendingUp />} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-6">
          {/* 2. Controls & Planner */}
          <div className="bg-neutral-900/50 p-6 rounded-xl border border-neutral-800">
            <h2 className="flex items-center gap-2 text-lg font-semibold mb-4 text-neutral-400"><Settings className="w-4 h-4" /> 策略配置</h2>

            <div className="space-y-4 mb-6">
              <select value={config.ticker} onChange={e => setConfig({ ...config, ticker: e.target.value })} className="w-full bg-neutral-950 border border-neutral-800 rounded p-2">
                {products.map(p => <option key={p.id} value={p.symbol}>{p.symbol}</option>)}
                {products.length === 0 && <option>{config.ticker}</option>}
              </select>
              <div className="relative">
                <input type="number" step="0.01" value={config.quantity} onChange={e => setConfig({ ...config, quantity: parseFloat(e.target.value) })} className="w-full bg-neutral-950 border border-neutral-800 rounded p-2" placeholder="下单数量" />
                {currentPrice > 0 && <div className="text-[10px] text-neutral-500 text-right mt-1">≈ ${(config.quantity * currentPrice).toFixed(2)}</div>}
              </div>

              <div className="relative">
                <input type="number" step="0.0001" value={config.spread} onChange={e => setConfig({ ...config, spread: parseFloat(e.target.value) })} className="w-full bg-neutral-950 border border-neutral-800 rounded p-2" placeholder="价差 (Spread)" disabled={config.boostMode} />
                {currentPrice > 0 && !config.boostMode && <div className="text-[10px] text-neutral-500 text-right mt-1">幅度: {(config.spread * 100).toFixed(2)}% (≈ ${(config.spread * currentPrice).toFixed(2)})</div>}
              </div>

              <div className="flex items-center gap-2 p-2 bg-neutral-950 border border-neutral-800 rounded">
                <input
                  type="checkbox"
                  checked={config.boostMode}
                  onChange={e => setConfig({ ...config, boostMode: e.target.checked })}
                  className="w-4 h-4 text-emerald-600 rounded focus:ring-emerald-500 border-gray-300"
                />
                <div className="flex-1">
                  <span className={`text-sm font-bold ${config.boostMode ? "text-red-400" : "text-neutral-400"}`}>狂暴模式 (Taker)</span>
                  {config.boostMode && <p className="text-[10px] text-red-500/80 leading-none mt-1">⚠️ 高费率警告：IOC 激进吃单</p>}
                </div>
              </div>

              <div>
                <label className="text-[10px] text-neutral-500 uppercase font-bold mb-1 block">最大风险限制 (USD)</label>
                <div className="relative">
                  <input
                    type="number"
                    value={config.maxExposure}
                    onChange={e => setConfig({ ...config, maxExposure: parseFloat(e.target.value) })}
                    className="w-full bg-neutral-950 border border-neutral-800 rounded p-2 font-mono text-orange-400 text-sm"
                    step="50"
                  />
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={handleStart}
                disabled={status === 'running'}
                className={`py-3 rounded font-bold transition flex justify-center gap-2 ${status === 'running' ? 'bg-emerald-800/50 text-neutral-500 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-500 text-white'}`}
              >
                <Play className="w-4 h-4" /> {status === 'running' ? "策略运行中..." : "启动策略"}
              </button>
              <button
                onClick={() => handlePanic('stop')}
                disabled={status !== 'running'}
                className={`py-3 rounded font-bold transition flex justify-center gap-2 ${status !== 'running' ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed' : 'bg-neutral-700 hover:bg-neutral-600 text-white'}`}
              >
                <Square className="w-4 h-4" /> 停止运行
              </button>
            </div>

            {/* Panic Actions */}
            <div className="grid grid-cols-2 gap-2 mt-2">
              <button onClick={() => handlePanic('close')} className="bg-red-900/50 border border-red-800 hover:bg-red-800 text-red-200 py-2 rounded text-xs font-bold flex items-center justify-center gap-1"><ShieldAlert className="w-3 h-3" /> 一键平仓</button>
              <button onClick={() => handlePanic('cancel')} className="bg-orange-900/50 border border-orange-800 hover:bg-orange-800 text-orange-200 py-2 rounded text-xs font-bold flex items-center justify-center gap-1"><XCircle className="w-3 h-3" /> 撤销全单</button>
            </div>
          </div>

          {/* Volume Planner */}
          <div className="bg-neutral-900/50 p-6 rounded-xl border border-neutral-800">
            <h2 className="flex items-center gap-2 text-lg font-semibold mb-4 text-neutral-400"><Calculator className="w-4 h-4" /> 刷量规划器</h2>
            <div className="space-y-4">
              <div>
                <label className="text-xs text-neutral-500 uppercase">目标交易量 ($)</label>
                <input type="number" value={targetVol} onChange={e => setTargetVol(parseFloat(e.target.value))} className="w-full bg-neutral-950 border border-neutral-800 rounded p-2 font-mono text-emerald-400" />
              </div>
              <div className="bg-black/40 p-3 rounded border border-neutral-800 text-sm space-y-2 min-h-[60px]">
                {isPlanning ? (
                  <>
                    <div className="flex justify-between"><span>预计耗时:</span> <span className="text-white font-bold">{volRate > 0 ? `${(timeToTarget / 60).toFixed(1)} 小时` : "需先启动策略..."}</span></div>
                    <div className="flex justify-between"><span>预估手续费 (0.05%):</span> <span className="text-orange-400 font-bold">${estFees.toFixed(0)}</span></div>
                  </>
                ) : (
                  <div className="h-full flex items-center justify-center text-neutral-500 py-2 text-xs">点击下方按钮开始规划刷量任务</div>
                )}
              </div>
              <button
                onClick={() => setIsPlanning(!isPlanning)}
                className={`w-full py-2 rounded text-xs font-bold transition flex justify-center gap-1 ${isPlanning ? 'bg-neutral-800 text-neutral-400' : 'bg-emerald-900/50 text-emerald-400 border border-emerald-800'}`}
              >
                <Calculator className="w-3 h-3" /> {isPlanning ? "停止规划" : "开始计算"}
              </button>
            </div>
          </div>
        </div>

        {/* Orders & Trades */}
        <div className="lg:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Active Orders Section */}
          <div className="bg-neutral-900/50 p-6 rounded-xl border border-neutral-800 h-[300px] flex flex-col">
            <h2 className="flex items-center gap-2 text-md font-semibold mb-3 text-neutral-400 uppercase tracking-wider"><ShieldCheck className="w-4 h-4" /> 当前挂单</h2>
            <div className="flex-1 overflow-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-neutral-600 border-b border-neutral-800">
                  <tr>
                    <th className="pb-2">侧</th>
                    <th className="pb-2">价格</th>
                    <th className="pb-2">数量</th>
                    <th className="pb-2">状态</th>
                  </tr>
                </thead>
                <tbody className="text-neutral-300">
                  {stats?.active_orders?.map((o: any, i: number) => (
                    <tr key={i} className="border-b border-neutral-800/30">
                      <td className={`py-2 font-bold ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>{o.side.toUpperCase()}</td>
                      <td className="py-2 font-mono">{o.price.toFixed(2)}</td>
                      <td className="py-2 font-mono">{o.size}</td>
                      <td className="py-2 text-[10px] text-neutral-500">OPEN</td>
                    </tr>
                  ))}
                  {(!stats?.active_orders || stats?.active_orders.length === 0) && (
                    <tr><td colSpan={4} className="py-8 text-center text-neutral-600 italic">当前暂无挂单</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Trade History Section */}
          <div className="bg-neutral-900/50 p-6 rounded-xl border border-neutral-800 h-[300px] flex flex-col">
            <h2 className="flex items-center gap-2 text-md font-semibold mb-3 text-neutral-400 uppercase tracking-wider"><Activity className="w-4 h-4" /> 成交历史</h2>
            <div className="flex-1 overflow-auto space-y-2">
              {stats?.trades?.map((t: any, i: number) => (
                <div key={i} className="flex justify-between items-center p-2 bg-black/40 border-l-2 border-emerald-500 rounded text-xs font-mono">
                  <span className={t.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}>{t.side.toUpperCase()}</span>
                  <span className="text-white">${t.price.toFixed(2)}</span>
                  <span className="text-neutral-500">{t.size}</span>
                  <span className="text-[10px] text-neutral-700">{t.time}</span>
                </div>
              ))}
              {(!stats?.trades || stats?.trades.length === 0) && (
                <div className="h-full flex items-center justify-center text-neutral-600 italic text-sm">暂无成交记录</div>
              )}
            </div>
          </div>
        </div>

        {/* Logs */}
        <div className="lg:col-span-2 bg-neutral-900/50 p-6 rounded-xl border border-neutral-800 h-[300px] flex flex-col">
          <h2 className="flex items-center gap-2 text-lg font-semibold mb-3 text-neutral-400 uppercase tracking-wider"><Terminal className="w-4 h-4" /> 运行日志</h2>
          <div className="flex-1 overflow-auto font-mono text-xs bg-black/60 p-4 rounded-lg border border-neutral-800/50">
            {logs.map((l, i) => <div key={i} className={`mb-1 ${l.includes("PANIC") || l.includes("Limit") || l.includes("Rejected") ? "text-red-500 font-bold" : l.includes("✅") || l.includes("Placing") ? "text-emerald-400" : "text-neutral-500"}`}>{l}</div>)}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatsCard({ label, value, subValue, icon }: any) {
  return (
    <div className="bg-neutral-900/50 p-4 rounded-xl border border-neutral-800 flex justify-between">
      <div><p className="text-xs text-neutral-500 uppercase font-bold">{label}</p><p className="text-xl font-bold text-white">{value}</p><p className="text-xs text-neutral-500">{subValue}</p></div>
      <div className="p-2 bg-neutral-950 rounded h-fit">{icon}</div>
    </div>
  )
}
