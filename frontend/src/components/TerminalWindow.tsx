import { useState } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'
import type { Host, HostType } from '../types'
import type { PaneIndex } from './TerminalView'
import { profileOf } from '../lib/shellProfiles'

interface Props {
  /** 窗口索引（分屏时 0/1；单屏恒为 0） */
  windowIndex: PaneIndex
  /** 本窗口的会话 id 列表（tab 顺序） */
  sessions: string[]
  /** 本窗口当前显示的会话 */
  activeId: string | null
  /** 是否焦点窗口（点击/拖拽目标时聚焦） */
  focused: boolean
  terminals: Map<string, TerminalInstance>
  hostTypes: HostType[]
  hosts: Host[]
  onSwitch: (id: string) => void
  onClose: (id: string) => void
  onNew: () => void
  /** 跨窗口拖拽：把 tab 移动到本窗口 */
  onMoveTabToWindow: (sid: string, toWindow: PaneIndex) => void
  onFocusWindow: (idx: PaneIndex) => void
  /** 点击空窗格占位 → 聚焦 */
  onPaneClick?: (index: PaneIndex, session_id: string | null) => void
  /** 是否分屏（单屏空窗口由 empty-state 展示，不渲染占位） */
  inSplit?: boolean
}

// 拖拽时隐藏浏览器默认拖影（幽灵图/半透明副本），只保留光标移动
function hideDefaultDragImage(e: React.DragEvent) {
  try {
    const canvas = document.createElement('canvas')
    canvas.width = 1
    canvas.height = 1
    e.dataTransfer.setDragImage(canvas, 0, 0)
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.dropEffect = 'move'
  } catch {
    /* 忽略 */
  }
}

export function TerminalWindow({
  windowIndex, sessions, activeId, focused,
  terminals, hostTypes, hosts, inSplit,
  onSwitch, onClose, onNew, onMoveTabToWindow, onFocusWindow, onPaneClick,
}: Props) {
  const getTypeColor = (key: string) => hostTypes.find((t) => t.key === key)?.color || '#999'

  const hostById = new Map(hosts.map((h) => [h.id, h]))
  // 会话 ip：ssh 链接会话用原始 host，已保存主机查配置，本地会话显示「本地」
  const hostIpOf = (t: TerminalInstance): string =>
    t.type === 'local' ? '本地' : t.ssh_conn?.host || hostById.get(t.host_id)?.host || t.host_name

  // 拖拽中的 tab（仅用于移除点击等误操作，不做额外视觉变化）
  const [dragSid, setDragSid] = useState<string | null>(null)

  const renderTab = (t: TerminalInstance, i: number) => {
    const profile = profileOf(t)
    const ip = hostIpOf(t)
    const stateHint = t.pending ? '（连接中…）' : t.reconnecting ? '（重连中…）' : t.disconnected ? '（已断开）' : ''
    return (
      <div
        key={t.session_id}
        className={`tab${t.session_id === activeId ? ' active' : ''}${t.disconnected ? ' disconnected' : ''}${t.reconnecting || t.pending ? ' connecting' : ''}${dragSid === t.session_id ? ' dragging' : ''}`}
        onClick={() => { onFocusWindow(windowIndex); onSwitch(t.session_id) }}
        draggable
        onDragStart={(e) => { setDragSid(t.session_id); hideDefaultDragImage(e); e.dataTransfer.setData('text/plain', t.session_id) }}
        onDragEnd={() => setDragSid(null)}
        title={`${i + 1} · ${ip} · ${t.terminal_name}${stateHint}\nShell: ${profile.label}（${profile.summary}）\n清屏: ${profile.clearCmd}\n退出: ${profile.exitCmd}`}
      >
        <span className="type-dot" style={{ width: 6, height: 6, borderRadius: '50%', background: getTypeColor(t.type), display: 'inline-block' }} />
        <span className="tab-title">{t.pending ? `${ip} · 连接中` : `${i + 1} · ${ip}`}</span>
        <span
          className="shell-type-badge"
          style={{
            color: profile.badgeColor,
            background: profile.badgeBg,
            fontSize: 'calc(10px * var(--ui-fs-scale))',
            padding: '1px 5px',
            borderRadius: 3,
            fontWeight: 600,
            marginLeft: 4,
          }}
          title={`当前环境: ${profile.label}`}
        >
          {profile.badge}
        </span>
        <span className="tab-close" onClick={(e) => { e.stopPropagation(); onClose(t.session_id) }}>✕</span>
      </div>
    )
  }

  // 拖放目标：把拖来的 tab 移入本窗口（同窗口拖拽忽略）
  const onDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('text/plain')) { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const sid = e.dataTransfer.getData('text/plain')
    setDragSid(null)
    if (sid) { onFocusWindow(windowIndex); onMoveTabToWindow(sid, windowIndex) }
  }

  return (
    <div className={`terminal-window${focused ? ' focused' : ''}`} onMouseDown={() => onFocusWindow(windowIndex)}>
      {/* 窗口 tab 栏：本窗口的会话标签 + 新建按钮；同时是跨窗口拖拽的接收区 */}
      <div className="terminal-window-tabs" onDragOver={onDragOver} onDrop={onDrop}>
        {sessions.map((sid, i) => {
          const t = terminals.get(sid)
          return t ? renderTab(t, i) : null
        })}
        <div className="tab-new" onClick={onNew} title="在当前窗口新建终端">+</div>
      </div>
      {/* 窗口终端区：本窗口的会话实例由共享实例层渲染（保持挂载不重建）；
          这里只放空窗口占位提示 */}
      <div className="terminal-window-body">
        {inSplit && sessions.length === 0 && (
          <div
            className="pane-placeholder"
            onMouseDown={(e) => { if (e.button === 0) onPaneClick?.(windowIndex, null) }}
          >
            <div className="pane-placeholder-icon">🖥️</div>
          </div>
        )}
      </div>
    </div>
  )
}
