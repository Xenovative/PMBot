import { useState, useEffect, useRef } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import AnalyticsDashboard from './AnalyticsDashboard'
import {
  Play, Square, Settings, TrendingUp, TrendingDown, Activity,
  Wifi, WifiOff, DollarSign, BarChart3, Clock, AlertTriangle,
  RefreshCw, Zap, Shield, ChevronDown, ChevronUp, Eye, EyeOff,
  GitMerge, ArrowRightLeft, Layers
} from 'lucide-react'

const API = ''

function App() {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
  const { status, markets, trades, mergeStatus, connected } = useWebSocket(wsUrl)
  const [config, setConfig] = useState(null)
  const [configOpen, setConfigOpen] = useState(false)
  const [configForm, setConfigForm] = useState({})
  const [showKey, setShowKey] = useState(false)
  const [manualMarkets, setManualMarkets] = useState([])
  const [loading, setLoading] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
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
        setConfigOpen(false)
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
  const displayMarkets = markets.length > 0 ? markets : manualMarkets

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

            {/* Settings */}
            <button
              onClick={() => setConfigOpen(!configOpen)}
              className="p-2 rounded-lg hover:bg-gray-800 transition-colors"
            >
              <Settings className="w-4 h-4 text-gray-400" />
            </button>

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
        {/* Config Panel */}
        {configOpen && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <Settings className="w-5 h-5 text-orange-400" />
                機器人設定
              </h2>
              <button onClick={() => setConfigOpen(false)} className="text-gray-500 hover:text-gray-300">
                <ChevronUp className="w-5 h-5" />
              </button>
            </div>

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
                onClick={() => { setConfigOpen(false); setConfigForm(config || {}) }}
                className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* Stats Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            icon={<Activity className="w-5 h-5" />}
            label="機器人狀態"
            value={isRunning ? '運行中' : '已停止'}
            color={isRunning ? 'emerald' : 'gray'}
          />
          <StatCard
            icon={<BarChart3 className="w-5 h-5" />}
            label="總交易次數"
            value={status?.total_trades ?? 0}
            color="blue"
          />
          <StatCard
            icon={<DollarSign className="w-5 h-5" />}
            label="累計利潤"
            value={`$${(status?.total_profit ?? 0).toFixed(4)}`}
            color={(status?.total_profit ?? 0) > 0 ? 'emerald' : 'gray'}
          />
          <StatCard
            icon={<RefreshCw className="w-5 h-5" />}
            label="掃描次數"
            value={status?.scan_count ?? 0}
            color="amber"
          />
        </div>

        {/* Multi-Market Price Display */}
        {status?.market_prices && Object.keys(status.market_prices).length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
            <h3 className="text-sm font-medium text-gray-400 mb-4 flex items-center gap-2">
              <TrendingUp className="w-4 h-4" />
              即時價格監控 — {Object.keys(status.market_prices).length} 個市場
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-800">
                    <th className="text-left py-2 pr-4 font-medium">市場</th>
                    <th className="text-right py-2 px-3 font-medium">
                      <span className="flex items-center justify-end gap-1"><TrendingUp className="w-3 h-3 text-emerald-400" />UP</span>
                    </th>
                    <th className="text-right py-2 px-3 font-medium">
                      <span className="flex items-center justify-end gap-1"><TrendingDown className="w-3 h-3 text-red-400" />DOWN</span>
                    </th>
                    <th className="text-right py-2 px-3 font-medium">
                      <span className="flex items-center justify-end gap-1"><DollarSign className="w-3 h-3 text-amber-400" />總成本</span>
                    </th>
                    <th className="text-right py-2 pl-3 font-medium">
                      <span className="flex items-center justify-end gap-1"><Zap className="w-3 h-3 text-orange-400" />價差</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(status.market_prices)
                    .sort(([,a], [,b]) => a.total_cost - b.total_cost)
                    .map(([slug, price]) => {
                      const profitable = price.total_cost < (config?.target_pair_cost ?? 0.99);
                      return (
                        <tr key={slug} className={`border-b border-gray-800/50 ${profitable ? 'bg-emerald-500/5' : ''}`}>
                          <td className="py-2.5 pr-4">
                            <span className="text-xs font-mono text-gray-300 truncate block max-w-[200px]" title={slug}>
                              {slug}
                            </span>
                          </td>
                          <td className="text-right py-2.5 px-3 font-mono text-white">
                            {price.up_best_ask > 0 ? price.up_best_ask.toFixed(4) : price.up_price.toFixed(4)}
                          </td>
                          <td className="text-right py-2.5 px-3 font-mono text-white">
                            {price.down_best_ask > 0 ? price.down_best_ask.toFixed(4) : price.down_price.toFixed(4)}
                          </td>
                          <td className={`text-right py-2.5 px-3 font-mono font-bold ${profitable ? 'text-emerald-400' : 'text-white'}`}>
                            {price.total_cost.toFixed(4)}
                          </td>
                          <td className={`text-right py-2.5 pl-3 font-mono ${price.spread > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {price.spread > 0 ? '+' : ''}{price.spread.toFixed(4)}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Markets & Opportunities */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Markets */}
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-gray-400 flex items-center gap-2">
                <BarChart3 className="w-4 h-4" />
                活躍每日市場
              </h3>
              <button
                onClick={scanMarkets}
                disabled={loading}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
                手動掃描
              </button>
            </div>

            {displayMarkets.length === 0 ? (
              <div className="text-center py-8 text-gray-600">
                <BarChart3 className="w-8 h-8 mx-auto mb-2 opacity-50" />
                <p className="text-sm">尚未發現市場</p>
                <p className="text-xs mt-1">啟動機器人或點擊手動掃描</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {displayMarkets.map((m, i) => (
                  <div key={m.id || i} className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{m.question || m.slug}</p>
                        <p className="text-xs text-gray-500 mt-0.5">{m.slug}</p>
                      </div>
                      <div className="flex items-center gap-1.5 ml-2">
                        <Clock className="w-3 h-3 text-gray-500" />
                        <span className={`text-xs font-mono ${
                          m.time_remaining_seconds < 3600 ? 'text-red-400' : 'text-gray-400'
                        }`}>
                          {m.time_remaining_display}
                        </span>
                      </div>
                    </div>
                    <div className="flex gap-4 mt-2 text-xs text-gray-500">
                      <span>UP: {m.up_token_id ? '✓' : '✗'}</span>
                      <span>DOWN: {m.down_token_id ? '✓' : '✗'}</span>
                      {m.accepting_orders && (
                        <span className="text-emerald-500">接受下單</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Opportunities */}
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
            <h3 className="text-sm font-medium text-gray-400 mb-4 flex items-center gap-2">
              <Zap className="w-4 h-4" />
              套利機會
            </h3>

            {(status?.current_opportunities || []).length === 0 ? (
              <div className="text-center py-8 text-gray-600">
                <Zap className="w-8 h-8 mx-auto mb-2 opacity-50" />
                <p className="text-sm">暫無套利機會</p>
                <p className="text-xs mt-1">機器人持續監控中...</p>
              </div>
            ) : (
              <div className="space-y-3">
                {status.current_opportunities.map((opp, i) => (
                  <div key={i} className={`rounded-lg p-4 border ${
                    opp.is_viable
                      ? 'bg-emerald-500/5 border-emerald-500/20'
                      : 'bg-gray-800/50 border-gray-700/50'
                  }`}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium">
                        {opp.is_viable ? '💰 可執行' : '⏳ 不可執行'}
                      </span>
                      <span className={`text-sm font-mono ${
                        opp.profit_pct > 0 ? 'text-emerald-400' : 'text-gray-400'
                      }`}>
                        {opp.profit_pct > 0 ? '+' : ''}{opp.profit_pct.toFixed(2)}%
                      </span>
                    </div>
                    <p className="text-xs text-gray-400">{opp.reason}</p>
                    <div className="grid grid-cols-3 gap-2 mt-2 text-xs">
                      <div>
                        <span className="text-gray-500">預期利潤</span>
                        <p className="font-mono text-emerald-400">${opp.potential_profit.toFixed(4)}</p>
                      </div>
                      <div>
                        <span className="text-gray-500">總成本</span>
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
        </div>

        {/* Merge Panel */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-gray-400 flex items-center gap-2">
              <GitMerge className="w-4 h-4 text-cyan-400" />
              持倉合併 (CTF Merge)
            </h3>
            <div className="flex items-center gap-2">
              <button
                onClick={toggleAutoMerge}
                className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors ${
                  mergeStatus?.auto_merge_enabled
                    ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                    : 'bg-gray-800 text-gray-500 border border-gray-700'
                }`}
              >
                <ArrowRightLeft className="w-3 h-3" />
                {mergeStatus?.auto_merge_enabled ? '自動合併: 開' : '自動合併: 關'}
              </button>
              <button
                onClick={mergeAll}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-cyan-600 hover:bg-cyan-700 rounded-lg transition-colors"
              >
                <Layers className="w-3 h-3" />
                全部合併
              </button>
              <button
                onClick={() => setMergeOpen(!mergeOpen)}
                className="p-1.5 rounded-lg hover:bg-gray-800 transition-colors"
              >
                {mergeOpen ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
              </button>
            </div>
          </div>

          {/* Merge Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500">追蹤持倉</p>
              <p className="text-lg font-bold font-mono">{mergeStatus?.total_tracked ?? 0}</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500">可合併數量</p>
              <p className="text-lg font-bold font-mono text-cyan-400">{(mergeStatus?.total_mergeable ?? 0).toFixed(0)}</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500">已合併 USDC</p>
              <p className="text-lg font-bold font-mono text-emerald-400">${(mergeStatus?.total_merged_usdc ?? 0).toFixed(2)}</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500">合併次數</p>
              <p className="text-lg font-bold font-mono">{mergeStatus?.merge_count ?? 0}</p>
            </div>
          </div>

          {/* Positions */}
          {mergeOpen && (
            <div className="space-y-4">
              <div>
                <h4 className="text-xs text-gray-500 mb-2 font-medium">配對持倉</h4>
                {(mergeStatus?.positions || []).length === 0 ? (
                  <p className="text-xs text-gray-600 py-3 text-center">尚無追蹤持倉 — 交易後自動追蹤</p>
                ) : (
                  <div className="space-y-2">
                    {mergeStatus.positions.map((pos, i) => (
                      <div key={i} className="bg-gray-800/50 rounded-lg p-3 border border-gray-700/50">
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm font-medium">{pos.market_slug}</p>
                            <p className="text-xs text-gray-500 font-mono mt-0.5">
                              CID: {pos.condition_id?.slice(0, 16)}...
                            </p>
                          </div>
                          <button
                            onClick={() => mergeOne(pos.condition_id)}
                            disabled={pos.mergeable_amount < 1}
                            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-cyan-600 hover:bg-cyan-700 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                          >
                            <GitMerge className="w-3 h-3" />
                            合併
                          </button>
                        </div>
                        <div className="grid grid-cols-3 gap-3 mt-2 text-xs">
                          <div>
                            <span className="text-gray-500">UP 餘額</span>
                            <p className="font-mono text-emerald-400">{pos.up_balance?.toFixed(1)}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">DOWN 餘額</span>
                            <p className="font-mono text-red-400">{pos.down_balance?.toFixed(1)}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">可合併</span>
                            <p className="font-mono text-cyan-400">{pos.mergeable_amount?.toFixed(1)}</p>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {(mergeStatus?.merge_history || []).length > 0 && (
                <div>
                  <h4 className="text-xs text-gray-500 mb-2 font-medium">合併記錄</h4>
                  <div className="space-y-1.5 max-h-48 overflow-y-auto">
                    {mergeStatus.merge_history.map((mr, i) => (
                      <div key={i} className="bg-gray-800/30 rounded-lg p-2.5 text-xs flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className={`px-1.5 py-0.5 rounded ${
                            mr.status === 'success' ? 'bg-emerald-500/10 text-emerald-400' :
                            mr.status === 'simulated' ? 'bg-amber-500/10 text-amber-400' :
                            'bg-red-500/10 text-red-400'
                          }`}>
                            {mr.status === 'success' ? '成功' : mr.status === 'simulated' ? '模擬' : '失敗'}
                          </span>
                          <span className="text-gray-400 truncate max-w-[150px]">{mr.market_slug}</span>
                        </div>
                        <div className="flex items-center gap-3 font-mono">
                          <span className="text-gray-500">{mr.amount?.toFixed(0)} 對</span>
                          <span className="text-emerald-400">${mr.usdc_received?.toFixed(2)}</span>
                          {mr.tx_hash && (
                            <a
                              href={`https://polygonscan.com/tx/${mr.tx_hash}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-cyan-400 hover:underline"
                            >
                              TX
                            </a>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(mergeStatus?.logs || []).length > 0 && (
                <div>
                  <h4 className="text-xs text-gray-500 mb-2 font-medium">合併日誌</h4>
                  <div className="bg-gray-950 rounded-lg p-3 max-h-32 overflow-y-auto font-mono text-xs space-y-0.5">
                    {mergeStatus.logs.map((log, i) => (
                      <p key={i} className={`${
                        log.includes('❌') ? 'text-red-400' :
                        log.includes('✅') ? 'text-emerald-400' :
                        log.includes('🔄') ? 'text-cyan-400' :
                        'text-gray-400'
                      }`}>{log}</p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Bargain Holdings */}
        {(status?.bargain_holdings || []).length > 0 && (
          <div className="bg-gray-900 border border-amber-800/30 rounded-xl p-6">
            <h3 className="text-sm font-medium text-amber-400 mb-4 flex items-center gap-2">
              🏷️ 撿便宜持倉 ({status.bargain_holdings.length} 筆)
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-800">
                    <th className="text-left py-2 px-2">輪次</th>
                    <th className="text-left py-2 px-2">市場</th>
                    <th className="text-center py-2 px-2">方向</th>
                    <th className="text-right py-2 px-2">買入價</th>
                    <th className="text-right py-2 px-2">股數</th>
                    <th className="text-right py-2 px-2">金額</th>
                    <th className="text-left py-2 px-2">時間</th>
                    <th className="text-center py-2 px-2">狀態</th>
                  </tr>
                </thead>
                <tbody>
                  {status.bargain_holdings.map((h, i) => (
                    <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="py-2 px-2 text-xs font-mono text-amber-400">R{h.round}</td>
                      <td className="py-2 px-2 text-xs truncate max-w-[140px]">{h.market_slug}</td>
                      <td className="py-2 px-2 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          h.side === 'UP' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
                        }`}>
                          {h.side}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right font-mono">{h.buy_price?.toFixed(4)}</td>
                      <td className="py-2 px-2 text-right font-mono">{h.shares?.toFixed(1)}</td>
                      <td className="py-2 px-2 text-right font-mono">${h.amount_usd?.toFixed(2)}</td>
                      <td className="py-2 px-2 text-xs text-gray-400 font-mono">
                        {new Date(h.timestamp).toLocaleTimeString('zh-TW')}
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400">
                          持有中
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Trade History */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
          <h3 className="text-sm font-medium text-gray-400 mb-4 flex items-center gap-2">
            <Shield className="w-4 h-4" />
            交易記錄
          </h3>

          {(status?.trade_history || []).length === 0 && trades.length === 0 ? (
            <div className="text-center py-6 text-gray-600">
              <p className="text-sm">尚無交易記錄</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-800">
                    <th className="text-left py-2 px-2">時間</th>
                    <th className="text-left py-2 px-2">市場</th>
                    <th className="text-right py-2 px-2">UP</th>
                    <th className="text-right py-2 px-2">DOWN</th>
                    <th className="text-right py-2 px-2">總成本</th>
                    <th className="text-right py-2 px-2">數量</th>
                    <th className="text-right py-2 px-2">利潤</th>
                    <th className="text-center py-2 px-2">狀態</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(status?.trade_history || []), ...trades].map((t, i) => (
                    <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="py-2 px-2 text-xs text-gray-400 font-mono">
                        {new Date(t.timestamp).toLocaleTimeString('zh-TW')}
                      </td>
                      <td className="py-2 px-2 text-xs truncate max-w-[120px]">{t.market_slug}</td>
                      <td className="py-2 px-2 text-right font-mono text-emerald-400">{t.up_price?.toFixed(4)}</td>
                      <td className="py-2 px-2 text-right font-mono text-red-400">{t.down_price?.toFixed(4)}</td>
                      <td className="py-2 px-2 text-right font-mono">{t.total_cost?.toFixed(4)}</td>
                      <td className="py-2 px-2 text-right font-mono">{t.order_size}</td>
                      <td className="py-2 px-2 text-right font-mono text-emerald-400">
                        ${t.expected_profit?.toFixed(4)}
                      </td>
                      <td className="py-2 px-2 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          t.status === 'executed' ? 'bg-emerald-500/10 text-emerald-400' :
                          t.status === 'simulated' ? 'bg-amber-500/10 text-amber-400' :
                          'bg-red-500/10 text-red-400'
                        }`}>
                          {t.status === 'executed' ? '已執行' :
                           t.status === 'simulated' ? '模擬' : '失敗'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Analytics Dashboard */}
        <AnalyticsDashboard />

        {/* Logs */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
          <h3 className="text-sm font-medium text-gray-400 mb-4 flex items-center gap-2">
            <Activity className="w-4 h-4" />
            運行日誌
          </h3>
          <div className="bg-gray-950 rounded-lg p-4 max-h-64 overflow-y-auto font-mono text-xs space-y-0.5">
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
                  'text-gray-400'
                }`}>
                  {log}
                </p>
              ))
            )}
            <div ref={logsEndRef} />
          </div>
        </div>

        {/* Arbitrage Explainer */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
          <h3 className="text-sm font-medium text-gray-400 mb-4 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            每日套利原理說明
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm text-gray-400">
            <div>
              <h4 className="text-white font-medium mb-2">什麼是每日套利？</h4>
              <p className="mb-2">
                每日 Up or Down 市場與 15 分鐘市場結構相同：當 UP + DOWN &lt; $1.00 時，同時買入兩邊穩賺！
              </p>
              <div className="bg-gray-800/50 rounded-lg p-3 text-xs font-mono space-y-1">
                <p>UP 價格:  $0.48</p>
                <p>DOWN 價格: $0.50</p>
                <p>總成本:   $0.98</p>
                <p className="border-t border-gray-700 pt-1 mt-1">
                  買入 50 股 → 投資 $49.00
                </p>
                <p className="text-emerald-400">
                  無論漲跌 → 回收 $50.00 → 利潤 $1.00 (2.04%)
                </p>
              </div>
            </div>
            <div>
              <h4 className="text-white font-medium mb-2">每日 vs 15 分鐘市場</h4>
              <ul className="space-y-1.5 text-xs">
                <li className="flex items-start gap-2">
                  <Clock className="w-3 h-3 mt-0.5 text-orange-400 shrink-0" />
                  <span>持續時間: ~24 小時（12:00 PM ET 到次日 12:00 PM ET）</span>
                </li>
                <li className="flex items-start gap-2">
                  <BarChart3 className="w-3 h-3 mt-0.5 text-orange-400 shrink-0" />
                  <span>解析來源: Binance BTC/USDT 1 分鐘 K 線收盤價</span>
                </li>
                <li className="flex items-start gap-2">
                  <Shield className="w-3 h-3 mt-0.5 text-orange-400 shrink-0" />
                  <span>支援幣種: BTC、ETH、SOL</span>
                </li>
                <li className="flex items-start gap-2">
                  <DollarSign className="w-3 h-3 mt-0.5 text-orange-400 shrink-0" />
                  <span>更長的掃描間隔和冷卻期，適合低頻套利</span>
                </li>
                <li className="flex items-start gap-2">
                  <RefreshCw className="w-3 h-3 mt-0.5 text-orange-400 shrink-0" />
                  <span>每日自動搜尋新市場，無需手動操作</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
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

function PriceBox({ label, value, highlight = false, icon }) {
  return (
    <div className={`rounded-lg p-3 ${
      highlight ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-gray-800/50'
    }`}>
      <div className="flex items-center gap-1.5 mb-1">
        {icon}
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <p className={`text-lg font-mono font-bold ${
        highlight ? 'text-emerald-400' : 'text-white'
      }`}>
        {typeof value === 'number' ? value.toFixed(4) : value}
      </p>
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
