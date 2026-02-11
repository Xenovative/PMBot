import { useState, useEffect, useCallback } from 'react'
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell
} from 'recharts'
import {
  TrendingUp, TrendingDown, DollarSign, BarChart3, Activity,
  RefreshCw, Award, Target, Layers, ChevronDown, ChevronUp,
  Download
} from 'lucide-react'

const API = ''

function AnalyticsDashboard() {
  const [overview, setOverview] = useState(null)
  const [cumProfit, setCumProfit] = useState([])
  const [dailyPnl, setDailyPnl] = useState([])
  const [tradeFreq, setTradeFreq] = useState([])
  const [winRate, setWinRate] = useState([])
  const [perMarket, setPerMarket] = useState([])
  const [recentTrades, setRecentTrades] = useState([])
  const [recentMerges, setRecentMerges] = useState([])
  const [activeTab, setActiveTab] = useState('overview')
  const [loading, setLoading] = useState(false)
  const [tradesOpen, setTradesOpen] = useState(false)
  const [days, setDays] = useState(30)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [ov, cp, dp, tf, wr, pm, tr, mr] = await Promise.all([
        fetch(`${API}/api/analytics/overview`).then(r => r.json()),
        fetch(`${API}/api/analytics/cumulative-profit?days=${days}`).then(r => r.json()),
        fetch(`${API}/api/analytics/daily-pnl?days=${days}`).then(r => r.json()),
        fetch(`${API}/api/analytics/trade-frequency?days=${days}`).then(r => r.json()),
        fetch(`${API}/api/analytics/win-rate?days=${days}`).then(r => r.json()),
        fetch(`${API}/api/analytics/per-market`).then(r => r.json()),
        fetch(`${API}/api/analytics/trades?limit=50`).then(r => r.json()),
        fetch(`${API}/api/analytics/merges?limit=20`).then(r => r.json()),
      ])
      setOverview(ov)
      setCumProfit(cp)
      setDailyPnl(dp)
      setTradeFreq(tf)
      setWinRate(wr)
      setPerMarket(pm)
      setRecentTrades(tr)
      setRecentMerges(mr)
    } catch (e) {
      console.error('Analytics fetch error:', e)
    }
    setLoading(false)
  }, [days])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [fetchAll])

  const exportCSV = () => {
    if (!recentTrades.length) return
    const headers = Object.keys(recentTrades[0]).join(',')
    const rows = recentTrades.map(t => Object.values(t).join(','))
    const csv = [headers, ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const tabs = [
    { id: 'overview', label: '總覽' },
    { id: 'profit', label: '利潤' },
    { id: 'trades', label: '交易' },
    { id: 'markets', label: '市場' },
  ]

  const formatHour = (h) => {
    if (!h) return ''
    return h.slice(5, 13)
  }

  const formatDate = (d) => {
    if (!d) return ''
    return d.slice(5, 10)
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-violet-400" />
          數據分析
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-300"
          >
            <option value={7}>7 天</option>
            <option value={14}>14 天</option>
            <option value={30}>30 天</option>
            <option value={90}>90 天</option>
          </select>
          <button
            onClick={exportCSV}
            className="flex items-center gap-1 text-xs px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors text-gray-400"
          >
            <Download className="w-3 h-3" />
            CSV
          </button>
          <button
            onClick={fetchAll}
            disabled={loading}
            className="flex items-center gap-1 text-xs px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors text-gray-400"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-800/50 rounded-lg p-1">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`flex-1 text-xs py-1.5 rounded-md transition-colors font-medium ${
              activeTab === t.id
                ? 'bg-violet-600 text-white'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && overview && (
        <div className="space-y-4">
          {/* Stat Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MiniStat
              icon={<DollarSign className="w-4 h-4" />}
              label="總利潤"
              value={`$${overview.total_profit.toFixed(4)}`}
              color={overview.total_profit >= 0 ? 'emerald' : 'red'}
            />
            <MiniStat
              icon={<Activity className="w-4 h-4" />}
              label="總交易"
              value={overview.total_trades}
              color="blue"
            />
            <MiniStat
              icon={<Target className="w-4 h-4" />}
              label="勝率"
              value={`${overview.win_rate}%`}
              color="violet"
            />
            <MiniStat
              icon={<Layers className="w-4 h-4" />}
              label="合併 USDC"
              value={`$${overview.total_merge_usdc.toFixed(2)}`}
              color="cyan"
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MiniStat
              icon={<TrendingUp className="w-4 h-4" />}
              label="今日利潤"
              value={`$${overview.today_profit.toFixed(4)}`}
              color={overview.today_profit >= 0 ? 'emerald' : 'red'}
            />
            <MiniStat
              icon={<Activity className="w-4 h-4" />}
              label="今日交易"
              value={overview.today_trades}
              color="blue"
            />
            <MiniStat
              icon={<Award className="w-4 h-4" />}
              label="最佳交易"
              value={`$${overview.best_trade.toFixed(4)}`}
              color="emerald"
            />
            <MiniStat
              icon={<TrendingDown className="w-4 h-4" />}
              label="最差交易"
              value={`$${overview.worst_trade.toFixed(4)}`}
              color="red"
            />
          </div>

          {/* Cumulative Profit Mini Chart */}
          {cumProfit.length > 0 && (
            <div>
              <h4 className="text-xs text-gray-500 mb-2 font-medium">累計利潤趨勢</h4>
              <div className="h-40 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={cumProfit}>
                    <defs>
                      <linearGradient id="profitGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="hour" tickFormatter={formatHour} tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `$${v.toFixed(2)}`} />
                    <Tooltip
                      contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                      formatter={(v) => [`$${Number(v).toFixed(4)}`, '累計利潤']}
                      labelFormatter={formatHour}
                    />
                    <Area type="monotone" dataKey="cumulative" stroke="#10b981" fill="url(#profitGrad)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Profit Tab */}
      {activeTab === 'profit' && (
        <div className="space-y-4">
          {/* Daily P&L Bar Chart */}
          <div>
            <h4 className="text-xs text-gray-500 mb-2 font-medium">每日損益</h4>
            {dailyPnl.length > 0 ? (
              <div className="h-48 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailyPnl}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `$${v.toFixed(2)}`} />
                    <Tooltip
                      contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                      formatter={(v) => [`$${Number(v).toFixed(4)}`, '利潤']}
                      labelFormatter={formatDate}
                    />
                    <Bar dataKey="total_profit" radius={[4, 4, 0, 0]}>
                      {dailyPnl.map((entry, i) => (
                        <Cell key={i} fill={entry.total_profit >= 0 ? '#10b981' : '#ef4444'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="text-xs text-gray-600 text-center py-8">暫無數據</p>
            )}
          </div>

          {/* Cumulative Profit Full Chart */}
          {cumProfit.length > 0 && (
            <div>
              <h4 className="text-xs text-gray-500 mb-2 font-medium">累計利潤</h4>
              <div className="h-48 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={cumProfit}>
                    <defs>
                      <linearGradient id="profitGrad2" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="hour" tickFormatter={formatHour} tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `$${v.toFixed(2)}`} />
                    <Tooltip
                      contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                      formatter={(v) => [`$${Number(v).toFixed(4)}`]}
                      labelFormatter={formatHour}
                    />
                    <Area type="monotone" dataKey="hourly_profit" name="每小時" stroke="#f59e0b" fill="none" strokeWidth={1.5} />
                    <Area type="monotone" dataKey="cumulative" name="累計" stroke="#8b5cf6" fill="url(#profitGrad2)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Trades Tab */}
      {activeTab === 'trades' && (
        <div className="space-y-4">
          {/* Trade Frequency Chart */}
          {tradeFreq.length > 0 && (
            <div>
              <h4 className="text-xs text-gray-500 mb-2 font-medium">每日交易次數</h4>
              <div className="h-40 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={tradeFreq}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <Tooltip
                      contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                      labelFormatter={formatDate}
                    />
                    <Bar dataKey="successful" name="成功" stackId="a" fill="#10b981" radius={[0, 0, 0, 0]} />
                    <Bar dataKey="failed" name="失敗" stackId="a" fill="#ef4444" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Win Rate Chart */}
          {winRate.length > 0 && (
            <div>
              <h4 className="text-xs text-gray-500 mb-2 font-medium">每日勝率</h4>
              <div className="h-40 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={winRate}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `${v}%`} />
                    <Tooltip
                      contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                      formatter={(v) => [`${Number(v).toFixed(1)}%`, '勝率']}
                      labelFormatter={formatDate}
                    />
                    <Line type="monotone" dataKey="win_rate" stroke="#8b5cf6" strokeWidth={2} dot={{ r: 3, fill: '#8b5cf6' }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Recent Trades Table */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs text-gray-500 font-medium">最近交易記錄</h4>
              <button
                onClick={() => setTradesOpen(!tradesOpen)}
                className="p-1 rounded hover:bg-gray-800 transition-colors"
              >
                {tradesOpen ? <ChevronUp className="w-3.5 h-3.5 text-gray-500" /> : <ChevronDown className="w-3.5 h-3.5 text-gray-500" />}
              </button>
            </div>
            {tradesOpen && (
              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-gray-900">
                    <tr className="text-gray-500 border-b border-gray-800">
                      <th className="text-left py-1.5 pr-2 font-medium">時間</th>
                      <th className="text-left py-1.5 px-2 font-medium">市場</th>
                      <th className="text-left py-1.5 px-2 font-medium">類型</th>
                      <th className="text-right py-1.5 px-2 font-medium">數量</th>
                      <th className="text-right py-1.5 px-2 font-medium">成本</th>
                      <th className="text-right py-1.5 px-2 font-medium">利潤</th>
                      <th className="text-center py-1.5 pl-2 font-medium">狀態</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentTrades.map((t, i) => (
                      <tr key={t.id || i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                        <td className="py-1.5 pr-2 text-gray-400 font-mono whitespace-nowrap">
                          {t.timestamp?.slice(5, 16)?.replace('T', ' ')}
                        </td>
                        <td className="py-1.5 px-2 text-gray-300 truncate max-w-[120px]" title={t.market_slug}>
                          {t.market_slug}
                        </td>
                        <td className="py-1.5 px-2">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            t.trade_type === 'arbitrage' ? 'bg-violet-500/10 text-violet-400' :
                            t.trade_type === 'bargain_pair' ? 'bg-emerald-500/10 text-emerald-400' :
                            t.trade_type === 'bargain_open' ? 'bg-amber-500/10 text-amber-400' :
                            'bg-red-500/10 text-red-400'
                          }`}>
                            {t.trade_type === 'arbitrage' ? '套利' :
                             t.trade_type === 'bargain_pair' ? '配對' :
                             t.trade_type === 'bargain_open' ? '開倉' : '止損'}
                          </span>
                        </td>
                        <td className="py-1.5 px-2 text-right font-mono text-gray-300">
                          {t.order_size?.toFixed(1)}
                        </td>
                        <td className="py-1.5 px-2 text-right font-mono text-gray-300">
                          {t.total_cost?.toFixed(4)}
                        </td>
                        <td className={`py-1.5 px-2 text-right font-mono font-medium ${
                          t.profit > 0 ? 'text-emerald-400' : t.profit < 0 ? 'text-red-400' : 'text-gray-400'
                        }`}>
                          {t.profit > 0 ? '+' : ''}{t.profit?.toFixed(4)}
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
                {recentTrades.length === 0 && (
                  <p className="text-center text-gray-600 py-4 text-xs">暫無交易記錄</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Markets Tab */}
      {activeTab === 'markets' && (
        <div className="space-y-4">
          {perMarket.length > 0 ? (
            <>
              {/* Per-Market Profit Bar Chart */}
              <div>
                <h4 className="text-xs text-gray-500 mb-2 font-medium">各市場利潤</h4>
                <div className="h-48 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={perMarket.slice(0, 10)} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                      <XAxis type="number" tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `$${v.toFixed(2)}`} />
                      <YAxis
                        type="category"
                        dataKey="market_slug"
                        width={120}
                        tick={{ fontSize: 9, fill: '#6b7280' }}
                        tickFormatter={v => v.length > 20 ? v.slice(0, 20) + '...' : v}
                      />
                      <Tooltip
                        contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                        formatter={(v) => [`$${Number(v).toFixed(4)}`, '利潤']}
                      />
                      <Bar dataKey="total_profit" radius={[0, 4, 4, 0]}>
                        {perMarket.slice(0, 10).map((entry, i) => (
                          <Cell key={i} fill={entry.total_profit >= 0 ? '#10b981' : '#ef4444'} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Per-Market Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-gray-500 border-b border-gray-800">
                      <th className="text-left py-1.5 pr-2 font-medium">市場</th>
                      <th className="text-right py-1.5 px-2 font-medium">交易</th>
                      <th className="text-right py-1.5 px-2 font-medium">勝</th>
                      <th className="text-right py-1.5 px-2 font-medium">敗</th>
                      <th className="text-right py-1.5 px-2 font-medium">總利潤</th>
                      <th className="text-right py-1.5 px-2 font-medium">平均利潤</th>
                      <th className="text-right py-1.5 pl-2 font-medium">平均成本</th>
                    </tr>
                  </thead>
                  <tbody>
                    {perMarket.map((m, i) => (
                      <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                        <td className="py-1.5 pr-2 text-gray-300 truncate max-w-[150px]" title={m.market_slug}>
                          {m.market_slug}
                        </td>
                        <td className="py-1.5 px-2 text-right font-mono text-gray-300">{m.total_trades}</td>
                        <td className="py-1.5 px-2 text-right font-mono text-emerald-400">{m.wins}</td>
                        <td className="py-1.5 px-2 text-right font-mono text-red-400">{m.losses}</td>
                        <td className={`py-1.5 px-2 text-right font-mono font-medium ${
                          m.total_profit >= 0 ? 'text-emerald-400' : 'text-red-400'
                        }`}>
                          ${m.total_profit?.toFixed(4)}
                        </td>
                        <td className="py-1.5 px-2 text-right font-mono text-gray-400">
                          ${m.avg_profit?.toFixed(4)}
                        </td>
                        <td className="py-1.5 pl-2 text-right font-mono text-gray-400">
                          {m.avg_cost?.toFixed(4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="text-xs text-gray-600 text-center py-8">暫無市場數據</p>
          )}

          {/* Recent Merges */}
          {recentMerges.length > 0 && (
            <div>
              <h4 className="text-xs text-gray-500 mb-2 font-medium">最近合併記錄</h4>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {recentMerges.map((m, i) => (
                  <div key={m.id || i} className="bg-gray-800/30 rounded-lg p-2.5 text-xs flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded ${
                        m.status === 'success' ? 'bg-emerald-500/10 text-emerald-400' :
                        m.status === 'simulated' ? 'bg-amber-500/10 text-amber-400' :
                        'bg-red-500/10 text-red-400'
                      }`}>
                        {m.status === 'success' ? '成功' : m.status === 'simulated' ? '模擬' : '失敗'}
                      </span>
                      <span className="text-gray-400 truncate max-w-[150px]">{m.market_slug}</span>
                    </div>
                    <div className="flex items-center gap-3 font-mono">
                      <span className="text-gray-500">{m.amount?.toFixed(0)} 對</span>
                      <span className="text-emerald-400">${m.usdc_received?.toFixed(2)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!overview && !loading && (
        <div className="text-center py-8 text-gray-600">
          <BarChart3 className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">暫無分析數據</p>
          <p className="text-xs mt-1">機器人開始交易後將自動記錄</p>
        </div>
      )}
    </div>
  )
}

function MiniStat({ icon, label, value, color = 'gray' }) {
  const colors = {
    emerald: 'border-emerald-500/20 text-emerald-400',
    blue: 'border-blue-500/20 text-blue-400',
    violet: 'border-violet-500/20 text-violet-400',
    cyan: 'border-cyan-500/20 text-cyan-400',
    amber: 'border-amber-500/20 text-amber-400',
    red: 'border-red-500/20 text-red-400',
    gray: 'border-gray-500/20 text-gray-400',
  }

  return (
    <div className={`bg-gray-800/50 border rounded-lg p-3 ${colors[color]?.split(' ')[0] || ''}`}>
      <div className="flex items-center gap-1.5 mb-1">
        <span className={colors[color]?.split(' ')[1] || 'text-gray-400'}>{icon}</span>
        <span className="text-[10px] text-gray-500">{label}</span>
      </div>
      <p className={`text-sm font-bold font-mono ${colors[color]?.split(' ')[1] || 'text-gray-400'}`}>{value}</p>
    </div>
  )
}

export default AnalyticsDashboard
