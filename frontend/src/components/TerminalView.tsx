import { useRef } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  registerContainer: (session_id: string, el: HTMLDivElement | null) => void
}

export function TerminalView({ terminals, activeId, registerContainer }: Props) {
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
      {Array.from(terminals.values()).map((t) => (
        <div
          key={t.session_id}
          ref={setRef(t.session_id)}
          className={`terminal-instance${t.session_id === activeId ? ' active' : ''}`}
        />
      ))}
      {terminals.size === 0 && (
        <div className="empty-state">
          <div className="icon">🖥️</div>
          <div className="text">选择左侧主机开始 SSH 连接</div>
          <div className="hint">支持多标签终端 · 后端统一维护连接 · 页面关闭不中断</div>
        </div>
      )}
    </div>
  )
}
