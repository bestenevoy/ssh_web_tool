import { useRef } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'

interface Props {
  terminals: Map<string, TerminalInstance>
  activeId: string | null
  registerContainer: (session_id: string, el: HTMLDivElement | null) => void
  /** 终端区右键：App 层弹终端专属菜单（块操作由 blockBar.hitTest 判定），与主机列表菜单区分 */
  onTerminalContextMenu?: (session_id: string, e: React.MouseEvent) => void
}

export function TerminalView({ terminals, activeId, registerContainer, onTerminalContextMenu }: Props) {
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
        // 断开后仍挂载 xterm 容器：内容保留可见（用户要求不清屏、不清内容），
        // 断开状态由 header「🔗 重连」按钮 + Tab 划线样式提示，不再使用覆盖层
        <div
          key={t.session_id}
          ref={setRef(t.session_id)}
          className={`terminal-instance${t.session_id === activeId ? ' active' : ''}`}
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
      ))}
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
