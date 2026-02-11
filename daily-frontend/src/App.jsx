import { useState, useEffect, useRef } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import AnalyticsDashboard from './AnalyticsDashboard'
import {
  Play, Square, Settings, TrendingUp, TrendingDown, Activity,
  Wifi, WifiOff, DollarSign, BarChart3,
  RefreshCw, Zap, Shield, ChevronDown, ChevronUp, Eye, EyeOff,
  GitMerge, ArrowRightLeft, Layers
} from 'lucide-react'

const API = ''

function App() {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
  const { status, markets, trades, mergeStatus, connected } = useWebSocket(wsUrl)
  const [config, setConfig] = useState(null)
  const [configForm, setConfigForm] = useState({})
  const [showKey, setShowKey] = useState(false)
  const [manualMarkets, setManualMarkets] = useState([])
  const [loading, setLoading] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [activeView, setActiveView] = useState('live')
  const logsEndRef = useRef(null)

  useEffect(() => {
    fetchConfig()
  }, [])

  // No auto-scroll on new log entries

  async function fetchConfig() {
    try {
      const res = await fetch(`${API}/api/config`)
      const data = await res.json()
      setConfig(data)
      setConfigForm(data)
    } catch (e) {
      console.error('Failed to fetch config:', e)
    }
  }

  async function saveConfig() {
    try {
      const res = await fetch(`${API}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configForm),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        fetchConfig()
      }
    } catch (e) {
      console.error('Failed to save config:', e)
    }
  }

  async function startBot() {
    try {
      await fetch(`${API}/api/bot/start`, { method: 'POST' })
    } catch (e) {
      console.error('Failed to start bot:', e)
    }
  }

  async function stopBot() {
    try {
      await fetch(`${API}/api/bot/stop`, { method: 'POST' })
    } catch (e) {
      console.error('Failed to stop bot:', e)
    }
  }

  async function toggleAutoMerge() {
    try {
      await fetch(`${API}/api/merge/toggle`, { method: 'POST' })
    } catch (e) {
      console.error('Failed to toggle merge:', e)
    }
  }

  async function mergeAll() {
    try {
      await fetch(`${API}/api/merge/all`, { method: 'POST' })
    } catch (e) {
      console.error('Failed to merge all:', e)
    }
  }

  async function mergeOne(conditionId) {
    try {
      await fetch(`${API}/api/merge/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ condition_id: conditionId }),
      })
    } catch (e) {
      console.error('Failed to merge:', e)
    }
  }

  async function scanMarkets() {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/markets`)
      const data = await res.json()
      setManualMarkets(data)
    } catch (e) {
      console.error('Failed to scan markets:', e)
    }
    setLoading(false)
  }

  const isRunning = status?.running || false

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500 to-amber-600 flex items-center justify-center">
              <Zap className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight">Polymarket 每日套利機器人</h1>
              <p className="text-xs text-gray-500">每日加密貨幣 Up or Down 市場自動套利</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Top-level tabs */}
            <div className="flex gap-1 bg-gray-800/50 rounded-lg p-0.5">
              {[
                { id: 'live', label: '即時監控', icon: <Activity className="w-3.5 h-3.5" /> },
                { id: 'analytics', label: '數據分析', icon: <BarChart3 className="w-3.5 h-3.5" /> },
                { id: 'settings', label: '設定', icon: <Settings className="w-3.5 h-3.5" /> },
              ].map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveView(tab.id)}
                  className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md transition-colors font-medium ${
                    activeView === tab.id
                      ? 'bg-orange-600 text-white'
                      : 'text-gray-400 hover:text-gray-200'
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Connection Status */}
            <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full ${
              connected ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
            }`}>
              {connected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
              {connected ? '已連線' : '未連線'}
            </div>

            {/* Mode Badge */}
            <div className={`text-xs px-2.5 py-1 rounded-full font-medium ${
              config?.dry_run !== false
                ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                : 'bg-red-500/10 text-red-400 border border-red-500/20'
            }`}>
              {config?.dry_run !== false ? '🔸 模擬模式' : '🔴 真實交易'}
            </div>

            {/* Start/Stop */}
            {isRunning ? (
              <button
                onClick={stopBot}
                className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium transition-colors"
              >
                <Square className="w-4 h-4" />
                停止
              </button>
            ) : (
              <button
                onClick={startBot}
                className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 rounded-lg text-sm font-medium transition-colors"
              >
                <Play className="w-4 h-4" />
                啟動
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">

        {/* ═══════════════ LIVE TAB ═══════════════ */}
        {activeView === 'live' && (
          <>
            {/* Compact Stats Row */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <StatCard
                icon={<Activity className="w-5 h-5" />}
                label="狀態"
                value={isRunning ? '運行中' : '已停止'}
                color={isRunning ? 'emerald' : 'gray'}
              />
              <StatCard
                icon={<BarChart3 className="w-5 h-5" />}
                label="交易"
                value={status?.total_trades ?? 0}
                color="blue"
              />
              <StatCard
                icon={<DollarSign className="w-5 h-5" />}
                label="利潤"
                value={`$${(status?.total_profit ?? 0).toFixed(4)}`}
                color={(status?.total_profit ?? 0) > 0 ? 'emerald' : 'red'}
              />
              <StatCard
                icon={<RefreshCw className="w-5 h-5" />}
                label="掃描"
                value={status?.scan_count ?? 0}
                color="amber"
              />
              <StatCard
                icon={<Layers className="w-5 h-5" />}
                label="合併 USDC"
                value={`$${(mergeStatus?.total_merged_usdc ?? 0).toFixed(2)}`}
                color="cyan"
              />
            </div>

            {/* Price Table + Bargain Holdings — side by side */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Price Monitoring */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" />
                  即時價格 — {Object.keys(status?.market_prices || {}).length} 個市場
                </h3>
                {status?.market_prices && Object.keys(status.market_prices).length > 0 ? (
                  <div className="overflow-x-auto max-h-72 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-gray-900">
                        <tr className="text-gray-500 border-b border-gray-800">
                          <th className="text-left py-1.5 pr-3 font-medium">市場</th>
                          <th className="text-right py-1.5 px-2 font-medium">UP</th>
                          <th className="text-right py-1.5 px-2 font-medium">DOWN</th>
                          <th className="text-right py-1.5 px-2 font-medium">成本</th>
                          <th className="text-right py-1.5 pl-2 font-medium">價差</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(status.market_prices)
                          .sort(([,a], [,b]) => a.total_cost - b.total_cost)
                          .map(([slug, price]) => {
                            const profitable = price.total_cost < (config?.target_pair_cost ?? 0.99);
                            return (
                              <tr key={slug} className={`border-b border-gray-800/50 ${profitable ? 'bg-emerald-500/5' : ''}`}>
                                <td className="py-2 pr-3">
                                  <span className="font-mono text-gray-300 truncate block max-w-[160px]" title={slug}>
                                    {slug}
                                  </span>
                                </td>
                                <td className="text-right py-2 px-2 font-mono text-white">
                                  {price.up_best_ask > 0 ? price.up_best_ask.toFixed(4) : price.up_price.toFixed(4)}
                                </td>
                                <td className="text-right py-2 px-2 font-mono text-white">
                                  {price.down_best_ask > 0 ? price.down_best_ask.toFixed(4) : price.down_price.toFixed(4)}
                                </td>
                                <td className={`text-right py-2 px-2 font-mono font-bold ${profitable ? 'text-emerald-400' : 'text-white'}`}>
                                  {price.total_cost.toFixed(4)}
                                </td>
                                <td className={`text-right py-2 pl-2 font-mono ${price.spread > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                  {price.spread > 0 ? '+' : ''}{price.spread.toFixed(4)}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-gray-600 text-center py-6">等待價格數據...</p>
                )}
              </div>

              {/* Bargain Holdings */}
              <div className="bg-gray-900 border border-amber-800/30 rounded-xl p-5">
                <h3 className="text-sm font-medium text-amber-400 mb-3 flex items-center gap-2">
                  🏷️ 撿便宜持倉 ({(status?.bargain_holdings || []).length} 筆)
                </h3>
                {(status?.bargain_holdings || []).length > 0 ? (
                  <div className="overflow-x-auto max-h-72 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-gray-900">
                        <tr className="text-gray-500 border-b border-gray-800">
                          <th className="text-left py-1.5 px-2 font-medium">R#</th>
                          <th className="text-left py-1.5 px-2 font-medium">市場</th>
                          <th className="text-center py-1.5 px-2 font-medium">方向</th>
                          <th className="text-right py-1.5 px-2 font-medium">買入</th>
                          <th className="text-right py-1.5 px-2 font-medium">股數</th>
                          <th className="text-right py-1.5 px-2 font-medium">金額</th>
                          <th className="text-center py-1.5 px-2 font-medium">狀態</th>
                        </tr>
                      </thead>
                      <tbody>
                        {status.bargain_holdings.map((h, i) => (
                          <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                            <td className="py-1.5 px-2 font-mono text-amber-400">R{h.round}</td>
                            <td className="py-1.5 px-2 truncate max-w-[120px]" title={h.market_slug}>{h.market_slug}</td>
                            <td className="py-1.5 px-2 text-center">
                              <span className={`px-1.5 py-0.5 rounded-full ${
                                h.side === 'UP' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
                              }`}>
                                {h.side}
                              </span>
                            </td>
                            <td className="py-1.5 px-2 text-right font-mono">{h.buy_price?.toFixed(4)}</td>
                            <td className="py-1.5 px-2 text-right font-mono">{h.shares?.toFixed(1)}</td>
                            <td className="py-1.5 px-2 text-right font-mono">${h.amount_usd?.toFixed(2)}</td>
                            <td className="py-1.5 px-2 text-center">
                              <span className={`px-1.5 py-0.5 rounded-full ${
                                h.status === 'holding' ? 'bg-amber-500/10 text-amber-400' :
                                h.status === 'paired' ? 'bg-emerald-500/10 text-emerald-400' :
                                'bg-red-500/10 text-red-400'
                              }`}>
                                {h.status === 'holding' ? '持有' : h.status === 'paired' ? '已配對' : '止損'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-gray-600 text-center py-6">暫無撿便宜持倉</p>
                )}
              </div>
            </div>

            {/* Opportunities + Merge — side by side */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Opportunities */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium text-gray-400 flex items-center gap-2">
                    <Zap className="w-4 h-4" />
                    套利機會
                  </h3>
                  <button
                    onClick={scanMarkets}
                    disabled={loading}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
                    掃描
                  </button>
                </div>

                {(status?.current_opportunities || []).length === 0 ? (
                  <div className="text-center py-6 text-gray-600">
                    <Zap className="w-6 h-6 mx-auto mb-1.5 opacity-50" />
                    <p className="text-xs">暫無套利機會</p>
                  </div>
                ) : (
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {status.current_opportunities.map((opp, i) => (
                      <div key={i} className={`rounded-lg p-3 border ${
                        opp.is_viable
                          ? 'bg-emerald-500/5 border-emerald-500/20'
                          : 'bg-gray-800/50 border-gray-700/50'
                      }`}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-medium">
                            {opp.is_viable ? '💰 可執行' : '⏳ 不可執行'}
                          </span>
                          <span className={`text-xs font-mono ${
                            opp.profit_pct > 0 ? 'text-emerald-400' : 'text-gray-400'
                          }`}>
                            {opp.profit_pct > 0 ? '+' : ''}{opp.profit_pct.toFixed(2)}%
                          </span>
                        </div>
                        <p className="text-[10px] text-gray-500">{opp.reason}</p>
                        <div className="grid grid-cols-3 gap-2 mt-1.5 text-[10px]">
                          <div>
                            <span className="text-gray-500">利潤</span>
                            <p className="font-mono text-emerald-400">${opp.potential_profit.toFixed(4)}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">成本</span>
                            <p className="font-mono">{opp.price_info?.total_cost?.toFixed(4)}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">價差</span>
                            <p className="font-mono">{opp.price_info?.spread?.toFixed(4)}</p>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Merge Panel (compact) */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium text-gray-400 flex items-center gap-2">
                    <GitMerge className="w-4 h-4 text-cyan-400" />
                    持倉合併
                  </h3>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={toggleAutoMerge}
                      className={`flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg transition-colors ${
                        mergeStatus?.auto_merge_enabled
                          ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                          : 'bg-gray-800 text-gray-500 border border-gray-700'
                      }`}
                    >
                      <ArrowRightLeft className="w-3 h-3" />
                      {mergeStatus?.auto_merge_enabled ? '自動: 開' : '自動: 關'}
                    </button>
                    <button
                      onClick={mergeAll}
                      className="flex items-center gap-1 text-[10px] px-2 py-1 bg-cyan-600 hover:bg-cyan-700 rounded-lg transition-colors"
                    >
                      <Layers className="w-3 h-3" />
                      全部合併
                    </button>
                    <button
                      onClick={() => setMergeOpen(!mergeOpen)}
                      className="p-1 rounded-lg hover:bg-gray-800 transition-colors"
                    >
                      {mergeOpen ? <ChevronUp className="w-3.5 h-3.5 text-gray-400" /> : <ChevronDown className="w-3.5 h-3.5 text-gray-400" />}
                    </button>
                  </div>
                </div>

                {/* Merge Stats */}
                <div className="grid grid-cols-4 gap-2 mb-3">
                  <div className="bg-gray-800/50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">追蹤</p>
                    <p className="text-sm font-bold font-mono">{mergeStatus?.total_tracked ?? 0}</p>
                  </div>
                  <div className="bg-gray-800/50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">可合併</p>
                    <p className="text-sm font-bold font-mono text-cyan-400">{(mergeStatus?.total_mergeable ?? 0).toFixed(0)}</p>
                  </div>
                  <div className="bg-gray-800/50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">已合併</p>
                    <p className="text-sm font-bold font-mono text-emerald-400">${(mergeStatus?.total_merged_usdc ?? 0).toFixed(2)}</p>
                  </div>
                  <div className="bg-gray-800/50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">次數</p>
                    <p className="text-sm font-bold font-mono">{mergeStatus?.merge_count ?? 0}</p>
                  </div>
                </div>

                {/* Expandable Positions */}
                {mergeOpen && (
                  <div className="space-y-3 max-h-48 overflow-y-auto">
                    {(mergeStatus?.positions || []).length === 0 ? (
                      <p className="text-xs text-gray-600 py-2 text-center">尚無追蹤持倉</p>
                    ) : (
                      mergeStatus.positions.map((pos, i) => (
                        <div key={i} className="bg-gray-800/50 rounded-lg p-2.5 border border-gray-700/50">
                          <div className="flex items-center justify-between">
                            <div>
                              <p className="text-xs font-medium truncate max-w-[160px]">{pos.market_slug}</p>
                              <p className="text-[10px] text-gray-500 font-mono">CID: {pos.condition_id?.slice(0, 12)}...</p>
                            </div>
                            <button
                              onClick={() => mergeOne(pos.condition_id)}
                              disabled={pos.mergeable_amount < 1}
                              className="flex items-center gap-1 text-[10px] px-2 py-1 bg-cyan-600 hover:bg-cyan-700 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                            >
                              <GitMerge className="w-3 h-3" />
                              合併
                            </button>
                          </div>
                          <div className="grid grid-cols-3 gap-2 mt-1.5 text-[10px]">
                            <div>
                              <span className="text-gray-500">UP</span>
                              <p className="font-mono text-emerald-400">{pos.up_balance?.toFixed(1)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500">DOWN</span>
                              <p className="font-mono text-red-400">{pos.down_balance?.toFixed(1)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500">可合併</span>
                              <p className="font-mono text-cyan-400">{pos.mergeable_amount?.toFixed(1)}</p>
                            </div>
                          </div>
                        </div>
                      ))
                    )}

                    {(mergeStatus?.merge_history || []).length > 0 && (
                      <div>
                        <h4 className="text-[10px] text-gray-500 mb-1 font-medium">合併記錄</h4>
                        <div className="space-y-1">
                          {mergeStatus.merge_history.slice(0, 5).map((mr, i) => (
                            <div key={i} className="bg-gray-800/30 rounded p-1.5 text-[10px] flex items-center justify-between">
                              <div className="flex items-center gap-1.5">
                                <span className={`px-1 py-0.5 rounded ${
                                  mr.status === 'success' ? 'bg-emerald-500/10 text-emerald-400' :
                                  mr.status === 'simulated' ? 'bg-amber-500/10 text-amber-400' :
                                  'bg-red-500/10 text-red-400'
                                }`}>
                                  {mr.status === 'success' ? '成功' : mr.status === 'simulated' ? '模擬' : '失敗'}
                                </span>
                                <span className="text-gray-400 truncate max-w-[100px]">{mr.market_slug}</span>
                              </div>
                              <span className="font-mono text-emerald-400">${mr.usdc_received?.toFixed(2)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Trade History (compact) + Logs — side by side */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Recent Trades */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
                  <Shield className="w-4 h-4" />
                  最近交易
                </h3>
                {(status?.trade_history || []).length === 0 && trades.length === 0 ? (
                  <p className="text-xs text-gray-600 text-center py-6">尚無交易記錄</p>
                ) : (
                  <div className="overflow-x-auto max-h-64 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-gray-900">
                        <tr className="text-gray-500 border-b border-gray-800">
                          <th className="text-left py-1.5 pr-2 font-medium">時間</th>
                          <th className="text-left py-1.5 px-2 font-medium">市場</th>
                          <th className="text-right py-1.5 px-2 font-medium">成本</th>
                          <th className="text-right py-1.5 px-2 font-medium">利潤</th>
                          <th className="text-center py-1.5 pl-2 font-medium">狀態</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...(status?.trade_history || []), ...trades].slice(0, 20).map((t, i) => (
                          <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                            <td className="py-1.5 pr-2 text-gray-400 font-mono whitespace-nowrap">
                              {new Date(t.timestamp).toLocaleTimeString('zh-TW')}
                            </td>
                            <td className="py-1.5 px-2 text-gray-300 truncate max-w-[100px]" title={t.market_slug}>
                              {t.market_slug}
                            </td>
                            <td className="py-1.5 px-2 text-right font-mono">{t.total_cost?.toFixed(4)}</td>
                            <td className={`py-1.5 px-2 text-right font-mono ${
                              (t.expected_profit ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'
                            }`}>
                              ${t.expected_profit?.toFixed(4)}
                            </td>
                            <td className="py-1.5 pl-2 text-center">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                                t.status === 'executed' ? 'bg-emerald-500/10 text-emerald-400' :
                                t.status === 'simulated' ? 'bg-amber-500/10 text-amber-400' :
                                'bg-red-500/10 text-red-400'
                              }`}>
                                {t.status === 'executed' ? '成交' : t.status === 'simulated' ? '模擬' : '失敗'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Logs */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
                  <Activity className="w-4 h-4" />
                  運行日誌
                </h3>
                <div className="bg-gray-950 rounded-lg p-3 max-h-64 overflow-y-auto font-mono text-[11px] space-y-0.5">
                  {(status?.logs || []).length === 0 ? (
                    <p className="text-gray-600">等待機器人啟動...</p>
                  ) : (
                    status.logs.map((log, i) => (
                      <p key={i} className={`${
                        log.includes('❌') ? 'text-red-400' :
                        log.includes('💰') ? 'text-emerald-400' :
                        log.includes('🔸') ? 'text-amber-400' :
                        log.includes('🔴') ? 'text-red-400' :
                        log.includes('⚙️') ? 'text-orange-400' :
                        log.includes('🚀') ? 'text-blue-400' :
                        log.includes('🏷️') ? 'text-amber-300' :
                        log.includes('✅') ? 'text-emerald-400' :
                        log.includes('🛡️') ? 'text-violet-400' :
                        'text-gray-400'
                      }`}>
                        {log}
                      </p>
                    ))
                  )}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </div>
          </>
        )}

        {/* ═══════════════ ANALYTICS TAB ═══════════════ */}
        {activeView === 'analytics' && (
          <AnalyticsDashboard />
        )}

        {/* ═══════════════ SETTINGS TAB ═══════════════ */}
        {activeView === 'settings' && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Settings className="w-5 h-5 text-orange-400" />
              機器人設定
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <ConfigField
                label="私鑰"
                type={showKey ? 'text' : 'password'}
                value={configForm.private_key || ''}
                onChange={(v) => setConfigForm({ ...configForm, private_key: v })}
                suffix={
                  <button onClick={() => setShowKey(!showKey)} className="text-gray-500 hover:text-gray-300">
                    {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                }
              />
              <ConfigField
                label="資金地址 (Funder)"
                value={configForm.funder_address || ''}
                onChange={(v) => setConfigForm({ ...configForm, funder_address: v })}
              />
              <ConfigField
                label="簽名類型"
                type="number"
                value={configForm.signature_type ?? 0}
                onChange={(v) => setConfigForm({ ...configForm, signature_type: parseInt(v) })}
                hint="0=EOA, 1=Email, 2=Proxy"
              />
              <ConfigField
                label="目標配對成本"
                type="number"
                step="0.001"
                value={configForm.target_pair_cost ?? 0.99}
                onChange={(v) => setConfigForm({ ...configForm, target_pair_cost: parseFloat(v) })}
                hint="低於此值觸發套利"
              />
              <ConfigField
                label="每筆下單數量"
                type="number"
                value={configForm.order_size ?? 50}
                onChange={(v) => setConfigForm({ ...configForm, order_size: parseFloat(v) })}
              />
              <ConfigField
                label="最少剩餘時間 (秒)"
                type="number"
                value={configForm.min_time_remaining_seconds ?? 3600}
                onChange={(v) => setConfigForm({ ...configForm, min_time_remaining_seconds: parseInt(v) })}
                hint="每日市場建議 3600 秒 (1 小時)"
              />
              <ConfigField
                label="每市場最大交易次數"
                type="number"
                value={configForm.max_trades_per_market ?? 10}
                onChange={(v) => setConfigForm({ ...configForm, max_trades_per_market: parseInt(v) })}
              />
              <ConfigField
                label="交易冷卻期 (秒)"
                type="number"
                value={configForm.trade_cooldown_seconds ?? 300}
                onChange={(v) => setConfigForm({ ...configForm, trade_cooldown_seconds: parseInt(v) })}
                hint="每日市場建議 300 秒 (5 分鐘)"
              />
              <ConfigField
                label="最低流動性"
                type="number"
                value={configForm.min_liquidity ?? 50}
                onChange={(v) => setConfigForm({ ...configForm, min_liquidity: parseFloat(v) })}
              />
              <ConfigField
                label="監控幣種 (逗號分隔)"
                value={(configForm.crypto_symbols || []).join(',')}
                onChange={(v) => setConfigForm({ ...configForm, crypto_symbols: v.split(',').map(s => s.trim()) })}
              />
              <div className="flex items-center gap-3">
                <label className="text-sm text-gray-400">模擬模式</label>
                <button
                  onClick={() => setConfigForm({ ...configForm, dry_run: !configForm.dry_run })}
                  className={`relative w-12 h-6 rounded-full transition-colors ${
                    configForm.dry_run !== false ? 'bg-emerald-600' : 'bg-red-600'
                  }`}
                >
                  <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                    configForm.dry_run !== false ? 'left-0.5' : 'left-6'
                  }`} />
                </button>
                <span className="text-xs text-gray-500">
                  {configForm.dry_run !== false ? '開啟 (安全)' : '關閉 (真實交易!)'}
                </span>
              </div>
            </div>

            {/* Bargain Hunter Settings */}
            <div className="border-t border-gray-800 pt-4 mt-2">
              <h3 className="text-sm font-medium text-amber-400 mb-3 flex items-center gap-2">
                🏷️ 撿便宜策略設定
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="flex items-center gap-3">
                  <label className="text-sm text-gray-400">撿便宜策略</label>
                  <button
                    onClick={() => setConfigForm({ ...configForm, bargain_enabled: !configForm.bargain_enabled })}
                    className={`relative w-12 h-6 rounded-full transition-colors ${
                      configForm.bargain_enabled !== false ? 'bg-amber-600' : 'bg-gray-600'
                    }`}
                  >
                    <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                      configForm.bargain_enabled !== false ? 'left-6' : 'left-0.5'
                    }`} />
                  </button>
                  <span className="text-xs text-gray-500">
                    {configForm.bargain_enabled !== false ? '啟用' : '停用'}
                  </span>
                </div>
                <ConfigField
                  label="最低買入價"
                  type="number"
                  step="0.01"
                  value={configForm.bargain_min_price ?? 0.10}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_min_price: parseFloat(v) })}
                  hint="低於此價格不買 (防垃圾股)"
                />
                <ConfigField
                  label="低價閾值"
                  type="number"
                  step="0.01"
                  value={configForm.bargain_price_threshold ?? 0.49}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_price_threshold: parseFloat(v) })}
                  hint="低於此價格觸發買入 (如 0.49)"
                />
                <ConfigField
                  label="配對成本閾值"
                  type="number"
                  step="0.01"
                  value={configForm.bargain_pair_threshold ?? 0.99}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_pair_threshold: parseFloat(v) })}
                  hint="兩側合計低於此才配對"
                />
                <ConfigField
                  label="止損幅度"
                  type="number"
                  step="0.01"
                  value={configForm.bargain_stop_loss_cents ?? 0.02}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_stop_loss_cents: parseFloat(v) })}
                  hint="跌多少賣出 (如 0.02 = 2 分錢)"
                />
                <ConfigField
                  label="止損延遲 (分鐘)"
                  type="number"
                  value={configForm.bargain_stop_loss_defer_minutes ?? 30}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_stop_loss_defer_minutes: parseInt(v) })}
                  hint="止損觸發後延遲多久才執行"
                />
                <ConfigField
                  label="堆疊上限"
                  type="number"
                  value={configForm.bargain_max_rounds ?? 56}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_max_rounds: parseInt(v) })}
                  hint="最多堆疊幾輪"
                />
              </div>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                onClick={saveConfig}
                className="px-4 py-2 bg-orange-600 hover:bg-orange-700 rounded-lg text-sm font-medium transition-colors"
              >
                儲存設定
              </button>
              <button
                onClick={() => setConfigForm(config || {})}
                className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors"
              >
                重置
              </button>
            </div>
          </div>
        )}

      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 mt-8 py-4 text-center text-xs text-gray-600">
        Polymarket 每日套利機器人 v1.0 | 僅供教育和研究用途 | 交易有風險，請謹慎操作
      </footer>
    </div>
  )
}

function StatCard({ icon, label, value, color = 'gray' }) {
  const colors = {
    emerald: 'from-emerald-500/10 to-emerald-500/5 border-emerald-500/20',
    blue: 'from-blue-500/10 to-blue-500/5 border-blue-500/20',
    violet: 'from-violet-500/10 to-violet-500/5 border-violet-500/20',
    amber: 'from-amber-500/10 to-amber-500/5 border-amber-500/20',
    orange: 'from-orange-500/10 to-orange-500/5 border-orange-500/20',
    red: 'from-red-500/10 to-red-500/5 border-red-500/20',
    gray: 'from-gray-500/10 to-gray-500/5 border-gray-500/20',
  }
  const iconColors = {
    emerald: 'text-emerald-400',
    blue: 'text-blue-400',
    violet: 'text-violet-400',
    amber: 'text-amber-400',
    orange: 'text-orange-400',
    red: 'text-red-400',
    gray: 'text-gray-400',
  }

  return (
    <div className={`bg-gradient-to-br ${colors[color]} border rounded-xl p-4`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={iconColors[color]}>{icon}</span>
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <p className="text-xl font-bold">{value}</p>
    </div>
  )
}

function ConfigField({ label, value, onChange, type = 'text', step, hint, suffix }) {
  return (
    <div>
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <div className="flex items-center gap-2">
        <input
          type={type}
          step={step}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-orange-500 transition-colors"
        />
        {suffix}
      </div>
      {hint && <p className="text-xs text-gray-600 mt-0.5">{hint}</p>}
    </div>
  )
}

export default App
