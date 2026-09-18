import { useState, memo } from 'react'
import type { Host, HostType } from '../types'
import { writeClipboardText } from '../lib/terminalCopy'
import { ContextMenu } from './ContextMenu'
import type { CtxMenuItem } from './ContextMenu'

interface Props {
  hosts: Host[]
  hostTypes: HostType[]
  groups: string[]
  activeHostId: string | null
  defaultShell: string
  onOpenDefaultTerminal: () => void
  onHostClick: (host: Host) => void
  onHostDoubleClick: (host: Host) => void
  onEdit: (host: Host) => void
  onDelete: (host: Host) => void
  onCopy: (host: Host) => void
  onDuplicate: (host: Host) => void
  onManageGroups: () => void
  onReorderHosts: (ids: string[]) => void
}

// 默认终端条目右侧的 shell 显示名
const SHELL_LABELS: Record<string, string> = { cmd: 'cmd', powershell: 'PowerShell', pwsh: 'pwsh' }

export const HostList = memo(function HostList({ hosts, hostTypes, groups, activeHostId, defaultShell, onOpenDefaultTerminal, onHostClick, onHostDoubleClick, onEdit, onDelete, onCopy, onDuplicate, onManageGroups, onReorderHosts }: Props) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [autoLoggingHosts, setAutoLoggingHosts] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  // 主机拖拽排序
  const [dragHostId, setDragHostId] = useState<string | null>(null)
  const [overHostId, setOverHostId] = useState<string | null>(null)
  // 主机右键菜单（原 hover 按钮的操作全部收编进来，条目上不再常驻按钮）
  const [menu, setMenu] = useState<{ x: number; y: number; host: Host } | null>(null)

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

    // 手动打开页面并复制管理密码（本地工具：密码明文下发；未配置管理密码时回退 SSH 密码）
    const openManually = (reason: string) => {
      window.open(url, '_blank')
      const pwd = h.mgmt_password || h.password || ''
      if (pwd) {
        writeClipboardText(pwd).then((ok) => {
          alert(
            ok
              ? `${reason}\n\n管理密码已复制到剪贴板，直接粘贴到登录框即可。\n如需自动登录，请编辑主机填写「Playwright 自动登录选择器配置」。`
              : `${reason}\n\n（自动复制密码失败：剪贴板不可用，请手动查看本地 data.json）`
          )
        })
      } else {
        alert(`${reason}\n\n（该主机未设置管理密码；如需自动登录，请编辑主机填写「Playwright 自动登录选择器配置」）`)
      }
    }

    // 如果配置了自动登录选择器，尝试自动登录
    if (hasAutoLoginConfig(h)) {
      setAutoLoggingHosts((prev) => new Set(prev).add(h.id))
      try {
        const resp = await fetch(`/api/hosts/${h.id}/auto-login`, { method: 'POST' })
        const result = await resp.json()
        if (result.status === 'success' || result.status === 'warning') {
          alert(`自动登录：${result.message}`)
        } else {
          // 自动登录失败，降级为手动打开并复制管理密码
          openManually(`自动登录失败：${result.message}`)
        }
      } catch (e) {
        openManually(`自动登录请求失败：${e}`)
      } finally {
        setAutoLoggingHosts((prev) => {
          const next = new Set(prev)
          next.delete(h.id)
          return next
        })
      }
    } else {
      // 没有配置自动登录：手动打开并复制管理密码
      openManually('管理页面已打开')
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

  /** 主机右键菜单项（原 hover 按钮动作收编：连接 / 管理页面 / 复制 / 编辑 / 删除） */
  const buildHostMenuSections = (h: Host): CtxMenuItem[][] => {
    const sections: CtxMenuItem[][] = [
      [
        { label: '连接', onClick: () => onHostClick(h) },
      ],
    ]
    if (h.device_type === 'storage') {
      sections[0].push({
        label: '打开管理页面',
        disabled: autoLoggingHosts.has(h.id),
        onClick: () => openMgmtPage(h),
      })
    }
    sections.push([
      { label: '复制连接信息', onClick: () => onCopy(h) },
      { label: '复制主机', onClick: () => onDuplicate(h) },
    ])
    sections.push([
      { label: '编辑', onClick: () => onEdit(h) },
      { label: '删除', danger: true, onClick: () => onDelete(h) },
    ])
    return sections
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
      {/* 默认终端：单行条目，打开配置的默认本机 shell（不经过 SSH） */}
      <div
        className="host-item local-default-terminal"
        onClick={onOpenDefaultTerminal}
        title={`打开本机 ${SHELL_LABELS[defaultShell] || defaultShell} 终端（不经过 SSH）`}
      >
        <span className="type-dot" style={{ background: '#10b981' }} />
        <div className="host-info">
          <div className="host-name">默认终端</div>
        </div>
        <span className="default-shell-label">{SHELL_LABELS[defaultShell] || defaultShell}</span>
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
                    onDoubleClick={() => onHostDoubleClick(h)}
                    onContextMenu={(e) => {
                      e.preventDefault()
                      setMenu({ x: e.clientX, y: e.clientY, host: h })
                    }}
                    draggable
                    onDragStart={() => handleHostDragStart(h)}
                    onDragOver={(e) => handleHostDragOver(e, h)}
                    onDrop={() => handleHostDrop(h)}
                    onDragEnd={handleHostDragEnd}
                    title="单击连接/切换 · 双击另开新连接 · 右键操作菜单 · 拖动调整顺序"
                  >
                    <span className="type-dot" style={{ background: getTypeColor(h.type) }} />
                    <span className={`conn-dot${h.is_connected ? ' online' : ''}`} title={h.is_connected ? '已连接' : '未连接'} />
                    <div className="host-info">
                      {h.name ? (
                        <>
                          <div className="host-name">{h.name}</div>
                          <div className="host-ip">
                            {h.host}
                            {h.device_type === 'storage' ? ' · 存储阵列' : ' · 主机'}
                          </div>
                        </>
                      ) : (
                        // 无名称：单行 ip · 类型（ip 不重复显示）
                        <div className="host-name">
                          {h.host}
                          {h.device_type === 'storage' ? ' · 存储阵列' : ' · 主机'}
                        </div>
                      )}
                    </div>
                    {h.terminal_count && h.terminal_count > 0 && (
                      <span className="term-count">{h.terminal_count}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
      {hosts.length === 0 && (
        <div style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: '30px 10px', fontSize: 'calc(11px * var(--ui-fs-scale))' }}>
          暂无主机<br />点击左侧「＋」新建主机
        </div>
      )}
      {/* 主机右键菜单：与终端窗口右键菜单（App 层）各自独立 */}
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          sections={buildHostMenuSections(menu.host)}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
})
