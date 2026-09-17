import { useCallback, useEffect, useRef, useState } from 'react'
import { EditorState, Compartment } from '@codemirror/state'
import {
  EditorView,
  keymap,
  lineNumbers,
  drawSelection,
  highlightSpecialChars,
  highlightActiveLine,
  highlightActiveLineGutter,
  rectangularSelection,
  crosshairCursor,
} from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { search, searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import { api } from '../lib/api'
import type { EditorDefaults, EditorSessionInfo } from '../lib/api'
import { FileOpenModal } from './FileOpenModal'
import { SessionSelectModal } from './SessionSelectModal'
import { cmHighlightExtension } from '../lib/cmHighlight'
import { zsExtensions } from '../lib/cmZs'
import { playZsBlock } from '../lib/zsScript'
import type { ZsBlock, ZsPlayHandle } from '../lib/zsScript'
import type { QuickCommand } from '../types'
import type { HighlightRule } from '../lib/highlight'

/** 大文件只读分页：单页行数（与后端 MAX_PAGE_LINES 一致） */
const PAGE_SIZE = 5000

/** 广播目标会话选择（localStorage 临时记忆；session id 重启后失效会被自动剪除） */
const BROADCAST_KEY = 'ssh-web-tool-editor-broadcast-sessions'

/** .zs 文件判定 */
function isZsPath(path: string): boolean {
  return /\.zs$/i.test(path)
}

interface FileEditorProps {
  fontFamily: string
  fontSize: number
  /** 终端关键字高亮规则（编辑器复用同一套规则） */
  highlightRules: HighlightRule[]
  /** 快捷指令集（.zs @ 引用的解析来源） */
  quickCommands: QuickCommand[]
  /** 活跃终端会话列表（广播目标候选） */
  sessions: EditorSessionInfo[]
  /** 向指定会话发送命令（不抢焦点） */
  sendToSession: (session_id: string, cmd: string, execute: boolean) => boolean
  /** 读取指定会话终端 buffer 最后 N 行纯文本（监控/预览） */
  readBufferTail: (session_id: string, lines: number) => string | null
  recentPaths: string[]
  onUpdateRecentPaths: (paths: string[]) => void
  onStatus: (msg: string) => void
  onClose: () => void
}

/** 路径拼接（目录 + 文件名）：按目录里出现的分隔符判断风格 */
function joinPath(dir: string, name: string): string {
  const sep = dir.includes('\\') ? '\\' : '/'
  return dir.replace(/[\\/]+$/, '') + sep + name
}

/** 监控抽屉主体：每个选中会话一块，1s 轮询 buffer tail */
function MonitorBody({ sessions, readBufferTail }: { sessions: EditorSessionInfo[]; readBufferTail: (session_id: string, lines: number) => string | null }) {
  const [tails, setTails] = useState<Record<string, string>>({})

  useEffect(() => {
    const refresh = () => {
      const next: Record<string, string> = {}
      for (const s of sessions) next[s.session_id] = readBufferTail(s.session_id, 30) ?? '（无输出）'
      setTails(next)
    }
    refresh()
    const timer = setInterval(refresh, 1000)
    return () => clearInterval(timer)
  }, [sessions, readBufferTail])

  return (
    <div className="file-editor-monitor-body">
      {sessions.length === 0 ? (
        <div className="file-open-empty">未选择广播目标会话</div>
      ) : (
        sessions.map((s) => (
          <div key={s.session_id} className="file-editor-monitor-block">
            <div className="file-editor-monitor-label">
              {s.label}
              <span className="file-editor-monitor-host">{s.host}</span>
            </div>
            <pre className="file-editor-monitor-tail">{tails[s.session_id] ?? '加载中…'}</pre>
          </div>
        ))
      )}
    </div>
  )
}

/** 编辑器页：本地文件查看/编辑；.zs 脚本支持块解析/▶️ 播放/广播目标会话/监控 */
export function FileEditor({
  fontFamily,
  fontSize,
  highlightRules,
  quickCommands,
  sessions,
  sendToSession,
  readBufferTail,
  recentPaths,
  onUpdateRecentPaths,
  onStatus,
  onClose,
}: FileEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef<EditorView | null>(null)
  const readOnlyComp = useRef(new Compartment())
  const hlComp = useRef(new Compartment())
  const zsComp = useRef(new Compartment())

  const [filePath, setFilePath] = useState('')
  const [dirty, setDirty] = useState(false)
  const [readOnly, setReadOnly] = useState(false)
  const [encoding, setEncoding] = useState('utf-8')
  const [fileSize, setFileSize] = useState(0)
  // 只读分页状态（null = 非分页模式）
  const [page, setPage] = useState<{ start: number; lines: number; hasMore: boolean } | null>(null)
  const [openModal, setOpenModal] = useState<{ mode: 'open' | 'save'; initialPath: string } | null>(null)
  const [defaults, setDefaults] = useState<EditorDefaults | null>(null)
  // .zs 广播：目标会话选择 / 多选弹窗 / 监控抽屉 / chip hover 预览
  const [selectedIds, setSelectedIds] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(BROADCAST_KEY)
      return raw ? (JSON.parse(raw) as string[]) : []
    } catch {
      return []
    }
  })
  const [selectModalOpen, setSelectModalOpen] = useState(false)
  const [monitorOpen, setMonitorOpen] = useState(false)
  const [hoverPreview, setHoverPreview] = useState<{ sessionId: string; x: number; y: number } | null>(null)

  // saveRef：CM keymap 里调用最新的 save（避免闭包捕获旧 state）
  const saveRef = useRef<() => void>(() => {})
  // .zs 播放取消句柄（同一时间只允许一个播放，新播放取消旧的）
  const playingRef = useRef<ZsPlayHandle | null>(null)
  // 最新值 ref：zs 装饰扩展的 cfg 闭包经 ref 取最新，避免陈旧闭包
  const hlRulesRef = useRef(highlightRules)
  hlRulesRef.current = highlightRules
  const quickCommandsRef = useRef(quickCommands)
  quickCommandsRef.current = quickCommands
  const sessionsRef = useRef(sessions)
  sessionsRef.current = sessions
  const sendRef = useRef(sendToSession)
  sendRef.current = sendToSession
  const selectedIdsRef = useRef(selectedIds)
  selectedIdsRef.current = selectedIds
  // zs 装饰插件 cfg 里的 onPlay 经此 ref 间接调用最新处理器
  const zsPlayHandlerRef = useRef<(block: ZsBlock) => void>(() => {})

  const zsActive = isZsPath(filePath)
  const selectedSessions = sessions.filter((s) => selectedIds.includes(s.session_id))

  useEffect(() => {
    api.editorDefaults().then(setDefaults).catch(() => {})
  }, [])

  const markRecent = useCallback(
    (path: string) => {
      onUpdateRecentPaths([path, ...recentPaths.filter((p) => p !== path)].slice(0, 20))
    },
    [recentPaths, onUpdateRecentPaths]
  )

  const removeRecent = useCallback(
    (p: string) => {
      onUpdateRecentPaths(recentPaths.filter((x) => x !== p))
    },
    [recentPaths, onUpdateRecentPaths]
  )

  // 广播选择持久化（临时内容进 localStorage，与项目设置存储策略一致）
  useEffect(() => {
    try {
      localStorage.setItem(BROADCAST_KEY, JSON.stringify(selectedIds))
    } catch {
      /* 忽略 */
    }
  }, [selectedIds])

  // 会话关闭/重启后剪除失效的 session id（session id 不跨后端进程有效）
  useEffect(() => {
    const valid = new Set(sessions.map((s) => s.session_id))
    setSelectedIds((prev) => {
      const pruned = prev.filter((id) => valid.has(id))
      return pruned.length === prev.length ? prev : pruned
    })
  }, [sessions])

  /** 取消进行中的播放（关编辑器/开新文件时） */
  const cancelPlaying = useCallback(() => {
    if (playingRef.current) {
      playingRef.current.cancelled = true
      playingRef.current = null
    }
  }, [])

  /** ▶️ 播放一个块：广播到当前选中会话（同一时间一个播放，新播放取消旧播放） */
  zsPlayHandlerRef.current = (block: ZsBlock) => {
    cancelPlaying()
    const handle: ZsPlayHandle = { cancelled: false }
    playingRef.current = handle
    const ids = selectedIdsRef.current.filter((id) => {
      const s = sessionsRef.current.find((x) => x.session_id === id)
      return s && !s.disconnected
    })
    void playZsBlock(block, quickCommandsRef.current, ids, {
      send: (sid, cmd, exec) => sendRef.current(sid, cmd, exec),
      onStatus,
      cancel: handle,
    })
  }

  /** 用新文档重建编辑器状态（file load / 分页跳转共用）。
   *  高亮/zs 扩展装在 Compartment 里：setDoc 用当前规则初始化，后续变化由 effect reconfigure。 */
  const setDoc = useCallback((content: string, editable: boolean) => {
    const view = viewRef.current
    if (!view) return
    view.setState(
      EditorState.create({
        doc: content,
        extensions: [
          lineNumbers(),
          highlightSpecialChars(),
          history(),
          drawSelection(),
          rectangularSelection(),
          crosshairCursor(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          highlightSelectionMatches(),
          search({ createPanel: undefined }), // 内置搜索面板（Ctrl+F）
          EditorView.lineWrapping,
          keymap.of([
            { key: 'Mod-s', preventDefault: true, run: () => { saveRef.current(); return true } },
            ...searchKeymap,
            ...defaultKeymap,
            ...historyKeymap,
            indentWithTab,
          ]),
          readOnlyComp.current.of(EditorState.readOnly.of(!editable)),
          EditorView.editable.of(editable),
          hlComp.current.of(cmHighlightExtension(hlRulesRef.current)),
          zsComp.current.of([]), // zs 扩展由 effect 按文件路径配置（setDoc 时新路径 state 尚未生效）
          EditorView.updateListener.of((u) => {
            if (u.docChanged) setDirty(true)
          }),
        ],
      })
    )
  }, [])

  // 挂载：创建 EditorView 实例（空文档；打开文件时 setState 替换内容）
  useEffect(() => {
    if (!hostRef.current) return
    const view = new EditorView({ parent: hostRef.current })
    viewRef.current = view
    setDoc('', true)
    return () => {
      view.destroy()
      viewRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 高亮规则变化：重建关键字高亮扩展
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch({ effects: hlComp.current.reconfigure(cmHighlightExtension(highlightRules)) })
  }, [highlightRules])

  // .zs 装饰扩展：文件路径成为 .zs / 快捷指令集变化时重建
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch({
      effects: zsComp.current.reconfigure(
        zsActive
          ? zsExtensions({
              quickCommands,
              onPlay: (block) => zsPlayHandlerRef.current(block),
            })
          : []
      ),
    })
  }, [zsActive, quickCommands])

  // 字体跟随设置
  useEffect(() => {
    if (hostRef.current) {
      hostRef.current.style.setProperty('--file-editor-font', `${fontFamily}`)
      hostRef.current.style.setProperty('--file-editor-font-size', `${fontSize}px`)
    }
  }, [fontFamily, fontSize])

  /** 打开文件：小文件全量加载可编辑；>20MB 只读分页 */
  const openFile = useCallback(
    async (path: string, isRecent = false) => {
      try {
        const r = await api.readFile(path)
        cancelPlaying()
        setFilePath(r.path)
        setReadOnly(r.readonly)
        setEncoding(r.encoding)
        setFileSize(r.size)
        setDirty(false)
        setDoc(r.content, !r.readonly)
        setPage(r.readonly ? { start: r.start_line, lines: r.lines_returned, hasMore: r.has_more } : null)
        setOpenModal(null)
        setMonitorOpen(false)
        markRecent(r.path)
        onStatus(`已打开 ${r.path}（${r.readonly ? `只读 · ${formatSize(r.size)}` : '可编辑'}）`)
      } catch (e) {
        const msg = (e as Error).message || ''
        if (isRecent && msg.includes('不存在')) {
          if (window.confirm(`文件不存在（可能已被移动或删除）：\n${path}\n\n是否删除该路径记录？`)) {
            removeRecent(path)
          }
        } else {
          onStatus('打开文件失败: ' + msg)
        }
      }
    },
    [setDoc, markRecent, removeRecent, onStatus, cancelPlaying]
  )

  /** 只读分页跳转 */
  const gotoPage = useCallback(
    async (startLine: number) => {
      if (!filePath) return
      try {
        const r = await api.readFile(filePath, startLine, PAGE_SIZE)
        setDoc(r.content, false)
        setPage({ start: r.start_line, lines: r.lines_returned, hasMore: r.has_more })
        setFileSize(r.size)
      } catch (e) {
        onStatus('翻页失败: ' + (e as Error).message)
      }
    },
    [filePath, setDoc, onStatus]
  )

  /** 保存：无路径时先弹保存对话框选路径 */
  const save = useCallback(async () => {
    if (readOnly) {
      onStatus('文件过大（>20MB），只读查看模式不支持保存')
      return
    }
    if (!filePath) {
      const init = defaults ? joinPath(defaults.scripts_dir, 'untitled.zs') : 'untitled.zs'
      setOpenModal({ mode: 'save', initialPath: init })
      return
    }
    const view = viewRef.current
    if (!view) return
    try {
      const r = await api.writeFile(filePath, view.state.doc.toString(), encoding)
      setDirty(false)
      setFileSize(r.size)
      onStatus(`已保存 ${filePath}`)
    } catch (e) {
      onStatus('保存失败: ' + (e as Error).message)
    }
  }, [readOnly, filePath, defaults, encoding, onStatus])

  // 保存函数挂到 ref（CM keymap 与工具栏按钮共用最新版本）
  useEffect(() => {
    saveRef.current = () => { void save() }
  }, [save])

  /** 新建：清空编辑器（未保存修改需确认） */
  const newFile = useCallback(() => {
    if (dirty && !window.confirm('当前文件有未保存的修改，确定放弃并新建？')) return
    cancelPlaying()
    setFilePath('')
    setReadOnly(false)
    setEncoding('utf-8')
    setFileSize(0)
    setDirty(false)
    setPage(null)
    setMonitorOpen(false)
    setDoc('', true)
    onStatus('新建文件（保存时可选择路径，.zs 建议存 scripts/ 目录）')
  }, [dirty, setDoc, onStatus, cancelPlaying])

  /** 关闭编辑器页：未保存修改需确认 */
  const requestClose = useCallback(() => {
    if (dirty && !window.confirm('当前文件有未保存的修改，确定关闭编辑器？')) return
    cancelPlaying()
    onClose()
  }, [dirty, onClose, cancelPlaying])

  /** 弹窗选定路径：open 模式直接打开；save 模式写入内容 */
  const handlePick = useCallback(
    async (path: string) => {
      if (openModal?.mode === 'save') {
        const view = viewRef.current
        if (!view) return
        try {
          const r = await api.writeFile(path, view.state.doc.toString(), encoding)
          cancelPlaying()
          setFilePath(r.path)
          setReadOnly(false)
          setDirty(false)
          setFileSize(r.size)
          setPage(null)
          setOpenModal(null)
          markRecent(r.path)
          onStatus(`已保存 ${r.path}`)
        } catch (e) {
          onStatus('保存失败: ' + (e as Error).message)
        }
      } else {
        void openFile(path)
      }
    },
    [openModal, encoding, openFile, markRecent, onStatus, cancelPlaying]
  )

  /** chip hover：显示该会话终端输出的尾部快照（一期纯文本 tail） */
  const chipMouseEnter = useCallback(
    (s: EditorSessionInfo, el: HTMLElement) => {
      const rect = el.getBoundingClientRect()
      const rootRect = rootRef.current?.getBoundingClientRect()
      setHoverPreview({
        sessionId: s.session_id,
        x: rootRect ? rect.left - rootRect.left : rect.left,
        y: rootRect ? rect.bottom - rootRect.top + 4 : rect.bottom,
      })
    },
    []
  )

  return (
    <div className="file-editor" ref={rootRef}>
      <div className="file-editor-toolbar">
        <button className="btn btn-secondary btn-sm" onClick={newFile} title="新建文件">新建</button>
        <button className="btn btn-secondary btn-sm" onClick={() => setOpenModal({ mode: 'open', initialPath: '' })} title="打开本地文件">打开</button>
        <button
          className={`btn btn-sm ${dirty ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => void save()}
          disabled={readOnly}
          title="保存（Ctrl+S）"
        >保存</button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => filePath ? setOpenModal({ mode: 'save', initialPath: filePath }) : void save()}
          disabled={readOnly}
          title="另存为"
        >另存为</button>
        <span className="file-editor-path" title={filePath || '（未保存的新文件）'}>
          {filePath || '（未保存的新文件）'}{dirty ? ' ●' : ''}
        </span>
        <span className="file-editor-meta">
          {readOnly ? `只读 · ${formatSize(fileSize)} · ${encoding}` : `${formatSize(fileSize)} · ${encoding}`}
        </span>
        <div style={{ flex: 1 }} />
        <button className="btn btn-secondary btn-sm" onClick={requestClose} title="关闭编辑器（返回终端）">✕ 关闭</button>
      </div>
      {readOnly && page && (
        <div className="file-editor-pager">
          <span>大文件只读查看（{formatSize(fileSize)}）· 当前第 {page.start + 1}–{page.start + page.lines} 行</span>
          <button className="btn btn-secondary btn-sm" disabled={page.start === 0} onClick={() => void gotoPage(Math.max(0, page.start - PAGE_SIZE))}>◀ 上一页</button>
          <button className="btn btn-secondary btn-sm" disabled={!page.hasMore} onClick={() => void gotoPage(page.start + PAGE_SIZE)}>下一页 ▶</button>
          <input
            className="file-editor-jump"
            placeholder="跳转行号"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const n = parseInt((e.target as HTMLInputElement).value, 10)
                if (Number.isFinite(n) && n >= 1) void gotoPage(n - 1)
              }
            }}
          />
        </div>
      )}
      {/* .zs 广播条：目标会话 chips + ＋ 添加 + 监控抽屉开关 */}
      {zsActive && (
        <div className="file-editor-broadcast">
          <span className="file-editor-broadcast-title">广播目标:</span>
          {selectedSessions.map((s) => (
            <span
              key={s.session_id}
              className={`broadcast-chip${s.disconnected ? ' disconnected' : ''}`}
              onMouseEnter={(e) => chipMouseEnter(s, e.currentTarget)}
              onMouseLeave={() => setHoverPreview(null)}
            >
              {s.label}
              <button
                className="broadcast-chip-remove"
                title="移除该会话"
                onClick={() => setSelectedIds((prev) => prev.filter((id) => id !== s.session_id))}
              >✕</button>
            </span>
          ))}
          <button
            className="btn btn-secondary btn-sm broadcast-add"
            onClick={() => setSelectModalOpen(true)}
            title="选择广播目标会话"
          >＋</button>
          <div style={{ flex: 1 }} />
          <button
            className={`btn btn-sm ${monitorOpen ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setMonitorOpen(!monitorOpen)}
            title="打开/关闭会话监控抽屉（实时查看目标会话输出尾部）"
          >📡 监控</button>
        </div>
      )}
      <div className={`file-editor-body${readOnly ? ' readonly' : ''}`} ref={hostRef} />
      {/* hover 预览浮层：chip 悬停时显示该会话输出尾部快照 */}
      {hoverPreview && (
        <div className="file-editor-hover-preview" style={{ left: hoverPreview.x, top: hoverPreview.y }}>
          <pre>{readBufferTail(hoverPreview.sessionId, 15) ?? '（无输出）'}</pre>
        </div>
      )}
      {/* 监控抽屉：右侧浮层，1s 轮询选中会话的输出尾部 */}
      {monitorOpen && (
        <div className="file-editor-monitor">
          <div className="file-editor-monitor-header">
            <span>会话监控</span>
            <button className="btn btn-secondary btn-sm" onClick={() => setMonitorOpen(false)} title="关闭">✕</button>
          </div>
          <MonitorBody sessions={selectedSessions} readBufferTail={readBufferTail} />
        </div>
      )}
      {openModal && (
        <FileOpenModal
          mode={openModal.mode}
          initialPath={openModal.initialPath}
          recentPaths={recentPaths}
          defaults={defaults}
          onPick={handlePick}
          onRemoveRecent={removeRecent}
          onClose={() => setOpenModal(null)}
        />
      )}
      {selectModalOpen && (
        <SessionSelectModal
          sessions={sessions}
          initialSelected={selectedIds}
          onConfirm={(ids) => {
            setSelectedIds(ids)
            setSelectModalOpen(false)
          }}
          onClose={() => setSelectModalOpen(false)}
        />
      )}
    </div>
  )
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
