import { useState, useEffect, useRef } from 'react'
import { Zap, Lock, Shield, Eye, EyeOff, KeyRound, Smartphone, Copy, Check } from 'lucide-react'

const API = ''

export default function LoginPage({ onLogin }) {
  const [authStatus, setAuthStatus] = useState(null) // { setup_complete, totp_enabled }
  const [mode, setMode] = useState('loading') // loading, setup, login, totp
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const totpRef = useRef(null)

  useEffect(() => {
    checkAuthStatus()
  }, [])

  async function checkAuthStatus() {
    try {
      const res = await fetch(`${API}/api/auth/status`)
      const data = await res.json()
      setAuthStatus(data)
      if (!data.setup_complete) {
        setMode('setup')
      } else {
        setMode('login')
      }
    } catch (e) {
      setError('Cannot connect to server')
      setMode('login')
    }
  }

  async function handleSetup(e) {
    e.preventDefault()
    if (password.length < 6) {
      setError('密碼至少 6 個字元')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/auth/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      const data = await res.json()
      if (data.token) {
        onLogin(data.token)
      } else {
        setError(data.error || 'Setup failed')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  async function handleLogin(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, totp_code: totpCode || undefined }),
      })
      const data = await res.json()
      if (data.token) {
        onLogin(data.token)
      } else if (data.needs_totp) {
        setMode('totp')
        setError('')
        setTimeout(() => totpRef.current?.focus(), 100)
      } else {
        setError(data.error || 'Login failed')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  async function handleTotpSubmit(e) {
    e.preventDefault()
    if (totpCode.length !== 6) {
      setError('請輸入 6 位數驗證碼')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, totp_code: totpCode }),
      })
      const data = await res.json()
      if (data.token) {
        onLogin(data.token)
      } else {
        setError(data.error || '驗證碼錯誤')
        setTotpCode('')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  if (mode === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center relative">
        <div className="fixed inset-0 z-0">
          <img src="/background.jpeg" alt="" className="w-full h-full object-cover" />
          <div className="absolute inset-0 bg-black/80" />
        </div>
        <div className="relative z-10 text-neon-cyan animate-pulse font-cyber text-lg">
          CONNECTING...
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center relative scanlines">
      {/* Background */}
      <div className="fixed inset-0 z-0">
        <img src="/background.jpeg" alt="" className="w-full h-full object-cover" />
        <div className="absolute inset-0 bg-black/80" />
        <div className="absolute inset-0 bg-gradient-to-b from-black/40 via-transparent to-black/80" />
      </div>

      <div className="relative z-10 w-full max-w-md px-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-neon-cyan/10 border border-neon-cyan/30 flex items-center justify-center shadow-neon-cyan-lg mx-auto mb-4">
            <Zap className="w-8 h-8 text-neon-cyan" />
          </div>
          <h1 className="text-2xl font-bold font-cyber neon-text-cyan tracking-wider">
            PM ARB BOT
          </h1>
          <p className="text-neon-cyan/30 text-sm tracking-widest uppercase mt-1">
            Daily Crypto Arbitrage
          </p>
        </div>

        {/* Card */}
        <div className="cyber-panel p-8">

          {/* ── SETUP MODE ── */}
          {mode === 'setup' && (
            <form onSubmit={handleSetup} className="space-y-6">
              <div className="text-center mb-2">
                <KeyRound className="w-6 h-6 text-neon-amber mx-auto mb-2" />
                <h2 className="text-lg font-cyber neon-text-amber">初始設定</h2>
                <p className="text-xs text-gray-500 mt-1">設定管理員密碼以保護您的機器人</p>
              </div>

              <div>
                <label className="text-xs text-neon-cyan/50 block mb-1.5 uppercase tracking-wider">
                  設定密碼
                </label>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="至少 6 個字元"
                    className="w-full bg-black/40 border border-neon-cyan/15 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-neon-cyan/50 focus:shadow-neon-cyan transition-all text-gray-200 pr-10"
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {error && (
                <p className="text-neon-pink text-xs text-center">{error}</p>
              )}

              <button
                type="submit"
                disabled={loading || password.length < 6}
                className="w-full py-3 bg-neon-cyan/20 hover:bg-neon-cyan/30 border border-neon-cyan/40 text-neon-cyan rounded-lg font-medium transition-all shadow-neon-cyan disabled:opacity-40 disabled:cursor-not-allowed font-cyber tracking-wider"
              >
                {loading ? 'SETTING UP...' : 'CREATE PASSWORD'}
              </button>
            </form>
          )}

          {/* ── LOGIN MODE ── */}
          {mode === 'login' && (
            <form onSubmit={handleLogin} className="space-y-6">
              <div className="text-center mb-2">
                <Lock className="w-6 h-6 text-neon-cyan mx-auto mb-2" />
                <h2 className="text-lg font-cyber neon-text-cyan">登入</h2>
                <p className="text-xs text-gray-500 mt-1">輸入密碼以存取控制面板</p>
              </div>

              <div>
                <label className="text-xs text-neon-cyan/50 block mb-1.5 uppercase tracking-wider">
                  密碼
                </label>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="輸入密碼"
                    className="w-full bg-black/40 border border-neon-cyan/15 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-neon-cyan/50 focus:shadow-neon-cyan transition-all text-gray-200 pr-10"
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {error && (
                <p className="text-neon-pink text-xs text-center">{error}</p>
              )}

              <button
                type="submit"
                disabled={loading || !password}
                className="w-full py-3 bg-neon-cyan/20 hover:bg-neon-cyan/30 border border-neon-cyan/40 text-neon-cyan rounded-lg font-medium transition-all shadow-neon-cyan disabled:opacity-40 disabled:cursor-not-allowed font-cyber tracking-wider"
              >
                {loading ? 'AUTHENTICATING...' : 'LOGIN'}
              </button>
            </form>
          )}

          {/* ── TOTP MODE ── */}
          {mode === 'totp' && (
            <form onSubmit={handleTotpSubmit} className="space-y-6">
              <div className="text-center mb-2">
                <Smartphone className="w-6 h-6 text-neon-magenta mx-auto mb-2" />
                <h2 className="text-lg font-cyber neon-text-magenta">兩步驟驗證</h2>
                <p className="text-xs text-gray-500 mt-1">請輸入 Authenticator 上的 6 位數驗證碼</p>
              </div>

              <div>
                <label className="text-xs text-neon-magenta/50 block mb-1.5 uppercase tracking-wider">
                  驗證碼
                </label>
                <input
                  ref={totpRef}
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder="000000"
                  className="w-full bg-black/40 border border-neon-magenta/15 rounded-lg px-4 py-3 text-center text-2xl font-mono tracking-[0.5em] focus:outline-none focus:border-neon-magenta/50 focus:shadow-neon-magenta transition-all text-gray-200"
                  autoFocus
                />
              </div>

              {error && (
                <p className="text-neon-pink text-xs text-center">{error}</p>
              )}

              <button
                type="submit"
                disabled={loading || totpCode.length !== 6}
                className="w-full py-3 bg-neon-magenta/20 hover:bg-neon-magenta/30 border border-neon-magenta/40 text-neon-magenta rounded-lg font-medium transition-all shadow-neon-magenta disabled:opacity-40 disabled:cursor-not-allowed font-cyber tracking-wider"
              >
                {loading ? 'VERIFYING...' : 'VERIFY'}
              </button>

              <button
                type="button"
                onClick={() => { setMode('login'); setTotpCode(''); setError('') }}
                className="w-full text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                ← 返回密碼登入
              </button>
            </form>
          )}
        </div>

        {/* Footer */}
        <p className="text-center text-neon-cyan/20 text-xs mt-6 font-cyber tracking-wider">
          SECURED ACCESS
        </p>
      </div>
    </div>
  )
}
