import { useMemo, useRef, useState } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'
import type { Host } from '../types'
import { groupKeyOf, groupLabelOf } from '../lib/sessionOrder'
import { profileOf } from '../lib/shellProfiles'

interface Props {
  terminals: Map<string, TerminalInstance>
  hosts: Host[]  // 用于把 host_id 解析成 ip / port / 用户名
  activeId: string | null
  /** 自定义组顺序（localStorage 持久化，App 统一持有；拖拽更新后回调） */
  customOrder: string[]
  onOrderChange: (keys: string[]) => void
  onSwitch: (id: string) => void
  onClose: (id: string) => void
}

interface SessionGroup {
  key: string    // '__local__' / '__tmp__' / 'ip:port'
  ip: string
  label: string  // 组头主显示：主机名 / 本地终端 / 临时 SSH
  items: TerminalInstance[]
}

// 右侧会话列表面板：按主机 ip:port 聚合分组（组头=主机名 + 会话数徽标，
// 组内=会话条目），点击条目切换到该会话；与顶部标签栏并存，不互相替代。
// 分组规则（与 tab 栏共用 sessionOrder）：
// - 已保存主机会话按 'ip:port' 分组；同 ip:port 不同登录用户允许共存，
//   组内条目以 username@host 区分（单击切换可分辨）
// - 本地终端 ssh 链接建立的会话（ssh user:pass@ip:port）独立成「SSH 链接」组，
//   不属于任何主机会话
// - 本地终端单列「本地终端」组
// 单会话主机不组织成树，直接一行显示；多会话主机才列成树。
// 排序：组间 = 自定义顺序（拖拽）优先，未收录按组 key 升序；组内 = 创建顺序。
export function SessionPanel({ terminals, hosts, activeId, customOrder, onOrderChange, onSwitch, onClose }: Props) {
  const groups = useMemo<SessionGroup[]>(() => {
    const hostMap = new Map(hosts.map((h) => [h.id, h]))
    const groups: SessionGroup[] = []
    const index = new Map<string, number>()
    for (const t of terminals.values()) {
      const key = groupKeyOf(t, hostMap)
      const gi = index.get(key) ?? groups.length
      if (gi === groups.length) {
        index.set(key, gi)
        const h = key !== '__local__' && key !== '__tmp__' ? hostMap.get(t.host_id) : undefined
        groups.push({ key, ip: h?.host || t.host_name, label: groupLabelOf(key, hostMap), items: [] })
      }
      groups[gi].items.push(t)
    }
    return groups
  }, [terminals, hosts])

  // 拖拽排序状态（组间自定义顺序，App 统一持久化）
  const dragKey = useRef<string | null>(null)
  const [dragOverKey, setDragOverKey] = useState<string | null>(null)

  // 组间排序：自定义顺序优先（未收录的组按组 key 升序排后面）
  const sortedGroups = useMemo(() => {
    const byKey = (a: SessionGroup, b: SessionGroup) => a.key.localeCompare(b.key)
    if (customOrder.length === 0) return [...groups].sort(byKey)
    const pos = new Map(customOrder.map((k, i) => [k, i]))
    return [...groups].sort((a, b) => {
      const pa = pos.get(a.key)
      const pb = pos.get(b.key)
      if (pa !== undefined && pb !== undefined) return pa - pb
      if (pa !== undefined) return -1
      if (pb !== undefined) return 1
      return byKey(a, b)
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
    onOrderChange(keys)
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

  // 会话条目标题（区分同 ip:port 不同用户 / ssh 链接会话 + shell 特性）
  const itemTitle = (t: TerminalInstance, hostMap: Map<string, Host>): string => {
    const parts: string[] = []
    if (t.ssh_conn) {
      // ssh 链接会话：完整连接信息
      parts.push(`ssh ${t.ssh_conn.username}:${t.ssh_conn.password ? '***' : ''}@${t.ssh_conn.host}:${t.ssh_conn.port}`)
    } else if (t.type !== 'local') {
      const h = hostMap.get(t.host_id)
      parts.push(`${h?.username || ''}@${h?.host || t.host_name}${h?.port && h.port !== 22 ? `:${h.port}` : ''}`)
    }
    parts.push(t.terminal_name)
    if (t.reconnecting) parts.push('（重连中…）')
    if (t.pending) parts.push('（连接中…）')
    if (t.disconnected) parts.push('（已断开）')
    const p = profileOf(t)
    return parts.join(' · ') + `\nShell: ${p.label}（${p.summary}）\n清屏: ${p.clearCmd}\n退出: ${p.exitCmd}`
  }
  // 条目主文本：ssh 链接会话显示完整「ssh user:pass@ip:port」；已保存主机会话显示
  // username@host（同 ip:port 不同用户可区分）；本地显示 terminal_name
  const itemText = (t: TerminalInstance): string => {
    if (t.ssh_conn) return `ssh ${t.ssh_conn.username}:${t.ssh_conn.password}@${t.ssh_conn.host}:${t.ssh_conn.port}`
    if (t.type !== 'local' && !t.host_id) return t.host_name || t.terminal_name
    return t.terminal_name
  }

  const hostMap = new Map(hosts.map((h) => [h.id, h]))

  // 会话条目：状态点 + 名称 + shell 徽标 + 关闭（两处渲染共用）
  const renderItem = (t: TerminalInstance) => {
    const p = profileOf(t)
    return (
      <div
        key={t.session_id}
        className={`session-item${t.session_id === activeId ? ' active' : ''}`}
        onClick={() => onSwitch(t.session_id)}
        title={itemTitle(t, hostMap)}
      >
        <span className={`status-dot${t.disconnected ? ' disconnected' : t.reconnecting || t.pending ? ' reconnecting' : ''}`} />
        <span className="sess-name">{itemText(t)}</span>
        <span
          className="sess-shell"
          style={{ color: p.badgeColor, background: p.badgeBg }}
          title={`${p.label}（${p.summary}）`}
        >{p.badge}</span>
        <span
          className="sess-close"
          title="关闭会话（同时关闭连接）"
          onClick={(e) => { e.stopPropagation(); onClose(t.session_id) }}
        >✕</span>
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
            // 单会话主机：不组织成树，直接一行显示
            g.items.map((t) => renderItem(t))
          ) : (
            // 多会话主机：组头（主机名 + 会话数）+ 组内会话条目（树形）
            <>
              <div className="session-group-header" title={`${g.label}${g.ip !== g.label ? ` · ${g.ip}` : ''}`}>
                <span className="session-group-name">{g.label}</span>
                {g.items.length > 1 && <span className="session-group-count">{g.items.length}</span>}
              </div>
              {g.items.map((t) => renderItem(t))}
            </>
          )}
        </div>
      ))}
    </div>
  )
}
