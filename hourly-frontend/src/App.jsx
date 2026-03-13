import { useState, useEffect, useRef } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import AnalyticsDashboard from './AnalyticsDashboard'
import LoginPage from './LoginPage'
import SecuritySettings from './SecuritySettings'
import {
  Play, Square, Settings, TrendingUp, TrendingDown, Activity,
  Wifi, WifiOff, DollarSign, BarChart3,
  RefreshCw, Zap, Shield, ChevronDown, ChevronUp, Eye, EyeOff,
  GitMerge, ArrowRightLeft, Layers
} from 'lucide-react'

const API = ''

function formatUsdPrice(incomingValue, maximumFractionDigits = 2) {
  const normalizedValue = Number(incomingValue)
  if (!Number.isFinite(normalizedValue) || normalizedValue <= 0) return '--'
  return `$${normalizedValue.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  })}`
}

function formatSignedUsdDifference(incomingValue, maximumFractionDigits = 2) {
  const normalizedValue = Number(incomingValue)
  if (!Number.isFinite(normalizedValue)) return '--'
  const absoluteValue = Math.abs(normalizedValue)
  const signPrefix = normalizedValue >= 0 ? '+' : '-'
  return `${signPrefix}$${absoluteValue.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  })}`
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('pmbot_hourly_token'))
  const [verified, setVerified] = useState(false)

  function handleLogin(newToken) {
    localStorage.setItem('pmbot_hourly_token', newToken)
    setToken(newToken)
    setVerified(true)
  }

  function handleLogout() {
    localStorage.removeItem('pmbot_hourly_token')
    setToken(null)
    setVerified(false)
  }

  // Auth headers helper
  const authHeaders = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
  }

  // Verify token on mount — block rendering until done
  useEffect(() => {
    if (!token) { setVerified(false); return }
    setVerified(false)
    fetch(`${API}/api/auth/verify`, { headers: { 'Authorization': `Bearer ${token}` } })
      .then(r => {
        if (r.status === 401) { handleLogout() }
        else { setVerified(true) }
      })
      .catch(() => { setVerified(true) }) // offline = let them through, API calls will fail anyway
  }, [token])

  // No token = login
  if (!token) {
    return <LoginPage onLogin={handleLogin} />
  }

  // Token exists but not yet verified = loading
  if (!verified) {
    return (
      <div className="min-h-screen flex items-center justify-center relative">
        <div className="fixed inset-0 z-0">
          <img src="/background.jpeg" alt="" className="w-full h-full object-cover" />
          <div className="absolute inset-0 bg-black/80" />
        </div>
        <div className="relative z-10 text-neon-cyan animate-pulse font-cyber text-lg">
          VERIFYING SESSION...
        </div>
      </div>
    )
  }

  return <Dashboard token={token} authHeaders={authHeaders} onLogout={handleLogout} />
}

function Dashboard({ token, authHeaders, onLogout }) {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
  const { status, markets, trades, mergeStatus, connected } = useWebSocket(wsUrl, token)
  const [polledStatus, setPolledStatus] = useState(null)
  const [config, setConfig] = useState(null)
  const [configForm, setConfigForm] = useState({})
  const [configErrors, setConfigErrors] = useState({})
  const [showKey, setShowKey] = useState(false)
  const [manualMarkets, setManualMarkets] = useState([])
  const [loading, setLoading] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [activeView, setActiveView] = useState('live')
  const [isStartingBot, setIsStartingBot] = useState(false)
  const logsEndRef = useRef(null)

  useEffect(() => {
    fetchConfig()
  }, [])

  useEffect(() => {
    let cancelled = false

    async function fetchStatus() {
      try {
        const res = await fetch(`${API}/api/status`, { headers: authHeaders })
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setPolledStatus(data)
      } catch (e) {
        console.error('Failed to fetch status:', e)
      }
    }

    fetchStatus()
    const intervalId = setInterval(fetchStatus, 3000)

    return () => {
      cancelled = true
      clearInterval(intervalId)
    }
  }, [authHeaders])

  // No auto-scroll on new log entries

  async function fetchConfig() {
    try {
      const res = await fetch(`${API}/api/config`, { headers: authHeaders })
      const data = await res.json()
      setConfig(data)
      setConfigForm(data)
    } catch (e) {
      console.error('Failed to fetch config:', e)
    }
  }

  function validateConfigForm(form) {
    const errors = {}
    const pk = (form.private_key || '').trim()
    if (pk) {
      const raw = pk.startsWith('0x') || pk.startsWith('0X') ? pk.slice(2) : pk
      if (!/^[0-9a-fA-F]{64}$/.test(raw))
        errors.private_key = `私鑰須為 64 位十六進位字串（32 bytes）。目前 ${raw.length} 位 — 是否貼了錢包地址？`
    }
    const fa = (form.funder_address || '').trim()
    if (fa && !/^0x[0-9a-fA-F]{40}$/.test(fa))
      errors.funder_address = '資金地址須為 0x 開頭的 40 位十六進位地址（共 42 字元）'
    const st = form.signature_type
    if (st !== undefined && st !== null && ![0, 1, 2].includes(Number(st)))
      errors.signature_type = '簽名類型須為 0、1 或 2'
    return errors
  }

  async function saveConfig() {
    const errors = validateConfigForm(configForm)
    setConfigErrors(errors)
    if (Object.keys(errors).length > 0) return
    try {
      const res = await fetch(`${API}/api/config`, {
        method: 'POST',
        headers: authHeaders,
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
    if (isStartingBot) return
    setIsStartingBot(true)
    try {
      const response = await fetch(`${API}/api/bot/start`, { method: 'POST', headers: authHeaders })
      if (!response.ok) {
        throw new Error(`Failed to start bot (${response.status})`)
      }
    } catch (e) {
      console.error('Failed to start bot:', e)
      setIsStartingBot(false)
    }
  }

  async function stopBot() {
    try {
      await fetch(`${API}/api/bot/stop`, { method: 'POST', headers: authHeaders })
    } catch (e) {
      console.error('Failed to stop bot:', e)
    }
  }

  async function toggleAutoMerge() {
    try {
      await fetch(`${API}/api/merge/toggle`, { method: 'POST', headers: authHeaders })
    } catch (e) {
      console.error('Failed to toggle merge:', e)
    }
  }

  async function mergeAll() {
    try {
      await fetch(`${API}/api/merge/all`, { method: 'POST', headers: authHeaders })
    } catch (e) {
      console.error('Failed to merge all:', e)
    }
  }

  async function mergeOne(conditionId) {
    try {
      await fetch(`${API}/api/merge/execute`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ condition_id: conditionId }),
      })
    } catch (e) {
      console.error('Failed to merge:', e)
    }
  }

  async function scanMarkets() {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/markets`, { headers: authHeaders })
      const data = await res.json()
      setManualMarkets(data)
    } catch (e) {
      console.error('Failed to scan markets:', e)
    }
    setLoading(false)
  }

  const effectiveStatus = status ?? polledStatus
  const isRunning = effectiveStatus?.running || false

  useEffect(() => {
    if (isRunning) {
      setIsStartingBot(false)
    }
  }, [isRunning])

  return (
    <div className="min-h-screen text-gray-100 scanlines relative">
      {isStartingBot && !isRunning && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm">
          <div className="mx-4 w-full max-w-md rounded-2xl border border-neon-cyan/40 bg-black/85 p-6 shadow-neon-cyan">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-full border border-neon-cyan/40 bg-neon-cyan/10">
                <RefreshCw className="h-6 w-6 animate-spin text-neon-cyan" />
              </div>
              <div>
                <h2 className="text-lg font-bold tracking-wide text-neon-cyan font-cyber">機器人啟動中</h2>
                <p className="mt-1 text-sm text-gray-300">正在連線並初始化交易流程，請勿重複點擊啟動按鈕。</p>
              </div>
            </div>
          </div>
        </div>
      )}
      {/* Background */}
      <div className="fixed inset-0 z-0">
        <img src="/background.jpeg" alt="" className="w-full h-full object-cover" />
        <div className="absolute inset-0 bg-black/70" />
        <div className="absolute inset-0 bg-gradient-to-b from-black/40 via-transparent to-black/80" />
      </div>
      {/* Header */}
      <header className="border-b border-neon-cyan/20 bg-black/60 backdrop-blur-xl sticky top-0 z-50 shadow-neon-cyan">
        <div className="max-w-7xl mx-auto px-3 sm:px-4 py-2 sm:py-3">
          {/* Top row: logo + badges + start/stop */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 sm:gap-3">
              <div className="w-8 h-8 sm:w-10 sm:h-10 rounded-xl bg-neon-cyan/10 border border-neon-cyan/30 flex items-center justify-center shadow-neon-cyan flex-shrink-0">
                <Zap className="w-4 h-4 sm:w-5 sm:h-5 text-neon-cyan" />
              </div>
              <div className="min-w-0">
                <h1 className="text-sm sm:text-lg font-bold tracking-wider font-cyber neon-text-cyan truncate">PM 1 小時套利機器人</h1>
                <p className="text-[10px] sm:text-xs text-neon-cyan/40 tracking-widest uppercase hidden sm:block">Hourly Crypto Arbitrage</p>
              </div>
            </div>

            <div className="flex items-center gap-1.5 sm:gap-3 flex-shrink-0">
              {/* Connection Status */}
              <div className={`flex items-center gap-1 text-[10px] sm:text-xs px-1.5 sm:px-2.5 py-0.5 sm:py-1 rounded-full border ${connected ? 'bg-neon-green/5 text-neon-green border-neon-green/30 shadow-neon-green' : 'bg-red-500/5 text-red-400 border-red-500/30 shadow-neon-pink'}`}>
                {connected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
                <span className="hidden sm:inline">{connected ? '後端連線' : '後端離線'}</span>
              </div>

              {/* Bot Loop Status */}
              <div className={`flex items-center gap-1 text-[10px] sm:text-xs px-1.5 sm:px-2.5 py-0.5 sm:py-1 rounded-full border ${isRunning ? 'bg-emerald-500/5 text-emerald-300 border-emerald-500/30 shadow-neon-green' : 'bg-gray-600/10 text-gray-300 border-gray-500/30'}`}>
                <Activity className="w-3 h-3" />
                <span className="hidden sm:inline">{isRunning ? `Bot運行中 · 掃描${status?.scan_count ?? 0}` : 'Bot已停止'}</span>
              </div>

              {/* Mode Badge */}
              <div className={`text-[10px] sm:text-xs px-1.5 sm:px-2.5 py-0.5 sm:py-1 rounded-full font-medium border ${config?.dry_run !== false ? 'bg-neon-amber/5 text-neon-amber border-neon-amber/30 shadow-neon-amber' : 'bg-neon-pink/5 text-neon-pink border-neon-pink/30 shadow-neon-pink neon-pulse'}`}>
                {config?.dry_run !== false ? '🔸 模擬' : '🔴 真實'}
              </div>

              {/* Start/Stop */}
              {isRunning ? (
                <button
                  onClick={stopBot}
                  className="flex items-center gap-1 sm:gap-2 px-2.5 sm:px-4 py-1.5 sm:py-2 bg-neon-pink/20 hover:bg-neon-pink/30 border border-neon-pink/50 text-neon-pink rounded-lg text-xs sm:text-sm font-medium transition-all shadow-neon-pink"
                >
                  <Square className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                  <span className="hidden sm:inline">停止</span>
                </button>
              ) : (
                <button
                  onClick={startBot}
                  disabled={isStartingBot}
                  className={`flex items-center gap-1 sm:gap-2 px-2.5 sm:px-4 py-1.5 sm:py-2 border rounded-lg text-xs sm:text-sm font-medium transition-all ${isStartingBot ? 'bg-neon-cyan/10 border-neon-cyan/30 text-neon-cyan/70 shadow-neon-cyan cursor-not-allowed' : 'bg-neon-green/20 hover:bg-neon-green/30 border-neon-green/50 text-neon-green shadow-neon-green'}`}
                >
                  {isStartingBot ? (
                    <RefreshCw className="w-3.5 h-3.5 sm:w-4 sm:h-4 animate-spin" />
                  ) : (
                    <Play className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                  )}
                  <span className="hidden sm:inline">{isStartingBot ? '啟動中' : '啟動'}</span>
                </button>
              )}
            </div>
          </div>

          {/* Bottom row: tabs */}
          <div className="flex gap-1 bg-black/40 border border-neon-cyan/10 rounded-lg p-0.5 mt-2">
            {[
              { id: 'live', label: '即時監控', icon: <Activity className="w-3.5 h-3.5" /> },
              { id: 'analytics', label: '數據分析', icon: <BarChart3 className="w-3.5 h-3.5" /> },
              { id: 'settings', label: '設定', icon: <Settings className="w-3.5 h-3.5" /> },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveView(tab.id)}
                className={`flex-1 flex items-center justify-center gap-1.5 text-xs px-2 sm:px-3 py-1.5 rounded-md transition-all font-medium ${
                  activeView === tab.id
                    ? 'bg-neon-cyan/15 text-neon-cyan border border-neon-cyan/30 shadow-neon-cyan'
                    : 'text-gray-500 hover:text-neon-cyan/70 border border-transparent'
                }`}
              >
                {tab.icon}
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-6 space-y-4 sm:space-y-6 relative z-10">

        {/* ═══════════════ LIVE TAB ═══════════════ */}
        {activeView === 'live' && (
          <>
            {/* Compact Stats Row */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2 sm:gap-3">
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
                value={effectiveStatus?.scan_count ?? 0}
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
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
              {/* Price Monitoring */}
              <div className="cyber-panel p-3 sm:p-5">
                <h3 className="text-sm font-medium neon-text-cyan mb-3 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" />
                  即時價格 — {Object.keys(effectiveStatus?.market_prices || {}).length} 個市場
                </h3>
                {effectiveStatus?.market_prices && Object.keys(effectiveStatus.market_prices).length > 0 ? (
                  <div className="overflow-x-auto max-h-72 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-black/80 backdrop-blur">
                        <tr className="text-neon-cyan/50 border-b border-neon-cyan/10">
                          <th className="text-left py-1.5 pr-3 font-medium">市場 / 倒數</th>
                          <th className="text-left py-1.5 px-2 font-medium">現價 / 參考</th>
                          <th className="text-right py-1.5 px-2 font-medium">UP</th>
                          <th className="text-right py-1.5 px-2 font-medium">DOWN</th>
                          <th className="text-right py-1.5 px-2 font-medium">成本</th>
                          <th className="text-right py-1.5 pl-2 font-medium">價差</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(effectiveStatus.market_prices)
                          .sort(([, a], [, b]) => {
                            const ta = a.time_remaining_seconds ?? 0
                            const tb = b.time_remaining_seconds ?? 0
                            if (ta !== tb) return ta - tb
                            return 0
                          })
                          .map(([slug, price]) => {
                            const profitable = price.total_cost < (config?.target_pair_cost ?? 0.99)
                            const secs = Math.max(0, Math.floor(price.time_remaining_seconds || 0))
                            const mins = Math.floor(secs / 60)
                            const rem = secs % 60
                            const timeLabel = price.time_remaining_display || `${mins}分${rem.toString().padStart(2, '0')}秒`
                            const underlyingLabel = price.underlying_symbol || slug.split('-')[0]?.toUpperCase() || 'SPOT'
                            const referencePrice = Number(price.reference_price)
                            const underlyingPrice = Number(price.underlying_price)
                            const hasPriceDifference = Number.isFinite(referencePrice) && referencePrice > 0 && Number.isFinite(underlyingPrice) && underlyingPrice > 0
                            const priceDifference = hasPriceDifference ? (underlyingPrice - referencePrice) : null
                            const priceDifferenceClassName = !hasPriceDifference
                              ? 'text-gray-500'
                              : Math.abs(priceDifference) < 0.005
                                ? 'text-gray-400'
                                : priceDifference > 0
                                  ? 'text-neon-green'
                                  : 'text-neon-pink'
                            return (
                              <tr key={slug} className={`border-b border-neon-cyan/5 ${profitable ? 'bg-neon-green/5' : ''}`}>
                                <td className="py-2 pr-3">
                                  <div className="flex items-center gap-2 min-w-0">
                                    <span className="font-mono text-gray-300 truncate block max-w-[150px]" title={slug}>
                                      {slug}
                                    </span>
                                    <span className="text-[10px] text-red-400 inline-flex items-center gap-1 font-medium shrink-0 whitespace-nowrap">
                                      <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></span>
                                      ⏳ {timeLabel}
                                    </span>
                                  </div>
                                </td>
                                <td className="py-2 px-2">
                                  <div className="flex flex-col items-start leading-tight">
                                    <span className="font-mono text-white whitespace-nowrap">
                                      {underlyingLabel} {formatUsdPrice(price.underlying_price, 2)}
                                    </span>
                                    <span className="text-[10px] text-gray-500 whitespace-nowrap">
                                      參考 {formatUsdPrice(price.reference_price, 2)}
                                      {hasPriceDifference ? (
                                        <span className={`ml-1 ${priceDifferenceClassName}`}>
                                          · {formatSignedUsdDifference(priceDifference, 2)}
                                        </span>
                                      ) : ''}
                                    </span>
                                    <span className="text-[9px] text-gray-600 max-w-[240px] truncate" title={price.reference_price_source || price.reference_source || ''}>
                                      {price.reference_price_source || price.reference_source || ''}
                                    </span>
                                  </div>
                                </td>
                                <td className="text-right py-2 px-2 font-mono text-white">
                                  {price.up_best_ask > 0 ? price.up_best_ask.toFixed(4) : price.up_price.toFixed(4)}
                                </td>
                                <td className="text-right py-2 px-2 font-mono text-white">
                                  {price.down_best_ask > 0 ? price.down_best_ask.toFixed(4) : price.down_price.toFixed(4)}
                                </td>
                                <td className={`text-right py-2 px-2 font-mono font-bold ${profitable ? 'neon-text-green' : 'text-white'}`}>
                                  {price.total_cost.toFixed(4)}
                                </td>
                                <td className={`text-right py-2 pl-2 font-mono ${price.spread > 0 ? 'text-neon-green' : 'text-neon-pink'}`}>
                                  {price.spread > 0 ? '+' : ''}{price.spread.toFixed(4)}
                                </td>
                              </tr>
                            )
                          })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-gray-600 text-center py-6">等待價格數據...</p>
                )}
              </div>

              {/* Bargain Holdings */}
              <div className="cyber-panel-amber p-3 sm:p-5">
                <h3 className="text-sm font-medium neon-text-amber mb-3 flex items-center gap-2">
                  🏷️ 撿便宜持倉 ({(effectiveStatus?.bargain_holdings || []).length} 筆)
                </h3>
                {(effectiveStatus?.bargain_holdings || []).length > 0 ? (
                  <div className="overflow-x-auto max-h-72 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-black/80 backdrop-blur">
                        <tr className="text-neon-amber/50 border-b border-neon-amber/10">
                          <th className="text-left py-1.5 px-2 font-medium">R#</th>
                          <th className="text-left py-1.5 px-2 font-medium">市場</th>
                          <th className="text-center py-1.5 px-2 font-medium">方向</th>
                          <th className="text-right py-1.5 px-2 font-medium">買入</th>
                          <th className="text-right py-1.5 px-2 font-medium">損益%</th>
                          <th className="text-right py-1.5 px-2 font-medium">股數</th>
                          <th className="text-right py-1.5 px-2 font-medium">金額</th>
                          <th className="text-center py-1.5 px-2 font-medium">狀態</th>
                        </tr>
                      </thead>
                      <tbody>
                        {effectiveStatus.bargain_holdings.map((h, i) => {
                          const livePriceInfo = effectiveStatus?.market_prices?.[h.market_slug]
                          const currentHoldingPrice = h.side === 'UP'
                            ? (livePriceInfo?.up_best_ask > 0 ? livePriceInfo.up_best_ask : livePriceInfo?.up_price)
                            : (livePriceInfo?.down_best_ask > 0 ? livePriceInfo.down_best_ask : livePriceInfo?.down_price)
                          const hasProfitPct = Number.isFinite(currentHoldingPrice) && Number.isFinite(h.buy_price) && h.buy_price > 0
                          const holdingProfitPct = hasProfitPct
                            ? ((currentHoldingPrice - h.buy_price) / h.buy_price) * 100
                            : null

                          return (
                            <tr key={i} className="border-b border-neon-amber/5 hover:bg-neon-amber/5">
                              <td className="py-1.5 px-2 font-mono text-neon-amber">R{h.round}</td>
                              <td className="py-1.5 px-2 truncate max-w-[120px]" title={h.market_slug}>{h.market_slug}</td>
                              <td className="py-1.5 px-2 text-center">
                                <span className={`px-1.5 py-0.5 rounded-full ${
                                  h.side === 'UP' ? 'bg-neon-green/10 text-neon-green' : 'bg-neon-pink/10 text-neon-pink'
                                }`}>
                                  {h.side}
                                </span>
                              </td>
                              <td className="py-1.5 px-2 text-right font-mono">{h.buy_price?.toFixed(4)}</td>
                              <td className={`py-1.5 px-2 text-right font-mono ${holdingProfitPct == null ? 'text-gray-500' : holdingProfitPct >= 0 ? 'text-neon-green' : 'text-neon-pink'}`}>
                                {holdingProfitPct == null ? '--' : `${holdingProfitPct >= 0 ? '+' : ''}${holdingProfitPct.toFixed(2)}%`}
                              </td>
                              <td className="py-1.5 px-2 text-right font-mono">{h.shares?.toFixed(1)}</td>
                              <td className="py-1.5 px-2 text-right font-mono">${h.amount_usd?.toFixed(2)}</td>
                              <td className="py-1.5 px-2 text-center">
                                <span className={`px-1.5 py-0.5 rounded-full ${
                                  h.status === 'holding' ? 'bg-neon-amber/10 text-neon-amber' :
                                  h.status === 'paired' ? 'bg-neon-green/10 text-neon-green' :
                                  'bg-neon-pink/10 text-neon-pink'
                                }`}>
                                  {h.status === 'holding' ? '持有' : h.status === 'paired' ? '已配對' : '止損'}
                                </span>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-gray-600 text-center py-6">暫無撿便宜持倉</p>
                )}
              </div>
            </div>

            {/* Opportunities + Merge — side by side */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
              {/* Opportunities */}
              <div className="cyber-panel p-3 sm:p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium neon-text-cyan flex items-center gap-2">
                    <Zap className="w-4 h-4" />
                    套利機會
                  </h3>
                  <button
                    onClick={scanMarkets}
                    disabled={loading}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1 bg-neon-cyan/10 hover:bg-neon-cyan/20 border border-neon-cyan/20 text-neon-cyan rounded-lg transition-all disabled:opacity-50"
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
                          ? 'bg-neon-green/5 border-neon-green/20 shadow-neon-green'
                          : 'bg-black/30 border-neon-cyan/10'
                      }`}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-medium">
                            {opp.is_viable ? '💰 可執行' : '⏳ 不可執行'}
                          </span>
                          <span className={`text-xs font-mono ${
                            opp.profit_pct > 0 ? 'text-neon-green' : 'text-gray-400'
                          }`}>
                            {opp.profit_pct > 0 ? '+' : ''}{opp.profit_pct.toFixed(2)}%
                          </span>
                        </div>
                        <p className="text-[10px] text-gray-500">{opp.reason}</p>
                        <div className="grid grid-cols-3 gap-2 mt-1.5 text-[10px]">
                          <div>
                            <span className="text-gray-500">利潤</span>
                            <p className="font-mono text-neon-green">${opp.potential_profit.toFixed(4)}</p>
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
              <div className="cyber-panel-magenta p-3 sm:p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium neon-text-magenta flex items-center gap-2">
                    <GitMerge className="w-4 h-4" />
                    持倉合併
                  </h3>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={toggleAutoMerge}
                      className={`flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg transition-all ${
                        mergeStatus?.auto_merge_enabled
                          ? 'bg-neon-magenta/10 text-neon-magenta border border-neon-magenta/30 shadow-neon-magenta'
                          : 'bg-black/30 text-gray-500 border border-gray-700'
                      }`}
                    >
                      <ArrowRightLeft className="w-3 h-3" />
                      {mergeStatus?.auto_merge_enabled ? '自動: 開' : '自動: 關'}
                    </button>
                    <button
                      onClick={mergeAll}
                      className="flex items-center gap-1 text-[10px] px-2 py-1 bg-neon-magenta/20 hover:bg-neon-magenta/30 border border-neon-magenta/40 text-neon-magenta rounded-lg transition-all"
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
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                  <div className="bg-black/30 border border-neon-magenta/10 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">追蹤</p>
                    <p className="text-sm font-bold font-mono">{mergeStatus?.total_tracked ?? 0}</p>
                  </div>
                  <div className="bg-black/30 border border-neon-magenta/10 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">可合併</p>
                    <p className="text-sm font-bold font-mono text-neon-magenta">{(mergeStatus?.total_mergeable ?? 0).toFixed(0)}</p>
                  </div>
                  <div className="bg-black/30 border border-neon-magenta/10 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">已合併</p>
                    <p className="text-sm font-bold font-mono text-neon-green">${(mergeStatus?.total_merged_usdc ?? 0).toFixed(2)}</p>
                  </div>
                  <div className="bg-black/30 border border-neon-magenta/10 rounded-lg p-2">
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
                        <div key={i} className="bg-black/30 rounded-lg p-2.5 border border-neon-magenta/10">
                          <div className="flex items-center justify-between">
                            <div>
                              <p className="text-xs font-medium truncate max-w-[160px]">{pos.market_slug}</p>
                              <p className="text-[10px] text-gray-500 font-mono">CID: {pos.condition_id?.slice(0, 12)}...</p>
                            </div>
                            <button
                              onClick={() => mergeOne(pos.condition_id)}
                              disabled={pos.mergeable_amount < 1}
                              className="flex items-center gap-1 text-[10px] px-2 py-1 bg-neon-magenta/20 hover:bg-neon-magenta/30 border border-neon-magenta/40 text-neon-magenta rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                            >
                              <GitMerge className="w-3 h-3" />
                              合併
                            </button>
                          </div>
                          <div className="grid grid-cols-3 gap-2 mt-1.5 text-[10px]">
                            <div>
                              <span className="text-gray-500">UP</span>
                              <p className="font-mono text-neon-green">{pos.up_balance?.toFixed(1)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500">DOWN</span>
                              <p className="font-mono text-neon-pink">{pos.down_balance?.toFixed(1)}</p>
                            </div>
                            <div>
                              <span className="text-gray-500">可合併</span>
                              <p className="font-mono text-neon-magenta">{pos.mergeable_amount?.toFixed(1)}</p>
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
                                  mr.status === 'success' ? 'bg-neon-green/10 text-neon-green' :
                                  mr.status === 'simulated' ? 'bg-neon-amber/10 text-neon-amber' :
                                  'bg-neon-pink/10 text-neon-pink'
                                }`}>
                                  {mr.status === 'success' ? '成功' : mr.status === 'simulated' ? '模擬' : '失敗'}
                                </span>
                                <span className="text-gray-400 truncate max-w-[100px]">{mr.market_slug}</span>
                              </div>
                              <span className="font-mono text-neon-green">${mr.usdc_received?.toFixed(2)}</span>
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
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
              {/* Recent Trades */}
              <div className="cyber-panel p-3 sm:p-5">
                <h3 className="text-sm font-medium neon-text-cyan mb-3 flex items-center gap-2">
                  <Shield className="w-4 h-4" />
                  最近交易
                </h3>
                {(status?.trade_history || []).length === 0 && trades.length === 0 ? (
                  <p className="text-xs text-gray-600 text-center py-6">尚無交易記錄</p>
                ) : (
                  <div className="overflow-x-auto max-h-64 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-black/80 backdrop-blur">
                        <tr className="text-neon-cyan/50 border-b border-neon-cyan/10">
                          <th className="text-left py-1.5 pr-2 font-medium">時間</th>
                          <th className="text-left py-1.5 px-2 font-medium">市場</th>
                          <th className="text-right py-1.5 px-2 font-medium">成本</th>
                          <th className="text-right py-1.5 px-2 font-medium">利潤</th>
                          <th className="text-center py-1.5 pl-2 font-medium">狀態</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...(status?.trade_history || []), ...trades].slice(0, 20).map((t, i) => (
                          <tr key={i} className="border-b border-neon-cyan/5 hover:bg-neon-cyan/5">
                            <td className="py-1.5 pr-2 text-gray-400 font-mono whitespace-nowrap">
                              {new Date(t.timestamp).toLocaleTimeString('zh-TW')}
                            </td>
                            <td className="py-1.5 px-2 text-gray-300 truncate max-w-[100px]" title={t.market_slug}>
                              {t.market_slug}
                            </td>
                            <td className="py-1.5 px-2 text-right font-mono">{t.total_cost?.toFixed(4)}</td>
                            <td className={`py-1.5 px-2 text-right font-mono ${
                              (t.expected_profit ?? 0) > 0 ? 'text-neon-green' : 'text-neon-pink'
                            }`}>
                              ${t.expected_profit?.toFixed(4)}
                            </td>
                            <td className="py-1.5 pl-2 text-center">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                                t.status === 'executed' ? 'bg-neon-green/10 text-neon-green' :
                                t.status === 'simulated' ? 'bg-neon-amber/10 text-neon-amber' :
                                'bg-neon-pink/10 text-neon-pink'
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
              <div className="cyber-panel p-3 sm:p-5">
                <h3 className="text-sm font-medium neon-text-cyan mb-3 flex items-center gap-2">
                  <Activity className="w-4 h-4" />
                  運行日誌
                </h3>
                <div className="bg-black/50 border border-neon-cyan/10 rounded-lg p-3 max-h-64 overflow-y-scroll font-mono text-[11px] space-y-0.5">
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
          <AnalyticsDashboard token={token} />
        )}

        {/* ═══════════════ SETTINGS TAB ═══════════════ */}
        {activeView === 'settings' && (
          <div className="cyber-panel p-4 sm:p-6 space-y-4">
            <h2 className="text-lg font-semibold flex items-center gap-2 font-cyber">
              <Settings className="w-5 h-5 text-neon-cyan" />
              <span className="neon-text-cyan">機器人設定</span>
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <ConfigField
                label="私鑰"
                type={showKey ? 'text' : 'password'}
                value={configForm.private_key || ''}
                onChange={(v) => { setConfigForm({ ...configForm, private_key: v }); setConfigErrors((e) => ({ ...e, private_key: undefined })) }}
                error={configErrors.private_key}
                suffix={
                  <button onClick={() => setShowKey(!showKey)} className="text-gray-500 hover:text-gray-300">
                    {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                }
              />
              <ConfigField
                label="資金地址 (Funder)"
                value={configForm.funder_address || ''}
                onChange={(v) => { setConfigForm({ ...configForm, funder_address: v }); setConfigErrors((e) => ({ ...e, funder_address: undefined })) }}
                error={configErrors.funder_address}
              />
              <ConfigField
                label="簽名類型"
                type="number"
                value={configForm.signature_type ?? 0}
                onChange={(v) => { setConfigForm({ ...configForm, signature_type: parseInt(v) }); setConfigErrors((e) => ({ ...e, signature_type: undefined })) }}
                hint="0=EOA, 1=Email, 2=Proxy"
                error={configErrors.signature_type}
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
                hint="每小時市場建議 600 秒 (10 分鐘)"
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
                hint="每小時市場建議 120 秒 (2 分鐘)"
              />
              <ConfigField
                label="掃描間隔 (秒)"
                type="number"
                value={configForm.scan_interval_seconds ?? 2}
                onChange={(v) => setConfigForm({ ...configForm, scan_interval_seconds: parseInt(v) })}
                hint="越低更新越快，但 API 請求會更多"
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
                  className={`relative w-12 h-6 rounded-full transition-all ${
                    configForm.dry_run !== false ? 'bg-neon-green/40 shadow-neon-green' : 'bg-neon-pink/40 shadow-neon-pink'
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

            <div className="border-t border-neon-cyan/15 pt-4 mt-2">
              <h3 className="text-sm font-medium neon-text-cyan mb-3 flex items-center gap-2">
                ₿ BTC RTDS 價差策略
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="flex items-center gap-3">
                  <label className="text-sm text-gray-400">價差趨勢鎖定</label>
                  <button
                    onClick={() => setConfigForm({ ...configForm, price_edge_distance_gate_enabled_btc: !configForm.price_edge_distance_gate_enabled_btc })}
                    className={`relative w-12 h-6 rounded-full transition-all ${
                      configForm.price_edge_distance_gate_enabled_btc !== false ? 'bg-neon-cyan/40 shadow-neon-cyan' : 'bg-gray-700'
                    }`}
                  >
                    <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                      configForm.price_edge_distance_gate_enabled_btc !== false ? 'left-6' : 'left-0.5'
                    }`} />
                  </button>
                  <span className="text-xs text-gray-500">
                    {configForm.price_edge_distance_gate_enabled_btc !== false ? '啟用' : '停用'}
                  </span>
                </div>
                <ConfigField
                  label="價差觸發門檻 ($)"
                  type="number"
                  step="1"
                  value={configForm.price_edge_min_distance_usd_btc ?? 70}
                  onChange={(v) => setConfigForm({ ...configForm, price_edge_min_distance_usd_btc: parseFloat(v) })}
                  hint="當 RTDS 現價與參考價價差達此門檻時，才依趨勢方向鎖定進場"
                />
              </div>
            </div>

            {/* Bargain Hunter Settings */}
            <div className="border-t border-neon-amber/15 pt-4 mt-2">
              <h3 className="text-sm font-medium neon-text-amber mb-3 flex items-center gap-2">
                🏷️ 撿便宜策略設定
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="flex items-center gap-3">
                  <label className="text-sm text-gray-400">撿便宜策略</label>
                  <button
                    onClick={() => setConfigForm({ ...configForm, bargain_enabled: !configForm.bargain_enabled })}
                    className={`relative w-12 h-6 rounded-full transition-all ${
                      configForm.bargain_enabled !== false ? 'bg-neon-amber/40 shadow-neon-amber' : 'bg-gray-700'
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
                  label="二次出場利潤%"
                  type="number"
                  step="0.1"
                  value={configForm.bargain_secondary_exit_profit_pct ?? 9.5}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_secondary_exit_profit_pct: parseFloat(v) })}
                  hint="未配對持倉利潤達此比例即直接賣出並視為配對"
                />
                <ConfigField
                  label="急跌護欄跌幅%"
                  type="number"
                  step="0.1"
                  value={configForm.bargain_plummet_exit_pct ?? 20}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_plummet_exit_pct: parseFloat(v) })}
                  hint="在時間窗內跌幅達此比例即立刻平倉"
                />
                <ConfigField
                  label="急跌護欄時間窗 (秒)"
                  type="number"
                  value={configForm.bargain_plummet_window_seconds ?? 15}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_plummet_window_seconds: parseInt(v) })}
                  hint="連續監控的秒數，用高點作為基準判斷跌幅"
                />
                <ConfigField
                  label="急跌護欄觸發剩餘秒數"
                  type="number"
                  value={configForm.bargain_plummet_trigger_seconds ?? 0}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_plummet_trigger_seconds: parseInt(v) })}
                  hint="僅在市場剩餘時間小於等於這個秒數時，急跌護欄才會開始生效；0 表示全程生效"
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
                <div>
                  <label className="block text-xs text-gray-400 mb-1">首次買入偏好</label>
                  <select
                    value={configForm.bargain_first_buy_bias ?? "AUTO"}
                    onChange={(e) => setConfigForm({ ...configForm, bargain_first_buy_bias: e.target.value })}
                    className="w-full px-3 py-2 bg-dark-card border border-dark-border rounded-lg text-sm text-white"
                  >
                    <option value="AUTO">自動 (最便宜)</option>
                    <option value="UP">偏好 UP</option>
                    <option value="DOWN">偏好 DOWN</option>
                  </select>
                  <p className="text-xs text-gray-500 mt-1">R1 開倉時優先買哪一側</p>
                </div>
                <ConfigField
                  label="配對加價時限 (分鐘)"
                  type="number"
                  value={configForm.bargain_pair_escalation_minutes ?? 15}
                  onChange={(v) => setConfigForm({ ...configForm, bargain_pair_escalation_minutes: parseInt(v) })}
                  hint="未配對超過此分鐘數，配對價格上限 +5¢"
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
                className="px-4 py-2 bg-neon-cyan/20 hover:bg-neon-cyan/30 border border-neon-cyan/40 text-neon-cyan rounded-lg text-sm font-medium transition-all shadow-neon-cyan"
              >
                儲存設定
              </button>
              <button
                onClick={() => setConfigForm(config || {})}
                className="px-4 py-2 bg-black/30 hover:bg-black/50 border border-gray-600 rounded-lg text-sm font-medium transition-all text-gray-400"
              >
                重置
              </button>
            </div>

            {/* Security Settings */}
            <div className="border-t border-neon-magenta/15 pt-4 mt-2">
              <SecuritySettings token={token} onLogout={onLogout} />
            </div>
          </div>
        )}

      </main>

      {/* Footer */}
      <footer className="border-t border-neon-cyan/10 mt-8 py-4 text-center text-xs text-neon-cyan/30 relative z-10">
        <span className="font-cyber tracking-wider">PM HOURLY ARB BOT</span> v1.0 | 僅供教育和研究用途 | 交易有風險，請謹慎操作
      </footer>
    </div>
  )
}

function StatCard({ icon, label, value, color = 'gray' }) {
  const neonMap = {
    emerald: { border: 'border-neon-green/25', shadow: 'shadow-neon-green', text: 'text-neon-green', glow: 'neon-text-green' },
    blue: { border: 'border-neon-blue/25', shadow: 'shadow-neon-cyan', text: 'text-neon-blue', glow: 'neon-text-cyan' },
    cyan: { border: 'border-neon-cyan/25', shadow: 'shadow-neon-cyan', text: 'text-neon-cyan', glow: 'neon-text-cyan' },
    violet: { border: 'border-neon-magenta/25', shadow: 'shadow-neon-magenta', text: 'text-neon-magenta', glow: 'neon-text-magenta' },
    amber: { border: 'border-neon-amber/25', shadow: 'shadow-neon-amber', text: 'text-neon-amber', glow: 'neon-text-amber' },
    red: { border: 'border-neon-pink/25', shadow: 'shadow-neon-pink', text: 'text-neon-pink', glow: '' },
    gray: { border: 'border-gray-600/25', shadow: '', text: 'text-gray-400', glow: '' },
  }
  const n = neonMap[color] || neonMap.gray

  return (
    <div className={`bg-black/40 backdrop-blur-sm border ${n.border} ${n.shadow} rounded-xl p-3 sm:p-4 transition-all hover:scale-[1.02]`}>
      <div className="flex items-center gap-1.5 sm:gap-2 mb-1 sm:mb-2">
        <span className={`${n.text} [&>svg]:w-4 [&>svg]:h-4 sm:[&>svg]:w-5 sm:[&>svg]:h-5`}>{icon}</span>
        <span className="text-[10px] sm:text-xs text-gray-500 uppercase tracking-wider">{label}</span>
      </div>
      <p className={`text-base sm:text-xl font-bold font-cyber ${n.glow}`}>{value}</p>
    </div>
  )
}

function ConfigField({ label, value, onChange, type = 'text', step, hint, suffix, error }) {
  return (
    <div>
      <label className="text-xs text-neon-cyan/50 block mb-1 uppercase tracking-wider">{label}</label>
      <div className="flex items-center gap-2">
        <input
          type={type}
          step={step}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`w-full bg-black/40 border rounded-lg px-3 py-2 text-sm focus:outline-none transition-all text-gray-200 ${
            error
              ? 'border-red-500/70 focus:border-red-400 focus:shadow-[0_0_8px_rgba(239,68,68,0.4)]'
              : 'border-neon-cyan/15 focus:border-neon-cyan/50 focus:shadow-neon-cyan'
          }`}
        />
        {suffix}
      </div>
      {error && <p className="text-xs text-red-400 mt-0.5">{error}</p>}
      {!error && hint && <p className="text-xs text-gray-600 mt-0.5">{hint}</p>}
    </div>
  )
}

export default App
