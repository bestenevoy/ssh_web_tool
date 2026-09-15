// 事件订阅 hook - 观察 Server 交互过程
import { useState, useEffect, useRef } from 'react'
import type { EventLogItem } from '../types'
import type { WsEventMessage } from '../types/ws'

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
          // 事件通道消息判别联合（types/ws.ts，对应后端 event_bus.publish 的构造）
          const msg = JSON.parse(event.data) as WsEventMessage
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
