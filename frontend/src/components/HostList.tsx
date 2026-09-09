import { useState, memo } from 'react'
import type { Host, HostType } from '../types'

interface Props {
  hosts: Host[]
  hostTypes: HostType[]
  groups: string[]
  activeHostId: string | null
  fallbackShell: string
  onSetFallbackShell: (shell: string) => void
  onHostClick: (host: Host) => void
  onOpenLocalTerminal: (shell: 'cmd' | 'powershell') => void
  onEdit: (host: Host) => void
  onDelete: (host: Host) => void
  onCopy: (host: Host) => void
  onDuplicate: (host: Host) => void
  onManageGroups: () => void
  onReorderHosts: (ids: string[]) => void
}

// 格式化连接时长：xx秒 / xx分钟 / xx小时
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, seconds)}秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`
  return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`
}

export const HostList = memo(function HostList({ hosts, hostTypes, groups, activeHostId, fallbackShell, onSetFallbackShell, onHostClick, onOpenLocalTerminal, onEdit, onDelete, onCopy, onDuplicate, onManageGroups, onReorderHosts }: Props) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [autoLoggingHosts, setAutoLoggingHosts] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  // 主机拖拽排序
  const [dragHostId, setDragHostId] = useState<string | null>(null)
  const [overHostId, setOverHostId] = useState<string | null>(null)

  const getTypeColor = (key: string) => hostTypes.find((t) => t.key === key)?.color || '#999'

  // 按名称或 IP 过滤主机
  const filteredHosts = searchQuery.trim()
    ? hosts.filter((h) => {
        const q = searchQuery.toLowerCase()
        return (
          h.name?.toLowerCase().includes(q) ||
          h.host.toLowerCase().includes(q) ||
          h.group?.toLowerCase().includes(q)
        )
      })
    : hosts

  // 检查是否配置了自动登录选择器
  const hasAutoLoginConfig = (h: Host) => {
    return !!(h.pw_username_selector && h.pw_password_selector && h.pw_login_btn_selector)
  }

  // 打开存储阵列管理页面（自动登录或手动打开）
  const openMgmtPage = async (h: Host) => {
    const port = h.mgmt_port || 8088
    const url = `https://${h.host}:${port}`

    // 如果配置了自动登录选择器，尝试自动登录
    if (hasAutoLoginConfig(h)) {
      setAutoLoggingHosts((prev) => new Set(prev).add(h.id))
      try {
        const resp = await fetch(`/api/hosts/${h.id}/auto-login`, { method: 'POST' })
        const result = await resp.json()
        if (result.status === 'success' || result.status === 'warning') {
          alert(`自动登录：${result.message}`)
        } else {
          // 自动登录失败，降级为手动打开（密码已脱敏，不会下发到前端）
          alert(`自动登录失败：${result.message}\n\n将手动打开管理页面（密码已脱敏，请在本地 data.json 中查看）`)
          window.open(url, '_blank')
        }
      } catch (e) {
        alert(`自动登录请求失败：${e}\n\n将手动打开管理页面（密码已脱敏，请在本地 data.json 中查看）`)
        window.open(url, '_blank')
      } finally {
        setAutoLoggingHosts((prev) => {
          const next = new Set(prev)
          next.delete(h.id)
          return next
        })
      }
    } else {
      // 没有配置自动登录，手动打开（密码已脱敏，不复制到剪贴板）
      window.open(url, '_blank')
      alert('管理页面已打开（密码已脱敏，不会下发到前端，请在本地 data.json 中查看）。\n\n如需自动登录，请编辑主机，在「Playwright 自动登录选择器配置」中填写选择器。')
    }
  }

  const toggleGroup = (g: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(g)) next.delete(g)
      else next.add(g)
      return next
    })
  }

  // 按存储的分组顺序渲染（未分组固定放最后）
  const groupNames = [...groups]
  const withUngrouped = filteredHosts.some((h) => !h.group)
  const orderedGroups = [...groupNames]
  if (withUngrouped) orderedGroups.push('未分组')

  const grouped: Record<string, Host[]> = {}
  filteredHosts.forEach((h) => {
    const g = h.group || '未分组'
    if (!grouped[g]) grouped[g] = []
    grouped[g].push(h)
  })

  // ---- 主机拖拽排序 ----
  const moveHost = (fromId: string, toId: string) => {
    if (fromId === toId) return
    const list = [...hosts]
    const fromIdx = list.findIndex((h) => h.id === fromId)
    const toIdx = list.findIndex((h) => h.id === toId)
    if (fromIdx < 0 || toIdx < 0) return
    const [item] = list.splice(fromIdx, 1)
    list.splice(toIdx, 0, item)
    onReorderHosts(list.map((h) => h.id))
  }

  const handleHostDragStart = (h: Host) => {
    setDragHostId(h.id)
  }

  const handleHostDragOver = (e: React.DragEvent, h: Host) => {
    e.preventDefault()
    if (dragHostId && dragHostId !== h.id) setOverHostId(h.id)
  }

  const handleHostDrop = (h: Host) => {
    if (dragHostId) moveHost(dragHostId, h.id)
    setDragHostId(null)
    setOverHostId(null)
  }

  const handleHostDragEnd = () => {
    setDragHostId(null)
    setOverHostId(null)
  }

  return (
    <div className="host-list">
      {/* 搜索框 + 分组管理 */}
      <div className="host-search">
        <input
          type="text"
          placeholder="搜索名称 / IP / 分组..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="host-search-input"
        />
        {searchQuery && (
          <button className="host-search-clear" onClick={() => setSearchQuery('')}>✕</button>
        )}
        <button className="host-search-group-btn" onClick={onManageGroups} title="管理分组">📁</button>
      </div>
      <div className="local-terminal-bar" title="打开本机终端（不经过 SSH）">
        <span className="local-terminal-label">本机</span>
        <button className="local-terminal-btn" onClick={() => onOpenLocalTerminal('cmd')}>cmd</button>
        <button className="local-terminal-btn" onClick={() => onOpenLocalTerminal('powershell')}>PowerShell</button>
        <span className="local-terminal-label" title="SSH 断开后自动进入的本机终端">断开后</span>
        <select
          className="fallback-shell-select"
          value={fallbackShell}
          onChange={(e) => onSetFallbackShell(e.target.value)}
          title="SSH 连接断开后自动切换到哪种本机终端"
        >
          <option value="cmd">cmd</option>
          <option value="powershell">PowerShell</option>
        </select>
      </div>
      {orderedGroups.map((group) => {
        const groupHosts = grouped[group] || []
        if (groupHosts.length === 0) return null
        return (
          <div key={group}>
            <div
              className="group-header"
              onClick={() => toggleGroup(group)}
              title="点击折叠/展开"
            >
              <span className="arrow" style={{ display: 'inline-block', transform: collapsedGroups.has(group) ? 'rotate(-90deg)' : 'none', transition: 'transform .15s' }}>▼</span>
              {group} ({groupHosts.length})
            </div>
            {!collapsedGroups.has(group) && (
              <div>
                {groupHosts.map((h) => (
                  <div
                    key={h.id}
                    className={`host-item${activeHostId === h.id ? ' active' : ''}${dragHostId === h.id ? ' dragging' : ''}${overHostId === h.id && dragHostId && dragHostId !== h.id ? ' drag-over' : ''}`}
                    onClick={() => onHostClick(h)}
                    draggable
                    onDragStart={() => handleHostDragStart(h)}
                    onDragOver={(e) => handleHostDragOver(e, h)}
                    onDrop={() => handleHostDrop(h)}
                    onDragEnd={handleHostDragEnd}
                    title="拖动可调整顺序"
                  >
                    <span className="type-dot" style={{ background: getTypeColor(h.type) }} />
                    <span className={`conn-dot${h.is_connected ? ' online' : ''}`} title={h.is_connected ? '已连接' : '未连接'} />
                    <div className="host-info">
                      <div className="host-name">
                        {h.name || h.host}
                      </div>
                      <div className="host-ip">
                        {h.host}
                        {h.device_type === 'storage' ? ' · 存储阵列' : ' · 主机'}
                        {h.is_connected && h.connected_duration != null && (
                          <span className="host-conn-info" title={`已连接 ${formatDuration(h.connected_duration)}`}>
                            {' '}· <span className="conn-dot-mini online" /> 已连 {formatDuration(h.connected_duration)}
                          </span>
                        )}
                      </div>
                    </div>
                    {h.terminal_count && h.terminal_count > 0 && (
                      <span className="term-count">{h.terminal_count}</span>
                    )}
                    <div className="actions">
                      {h.device_type === 'storage' && (
                        <button
                          className={`action-btn mgmt-btn${autoLoggingHosts.has(h.id) ? ' loading' : ''}`}
                          onClick={(e) => { e.stopPropagation(); openMgmtPage(h) }}
                          title={hasAutoLoginConfig(h) ? '自动登录管理页面' : '打开管理页面'}
                          disabled={autoLoggingHosts.has(h.id)}
                        >
                          {autoLoggingHosts.has(h.id) ? '⏳' : '🌐'}
                        </button>
                      )}
                      <button className="action-btn" onClick={(e) => { e.stopPropagation(); onDuplicate(h) }} title="复制主机">📄</button>
                      <button className="action-btn" onClick={(e) => { e.stopPropagation(); onCopy(h) }} title="复制连接信息">📋</button>
                      <button className="action-btn" onClick={(e) => { e.stopPropagation(); onEdit(h) }} title="编辑">✎</button>
                      <button className="action-btn" onClick={(e) => { e.stopPropagation(); onDelete(h) }} title="删除">🗑</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {hosts.length === 0 && (
        <div style={{ textAlign: 'center', color: '#3a4a6a', padding: '30px 10px', fontSize: 11 }}>
          暂无主机<br />点击右上角「+ 新建主机」添加
        </div>
      )}
    </div>
  )
})
