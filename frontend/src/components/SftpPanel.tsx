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
      const win = window.open('', '_blank')
      if (win) {
        win.document.write(`<pre style="font-family:monospace;padding:20px;white-space:pre-wrap;word-break:break-all;background:#0a0e1a;color:#c8d0e0;">${escapeHtml(data.content)}</pre>`)
        win.document.title = name
      }
    } catch (e) {
      alert('读取失败: ' + (e as Error).message)
    }
  }

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
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      try {
        const content = await file.text()
        const path = currentPath.replace(/\/$/, '') + '/' + file.name
        await api.sftpWrite(sessionId, path, content)
      } catch (e) {
        alert('上传失败 ' + file.name + ': ' + (e as Error).message)
      }
    }
    loadList(currentPath)
  }

  if (!sessionId) {
    return <div style={{ textAlign: 'center', color: '#555', padding: '30px 10px', fontSize: 12 }}>请先连接 SSH</div>
  }

  return (
    <div>
      <div className="sftp-path">📂 {currentPath}</div>
      <div className="sftp-toolbar">
        <button className="btn btn-secondary btn-sm" onClick={goUp}>⬆ 上级</button>
        <button className="btn btn-secondary btn-sm" onClick={() => loadList(currentPath)}>🔄</button>
        <button className="btn btn-primary btn-sm" onClick={() => fileInputRef.current?.click()}>⬆ 上传</button>
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
          <div style={{ textAlign: 'center', color: '#555', padding: '25px 10px', fontSize: 11 }}>
            空目录<br />拖拽文件到此处上传
          </div>
        )}
      </div>
    </div>
  )
}

function escapeHtml(s: string): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}
