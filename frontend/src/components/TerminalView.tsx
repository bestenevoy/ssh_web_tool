import { TerminalWindow } from './TerminalWindow'
import type { TerminalInstance } from '../lib/useTerminals'
import type { Host } from '../types'

// 分屏模式：none 单屏 / h 左右均分 / v 上下均分（固定 2 窗口，不做拖拽分割树）
export type SplitMode = 'none' | 'h' | 'v'

export type PaneIndex = 0 | 1

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  splitMode: SplitMode
  /** 两个窗口各自的会话 id 列表（tab 顺序；单屏只用窗口0） */
  windowSessions: [string[], string[]]
  /** 两个窗口各自当前显示的会话 id */
  windowActive: [string | null, string | null]
  /** 焦点窗口索引 */
  focusedPane: PaneIndex
  hosts: Host[]
  /** 会话显示顺序（单屏时窗口0 的 tab 顺序 = 会话列表顺序；缺省 = 创建顺序） */
  order?: string[]
  registerContainer: (session_id: string, el: HTMLDivElement | null) => void
  onSwitch: (id: string) => void
  onClose: (id: string) => void
  onNew: () => void
  /** 跨窗口拖拽：把会话移动到目标窗口 */
  onMoveTabToWindow: (sid: string, toWindow: PaneIndex) => void
  onFocusWindow: (idx: PaneIndex) => void
  /** 终端区右键：App 层弹终端专属菜单（块操作由 blockBar.hitTest 判定），与主机列表菜单区分 */
  onTerminalContextMenu?: (session_id: string, e: React.MouseEvent) => void
  /** 点击窗格（含空窗格占位）→ App 聚焦该窗格 */
  onPaneClick?: (index: PaneIndex, session_id: string | null) => void
}

export function TerminalView({
  terminals, activeId, splitMode, windowSessions, windowActive, focusedPane,
  hosts, order, registerContainer,
  onSwitch, onClose, onNew, onMoveTabToWindow, onFocusWindow,
  onTerminalContextMenu, onPaneClick,
}: Props) {
  const inSplit = splitMode !== 'none'

  // 单屏窗口0 的会话列表：优先共享顺序（tab 顺序 = 会话列表顺序）
  const singleSessions = order && order.length > 0
    ? order.filter((sid) => terminals.has(sid))
    : Array.from(terminals.keys())

  // 窗口列表：单屏 = 1 个窗口（全部会话，激活 = activeId）；分屏 = 2 个窗口
  const windows: { idx: PaneIndex; sessions: string[]; active: string | null }[] = inSplit
    ? [
        { idx: 0, sessions: windowSessions[0], active: windowActive[0] },
        { idx: 1, sessions: windowSessions[1], active: windowActive[1] },
      ]
    : [{ idx: 0, sessions: singleSessions, active: activeId }]

  // 会话所属窗口（分屏）：窗口0 / 窗口1 / -1（不属于任何窗口，不显示）
  const paneOf = (sid: string): PaneIndex | -1 =>
    inSplit ? (windowSessions[0].includes(sid) ? 0 : windowSessions[1].includes(sid) ? 1 : -1) : 0
  // 会话是否可见（所属窗口的当前显示会话）
  const visibleOf = (sid: string): boolean => {
    const idx = paneOf(sid)
    if (inSplit) {
      if (idx < 0) return false
      return windowActive[idx as PaneIndex] === sid
    }
    return sid === activeId
  }

  return (
    <div className={`terminal-container${inSplit ? ` split split-${splitMode}` : ''}`}>
      {/* 窗口层：tab 栏 + 空窗口占位（终端实例不在此层渲染） */}
      {windows.map((w) => (
        <TerminalWindow
          key={w.idx}
          windowIndex={w.idx}
          sessions={w.sessions}
          activeId={w.active}
          focused={!inSplit || w.idx === focusedPane}
          terminals={terminals}
          hosts={hosts}
          onSwitch={onSwitch}
          onClose={onClose}
          onNew={onNew}
          onMoveTabToWindow={onMoveTabToWindow}
          onFocusWindow={onFocusWindow}
          onPaneClick={onPaneClick}
          inSplit={inSplit}
        />
      ))}

      {/* 共享实例层：所有会话实例保持挂载（跨窗口拖拽只改 pane/active 类，
          xterm DOM 不重建），定位到所属窗口终端区 */}
      {Array.from(terminals.values()).map((t) => {
        const idx = paneOf(t.session_id)
        const visible = visibleOf(t.session_id)
        return (
          <div
            key={t.session_id}
            ref={(el) => registerContainer(t.session_id, el)}
            className={`terminal-instance${idx >= 0 ? ` pane-${idx}` : ''}${visible ? ' active' : ''}`}
            onMouseDown={(e) => { if (e.button === 0 && idx >= 0) onPaneClick?.(idx as PaneIndex, t.session_id) }}
            onContextMenu={(e) => onTerminalContextMenu?.(t.session_id, e)}
          >
            {/* 重连不盖遮罩（历史内容需可见、进度提示在终端内），仅初始连接/启动本机终端时遮罩 */}
            {(t.pending || t.local_starting) && (
              <div className="terminal-starting">
                <div className="terminal-starting-spinner" />
                <div className="terminal-starting-text">
                  {t.pending ? '正在连接，请稍候…' : '正在启动本机终端…'}
                </div>
              </div>
            )}
          </div>
        )
      })}

      {!inSplit && terminals.size === 0 && (
        <div className="empty-state">
          <div className="icon">🖥️</div>
          <div className="text">选择左侧主机开始 SSH 连接</div>
        </div>
      )}
    </div>
  )
}
