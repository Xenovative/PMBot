import { useState, useEffect } from 'react'
import { Lock, Smartphone, Shield, Trash2 } from 'lucide-react'

const API = ''

export default function SecuritySettings({ token }) {
  const [passwordSet, setPasswordSet] = useState(false)
  const [totpEnabled, setTotpEnabled] = useState(false)
  const [totpSecret, setTotpSecret] = useState(null)
  const [qrSvg, setQrSvg] = useState(null)
  const [totpCode, setTotpCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [deviceName, setDeviceName] = useState('Authenticator')
  const [devices, setDevices] = useState([])
  const authHeaders = token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : {}

  useEffect(() => {
    refreshStatus()
  }, [])

  async function refreshStatus() {
    try {
      const status = await fetch(`${API}/api/auth/status`).then(r => r.json())
      setPasswordSet(status.setup_complete)
      setTotpEnabled(status.totp_enabled)
      await fetchDevices()
    } catch (e) {
      console.error('status error', e)
    }
  }

  async function fetchDevices() {
    if (!token) return
    try {
      const res = await fetch(`${API}/api/auth/2fa/devices`, { headers: authHeaders })
      const data = await res.json()
      setDevices(data.devices || [])
    } catch (e) {
      console.error('devices error', e)
    }
  }

  async function startTotpSetup() {
    setLoading(true)
    setTotpSecret(null)
    setQrSvg(null)
    try {
      const res = await fetch(`${API}/api/auth/2fa/setup`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ device_name: deviceName || 'Authenticator' })
      })
      const data = await res.json()
      setTotpSecret(data.secret)
      setQrSvg(data.qr_svg)
    } catch (e) {
      console.error('setup error', e)
    }
    setLoading(false)
  }

  async function verifyTotp() {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/auth/2fa/verify`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ code: totpCode })
      })
      const data = await res.json()
      if (data.status === 'ok') {
        setTotpEnabled(true)
        setTotpSecret(null)
        setQrSvg(null)
        setTotpCode('')
        await fetchDevices()
      }
    } catch (e) {
      console.error('verify error', e)
    }
    setLoading(false)
  }

  async function removeDevice(id) {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/auth/2fa/remove`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ device_id: id })
      })
      await res.json()
      await fetchDevices()
    } catch (e) {
      console.error('remove error', e)
    }
    setLoading(false)
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            <Shield className="w-4 h-4 text-cyan-400" /> 安全設定
          </h3>
          <p className="text-xs text-gray-500">管理登入密碼與 2FA 裝置</p>
        </div>
      </div>

      <div className="space-y-3 text-sm text-gray-300">
        <div className="flex items-center gap-2">
          <Lock className="w-4 h-4 text-cyan-400" />
          <span>密碼狀態：</span>
          <span className="font-semibold">{passwordSet ? '已設定' : '未設定 (需先 /api/auth/setup)'} </span>
        </div>
        <div className="flex items-center gap-2">
          <Smartphone className="w-4 h-4 text-cyan-400" />
          <span>2FA：</span>
          <span className="font-semibold">{totpEnabled ? '已啟用' : '未啟用'}</span>
        </div>
      </div>

      <div className="space-y-3">
        <label className="text-xs text-gray-400">裝置名稱</label>
        <input
          value={deviceName}
          onChange={(e) => setDeviceName(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200"
          placeholder="Authenticator"
        />
        <button
          onClick={startTotpSetup}
          disabled={loading}
          className="px-3 py-2 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-sm disabled:opacity-50"
        >
          產生 2FA QR Code
        </button>
      </div>

      {qrSvg && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
          <div className="text-xs text-gray-400 mb-2">掃描 QR 並輸入 6 位數驗證碼以啟用</div>
          <div dangerouslySetInnerHTML={{ __html: qrSvg }} />
          <input
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            placeholder="6 位數驗證碼"
            className="mt-2 w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200"
          />
          <button
            onClick={verifyTotp}
            disabled={loading || totpCode.length !== 6}
            className="mt-2 px-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm disabled:opacity-50"
          >
            驗證並啟用
          </button>
        </div>
      )}

      <div>
        <h4 className="text-xs text-gray-400 mb-2">已註冊的裝置</h4>
        <div className="space-y-2">
          {devices.length === 0 && (
            <div className="text-xs text-gray-500">尚無裝置</div>
          )}
          {devices.map(d => (
            <div key={d.id} className="flex items-center justify-between bg-gray-800 border border-gray-700 rounded-lg px-3 py-2">
              <div>
                <div className="text-sm text-gray-200">{d.name || 'Authenticator'}</div>
                <div className="text-xs text-gray-500">ID: {d.id}</div>
              </div>
              <button
                onClick={() => removeDevice(d.id)}
                className="text-red-400 hover:text-red-300 text-xs flex items-center gap-1"
              >
                <Trash2 className="w-3 h-3" /> 移除
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
