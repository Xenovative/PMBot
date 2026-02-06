import { useEffect, useRef, useState, useCallback } from 'react'

export function useWebSocket(url) {
  const [status, setStatus] = useState(null)
  const [markets, setMarkets] = useState([])
  const [trades, setTrades] = useState([])
  const [mergeStatus, setMergeStatus] = useState(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          switch (msg.type) {
            case 'status':
              setStatus(msg.data)
              break
            case 'markets':
              setMarkets(msg.data)
              break
            case 'trade':
              setTrades((prev) => [msg.data, ...prev].slice(0, 50))
              break
            case 'merge_status':
              setMergeStatus(msg.data)
              break
            case 'merge':
              // refresh merge status on next status push
              break
            case 'pong':
              break
          }
        } catch (e) {
          console.error('WS parse error:', e)
        }
      }

      ws.onclose = () => {
        setConnected(false)
        reconnectTimer.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch (e) {
      console.error('WS connect error:', e)
      reconnectTimer.current = setTimeout(connect, 3000)
    }
  }, [url])

  useEffect(() => {
    connect()
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30000)

    return () => {
      clearInterval(pingInterval)
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { status, markets, trades, mergeStatus, connected }
}
