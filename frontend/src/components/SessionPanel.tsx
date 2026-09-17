import { useMemo, useRef, useState } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'
import type { Host } from '../types'

interface Props {
  terminals: Map<string, TerminalInstance>
  hosts: Host[]  // 用于把 host_id 解析成 ip / 主机名
  activeId: string | null
  onSwitch: (id: string) => void
  onClose: (id: string) => void
}

interface SessionGroup {
  key: string    // ip 或 '__local__'
  ip: string
  label: string  // 组头主显示：主机名（无则 ip）
  items: TerminalInstance[]
}

// 会话列表面板的自定义排序（按组 key=ip 持久化；未保存时默认按 ip 升序）
const ORDER_KEY = 'ssh-web-tool-session-order'

function loadOrder(): string[] {
  try {
    const raw = localStorage.getItem(ORDER_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

function saveOrder(order: string[]) {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(order))
  } catch {
    /* localStorage 不可用时忽略（排序仅在当前会话生效） */
  }
}

// 右侧会话列表面板：按主机 ip 聚合分组（组头=主机名/ip + 会话数徽标，
// 组内=会话条目），点击条目切换到该会话；与顶部标签栏并存，不互相替代。
// 单会话主机不组织成树，直接一行显示「ip · 会话名」；多会话主机才列成树。
// 组/条目支持拖拽排序（默认按 ip 升序，拖拽后按用户顺序持久化到 localStorage）。
export function SessionPanel({ terminals, hosts, activeId, onSwitch, onClose }: Props) {
  const groups = useMemo<SessionGroup[]>(() => {
    const hostMap = new Map(hosts.map((h) => [h.id, h]))
    const groups: SessionGroup[] = []
    const index = new Map<string, number>()
    for (const t of terminals.values()) {
      if (t.type === 'local') {
        // 本机会话不经过 SSH，单列「本地终端」组
        const gi = index.get('__local__') ?? groups.length
        if (gi === groups.length) {
          index.set('__local__', gi)
          groups.push({ key: '__local__', ip: 'localhost', label: '本地终端', items: [] })
        }
        groups[gi].items.push(t)
        continue
      }
      // 分组键 = 连接 ip：拦截 SSH 会话用原始凭据，已保存主机查配置，都无则退回显示名
      const ip = t.ssh_conn?.host || hostMap.get(t.host_id)?.host || t.host_name
      const name = hostMap.get(t.host_id)?.name || ''
      const gi = index.get(ip) ?? groups.length
      if (gi === groups.length) {
        index.set(ip, gi)
        groups.push({ key: ip, ip, label: name || ip, items: [] })
      }
      groups[gi].items.push(t)
    }
    return groups
  }, [terminals, hosts])

  // 拖拽排序状态（持久化到 localStorage）
  const [customOrder, setCustomOrder] = useState<string[]>(loadOrder)
  const dragKey = useRef<string | null>(null)
  const [dragOverKey, setDragOverKey] = useState<string | null>(null)

  // 排序：自定义顺序优先（未收录的组按 ip 排后面）；未保存过时按 ip 升序
  const sortedGroups = useMemo(() => {
    const byIp = (a: SessionGroup, b: SessionGroup) => a.key.localeCompare(b.key)
    if (customOrder.length === 0) return [...groups].sort(byIp)
    const pos = new Map(customOrder.map((k, i) => [k, i]))
    return [...groups].sort((a, b) => {
      const pa = pos.get(a.key)
      const pb = pos.get(b.key)
      if (pa !== undefined && pb !== undefined) return pa - pb
      if (pa !== undefined) return -1
      if (pb !== undefined) return 1
      return byIp(a, b)
    })
  }, [groups, customOrder])

  const onDragStart = (key: string) => (e: React.DragEvent) => {
    dragKey.current = key
    e.dataTransfer.effectAllowed = 'move'
  }
  const onDragOver = (key: string) => (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOverKey(key)
  }
  const onDrop = (key: string) => (e: React.DragEvent) => {
    e.preventDefault()
    const from = dragKey.current
    setDragOverKey(null)
    if (!from || from === key) return
    const keys = sortedGroups.map((g) => g.key)
    const fromIdx = keys.indexOf(from)
    const toIdx = keys.indexOf(key)
    keys.splice(fromIdx, 1)
    keys.splice(toIdx, 0, from)
    setCustomOrder(keys)
    saveOrder(keys)
  }
  const onDragEnd = () => {
    dragKey.current = null
    setDragOverKey(null)
  }

  if (terminals.size === 0) {
    return (
      <div className="session-panel-body">
        <div className="session-panel-empty">暂无会话<br />连接主机后在此显示</div>
      </div>
    )
  }

  return (
    <div className="session-panel-body">
      {sortedGroups.map((g) => (
        <div
          key={g.key}
          className={`session-draggable${dragOverKey === g.key ? ' drag-over' : ''}`}
          draggable
          onDragStart={onDragStart(g.key)}
          onDragOver={onDragOver(g.key)}
          onDrop={onDrop(g.key)}
          onDragEnd={onDragEnd}
          title="拖拽调整排序"
        >
          {g.items.length <= 1 ? (
            // 单会话主机：不组织成树，直接一行显示 ip 会话（无组头层级）
            g.items.map((t) => (
              <div
                key={t.session_id}
                className={`session-item${t.session_id === activeId ? ' active' : ''}`}
                onClick={() => onSwitch(t.session_id)}
                title={`${t.host_name} · ${t.terminal_name}${t.reconnecting ? '（重连中…）' : t.disconnected ? '（已断开）' : ''}`}
              >
                <span className={`status-dot${t.disconnected ? ' disconnected' : t.reconnecting ? ' reconnecting' : ''}`} />
                <span className="sess-name">{g.label} · {t.terminal_name}</span>
                <span
                  className="sess-close"
                  title="关闭会话（同时关闭连接）"
                  onClick={(e) => { e.stopPropagation(); onClose(t.session_id) }}
                >✕</span>
              </div>
            ))
          ) : (
            // 多会话主机：组头（主机名/ip + 会话数）+ 组内会话条目（树形）
            <>
              <div className="session-group-header" title={`${g.label}${g.ip !== g.label ? ` · ${g.ip}` : ''}`}>
                <span className="session-group-name">{g.label}</span>
                {g.items.length > 1 && <span className="session-group-count">{g.items.length}</span>}
              </div>
              {g.items.map((t) => (
                <div
                  key={t.session_id}
                  className={`session-item${t.session_id === activeId ? ' active' : ''}`}
                  onClick={() => onSwitch(t.session_id)}
                  title={`${t.host_name} · ${t.terminal_name}${t.reconnecting ? '（重连中…）' : t.disconnected ? '（已断开）' : ''}`}
                >
                  <span className={`status-dot${t.disconnected ? ' disconnected' : t.reconnecting ? ' reconnecting' : ''}`} />
                  <span className="sess-name">{t.terminal_name}</span>
                  <span
                    className="sess-close"
                    title="关闭会话（同时关闭连接）"
                    onClick={(e) => { e.stopPropagation(); onClose(t.session_id) }}
                  >✕</span>
                </div>
              ))}
            </>
          )}
        </div>
      ))}
    </div>
  )
}
