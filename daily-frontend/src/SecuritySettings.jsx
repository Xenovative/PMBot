import { useState, useRef } from 'react'
import { Shield, Smartphone, Copy, Check, Lock, KeyRound, Eye, EyeOff, LogOut } from 'lucide-react'

const API = ''

export default function SecuritySettings({ token, onLogout }) {
  const [totpEnabled, setTotpEnabled] = useState(null)
  const [setupData, setSetupData] = useState(null) // { qr, secret, uri }
  const [verifyCode, setVerifyCode] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [changePw, setChangePw] = useState(false)
  const [newPassword, setNewPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const codeRef = useRef(null)

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  }

  // Check 2FA status on mount
  useState(() => {
    fetch(`${API}/api/auth/status`)
      .then(r => r.json())
      .then(d => setTotpEnabled(d.totp_enabled))
      .catch(() => {})
  })

  async function start2FASetup() {
    setLoading(true)
    setError('')
    setSuccess('')
    try {
      const res = await fetch(`${API}/api/auth/2fa/setup`, { method: 'POST', headers })
      const data = await res.json()
      if (data.qr) {
        setSetupData(data)
        setTimeout(() => codeRef.current?.focus(), 200)
      } else {
        setError(data.error || 'Failed to start 2FA setup')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  async function verify2FA(e) {
    e.preventDefault()
    if (verifyCode.length !== 6) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/auth/2fa/verify`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ code: verifyCode }),
      })
      const data = await res.json()
      if (data.totp_enabled) {
        setTotpEnabled(true)
        setSetupData(null)
        setVerifyCode('')
        setSuccess('2FA 已成功啟用！')
      } else {
        setError(data.error || '驗證碼錯誤，請重試')
        setVerifyCode('')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  async function disable2FA() {
    if (!confirm('確定要停用兩步驟驗證嗎？')) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/auth/2fa/disable`, { method: 'POST', headers })
      const data = await res.json()
      if (!data.totp_enabled && data.status === 'ok') {
        setTotpEnabled(false)
        setSuccess('2FA 已停用')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  async function changePassword(e) {
    e.preventDefault()
    if (newPassword.length < 6) {
      setError('密碼至少 6 個字元')
      return
    }
    setLoading(true)
    setError('')
    setSuccess('')
    try {
      const res = await fetch(`${API}/api/auth/change-password`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ password: newPassword }),
      })
      const data = await res.json()
      if (data.status === 'ok') {
        setSuccess('密碼已更新')
        setNewPassword('')
        setChangePw(false)
      } else {
        setError(data.error || 'Failed')
      }
    } catch (e) {
      setError('Connection error')
    }
    setLoading(false)
  }

  function copySecret() {
    if (setupData?.secret) {
      navigator.clipboard.writeText(setupData.secret)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium neon-text-magenta flex items-center gap-2">
          <Shield className="w-4 h-4" />
          安全設定
        </h3>
      </div>

      {error && (
        <div className="bg-neon-pink/10 border border-neon-pink/20 rounded-lg px-4 py-2 text-xs text-neon-pink">
          {error}
        </div>
      )}
      {success && (
        <div className="bg-neon-green/10 border border-neon-green/20 rounded-lg px-4 py-2 text-xs text-neon-green">
          {success}
        </div>
      )}

      {/* ── 2FA Section ── */}
      <div className="bg-black/30 border border-neon-magenta/10 rounded-lg p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Smartphone className="w-4 h-4 text-neon-magenta" />
            <span className="text-sm font-medium">兩步驟驗證 (2FA)</span>
          </div>
          <span className={`text-xs px-2 py-0.5 rounded-full ${
            totpEnabled
              ? 'bg-neon-green/10 text-neon-green border border-neon-green/20'
              : 'bg-gray-700/50 text-gray-500 border border-gray-600'
          }`}>
            {totpEnabled ? '已啟用' : '未啟用'}
          </span>
        </div>

        {/* Setup flow */}
        {!totpEnabled && !setupData && (
          <div>
            <p className="text-xs text-gray-500 mb-3">
              使用 Google Authenticator、Authy 或其他 TOTP 應用程式增加帳戶安全性
            </p>
            <button
              onClick={start2FASetup}
              disabled={loading}
              className="flex items-center gap-2 text-xs px-3 py-2 bg-neon-magenta/20 hover:bg-neon-magenta/30 border border-neon-magenta/30 text-neon-magenta rounded-lg transition-all disabled:opacity-40"
            >
              <Shield className="w-3.5 h-3.5" />
              {loading ? '設定中...' : '啟用 2FA'}
            </button>
          </div>
        )}

        {/* QR Code display */}
        {setupData && (
          <div className="space-y-4">
            <p className="text-xs text-gray-400">
              1. 使用 Authenticator 應用掃描下方 QR Code
            </p>
            <div className="flex justify-center">
              <div className="bg-white p-2 rounded-lg">
                <img src={setupData.qr} alt="2FA QR Code" className="w-48 h-48" />
              </div>
            </div>

            <div>
              <p className="text-xs text-gray-400 mb-1">或手動輸入密鑰：</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-black/40 border border-neon-magenta/10 rounded px-3 py-2 text-xs font-mono text-neon-magenta break-all">
                  {setupData.secret}
                </code>
                <button
                  onClick={copySecret}
                  className="p-2 bg-black/40 border border-neon-magenta/10 rounded hover:bg-neon-magenta/10 transition-all"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-neon-green" /> : <Copy className="w-3.5 h-3.5 text-gray-400" />}
                </button>
              </div>
            </div>

            <form onSubmit={verify2FA} className="space-y-3">
              <p className="text-xs text-gray-400">
                2. 輸入應用程式顯示的 6 位數驗證碼
              </p>
              <input
                ref={codeRef}
                type="text"
                inputMode="numeric"
                maxLength={6}
                value={verifyCode}
                onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="000000"
                className="w-full bg-black/40 border border-neon-magenta/15 rounded-lg px-4 py-3 text-center text-xl font-mono tracking-[0.5em] focus:outline-none focus:border-neon-magenta/50 focus:shadow-neon-magenta transition-all text-gray-200"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={loading || verifyCode.length !== 6}
                  className="flex-1 py-2 bg-neon-magenta/20 hover:bg-neon-magenta/30 border border-neon-magenta/40 text-neon-magenta rounded-lg text-sm font-medium transition-all disabled:opacity-40"
                >
                  {loading ? '驗證中...' : '驗證並啟用'}
                </button>
                <button
                  type="button"
                  onClick={() => { setSetupData(null); setVerifyCode('') }}
                  className="px-4 py-2 bg-black/30 border border-gray-600 rounded-lg text-sm text-gray-400 transition-all hover:bg-black/50"
                >
                  取消
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Disable 2FA */}
        {totpEnabled && !setupData && (
          <div>
            <p className="text-xs text-gray-500 mb-3">
              兩步驟驗證已啟用。每次登入時需要輸入 Authenticator 驗證碼。
            </p>
            <button
              onClick={disable2FA}
              disabled={loading}
              className="flex items-center gap-2 text-xs px-3 py-2 bg-neon-pink/10 hover:bg-neon-pink/20 border border-neon-pink/20 text-neon-pink rounded-lg transition-all disabled:opacity-40"
            >
              停用 2FA
            </button>
          </div>
        )}
      </div>

      {/* ── Change Password ── */}
      <div className="bg-black/30 border border-neon-cyan/10 rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-neon-cyan" />
          <span className="text-sm font-medium">變更密碼</span>
        </div>

        {!changePw ? (
          <button
            onClick={() => { setChangePw(true); setError(''); setSuccess('') }}
            className="flex items-center gap-2 text-xs px-3 py-2 bg-neon-cyan/10 hover:bg-neon-cyan/20 border border-neon-cyan/20 text-neon-cyan rounded-lg transition-all"
          >
            <Lock className="w-3.5 h-3.5" />
            變更密碼
          </button>
        ) : (
          <form onSubmit={changePassword} className="space-y-3">
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="新密碼（至少 6 個字元）"
                className="w-full bg-black/40 border border-neon-cyan/15 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-neon-cyan/50 focus:shadow-neon-cyan transition-all text-gray-200 pr-10"
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowPw(!showPw)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={loading || newPassword.length < 6}
                className="px-4 py-2 bg-neon-cyan/20 hover:bg-neon-cyan/30 border border-neon-cyan/40 text-neon-cyan rounded-lg text-sm font-medium transition-all disabled:opacity-40"
              >
                {loading ? '更新中...' : '更新密碼'}
              </button>
              <button
                type="button"
                onClick={() => { setChangePw(false); setNewPassword('') }}
                className="px-4 py-2 bg-black/30 border border-gray-600 rounded-lg text-sm text-gray-400 transition-all hover:bg-black/50"
              >
                取消
              </button>
            </div>
          </form>
        )}
      </div>

      {/* ── Logout ── */}
      <div className="bg-black/30 border border-neon-pink/10 rounded-lg p-4">
        <button
          onClick={onLogout}
          className="flex items-center gap-2 text-xs px-3 py-2 bg-neon-pink/10 hover:bg-neon-pink/20 border border-neon-pink/20 text-neon-pink rounded-lg transition-all"
        >
          <LogOut className="w-3.5 h-3.5" />
          登出
        </button>
      </div>
    </div>
  )
}
