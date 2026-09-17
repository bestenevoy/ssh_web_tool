import { useRef } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'

// 分屏模式：none 单屏 / h 左右均分 / v 上下均分（固定 2 窗格，不做拖拽分割树）
export type SplitMode = 'none' | 'h' | 'v'

export type PaneIndex = 0 | 1

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  splitMode: SplitMode
  /** 两个窗格各自显示的会话 id（null = 空窗格，显示占位提示） */
  panes: [string | null, string | null]
  /** 焦点窗格索引（activeId 所在窗格） */
  focusedPane: PaneIndex
  registerContainer: (session_id: string, el: HTMLDivElement | null) => void
  /** 终端区右键：App 层弹终端专属菜单（块操作由 blockBar.hitTest 判定），与主机列表菜单区分 */
  onTerminalContextMenu?: (session_id: string, e: React.MouseEvent) => void
  /** 点击窗格（含空窗格占位）→ App 聚焦该窗格 */
  onPaneClick?: (index: PaneIndex, session_id: string | null) => void
}

export function TerminalView({
  terminals, activeId, splitMode, panes, focusedPane, registerContainer, onTerminalContextMenu, onPaneClick,
}: Props) {
  const containerRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  const setRef = (session_id: string) => (el: HTMLDivElement | null) => {
    if (el) {
      containerRefs.current.set(session_id, el)
    } else {
      containerRefs.current.delete(session_id)
    }
    registerContainer(session_id, el)
  }

  const inSplit = splitMode !== 'none'

  // 单个终端实例渲染：单屏模式用 .active 控制显隐；分屏模式用 .pane-N 控制网格定位
  // （未上屏的会话保持挂载但 display:none，保住 xterm 容器不被卸载重建）
  const renderInstance = (t: TerminalInstance, paneIdx: PaneIndex | -1) => {
    const paneCls = paneIdx >= 0 ? ` pane-${paneIdx}` : ''
    const focusedCls = inSplit && paneIdx === focusedPane ? ' focused' : ''
    return (
      <div
        key={t.session_id}
        ref={setRef(t.session_id)}
        className={`terminal-instance${!inSplit && t.session_id === activeId ? ' active' : ''}${paneCls}${focusedCls}`}
        onMouseDown={inSplit && paneIdx >= 0 ? () => onPaneClick?.(paneIdx as PaneIndex, t.session_id) : undefined}
        onContextMenu={(e) => onTerminalContextMenu?.(t.session_id, e)}
      >
        {(t.reconnecting || t.local_starting) && (
          <div className="terminal-starting">
            <div className="terminal-starting-spinner" />
            <div className="terminal-starting-text">
              {t.reconnecting ? '正在重新连接，请稍候…' : '正在启动本机终端…'}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={`terminal-container${inSplit ? ` split split-${splitMode}` : ''}`}>
      {Array.from(terminals.values()).map((t) => (
        renderInstance(t, inSplit ? (panes.indexOf(t.session_id) as PaneIndex | -1) : -1)
      ))}
      {/* 空窗格 / 会话已被关闭的窗格：显示占位（也复用 pane-N 做网格定位） */}
      {inSplit && panes.map((sid, i) => {
        if (sid && terminals.has(sid)) return null
        return (
          <div
            key={`pane-empty-${i}`}
            className={`pane-placeholder pane-${i}${i === focusedPane ? ' focused' : ''}`}
            onMouseDown={() => onPaneClick?.(i as PaneIndex, null)}
          >
            <div className="text">空白窗格</div>
            <div className="hint">点击顶部标签在此窗格打开会话</div>
          </div>
        )
      })}
      {terminals.size === 0 && !inSplit && (
        <div className="empty-state">
          <div className="icon">🖥️</div>
          <div className="text">选择左侧主机开始 SSH 连接</div>
          <div className="hint">支持多标签终端 · 关闭标签同时关闭 SSH 连接 · 整关页面后重开会自动恢复仍连接的会话</div>
        </div>
      )}
    </div>
  )
}
