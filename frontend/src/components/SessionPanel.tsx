import { useMemo } from 'react'
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

// 右侧会话列表面板：按主机 ip 聚合分组（组头=主机名/ip + 会话数徽标，
// 组内=会话条目），点击条目切换到该会话；与顶部标签栏并存，不互相替代
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

  if (terminals.size === 0) {
    return (
      <div className="session-panel-body">
        <div className="session-panel-empty">暂无会话<br />连接主机后在此显示</div>
      </div>
    )
  }

  return (
    <div className="session-panel-body">
      {groups.map((g) => (
        <div key={g.key} className="session-group">
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
        </div>
      ))}
    </div>
  )
}
