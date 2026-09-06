import type { TerminalInstance } from '../lib/useTerminals'
import type { HostType } from '../types'

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  hostTypes: HostType[]
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

export function TerminalTabs({ terminals, activeId, hostTypes, onSwitch, onClose, onNew }: Props) {
  const getTypeColor = (key: string) => hostTypes.find((t) => t.key === key)?.color || '#999'
  const getShellStyle = (type: string) => SHELL_TYPE_STYLES[type] || SHELL_TYPE_STYLES.other

  return (
    <div className="tab-bar">
      {Array.from(terminals.values()).map((t) => {
        const shellStyle = getShellStyle(t.shell_type)
        return (
          <div
            key={t.session_id}
            className={`tab${t.session_id === activeId ? ' active' : ''}`}
            onClick={() => onSwitch(t.session_id)}
          >
            <span className="type-dot" style={{ width: 6, height: 6, borderRadius: '50%', background: getTypeColor(t.type), display: 'inline-block' }} />
            <span className="tab-title">{t.host_name} · {t.terminal_name}</span>
            <span
              className="shell-type-badge"
              style={{
                color: shellStyle.color,
                background: shellStyle.bg,
                fontSize: 10,
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
  )
}
