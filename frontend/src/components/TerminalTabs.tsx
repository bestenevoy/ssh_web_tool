import { useState } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'
import type { Host, HostType } from '../types'

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  hostTypes: HostType[]
  hosts: Host[]  // 用于按分组/主机筛选
  groups: string[]
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

export function TerminalTabs({ terminals, activeId, hostTypes, hosts, groups, onSwitch, onClose, onNew }: Props) {
  const [groupFilter, setGroupFilter] = useState('')
  const [hostFilter, setHostFilter] = useState('')

  const getTypeColor = (key: string) => hostTypes.find((t) => t.key === key)?.color || '#999'
  const getShellStyle = (type: string) => SHELL_TYPE_STYLES[type] || SHELL_TYPE_STYLES.other

  // host_id -> group 映射
  const hostGroupMap = new Map<string, string>()
  hosts.forEach((h) => {
    if (h.id && h.group) hostGroupMap.set(h.id, h.group)
  })

  // 按分组 / 主机名筛选
  const filtered = Array.from(terminals.values()).filter((t) => {
    if (groupFilter && hostGroupMap.get(t.host_id) !== groupFilter) return false
    if (hostFilter.trim()) {
      const q = hostFilter.trim().toLowerCase()
      if (!t.host_name.toLowerCase().includes(q) && !t.terminal_name.toLowerCase().includes(q)) return false
    }
    return true
  })

  // 仅在有筛选需求时显示筛选栏（终端数量多时才值得）
  const showFilter = terminals.size >= 4

  return (
    <div className="tab-bar-wrap">
      <div className="tab-bar">
        {filtered.map((t) => {
          const shellStyle = getShellStyle(t.shell_type)
          const group = hostGroupMap.get(t.host_id)
          return (
            <div
              key={t.session_id}
              className={`tab${t.session_id === activeId ? ' active' : ''}`}
              onClick={() => onSwitch(t.session_id)}
              title={`${t.host_name} · ${t.terminal_name}${group ? ` · ${group}` : ''}`}
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
      {showFilter && (
        <div className="tab-filter-bar">
          <select
            className="tab-filter-select"
            value={groupFilter}
            onChange={(e) => setGroupFilter(e.target.value)}
            title="按分组筛选终端"
          >
            <option value="">全部分组</option>
            {groups.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
          <input
            type="text"
            className="tab-filter-input"
            placeholder="筛选主机/终端..."
            value={hostFilter}
            onChange={(e) => setHostFilter(e.target.value)}
            title="按主机名或终端名筛选"
          />
          {(groupFilter || hostFilter) && (
            <button className="tab-filter-clear" onClick={() => { setGroupFilter(''); setHostFilter('') }}>✕</button>
          )}
        </div>
      )}
    </div>
  )
}
