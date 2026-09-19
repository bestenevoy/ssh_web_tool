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
  // 正在查看特性的会话浮层（fixed 定位，避免被 tab 栏 overflow 裁剪）
  const [infoPop, setInfoPop] = useState<{ sid: string; x: number; y: number } | null>(null)

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
        <span
          className="tab-info"
          title={`查看「${profile.label}」特性与注意事项`}
          onClick={(e) => {
            e.stopPropagation()
            const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
            setInfoPop(infoPop?.sid === t.session_id ? null : { sid: t.session_id, x: r.left, y: r.bottom + 4 })
          }}
        >ⓘ</span>
        <span className="tab-close" onClick={(e) => { e.stopPropagation(); onClose(t.session_id) }}>✕</span>
      </div>
    )
  }

  // 特性浮层（fixed 定位，独立于 tab 渲染，避免被 tab 栏 overflow 裁剪）
  const renderInfoPop = () => {
    if (!infoPop) return null
    const t = terminals.get(infoPop.sid)
    if (!t) return null
    const p = profileOf(t)
    const nearRight = infoPop.x > window.innerWidth - 340
    return (
      <div
        className="shell-info-pop"
        style={{ position: 'fixed', left: nearRight ? undefined : infoPop.x, right: nearRight ? window.innerWidth - infoPop.x : undefined, top: infoPop.y }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sip-head" style={{ color: p.badgeColor }}>
          <span className="sip-badge" style={{ background: p.badgeBg, color: p.badgeColor }}>{p.badge}</span>
          {p.label}
        </div>
        <div className="sip-summary">{p.summary}</div>
        <div className="sip-row"><b>清屏</b>{p.clearCmd}</div>
        <div className="sip-row"><b>退出</b>{p.exitCmd}</div>
        {p.features.length > 0 && (
          <div className="sip-block">
            <div className="sip-block-title">特性</div>
            {p.features.map((f) => (
              <div className="sip-item" key={f.label}><b>{f.label}</b>{f.desc}</div>
            ))}
          </div>
        )}
        {p.cautions.length > 0 && (
          <div className="sip-block sip-caution">
            <div className="sip-block-title">注意事项（防误操作）</div>
            {p.cautions.map((c) => (
              <div className="sip-item" key={c.label}><b>{c.label}</b>{c.desc}</div>
            ))}
          </div>
        )}
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
    <div className={`terminal-window${focused ? ' focused' : ''}`} onMouseDown={() => { onFocusWindow(windowIndex); setInfoPop(null) }}>
      {/* 窗口 tab 栏：本窗口的会话标签 + 新建按钮；同时是跨窗口拖拽的接收区 */}
      <div className="terminal-window-tabs" onDragOver={onDragOver} onDrop={onDrop}>
        {sessions.map((sid, i) => {
          const t = terminals.get(sid)
          return t ? renderTab(t, i) : null
        })}
        <div className="tab-new" onClick={onNew} title="在当前窗口新建终端">+</div>
      </div>
      {/* 特性浮层（fixed，随窗口渲染） */}
      {renderInfoPop()}
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
