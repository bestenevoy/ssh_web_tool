import type { TerminalInstance } from '../lib/useTerminals'
import type { Host, HostType } from '../types'

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  hostTypes: HostType[]
  hosts: Host[]  // 用于解析会话 ip / 分组（tab title 提示）
  /** 分屏窗格中的会话集合（tab 加分屏标记） */
  splitSessions?: Set<string>
  onSwitch: (id: string) => void
  onClose: (id: string) => void
  onNew: () => void
}

// shell 类型标签和颜色
const SHELL_TYPE_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  shell: { label: 'sh', color: '#4caf50', bg: 'rgba(76,175,80,0.15)' },
  python: { label: 'py', color: '#ff9800', bg: 'rgba(255,152,0,0.15)' },
  mysql: { label: 'sql', color: '#2196f3', bg: 'rgba(33,150,243,0.15)' },
  pager: { label: 'pg', color: '#9c27b0', bg: 'rgba(156,39,176,0.15)' },
  other: { label: '··', color: '#999', bg: 'rgba(153,153,153,0.15)' },
}

export function TerminalTabs({ terminals, activeId, hostTypes, hosts, splitSessions, onSwitch, onClose, onNew }: Props) {
  const getTypeColor = (key: string) => hostTypes.find((t) => t.key === key)?.color || '#999'
  const getShellStyle = (type: string) => SHELL_TYPE_STYLES[type] || SHELL_TYPE_STYLES.other

  // host_id -> group 映射
  const hostGroupMap = new Map<string, string>()
  const hostById = new Map(hosts.map((h) => [h.id, h]))
  hosts.forEach((h) => {
    if (h.id && h.group) hostGroupMap.set(h.id, h.group)
  })

  // 会话 ip：拦截 SSH 会话用原始凭据，已保存主机查配置，本地会话显示「本地」
  const hostIpOf = (t: TerminalInstance): string =>
    t.type === 'local' ? '本地' : t.ssh_conn?.host || hostById.get(t.host_id)?.host || t.host_name

  return (
    <div className="tab-bar-wrap">
      <div className="tab-bar">
        {Array.from(terminals.values()).map((t, i) => {
          const shellStyle = getShellStyle(t.shell_type)
          const group = hostGroupMap.get(t.host_id)
          const ip = hostIpOf(t)
          const stateHint = t.pending ? '（连接中…）' : t.reconnecting ? '（重连中…）' : t.disconnected ? '（已断开）' : ''
          return (
            <div
              key={t.session_id}
              className={`tab${t.session_id === activeId ? ' active' : ''}${t.disconnected ? ' disconnected' : ''}${t.reconnecting || t.pending ? ' connecting' : ''}`}
              onClick={() => onSwitch(t.session_id)}
              title={`${i + 1} · ${ip} · ${t.terminal_name}${group ? ` · ${group}` : ''}${stateHint}`}
            >
              <span className="type-dot" style={{ width: 6, height: 6, borderRadius: '50%', background: getTypeColor(t.type), display: 'inline-block' }} />
              <span className="tab-title">{t.pending ? `${ip} · 连接中` : `${i + 1} · ${ip}`}</span>
              {splitSessions?.has(t.session_id) && (
                <span className="tab-split-badge" title="该会话正在分屏中显示">◫</span>
              )}
              <span
                className="shell-type-badge"
                style={{
                  color: shellStyle.color,
                  background: shellStyle.bg,
                  fontSize: 'calc(10px * var(--ui-fs-scale))',
                  padding: '1px 5px',
                  borderRadius: 3,
                  fontWeight: 600,
                  marginLeft: 4,
                }}
                title={`当前环境: ${t.shell_type}`}
              >
                {shellStyle.label}
              </span>
              <span className="tab-close" onClick={(e) => { e.stopPropagation(); onClose(t.session_id) }}>✕</span>
            </div>
          )
        })}
        <div className="tab-new" onClick={onNew} title="在当前主机新建终端">+</div>
      </div>
    </div>
  )
}
