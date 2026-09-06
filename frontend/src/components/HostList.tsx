import { useState } from 'react'
import type { Host, HostType } from '../types'

interface Props {
  hosts: Host[]
  hostTypes: HostType[]
  activeHostId: string | null
  onHostClick: (host: Host) => void
  onEdit: (host: Host) => void
  onDelete: (host: Host) => void
  onCopy: (host: Host) => void
  onDuplicate: (host: Host) => void
  onManageGroups: () => void
}

export function HostList({ hosts, hostTypes, activeHostId, onHostClick, onEdit, onDelete, onCopy, onDuplicate, onManageGroups }: Props) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [autoLoggingHosts, setAutoLoggingHosts] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')

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
          // 自动登录失败，降级为手动打开
          alert(`自动登录失败：${result.message}\n\n将手动打开管理页面，密码已复制到剪贴板`)
          if (h.mgmt_password) {
            navigator.clipboard.writeText(h.mgmt_password).catch(() => {})
          }
          window.open(url, '_blank')
        }
      } catch (e) {
        alert(`自动登录请求失败：${e}\n\n将手动打开管理页面，密码已复制到剪贴板`)
        if (h.mgmt_password) {
          navigator.clipboard.writeText(h.mgmt_password).catch(() => {})
        }
        window.open(url, '_blank')
      } finally {
        setAutoLoggingHosts((prev) => {
          const next = new Set(prev)
          next.delete(h.id)
          return next
        })
      }
    } else {
      // 没有配置自动登录，手动打开
      if (h.mgmt_password) {
        navigator.clipboard.writeText(h.mgmt_password).catch(() => {})
      }
      window.open(url, '_blank')
      alert('管理页面已打开，密码已复制到剪贴板。\n\n如需自动登录，请编辑主机，在「Playwright 自动登录选择器配置」中填写选择器。')
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

  const grouped: Record<string, Host[]> = {}
  filteredHosts.forEach((h) => {
    const g = h.group || '未分组'
    if (!grouped[g]) grouped[g] = []
    grouped[g].push(h)
  })

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
      {Object.entries(grouped).map(([group, groupHosts]) => (
        <div key={group}>
          <div
            className="group-header"
            onClick={() => toggleGroup(group)}
          >
            <span className="arrow" style={{ display: 'inline-block', transform: collapsedGroups.has(group) ? 'rotate(-90deg)' : 'none', transition: 'transform .15s' }}>▼</span>
            {group} ({groupHosts.length})
          </div>
          {!collapsedGroups.has(group) && (
            <div>
              {groupHosts.map((h) => (
                <div
                  key={h.id}
                  className={`host-item${activeHostId === h.id ? ' active' : ''}`}
                  onClick={() => onHostClick(h)}
                >
                  <span className="type-dot" style={{ background: getTypeColor(h.type) }} />
                  <span className={`conn-dot${h.is_connected ? ' online' : ''}`} title={h.is_connected ? '已连接' : '未连接'} />
                  <div className="host-info">
                    <div className="host-name">
                      {h.name || h.host}
                    </div>
                    <div className="host-ip">
                      {h.name ? h.host : h.host}
                      {h.device_type === 'storage' ? ' · 存储阵列' : ' · 主机'}
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
      ))}
      {hosts.length === 0 && (
        <div style={{ textAlign: 'center', color: '#3a4a6a', padding: '30px 10px', fontSize: 11 }}>
          暂无主机<br />点击右上角「+ 新建主机」添加
        </div>
      )}
    </div>
  )
}
