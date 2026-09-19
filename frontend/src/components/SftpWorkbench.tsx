import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { FileListEntry } from '../lib/api'
import type { SftpItem } from '../types'

interface Props {
  sessionId: string
  /** 打开时的初始远程目录（当前终端 cd 跟踪）；null/缺省走保存或默认 / */
  initialRemote?: string | null
  /** 全局浮动提示通道（App 的 toast）：传输/删除等反馈同步弹出 */
  onNotify?: (msg: string) => void
  onClose: () => void
}

/** 双窗统一的行视图模型（本地 entries / 远程 items 归一化） */
interface Row {
  name: string
  isDir: boolean
  size: number
  mtime: number
  path: string
}

interface PaneState<T> {
  path: string
  parent: string | null
  entries: T[]
  loading: boolean
}

const fmtSize = (n: number): string => {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

const fmtTime = (ts: number): string => {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// 远程路径拼接（SFTP 恒用 /）；本地路径混用 / 也合法（Windows Path 接受）
const joinPath = (dir: string, name: string) => (dir.endsWith('/') ? `${dir}${name}` : `${dir}/${name}`)
// 远程上级目录：去掉最后一段（根目录返回 '/'）
const remoteParent = (path: string) => {
  const trimmed = path.replace(/\/+$/, '')
  if (!trimmed) return '/'
  const idx = trimmed.lastIndexOf('/')
  return idx <= 0 ? '/' : trimmed.slice(0, idx)
}

export function SftpWorkbench({ sessionId, initialRemote, onNotify, onClose }: Props) {
  // 上次会话的本地/远程目录（localStorage 持久化：关闭再打开恢复原状态）
  const savedPath = (() => {
    try {
      return JSON.parse(localStorage.getItem('ssh_web_tool.sftp_wb') || '{}')
    } catch {
      return {}
    }
  })()
  const [local, setLocal] = useState<PaneState<FileListEntry>>({ path: savedPath.local || '~', parent: null, entries: [], loading: false })
  const [remote, setRemote] = useState<PaneState<SftpItem>>({ path: savedPath.remote || '/', parent: null, entries: [], loading: false })
  // 路径输入框草稿（回车跳转，独立于加载完成的真实路径）
  const [localInput, setLocalInput] = useState(savedPath.local || '~')
  const [remoteInput, setRemoteInput] = useState(savedPath.remote || '/')
  // 底部状态栏：传输中/结果/错误。setStatus 同时把信息推给全局 toast 浮动提示；
  // onNotify 存 ref 保证 setStatus 引用稳定（各传输回调的依赖数组不受影响）
  const [status, setStatusRaw] = useState<{ text: string; kind: 'info' | 'error' } | null>(null)
  const onNotifyRef = useRef(onNotify)
  useEffect(() => {
    onNotifyRef.current = onNotify
  }, [onNotify])
  const setStatus = useCallback((s: { text: string; kind: 'info' | 'error' } | null) => {
    setStatusRaw(s)
    if (s) onNotifyRef.current?.(s.text)
  }, [])
  const [dragOver, setDragOver] = useState<'local' | 'remote' | null>(null)
  const [busy, setBusy] = useState(false)
  // 拖拽悬停的目标文件夹（文件夹行高亮 + 放下后作为传输目标目录）
  const [dropFolder, setDropFolder] = useState<string | null>(null)
  // 远程右键菜单
  const [ctx, setCtx] = useState<{ x: number; y: number; path: string; name: string; isDir: boolean } | null>(null)

  // 目录变化即持久化，下次打开恢复上次状态
  useEffect(() => {
    try {
      localStorage.setItem('ssh_web_tool.sftp_wb', JSON.stringify({ local: local.path, remote: remote.path }))
    } catch {
      /* 存储不可用不影响使用 */
    }
  }, [local.path, remote.path])

  const loadLocal = useCallback(async (path: string) => {
    setLocal((p) => ({ ...p, loading: true }))
    try {
      const data = await api.listDir(path || '~')
      setLocal({ path: data.dir, parent: data.parent, entries: data.entries, loading: false })
      setLocalInput(data.dir)
    } catch (e) {
      setLocal((p) => ({ ...p, loading: false }))
      setStatus({ text: '本地目录打开失败: ' + (e as Error).message, kind: 'error' })
    }
  }, [])

  const loadRemote = useCallback(
    async (path: string) => {
      setRemote((p) => ({ ...p, loading: true }))
      try {
        const data = await api.sftpList(sessionId, path || '/')
        setRemote({ path: data.path, parent: null, entries: data.items, loading: false })
        setRemoteInput(data.path)
      } catch (e) {
        setRemote((p) => ({ ...p, loading: false }))
        setStatus({ text: '远程目录打开失败: ' + (e as Error).message, kind: 'error' })
      }
    },
    [sessionId],
  )

  useEffect(() => {
    // 优先打开当前终端所在目录（cd 跟踪），其次上次会话目录（localStorage），默认 /
    loadLocal(savedPath.local || '~')
    loadRemote(initialRemote || savedPath.remote || '/')
  }, [loadLocal, loadRemote])

  // ESC 关闭（与文件查看器同款快捷关闭）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 本地文件 → 远程当前目录（后端直读本机路径，无需经过浏览器）
  const transferLocalToRemote = async (localPath: string, name: string, destDir?: string) => {
    if (busy) return
    setBusy(true)
    try {
      const dest = joinPath(destDir || remote.path, name)
      setStatus({ text: `上传中 ${name} → ${dest}`, kind: 'info' })
      const r = await api.preopUpload(sessionId, localPath, 'path', dest)
      setStatus({ text: `已上传 ${name} (${fmtSize(r.size)}) → ${dest}`, kind: 'info' })
      loadRemote(remote.path)
    } catch (e) {
      setStatus({ text: '上传失败: ' + (e as Error).message, kind: 'error' })
    } finally {
      setBusy(false)
    }
  }

  // 远程文件 → 本地当前目录（服务器端流式下载，浏览器只发指令）
  const transferRemoteToLocal = async (remotePath: string, name: string, localDir?: string) => {
    if (busy) return
    setBusy(true)
    try {
      const destDir = localDir || local.path
      setStatus({ text: `下载中 ${name} → ${destDir}`, kind: 'info' })
      const r = await api.sftpDownloadTo(sessionId, remotePath, destDir)
      setStatus({ text: `已下载 ${name} (${fmtSize(r.size)}) → ${r.local}`, kind: 'info' })
      loadLocal(local.path)
    } catch (e) {
      setStatus({ text: '下载失败: ' + (e as Error).message, kind: 'error' })
    } finally {
      setBusy(false)
    }
  }

  const handleActivate = (side: 'local' | 'remote', row: Row) => {
    if (row.isDir) {
      if (side === 'local') loadLocal(row.path)
      else loadRemote(row.path)
      return
    }
    if (side === 'local') {
      // 双击本地文件 = 上传到右侧当前目录（Xftp 习惯）
      transferLocalToRemote(row.path, row.name)
      return
    }
    // 双击远程文件 = 下载到左侧当前目录（Xftp 习惯）
    transferRemoteToLocal(row.path, row.name)
  }

  const handleDrop = (target: 'local' | 'remote', e: React.DragEvent, folderPath?: string | null) => {
    e.preventDefault()
    setDragOver(null)
    setDropFolder(null)
    const raw = e.dataTransfer.getData('text/plain')
    if (!raw) return
    let item: { side: string; name: string; isDir: boolean; path: string }
    try {
      item = JSON.parse(raw)
    } catch {
      return
    }
    if (item.side === target) return // 同侧拖放无效
    if (item.isDir) {
      setStatus({ text: '暂不支持目录传输，请选择文件', kind: 'error' })
      return
    }
    // 拖到文件夹行上 → 传输到该文件夹；否则传输到当前目录
    if (target === 'remote') transferLocalToRemote(item.path, item.name, folderPath || remote.path)
    else transferRemoteToLocal(item.path, item.name, folderPath || local.path)
  }

  // 右键删除远程文件/目录（防误删：confirm 确认）
  const doDelete = async (t: { path: string; name: string; isDir: boolean }) => {
    setCtx(null)
    if (!window.confirm(`确定删除${t.isDir ? '目录' : '文件'} ${t.name}？`)) return
    setBusy(true)
    try {
      await api.sftpDelete(sessionId, t.path)
      setStatus({ text: `已删除 ${t.name}`, kind: 'info' })
      loadRemote(remote.path)
    } catch (e) {
      setStatus({ text: '删除失败: ' + (e as Error).message, kind: 'error' })
    } finally {
      setBusy(false)
    }
  }

  const renderPane = (side: 'local' | 'remote') => {
    const isLocal = side === 'local'
    const st = isLocal ? local : remote
    const input = isLocal ? localInput : remoteInput
    const setInput = isLocal ? setLocalInput : setRemoteInput
    const go = (p: string) => (isLocal ? loadLocal(p) : loadRemote(p))
    const rows: Row[] = isLocal
      ? local.entries.map((e) => ({ name: e.name, isDir: e.is_dir, size: e.size, mtime: e.mtime, path: joinPath(local.path, e.name) }))
      : remote.entries
          .filter((i) => i.type !== 'unknown')
          .map((i) => ({ name: i.name, isDir: i.type === 'dir', size: i.size, mtime: i.mtime, path: joinPath(remote.path, i.name) }))
    return (
      <div
        className={`sftp-wb-pane${dragOver === side ? ' drag-over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'copy' // 显式声明可放置，避免系统显示禁止符号
          setDragOver(side)
        }}
        onDragLeave={() => setDragOver((cur) => (cur === side ? null : cur))}
        onDrop={(e) => handleDrop(side, e)}
      >
        <div className="sftp-wb-pane-head">
          <span className="sftp-wb-pane-label">{isLocal ? '💻 本地' : '🌐 远程'}</span>
          <input
            className="sftp-wb-path"
            value={input}
            spellCheck={false}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') go((e.target as HTMLInputElement).value)
            }}
          />
          <button
            className="btn btn-secondary btn-sm"
            title="上级目录"
            onClick={() => {
              if (isLocal) {
                if (local.parent) loadLocal(local.parent)
              } else {
                loadRemote(remoteParent(remote.path))
              }
            }}
          >
            ⬆
          </button>
          <button className="btn btn-secondary btn-sm" title="刷新" onClick={() => go(st.path)}>
            🔄
          </button>
        </div>
        <div className="sftp-wb-list">
          {st.loading && <div className="sftp-wb-empty">加载中…</div>}
          {!st.loading && rows.length === 0 && <div className="sftp-wb-empty">空目录</div>}
          {rows.map((r) => (
            <div
              key={r.name}
              className={`sftp-wb-row${r.isDir && dropFolder === joinPath(st.path, r.name) ? ' drop-target' : ''}`}
              draggable={!busy}
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', JSON.stringify({ side, name: r.name, isDir: r.isDir, path: r.path }))
                e.dataTransfer.effectAllowed = 'copy'
              }}
              onDragOver={(e) => {
                // 文件夹行可作为拖放目标：悬停高亮，放下后传输到该文件夹
                if (!busy && r.isDir) {
                  e.preventDefault()
                  e.stopPropagation()
                  e.dataTransfer.dropEffect = 'copy'
                  setDragOver(side)
                  setDropFolder(joinPath(st.path, r.name))
                }
              }}
              onDragLeave={(e) => {
                if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
                  setDropFolder(null)
                }
              }}
              onDrop={(e) => {
                if (r.isDir) {
                  e.preventDefault()
                  e.stopPropagation()
                  handleDrop(side, e, joinPath(st.path, r.name))
                }
              }}
              onContextMenu={(e) => {
                // 远程窗格右键 → 操作菜单（删除/下载）
                if (side !== 'remote') return
                e.preventDefault()
                setCtx({ x: e.clientX, y: e.clientY, path: r.path, name: r.name, isDir: r.isDir })
              }}
              onDoubleClick={() => handleActivate(side, r)}
              title={r.isDir ? '双击进入目录 · 拖拽到此处可传输到该文件夹' : '双击传输到对侧 · 或拖拽到对侧窗格/文件夹'}
            >
              <span className="sftp-wb-icon">{r.isDir ? '📁' : '📄'}</span>
              <span className="sftp-wb-name">{r.name}</span>
              <span className="sftp-wb-size">{r.isDir ? '-' : fmtSize(r.size)}</span>
              <span className="sftp-wb-mtime">{fmtTime(r.mtime)}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="sftp-wb-overlay">
      <div className="sftp-wb">
        <div className="sftp-wb-header">
          <span className="sftp-wb-title">🗂 SFTP 双窗工作台</span>
          <button className="sftp-wb-close" onClick={onClose} title="关闭 (ESC)">
            ✕
          </button>
        </div>
        <div className="sftp-wb-body">
          {renderPane('local')}
          <div className="sftp-wb-divider" />
          {renderPane('remote')}
        </div>
        <div className={`sftp-wb-status${status?.kind === 'error' ? ' error' : ''}${busy ? ' busy' : ''}`}>
          {status ? status.text : '拖拽文件跨窗传输 · 拖到文件夹行可传到该文件夹 · 双击远程文件下载'}
        </div>
        {/* 远程右键菜单 */}
        {ctx && (
          <div className="sftp-wb-ctx-backdrop" onClick={() => setCtx(null)} onContextMenu={(e) => { e.preventDefault(); setCtx(null) }} />
        )}
        {ctx && (
          <div className="sftp-wb-ctx" style={{ left: ctx.x, top: ctx.y }}>
            <div className="sftp-wb-ctx-title">{ctx.name}</div>
            {!ctx.isDir && (
              <div
                className="sftp-wb-ctx-item"
                onClick={() => { transferRemoteToLocal(ctx.path, ctx.name); setCtx(null) }}
              >
                ⬇ 下载到本地
              </div>
            )}
            <div className="sftp-wb-ctx-item danger" onClick={() => doDelete(ctx)}>
              🗑 删除{ctx.isDir ? '目录' : '文件'}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
