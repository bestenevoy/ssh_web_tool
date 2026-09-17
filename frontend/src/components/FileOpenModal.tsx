import { useEffect, useState } from 'react'
import { api, type FileListEntry, type FileListResult } from '../lib/api'

interface FileOpenModalProps {
  /** open：选择文件打开；save：选择保存路径（允许输入新路径） */
  mode: 'open' | 'save'
  /** save 模式下的初始路径（当前文件路径或默认目录 + 文件名） */
  initialPath?: string
  /** 最近打开的路径（点击快速打开；open 模式显示删除入口） */
  recentPaths: string[]
  /** 后端默认目录（scripts/logs 快捷入口） */
  defaults: { scripts_dir: string; logs_dir: string } | null
  onPick: (path: string) => void
  /** 删除一条最近路径记录（路径失效场景） */
  onRemoveRecent: (path: string) => void
  onClose: () => void
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 文件打开/保存弹窗：路径输入 + 最近路径 + 目录浏览（scripts/logs 快捷入口） */
export function FileOpenModal({ mode, initialPath = '', recentPaths, defaults, onPick, onRemoveRecent, onClose }: FileOpenModalProps) {
  const [pathInput, setPathInput] = useState(initialPath)
  const [browseDir, setBrowseDir] = useState<string | null>(null)
  const [listing, setListing] = useState<FileListResult | null>(null)
  const [error, setError] = useState('')

  // 初始浏览目录：defaults 里的 scripts 目录（打开 .zs 的主场景）
  useEffect(() => {
    if (defaults && !browseDir) setBrowseDir(mode === 'save' ? defaults.scripts_dir : defaults.scripts_dir)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaults])

  // browseDir 变化时加载目录列表
  useEffect(() => {
    if (!browseDir) return
    let cancelled = false
    api.listDir(browseDir)
      .then((r) => { if (!cancelled) { setListing(r); setError('') } })
      .catch((e) => { if (!cancelled) { setListing(null); setError('打开目录失败: ' + (e as Error).message) } })
    return () => { cancelled = true }
  }, [browseDir])

  const pick = () => {
    const p = pathInput.trim()
    if (!p) { setError('请输入文件路径'); return }
    onPick(p)
  }

  const onEntryClick = (entry: FileListEntry) => {
    const base = listing?.dir ?? ''
    const sep = base.includes('\\') || /^[A-Za-z]:/.test(base) ? '\\' : '/'
    const full = base.replace(/[\\/]+$/, '') + sep + entry.name
    if (entry.is_dir) {
      setBrowseDir(full)
      setPathInput(full + sep)
    } else {
      setPathInput(full)
    }
  }

  const tryOpenRecent = (p: string) => {
    // 打开前探测文件是否存在：失效路径提示并询问是否删除记录
    api.readFile(p, 0, 1)
      .then(() => onPick(p))
      .catch((e) => {
        const msg = (e as Error).message || ''
        if (msg.includes('不存在')) {
          if (window.confirm(`文件不存在（可能已被移动或删除）：\n${p}\n\n是否删除该路径记录？`)) {
            onRemoveRecent(p)
          }
        } else {
          setError('打开失败: ' + msg)
        }
      })
  }

  return (
    <div className="modal-overlay show">
      <div className="modal file-open-modal" onKeyDown={(e) => { if (e.key === 'Escape') e.stopPropagation() }}>
        <div className="modal-header">
          <span>{mode === 'open' ? '打开文件' : '保存文件'}</span>
          <button className="btn btn-secondary btn-sm" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="modal-body">
          <div className="file-open-path-row">
            <input
              autoFocus
              className="file-open-path-input"
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') pick() }}
              placeholder={mode === 'open' ? '输入或粘贴文件路径…' : '输入保存路径…'}
              spellCheck={false}
            />
            <button className="btn btn-primary btn-sm" onClick={pick}>
              {mode === 'open' ? '打开' : '保存'}
            </button>
          </div>
          {error && <div className="file-open-error">{error}</div>}

          {recentPaths.length > 0 && (
            <div className="file-open-section">
              <div className="file-open-section-title">最近打开</div>
              <div className="file-open-recent-list">
                {recentPaths.map((p) => (
                  <div key={p} className="file-open-recent-item" onClick={() => tryOpenRecent(p)} title={p}>
                    <span className="file-open-recent-path">{p}</span>
                    <button
                      className="file-open-recent-remove"
                      title="删除该记录"
                      onClick={(e) => { e.stopPropagation(); onRemoveRecent(p) }}
                    >✕</button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="file-open-section">
            <div className="file-open-section-title">
              目录浏览
              {defaults && (
                <span className="file-open-quick">
                  <button className="btn btn-secondary btn-sm" onClick={() => setBrowseDir(defaults.scripts_dir)}>scripts/</button>
                  <button className="btn btn-secondary btn-sm" onClick={() => setBrowseDir(defaults.logs_dir)}>logs/</button>
                </span>
              )}
            </div>
            <div className="file-open-crumbs">
              {listing && (
                <>
                  {listing.parent !== null && (
                    <button className="btn btn-secondary btn-sm" onClick={() => setBrowseDir(listing.parent!)} title="上一级">⬆ 上一级</button>
                  )}
                  <span className="file-open-dir">{listing.dir}</span>
                </>
              )}
            </div>
            <div className="file-open-list">
              {listing ? (
                listing.entries.length === 0
                  ? <div className="file-open-empty">（空目录）</div>
                  : listing.entries.map((entry) => (
                      <div key={entry.name} className={`file-open-entry${entry.is_dir ? ' is-dir' : ''}`} onClick={() => onEntryClick(entry)}>
                        <span className="file-open-entry-name">{entry.is_dir ? '📁' : '📄'} {entry.name}</span>
                        {!entry.is_dir && <span className="file-open-entry-size">{formatSize(entry.size)}</span>}
                      </div>
                    ))
              ) : (
                <div className="file-open-empty">{error ? '—' : '加载中…'}</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
