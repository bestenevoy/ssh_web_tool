import { useState } from 'react'
import type { EditorSessionInfo } from '../lib/api'

interface SessionSelectModalProps {
  sessions: EditorSessionInfo[]
  /** 初始勾选的会话 id 集合 */
  initialSelected: string[]
  onConfirm: (selected: string[]) => void
  onClose: () => void
}

/** 广播目标会话多选弹窗：列出全部活跃终端，勾选后作为 .zs 播放的广播目标 */
export function SessionSelectModal({ sessions, initialSelected, onConfirm, onClose }: SessionSelectModalProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set(initialSelected))

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected((prev) => (prev.size === sessions.length ? new Set() : new Set(sessions.map((s) => s.session_id))))
  }

  return (
    <div className="modal-overlay show">
      <div className="modal session-select-modal" onKeyDown={(e) => { if (e.key === 'Escape') e.stopPropagation() }}>
        <div className="modal-header">
          <span>选择广播目标会话</span>
          <button className="btn btn-secondary btn-sm" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="modal-body">
          {sessions.length === 0 ? (
            <div className="file-open-empty">当前没有活跃终端会话</div>
          ) : (
            <div className="session-select-list">
              {sessions.map((s) => (
                <label key={s.session_id} className="session-select-item" title={s.session_id}>
                  <input type="checkbox" checked={selected.has(s.session_id)} onChange={() => toggle(s.session_id)} />
                  <span className="session-select-label">{s.label}</span>
                  <span className="session-select-host">{s.host}</span>
                  {s.disconnected && <span className="session-select-badge">已断开</span>}
                </label>
              ))}
            </div>
          )}
        </div>
        <div className="modal-actions">
          <button className="btn btn-secondary btn-sm" onClick={toggleAll} disabled={sessions.length === 0}>
            {selected.size === sessions.length ? '全不选' : '全选'}
          </button>
          <div style={{ flex: 1 }} />
          <button className="btn btn-secondary btn-sm" onClick={onClose}>取消</button>
          <button className="btn btn-primary btn-sm" onClick={() => onConfirm([...selected])}>
            确定（{selected.size}）
          </button>
        </div>
      </div>
    </div>
  )
}
