// 事件订阅 hook - 观察 Server 交互过程
import { useState, useEffect, useRef } from 'react'
import type { EventLogItem } from '../types'

export function useEvents() {
  const [events, setEvents] = useState<EventLogItem[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${location.host}/ws/events`)
      wsRef.current = ws

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'event') {
            setEvents((prev) => [msg, ...prev].slice(0, 500))
          }
        } catch {}
      }

      ws.onclose = () => {
        reconnectTimer = setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      clearTimeout(reconnectTimer)
      if (wsRef.current) wsRef.current.close()
    }
  }, [])

  const clearEvents = () => setEvents([])

  return { events, clearEvents }
}
