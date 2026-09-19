import { useState, useEffect, useRef } from 'react'
import type { SftpItem } from '../types'
import { api } from '../lib/api'

interface Props {
  sessionId: string | null
}

function formatSize(bytes: number): string {
  if (!bytes || bytes === 0) return '0B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let b = bytes
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++ }
  return b.toFixed(1) + units[i]
}

export function SftpPanel({ sessionId }: Props) {
  const [currentPath, setCurrentPath] = useState('/')
  const [items, setItems] = useState<SftpItem[]>([])
  const [loading, setLoading] = useState(false)
  const [viewer, setViewer] = useState<{ name: string; content: string } | null>(null)
  // 上传状态：{name: 百分比}（多文件逐个上传时只显示当前文件）；null = 空闲
  const [uploading, setUploading] = useState<{ name: string; pct: number } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dropZoneRef = useRef<HTMLDivElement>(null)

  const loadList = async (path: string) => {
    if (!sessionId) return
    setLoading(true)
    try {
      const data = await api.sftpList(sessionId, path)
      setItems(data.items)
      setCurrentPath(path)
    } catch (e) {
      console.error('SFTP list error', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (sessionId) loadList('/')
  }, [sessionId])

  useEffect(() => {
    const zone = dropZoneRef.current
    const hint = document.getElementById('dropHint')
    if (!zone) return

    const onDragOver = (e: DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (hint) hint.style.display = 'flex'
    }
    const onDragLeave = (e: DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (hint) hint.style.display = 'none'
    }
    const onDrop = (e: DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (hint) hint.style.display = 'none'
      if (e.dataTransfer?.files.length) {
        uploadFiles(e.dataTransfer.files)
      }
    }

    zone.addEventListener('dragover', onDragOver)
    zone.addEventListener('dragleave', onDragLeave)
    zone.addEventListener('drop', onDrop)
    return () => {
      zone.removeEventListener('dragover', onDragOver)
      zone.removeEventListener('dragleave', onDragLeave)
      zone.removeEventListener('drop', onDrop)
    }
  }, [sessionId, currentPath])

  const handleItemClick = (item: SftpItem) => {
    if (item.type === 'dir') {
      const newPath = currentPath.replace(/\/$/, '') + '/' + item.name
      loadList(newPath)
    } else {
      viewFile(item.name)
    }
  }

  const goUp = () => {
    if (currentPath === '/') return
    const parts = currentPath.replace(/\/$/, '').split('/')
    parts.pop()
    loadList(parts.join('/') || '/')
  }

  const viewFile = async (name: string) => {
    if (!sessionId) return
    const path = currentPath.replace(/\/$/, '') + '/' + name
    try {
      const data = await api.sftpRead(sessionId, path)
      // 应用内弹窗预览：pywebview 桌面窗口下 window.open 新窗口请求会被转交系统浏览器，无法再用新标签页
      setViewer({ name, content: data.content })
    } catch (e) {
      alert('读取失败: ' + (e as Error).message)
    }
  }

  // 预览弹窗打开时支持 ESC 关闭
  useEffect(() => {
    if (!viewer) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setViewer(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewer])

  const downloadFile = (name: string) => {
    if (!sessionId) return
    const path = currentPath.replace(/\/$/, '') + '/' + name
    const a = document.createElement('a')
    a.href = api.sftpDownloadUrl(sessionId, path)
    a.download = name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  const uploadFiles = async (files: FileList) => {
    if (!sessionId) return
    // 逐个上传，XHR 进度回调驱动上传状态显示（大文件可见进度，不再像卡死）
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      setUploading({ name: file.name, pct: 0 })
      try {
        const path = currentPath.replace(/\/$/, '') + '/' + file.name
        await api.sftpUpload(sessionId, path, file, (pct) => setUploading({ name: file.name, pct }))
      } catch (e) {
        alert('上传失败 ' + file.name + ': ' + (e as Error).message)
      }
    }
    setUploading(null)
    loadList(currentPath)
  }

  if (!sessionId) {
    return <div style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: '30px 10px', fontSize: 'calc(12px * var(--ui-fs-scale))' }}>请先连接 SSH</div>
  }

  return (
    <div>
      {viewer && (
        <div className="sftp-viewer-overlay" onClick={() => setViewer(null)}>
          <div className="sftp-viewer" onClick={(e) => e.stopPropagation()}>
            <div className="sftp-viewer-header">
              <span className="sftp-viewer-title">📄 {viewer.name}</span>
              <button className="sftp-viewer-close" onClick={() => setViewer(null)} title="关闭 (ESC)">✕</button>
            </div>
            <pre className="sftp-viewer-content">{viewer.content}</pre>
          </div>
        </div>
      )}
      <div className="sftp-path">📂 {currentPath}</div>
      <div className="sftp-toolbar">
        <button className="btn btn-secondary btn-sm" onClick={goUp}>⬆ 上级</button>
        <button className="btn btn-secondary btn-sm" onClick={() => loadList(currentPath)}>🔄</button>
        <button className="btn btn-primary btn-sm" disabled={!!uploading} onClick={() => fileInputRef.current?.click()}>
          {uploading ? `⬆ ${uploading.pct}%` : '⬆ 上传'}
        </button>
        {uploading && <span className="sftp-upload-status">{uploading.name} {uploading.pct}%</span>}
      </div>
      <input
        ref={fileInputRef}
        type="file"
        style={{ display: 'none' }}
        multiple
        onChange={(e) => e.target.files && uploadFiles(e.target.files)}
      />
      <div ref={dropZoneRef} style={{ outline: 'none', minHeight: 100 }}>
        {items.map((item) => (
          <div
            key={item.name}
            className={`sftp-item ${item.type}`}
            onClick={() => handleItemClick(item)}
          >
            <span className="icon">{item.type === 'dir' ? '📁' : '📄'}</span>
            <span className="name">{item.name}</span>
            <span className="size">{item.type === 'dir' ? '' : formatSize(item.size)}</span>
            {item.type !== 'dir' && (
              <span
                className="sftp-dl-btn"
                onClick={(e) => { e.stopPropagation(); downloadFile(item.name) }}
              >⬇</span>
            )}
          </div>
        ))}
        {items.length === 0 && !loading && (
          <div style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: '25px 10px', fontSize: 'calc(11px * var(--ui-fs-scale))' }}>
            空目录<br />拖拽文件到此处上传
          </div>
        )}
      </div>
    </div>
  )
}
