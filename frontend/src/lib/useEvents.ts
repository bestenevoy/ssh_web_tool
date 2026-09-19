// 事件订阅 store（模块级单例，useSyncExternalStore 的外部数据源）：
// 事件 WS 后台推送，列表变化只通知订阅者（设置页「日志」分区）。
// 原实现是 App 层 useState：每条服务端事件都 setEvents 重渲染整个应用
// （含全部终端层），改为外部 store 后 App 不再因事件刷新。
import { useSyncExternalStore } from 'react'
import type { EventLogItem } from '../types'
import type { WsEventMessage } from '../types/ws'

// 事件列表上限：超出丢弃最旧条目
const MAX_EVENTS = 500

let eventList: EventLogItem[] = []
const listeners = new Set<() => void>()

function emitChange() {
  listeners.forEach((l) => l())
}

/** 追加一条事件（最新在前，保留 MAX_EVENTS 条） */
function pushEvent(item: EventLogItem) {
  eventList = [item, ...eventList].slice(0, MAX_EVENTS)
  emitChange()
}

/** 清空事件列表（设置页「清空日志」调用） */
export function clearEvents() {
  if (eventList.length === 0) return
  eventList = []
  emitChange()
}

// ---- 事件 WS：首个订阅者出现时懒建连，之后应用存续期间保持 ----
// （与旧版 App 级挂载等价；断开 3s 重连）
let wsStarted = false

function startEventWs() {
  if (wsStarted) return
  wsStarted = true
  const connect = () => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/events`)
    ws.onmessage = (e) => {
      try {
        // 事件通道消息判别联合（types/ws.ts，对应后端 event_bus.publish 的构造）
        const msg = JSON.parse(e.data) as WsEventMessage
        if (msg.type === 'event') pushEvent(msg)
      } catch { /* 非法消息忽略 */ }
    }
    ws.onclose = () => setTimeout(connect, 3000)
    ws.onerror = () => ws.close()
  }
  connect()
}

function subscribe(listener: () => void): () => void {
  startEventWs()
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 订阅事件列表：仅当前组件随事件重渲染 */
export function useEventList(): EventLogItem[] {
  return useSyncExternalStore(subscribe, () => eventList)
}
