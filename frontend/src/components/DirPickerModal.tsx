import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../lib/api'
import type { FileListEntry } from '../lib/api'

interface Props {
  title: string // 弹窗标题（如「选择日志保存目录」）
  initialDir: string // 初始目录（空 = 用户主目录 ~）
  onConfirm: (dir: string) => void
  onCancel: () => void
}

/**
 * 本地目录选择弹窗：路径输入框（可直接粘贴）+ 子目录列表点击进入 + 上一级。
 * 复用编辑器的 /api/files/list 目录浏览接口（支持 ~ 展开）。
 * 仅取消/确认/Esc 可关闭（遵循 AGENTS.md UI 规范：不点遮罩关闭）；
 * 打开时聚焦路径输入框（焦点从 xterm textarea 移出，保证 Esc 冒泡生效）。
 */
export function DirPickerModal({ title, initialDir, onConfirm, onCancel }: Props) {
  // 路径草稿：可直接编辑/粘贴，「转到」与「使用此目录」都以它为准
  const [dirDraft, setDirDraft] = useState(initialDir.trim() || '~')
  const [entries, setEntries] = useState<FileListEntry[]>([])
  const [parent, setParent] = useState<string | null>(null)
  const [currentDir, setCurrentDir] = useState('')
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const browse = useCallback((dir: string) => {
    setError('')
    api
      .listDir(dir)
      .then((r) => {
        setEntries(r.entries.filter((e) => e.is_dir))
        setParent(r.parent)
        setCurrentDir(r.dir)
        setDirDraft(r.dir)
      })
      .catch((e) => setError((e as Error).message))
  }, [])

  // 打开时聚焦输入框 + 加载初始目录
  useEffect(() => {
    inputRef.current?.focus()
    browse(dirDraft)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  const confirm = () => {
    const dir = dirDraft.trim()
    if (!dir) return setError('请输入或选择目录')
    onConfirm(dir)
  }

  const dirs = entries

  return (
    <div className="modal-overlay show">
      <div className="modal" style={{ width: 460 }}>
        <h3>📁 {title}</h3>
        <div className="form-group">
          <label>目录路径（支持 ~ 表示主目录，可直接粘贴）</label>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              ref={inputRef}
              type="text"
              value={dirDraft}
              placeholder="例如 D:\logs 或 ~"
              onChange={(e) => setDirDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  browse(dirDraft)
                }
              }}
              style={{ flex: 1 }}
            />
            <button className="btn btn-secondary" onClick={() => browse(dirDraft)}>
              转到
            </button>
          </div>
        </div>
        {/* 子目录列表：点击进入；上级按钮在根目录（parent=null）时禁用 */}
        <div
          style={{
            border: '1px solid var(--border-color)',
            borderRadius: 6,
            maxHeight: 220,
            overflowY: 'auto',
            margin: '4px 0 8px',
          }}
        >
          {dirs.length === 0 ? (
            <div style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: 'calc(12px * var(--ui-fs-scale))' }}>（无子目录）</div>
          ) : (
            dirs.map((d) => (
              <button
                key={d.name}
                className="dir-picker-entry"
                onClick={() => browse(currentDir ? `${currentDir.replace(/[\\/]+$/, '')}/${d.name}` : d.name)}
              >
                📂 {d.name}
              </button>
            ))
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <button className="btn btn-secondary btn-sm" disabled={!parent} onClick={() => parent && browse(parent)}>
            ⬆️ 上一级
          </button>
          <span style={{ fontSize: 'calc(12px * var(--ui-fs-scale))', color: 'var(--text-secondary)', alignSelf: 'center', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {currentDir || '—'}
          </span>
        </div>
        {error && <div style={{ color: 'var(--danger)', fontSize: 'calc(12px * var(--ui-fs-scale))', marginBottom: 8 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onCancel}>
            取消
          </button>
          <button className="btn btn-primary" onClick={confirm}>
            使用此目录
          </button>
        </div>
      </div>
    </div>
  )
}
