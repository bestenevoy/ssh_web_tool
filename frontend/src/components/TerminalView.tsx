import { useRef } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  registerContainer: (session_id: string, el: HTMLDivElement | null) => void
  onReconnect: (session_id: string) => void
  onCloseTab: (session_id: string) => void
}

export function TerminalView({ terminals, activeId, registerContainer, onReconnect, onCloseTab }: Props) {
  const containerRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  const setRef = (session_id: string) => (el: HTMLDivElement | null) => {
    if (el) {
      containerRefs.current.set(session_id, el)
    } else {
      containerRefs.current.delete(session_id)
    }
    registerContainer(session_id, el)
  }

  return (
    <div className="terminal-container">
      {Array.from(terminals.values()).map((t) =>
        t.disconnected ? (
          // 已断开：显示重连覆盖层（不再挂载 xterm 容器）
          <div
            key={t.session_id}
            className={`terminal-instance disconnected${t.session_id === activeId ? ' active' : ''}`}
          >
            <div className="terminal-disconnected">
              <div className="icon">🔌</div>
              <div className="text">连接已断开</div>
              <div className="hint">{t.host_name} · {t.terminal_name}</div>
              <div className="disconnected-actions">
                <button className="btn btn-primary btn-sm" onClick={() => onReconnect(t.session_id)}>
                  ↻ 重新连接
                </button>
                <button className="btn btn-secondary btn-sm" onClick={() => onCloseTab(t.session_id)}>
                  关闭标签
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div
            key={t.session_id}
            ref={setRef(t.session_id)}
            className={`terminal-instance${t.session_id === activeId ? ' active' : ''}`}
          />
        )
      )}
      {terminals.size === 0 && (
        <div className="empty-state">
          <div className="icon">🖥️</div>
          <div className="text">选择左侧主机开始 SSH 连接</div>
          <div className="hint">支持多标签终端 · 关闭标签同时关闭 SSH 连接 · 整关页面后重开会自动恢复仍连接的会话</div>
        </div>
      )}
    </div>
  )
}
