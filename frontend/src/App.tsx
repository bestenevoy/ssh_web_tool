import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import '@xterm/xterm/css/xterm.css'
import './App.css'
import type { Host, HostType, QuickCommand } from './types'
import type { SshConnInfo, TerminalInstance } from './lib/useTerminals'
import { api } from './lib/api'
import type { EditorSessionInfo } from './lib/api'
import { ContextMenu } from './components/ContextMenu'
import type { CtxMenuItem } from './components/ContextMenu'
import { useTerminals } from './lib/useTerminals'
import { writeClipboardText } from './lib/terminalCopy'
import { useEvents } from './lib/useEvents'
import { useSettings, FONT_OPTIONS } from './lib/useSettings'
import { HostList } from './components/HostList'
import { GroupManager } from './components/GroupManager'
import { TerminalView } from './components/TerminalView'
import type { SplitMode, PaneIndex } from './components/TerminalView'
import { SessionPanel } from './components/SessionPanel'
import { loadOrder, sortSessionIds } from './lib/sessionOrder'
import { QuickCommands } from './components/QuickCommands'
import { SftpPanel } from './components/SftpPanel'
import { SftpWorkbench } from './components/SftpWorkbench'
import { TransferList } from './components/TransferList'
import { HostModal } from './components/HostModal'
import { QuickCommandModal } from './components/QuickCommandModal'
import { PasswordModal } from './components/PasswordModal'
import { SettingsModal } from './components/SettingsModal'
import { DirPickerModal } from './components/DirPickerModal'
import { SearchBar } from './components/SearchBar'
import { FileEditor } from './components/FileEditor'
import HistorySearchModal from './components/HistorySearchModal'

type PanelTab = 'sessions' | 'quick' | 'sftp' | 'transfers'

// ---- 布局宽度持久化 ----
const LAYOUT_KEY_PREFIX = 'ssh-web-tool-layout-'

function loadLayoutWidth(key: string, def: number, min: number, max: number): number {
  try {
    const v = Number(localStorage.getItem(LAYOUT_KEY_PREFIX + key))
    if (Number.isFinite(v) && v >= min && v <= max) return v
  } catch { /* localStorage 不可用时用默认值 */ }
  return def
}

function App() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [groups, setGroups] = useState<string[]>([])
  const [hostTypes, setHostTypes] = useState<HostType[]>([])
  const [quickCommands, setQuickCommands] = useState<QuickCommand[]>([])
  const [fallbackShell, setFallbackShell] = useState<string>('powershell')
  const [shellChoices, setShellChoices] = useState<string[]>(['cmd', 'powershell', 'pwsh'])
  const [connectTimeout, setConnectTimeout] = useState(10)
  const [configFile, setConfigFile] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  // 右侧面板互斥显示：none=隐藏 / tools=工具面板（会话/快速指令/SFTP/传输 四个 tab）；
  // 显示与关闭都走顶栏「📋 面板」按钮切换，面板自身不再带标题栏/关闭叉号
  const [rightMode, setRightMode] = useState<'none' | 'tools'>('tools')
  // 会话列表 / tab 栏共用排序：组间自定义顺序（localStorage 持久化，拖拽更新）
  const [customOrder, setCustomOrder] = useState<string[]>(loadOrder)
  // 分屏（窗口模型）：none 单屏 / h 左右均分 / v 上下均分（固定 2 窗口）；
  // 每个窗口 = 自己的 tab 列表（windowSessions）+ 当前显示会话（windowActive），
  // 单屏时只用窗口0（会话 = sortedSessionIds，激活 = activeId）；
  // 分屏时 activeId 恒等于焦点窗口的激活会话
  const [splitMode, setSplitMode] = useState<SplitMode>('none')
  const [windowSessions, setWindowSessions] = useState<[string[], string[]]>([[], []])
  const [windowActive, setWindowActive] = useState<[string | null, string | null]>([null, null])
  const [focusedPane, setFocusedPane] = useState<PaneIndex>(0)
  // 右侧会话列表面板（按主机 ip 聚合分组，与顶部标签栏并存）

  // 布局宽度（侧栏/右面板可拖拽调宽，localStorage 持久化；终端是核心区，宽度自适应）
  const [sidebarWidth, setSidebarWidth] = useState(() => loadLayoutWidth('sidebar', 240, 180, 440))
  const [panelWidth, setPanelWidth] = useState(() => loadLayoutWidth('panel', 320, 260, 600))
  const [panelTab, setPanelTab] = useState<PanelTab>('quick')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingHost, setEditingHost] = useState<Host | null>(null)
  // 浮动提示：所有提示信息统一走 toast（顶栏不承载信息）。多条向下堆叠，
  // 每条独立显示 3.2s 后消失，最多同时 5 条；setStatus 签名不变，调用点无需感知
  const [toasts, setToasts] = useState<{ id: number; msg: string }[]>([])
  const toastIdRef = useRef(0)
  const setStatus = useCallback((msg: string) => {
    if (!msg) return
    const id = ++toastIdRef.current
    setToasts((list) => [...list.slice(-4), { id, msg }])
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 3200)
  }, [])
  const [historySearchOpen, setHistorySearchOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 编辑器页：打开后替换中间终端区（终端实例保持挂载，仅隐藏容器）
  const [editorOpen, setEditorOpen] = useState(false)
  const [sftpWbOpen, setSftpWbOpen] = useState(false)
  // SFTP 打开时的初始远程目录（当前终端 cd 跟踪；null 走保存/默认）
  const [sftpInitialRemote, setSftpInitialRemote] = useState<string | null>(null)
  const [quickCommandModalOpen, setQuickCommandModalOpen] = useState(false)
  const [editingQuickCommand, setEditingQuickCommand] = useState<QuickCommand | null>(null)
  // 终端窗口右键菜单（与主机列表右键菜单各自独立）：坐标 + 会话 + 命中的色块 id
  const [terminalMenu, setTerminalMenu] = useState<{
    x: number; y: number; sessionId: string; blockId: number | null
  } | null>(null)
  // 右键菜单打开时异步拉取的当前会话日志记录状态（null=加载中）
  const [terminalRecording, setTerminalRecording] = useState<boolean | null>(null)
  // 开启日志记录时的目录选择弹窗（默认关闭询问 + 未勾选「不再询问」时弹出）
  const [recordDirModal, setRecordDirModal] = useState<{ sessionId: string } | null>(null)

  const { settings, toggleTheme, setFontFamily, setFontSize, updateSettings } = useSettings()
  const terminals = useTerminals(settings)
  const { focusActiveTerminal } = terminals
  const events = useEvents()

  // 编辑器广播目标候选会话（终端实例 Map → 精简列表；Map 状态变化即重新派生）
  const editorSessions: EditorSessionInfo[] = useMemo(
    () =>
      Array.from(terminals.terminals.values()).map((t) => ({
        session_id: t.session_id,
        label: t.terminal_name || t.host_name,
        host: t.type === 'local' ? 'localhost' : t.host_name,
        disconnected: t.disconnected,
      })),
    [terminals.terminals]
  )

  // 拖拽调宽：sidebar 向右拖变宽，panel 向左拖变宽；松开时持久化
  const startResize = useCallback((side: 'sidebar' | 'panel') => (e: React.MouseEvent) => {
    e.preventDefault()
    const isSidebar = side === 'sidebar'
    const startX = e.clientX
    const startW = isSidebar ? sidebarWidth : panelWidth
    const min = isSidebar ? 180 : 260
    const max = isSidebar ? 440 : 600
    const dir = isSidebar ? 1 : -1
    let last = startW
    const onMove = (ev: MouseEvent) => {
      last = Math.min(max, Math.max(min, startW + dir * (ev.clientX - startX)))
      if (isSidebar) setSidebarWidth(last)
      else setPanelWidth(last)
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      try { localStorage.setItem(LAYOUT_KEY_PREFIX + side, String(last)) } catch { /* 忽略 */ }
    }
    document.body.style.cursor = 'col-resize'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [sidebarWidth, panelWidth])

  // 设置快捷键处理函数（Alt+R 打开历史搜索）
  useEffect(() => {
    terminals.setShortcutHandler(() => {
      if (terminals.activeId) {
        setHistorySearchOpen(true)
      }
    })
    return () => terminals.setShortcutHandler(null)
  }, [terminals, terminals.activeId])

  // 终端内 Ctrl+F：打开内容搜索条（unfoldAll/暂停自动折叠由 SearchBar 挂载时处理，
  // App 层不感知折叠）
  useEffect(() => {
    terminals.setSearchHandler((session_id) => {
      terminals.setSearchOpenId(session_id)
    })
    return () => terminals.setSearchHandler(null)
  }, [terminals])

  // 全局快捷键监听（备用，终端未获得焦点时也能触发）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'r' || e.key === 'R')) {
        if (terminals.activeId) {
          e.preventDefault()
          setHistorySearchOpen(true)
        }
      }
      // Ctrl+B：显示/折叠主机列表（终端聚焦时由 terminalCopy 的 customKeyEventHandler
      // 拦截并调用同一回调；此处覆盖终端未聚焦的其余场景）
      if (e.ctrlKey && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault()
        setSidebarCollapsed(v => !v)
      }
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [terminals.activeId])

  // 终端内 Ctrl+B：显示/折叠主机列表（经 customKeyEventHandler 拦截 ^B 不发给远端）
  useEffect(() => {
    terminals.setSidebarToggleHandler(() => setSidebarCollapsed(v => !v))
    return () => terminals.setSidebarToggleHandler(null)
  }, [terminals])

  // 默认本机终端 shell（左侧「默认终端」条目 + SSH 断开后自动进入共用，保存到 config.json；
  // 取值为短标识或检测列表里的完整路径）
  const handleSetFallbackShell = useCallback(async (shell: string) => {
    try {
      await api.setFallbackShell(shell)
      setFallbackShell(shell)
      setStatus(`默认本机终端已设为 ${shell}`)
    } catch (e) {
      setStatus('保存配置失败: ' + (e as Error).message)
    }
  }, [])

  // SSH 连接超时（秒，1-300，保存到 config.json 顶层）
  const handleSetConnectTimeout = useCallback(async (seconds: number) => {
    try {
      const r = await api.setConnectTimeout(seconds)
      setConnectTimeout(r.connect_timeout)
      setStatus(`SSH 连接超时已设为 ${r.connect_timeout} 秒`)
    } catch (e) {
      setStatus('保存配置失败: ' + (e as Error).message)
      throw e // 设置弹窗感知失败以回退输入显示
    }
  }, [])

  const loadHosts = useCallback(async () => {
    try {
      const data = await api.listHosts()
      setHosts(data.hosts)
      setGroups(data.groups)
      setHostTypes(data.host_types)
    } catch (e) {
      console.error('加载主机失败', e)
    }
  }, [])

  const loadQuickCommands = useCallback(async () => {
    try {
      const data = await api.listQuickCommands()
      setQuickCommands(data.commands)
    } catch (e) {
      console.error('加载快速指令失败', e)
    }
  }, [])

  useEffect(() => {
    loadHosts()
      api.getConfig()
        .then((c) => {
          if (c?.fallback_local_shell) setFallbackShell(c.fallback_local_shell)
          if (c?.local_shell_choices?.length) setShellChoices(c.local_shell_choices)
          if (typeof c?.connect_timeout === 'number') setConnectTimeout(c.connect_timeout)
          if (c?.config_file) setConfigFile(c.config_file)
        })
        .catch(() => {})
    loadQuickCommands()
    terminals.restoreTerminals()
    // 定期刷新主机列表，确保终端计数及时更新（CLI/Python 包创建的终端也能显示）
    // 优化：前端操作（连接/断开/关闭）已立即刷新，定期轮询仅需低频兜底
    const timer = setInterval(() => {
      loadHosts()
    }, 15000)
    return () => clearInterval(timer)
  }, [])

  const activeHostId: string | null = terminals.activeId ? terminals.terminals.get(terminals.activeId)?.host_id || null : null

  // 会话显示顺序（tab 栏与会话列表共用：组间自定义顺序 + ip:port 排序，组内创建顺序）
  const sortedSessionIds = useMemo(() => {
    const hostMap = new Map(hosts.map((h) => [h.id, h]))
    return sortSessionIds(terminals.terminals, hostMap, customOrder)
  }, [terminals.terminals, hosts, customOrder])

  // 会话切换（顶部 tab / 右侧会话列表共用）：
  // 单屏 = 普通切换；分屏 = 目标在某窗口则聚焦该窗口，否则放进焦点窗口显示
  const handleSwitchTerminal = useCallback((id: string) => {
    if (splitMode === 'none') { terminals.switchTerminal(id); return }
    const in0 = windowSessions[0].includes(id)
    const in1 = windowSessions[1].includes(id)
    if (in0 || in1) {
      const idx: PaneIndex = in0 ? 0 : 1
      setFocusedPane(idx)
      setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[idx] = id; return n })
      terminals.switchTerminal(id)
      return
    }
    // 不在任何窗口（新建/未分屏会话）：放进焦点窗口并激活
    setFocusedPane(focusedPane)
    setWindowSessions((prev) => {
      const n: [string[], string[]] = [[...prev[0]], [...prev[1]]]
      if (!n[focusedPane].includes(id)) n[focusedPane].push(id)
      return n
    })
    setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[focusedPane] = id; return n })
    terminals.switchTerminal(id)
  }, [splitMode, windowSessions, focusedPane, terminals])

  // 进入分屏：当前活动的 tab 自动移到新窗口（窗口1），其余留在窗口0
  const enterSplit = useCallback((mode: 'h' | 'v') => {
    if (terminals.terminals.size === 0) { setStatus('请先打开一个终端会话'); return }
    if (window.innerWidth < 800) { setStatus('窗口太窄，无法分屏'); return }
    const ids = Array.from(terminals.terminals.keys())
    const first = terminals.activeId && ids.includes(terminals.activeId) ? terminals.activeId : ids[0]
    const rest = ids.filter((s) => s !== first)
    setSplitMode(mode)
    setWindowSessions([rest, [first]])
    setWindowActive([rest.length > 0 ? rest[0] : null, first])
    setFocusedPane(1)
    if (terminals.activeId !== first) terminals.switchTerminal(first)
  }, [terminals])

  // 退出分屏：合并两窗口会话回单屏，显示退出前焦点窗口的会话
  const exitSplit = useCallback(() => {
    const merged = [...windowSessions[0], ...windowSessions[1]]
    const sid = windowActive[focusedPane] ?? windowActive[0] ?? merged[0] ?? null
    setSplitMode('none')
    setWindowSessions([[], []])
    setWindowActive([null, null])
    setFocusedPane(0)
    if (sid && terminals.terminals.has(sid)) terminals.switchTerminal(sid)
  }, [windowSessions, windowActive, focusedPane, terminals])

  // 跨窗口拖拽 tab：把会话从原窗口移动到目标窗口（原窗口移除，目标窗口追加末尾并激活）
  const handleMoveTabToWindow = useCallback((sid: string, toIdx: PaneIndex) => {
    if (splitMode === 'none') return
    setWindowSessions((prev) => {
      const n: [string[], string[]] = [[...prev[0]], [...prev[1]]]
      const in0 = n[0].includes(sid)
      const in1 = n[1].includes(sid)
      if (!in0 && !in1) return prev
      const from: PaneIndex = in0 ? 0 : 1
      if (from === toIdx) return prev
      n[from] = n[from].filter((s) => s !== sid)
      n[toIdx] = [...n[toIdx], sid]
      return n
    })
    setFocusedPane(toIdx)
    setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[toIdx] = sid; return n })
    terminals.switchTerminal(sid)
  }, [splitMode, terminals])

  // 点击窗口 → 聚焦（会话切换交给具体交互）
  const handleFocusWindow = useCallback((idx: PaneIndex) => {
    setFocusedPane(idx)
  }, [])

  // 分屏状态自愈（每次渲染收敛检查，守卫保证只跑一次有效分支）：
  // ① 清理窗口里已被关闭的会话；② 两窗口全空则退出分屏；
  // ③ activeId 指向的新会话（新建/重连/自动恢复）进入焦点窗口；
  // ④ 焦点窗口激活与会话列表同步；⑤ 非焦点窗口激活失效时取该窗口第一个
  useEffect(() => {
    if (splitMode === 'none') return
    const liveOf = (sid: string): string | null => (terminals.terminals.has(sid) ? sid : null)
    const w0 = windowSessions[0].map(liveOf).filter((x): x is string => !!x)
    const w1 = windowSessions[1].map(liveOf).filter((x): x is string => !!x)
    if (w0.length !== windowSessions[0].length || w1.length !== windowSessions[1].length) { setWindowSessions([w0, w1]); return }
    if (w0.length === 0 && w1.length === 0) { setSplitMode('none'); setFocusedPane(0); return }
    const aid = terminals.activeId
    // ③ 新会话（activeId 不在任何窗口）→ 放进焦点窗口
    if (aid && !w0.includes(aid) && !w1.includes(aid)) {
      setWindowSessions((prev) => { const n: [string[], string[]] = [[...prev[0]], [...prev[1]]]; if (!n[focusedPane].includes(aid)) n[focusedPane].push(aid); return n })
      setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[focusedPane] = aid; return n })
      return
    }
    // ④ 焦点窗口：窗口列表与激活会话保持一致
    const focusedWin = focusedPane === 0 ? w0 : w1
    if (aid && focusedWin.includes(aid)) {
      if (windowActive[focusedPane] !== aid) setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[focusedPane] = aid; return n })
    } else if (aid && !focusedWin.includes(aid)) {
      // activeId 在另一窗口 → 同步聚焦
      const otherIdx: PaneIndex = focusedPane === 0 ? 1 : 0
      setFocusedPane(otherIdx)
      setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[otherIdx] = aid; return n })
    } else if (!aid && focusedWin[0]) {
      terminals.switchTerminal(focusedWin[0])
    }
    // ⑤ 非焦点窗口激活失效 → 取该窗口第一个
    const otherIdx: PaneIndex = focusedPane === 0 ? 1 : 0
    const otherWin = otherIdx === 0 ? w0 : w1
    const oa = windowActive[otherIdx]
    if (oa && !otherWin.includes(oa)) setWindowActive((prev) => { const n: [string | null, string | null] = [prev[0], prev[1]]; n[otherIdx] = otherWin[0] ?? null; return n })
  }, [splitMode, focusedPane, windowSessions, windowActive, terminals])

  // 分屏布局/窗口内容变化后重新 fit 两个窗口的激活会话（等布局生效后的下一帧再量尺寸）
  const refitSplit = terminals.refitTerminals
  useEffect(() => {
    if (splitMode === 'none') return
    const ids = [windowActive[0], windowActive[1]].filter((x): x is string => !!x)
    const raf = requestAnimationFrame(() => refitSplit(ids))
    return () => cancelAnimationFrame(raf)
  }, [splitMode, windowActive, windowSessions, refitSplit])

  // 分屏模式下窗口尺寸变化：两个窗口都要重新 fit（单屏场景由 useTerminals 内部处理 activeId）
  useEffect(() => {
    if (splitMode === 'none') return
    const handler = () => refitSplit([windowActive[0], windowActive[1]].filter((x): x is string => !!x))
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [splitMode, windowActive, refitSplit])

  const handleCloseTerminal = useCallback((session_id: string) => {
    const ok = confirm('关闭终端标签？\n\n将同时关闭后端 SSH 连接，重开页面不会恢复该会话。')
    if (!ok) return
    terminals.closeTerminal(session_id)
      .then(() => loadHosts())
  }, [terminals, loadHosts])

  // 手动断开当前终端连接（保留标签，可重新连接）
  const handleDisconnectTerminal = useCallback((session_id: string) => {
    terminals.disconnectTerminal(session_id).then(() => loadHosts())
  }, [terminals, loadHosts])

  // ---- 终端窗口右键菜单（rssh 同款：右键命中色条 → 未选中块排他选中，已多选则保留）----
  const handleTerminalContextMenu = useCallback((sessionId: string, e: React.MouseEvent) => {
    e.preventDefault()
    const inst = terminals.terminals.get(sessionId)
    if (!inst) return
    const bb = inst.blockBar
    const blockId = bb?.hitTest(e.clientX, e.clientY) ?? null
    if (blockId != null && bb) {
      const sel = bb.getSelection()
      if (!sel.includes(blockId)) bb.selectBlock(blockId)
    }
    setTerminalMenu({ x: e.clientX, y: e.clientY, sessionId, blockId })
  }, [terminals])

  // 粘贴到终端：pywebview/WebView2 下 navigator.clipboard.readText 可能被拒，失败则提示用 Ctrl+V
  const pasteToTerminal = useCallback((inst: TerminalInstance) => {
    navigator.clipboard.readText()
      .then((text) => { if (text) inst.term.paste(text) })
      .catch(() => alert('读取剪贴板失败，请在终端中直接按 Ctrl+V 粘贴'))
  }, [])

  // 右键菜单打开时拉取该会话的日志记录状态（决定「暂停/恢复记录」菜单项文案）
  useEffect(() => {
    if (!terminalMenu) return
    setTerminalRecording(null)
    api.getSessionRecord(terminalMenu.sessionId)
      .then((r) => setTerminalRecording(r.recording))
      .catch(() => setTerminalRecording(null))
  }, [terminalMenu])

  /** 开启指定会话的日志记录：dir 为空时用后端默认目录（~/.ai4one/sshtool/logs） */
  const startRecording = useCallback((sessionId: string, dir: string) => {
    api.setSessionRecord(sessionId, true, dir || undefined)
      .then(() => {
        setTerminalRecording(true)
        setStatus(dir ? `已开始记录本会话日志到 ${dir}` : '已开始记录本会话日志（默认日志目录）')
      })
      .catch((e) => setStatus('开启日志记录失败: ' + (e as Error).message))
  }, [])

  // ---- 重连密码弹窗（Promise 化：askPassword().then(pw => ...)）----
  // 会话/主机未保存密码且无私钥时，统一重连流程先弹窗取密码再建连
  const pwResolveRef = useRef<((v: string | null) => void) | null>(null)
  const [pwPromptTitle, setPwPromptTitle] = useState('')
  const askPassword = useCallback((title: string) => {
    return new Promise<string | null>((resolve) => {
      pwResolveRef.current?.(null)  // 并发弹窗保护：新弹窗顶掉旧等待（旧流程按"已取消"收尾）
      pwResolveRef.current = resolve
      setPwPromptTitle(title)
    })
  }, [])
  const handlePwSubmit = useCallback((pw: string) => {
    pwResolveRef.current?.(pw)
    pwResolveRef.current = null
    setPwPromptTitle('')
  }, [])
  const handlePwCancel = useCallback(() => {
    pwResolveRef.current?.(null)
    pwResolveRef.current = null
    setPwPromptTitle('')
  }, [])

  // 统一重连流程：置会话为连接中 → 终端写入「连接到 ip」信息 →（未存密码时弹窗）→ 建连。
  // 成功：新终端显示「已连接」并关闭旧标签；失败/取消：统一走断开流程收尾
  // （终端写明原因、保持断开标记，重连按钮继续可用，不弹 alert）
  const handleReconnectTerminal = useCallback(async (session_id: string) => {
    const inst = terminals.terminals.get(session_id)
    if (!inst || inst.reconnecting) return

    // 解析重连目标凭据（两类会话统一）：拦截 ssh 命令的会话用 ssh_conn，已保存主机用主机配置
    let target: { kind: 'raw'; conn: SshConnInfo; display: string } | { kind: 'host'; host: Host; display: string } | null = null
    if (inst.ssh_conn?.host) {
      const c = inst.ssh_conn
      target = { kind: 'raw', conn: c, display: `${c.username}@${c.host}` }
    } else {
      const host = hosts.find((h) => h.id === inst.host_id)
      if (host) target = { kind: 'host', host, display: `${host.username}@${host.host}` }
    }
    if (!target) {
      // 无凭据：终端提示原因，保持断开态（与断开流程一致的收尾）
      inst.term.write('\r\n\x1b[31m[重连] 找不到该终端的主机连接信息，无法重连\x1b[0m\r\n')
      return
    }

    // ① 置为连接中（Tab 闪烁 + 终端遮罩）+ 终端显示「连接到 ip:port」信息
    const port = target.kind === 'raw' ? target.conn.port : target.host.port
    terminals.setReconnecting(session_id, true)
    inst.term.write(`\r\n\x1b[33m[重连] 正在连接 ${target.display}:${port} ...\x1b[0m\r\n`)

    try {
      // ③ 密码解析：未保存密码且无私钥 → 弹窗输入（取消则按断开流程收尾）
      const storedPassword = target.kind === 'raw' ? (target.conn.password || '') : (target.host.password || '')
      const hasKey = target.kind === 'host' && !!target.host.private_key
      let password = storedPassword
      let prompted = false
      if (!password && !hasKey) {
        const pw = await askPassword(`连接 ${target.display}`)
        if (pw === null) {
          inst.term.write('\r\n\x1b[33m[重连] 已取消连接\x1b[0m\r\n')
          return
        }
        password = pw
        prompted = true
      }

      // ④ 复用当前终端会话重连：后端 switch_to_ssh 从本机 shell 切回远端，
      //    终端实例不重建、历史/滚动缓冲完整保留；连接结果经 ws 推送 ssh_connected 更新 UI
      const payload = target.kind === 'raw'
        ? { host: target.conn.host, port: target.conn.port, username: target.conn.username, password, private_key: undefined, passphrase: undefined }
        : { host: target.host.host, port: target.host.port, username: target.host.username, password, private_key: target.host.private_key || undefined, passphrase: target.host.passphrase || undefined }
      await api.reconnectSession(session_id, payload)
      // 复用会话重连成功：清除断开/exit 标记并（必要时）重建 WebSocket。
      // already_ssh（会话本就是 SSH）时后端不推 ssh_connected，若实例 ws 已死
      //（意外断开置空）必须重建，否则终端无输出、UI 停在"正在连接"。
      terminals.reconnectWs(session_id)
      // 弹窗输入的密码连接成功后持久化到主机配置（PUT 为整体替换语义，须带完整主机字段）
      if (target.kind === 'host' && prompted && password) {
        api.updateHost(target.host.id, { ...target.host, password }).catch(() => {})
      }
      loadHosts()
      setTimeout(() => loadHosts(), 800)
    } catch (e) {
      // ⑤ 失败统一走断开流程：终端写失败原因、保持断开态（重连按钮可用），不弹 alert
      const msg = (e as Error)?.message || '未知错误'
      inst.term.write(`\r\n\x1b[31m[重连] 连接失败：${msg}\x1b[0m\r\n`)
    } finally {
      terminals.setReconnecting(session_id, false)
    }
  }, [terminals, hosts, loadHosts, askPassword])

  // ---- 主机连接入口（单击复用 / 双击新建）----
  // 新建连接：先建 tab 显示"连接中"，成功后接管显示；失败退到本地终端并写入失败原因
  // （占位 tab 的失败信息由 useTerminals 写入并延迟自动关闭）。连接状态不再进顶栏 status
  const connectHostWithFallback = useCallback((host: Host) => {
    return terminals.createTerminal(host)
      .then(() => {
        loadHosts()
        setTimeout(() => loadHosts(), 800)
      })
      .catch((e) => {
        const failText = `\r\n\x1b[31m[SSH 连接失败] ${host.username}@${host.host}:${host.port} - ${(e as Error)?.message || '未知错误'}\x1b[0m\r\n`
        const local = Array.from(terminals.terminals.values()).find((t) => t.type === 'local' && !t.disconnected)
        if (local) {
          terminals.switchTerminal(local.session_id)
          local.term.write(failText)
        } else {
          terminals.openLocalTerminal(fallbackShell)
            .then((sid) => { setTimeout(() => terminals.getTerminal(sid)?.term.write(failText), 50) })
            .catch(() => alert('SSH连接失败: ' + (e as Error).message))
        }
        loadHosts()
      })
  }, [terminals, loadHosts, fallbackShell])

  // 单击主机：不发起新连接。只在该主机的已连接会话间切换：
  // 当前激活不是该主机 → 选中分组内第一个（= 第一次连接的会话）；
  // 当前激活已是该主机 → 轮换到下一个已连接会话（同 ip:port 不同用户也能区分切换）。
  // 无已连接会话 → 无操作（仅列表选中高亮）；连接请用双击 / 右键菜单。
  const handleHostClick = useCallback((host: Host) => {
    const insts = Array.from(terminals.terminals.values()).filter(
      (t) => t.host_id === host.id && !t.disconnected && !t.pending && !t.reconnecting,
    )
    if (insts.length === 0) return
    const cur = terminals.activeId ? terminals.terminals.get(terminals.activeId) : null
    if (cur && cur.host_id === host.id && insts.some((t) => t.session_id === cur.session_id)) {
      // 当前激活就是该主机：切到分组内下一个（轮换）
      const idx = insts.findIndex((t) => t.session_id === cur.session_id)
      terminals.switchTerminal(insts[(idx + 1) % insts.length].session_id)
    } else {
      // 第一次单击：选中分组内第一个
      terminals.switchTerminal(insts[0].session_id)
    }
  }, [terminals])

  // 单击本地终端条目：在该主机已打开的本地终端会话间切换/轮换（不新建）
  // 当前激活是本地终端 → 轮换到下一个；否则切到第一个（第一次打开的）；无会话则无操作
  const handleLocalClick = useCallback(() => {
    const insts = Array.from(terminals.terminals.values()).filter(
      (t) => t.type === 'local' && !t.disconnected && !t.pending && !t.reconnecting,
    )
    if (insts.length === 0) return
    const cur = terminals.activeId ? terminals.terminals.get(terminals.activeId) : null
    if (cur && cur.type === 'local' && insts.some((t) => t.session_id === cur.session_id)) {
      const idx = insts.findIndex((t) => t.session_id === cur.session_id)
      terminals.switchTerminal(insts[(idx + 1) % insts.length].session_id)
    } else {
      terminals.switchTerminal(insts[0].session_id)
    }
  }, [terminals])

  // 双击主机：新建一条连接（即使该主机已有连接；连接唯一入口之一，右键菜单同）
  const handleHostDoubleClick = useCallback((host: Host) => {
    const insts = Array.from(terminals.terminals.values())
    if (insts.some((t) => t.host_id === host.id && (t.pending || t.reconnecting))) return
    connectHostWithFallback(host)
  }, [terminals, connectHostWithFallback])

  // 顶部 tab 栏「+」：以当前会话的主机另开一条新连接（无会话则提示）
  const handleNewTerminal = useCallback(() => {
    // 分屏焦点是空窗格（activeId=null）时回退：取任一存活会话的主机
    const inst = (terminals.activeId ? terminals.terminals.get(terminals.activeId) : null)
      ?? Array.from(terminals.terminals.values()).find((t) => !t.disconnected)
      ?? null
    if (inst) {
      const host = hosts.find((h) => h.id === inst.host_id)
      if (host) connectHostWithFallback(host)
    } else {
      alert('请先选择一个主机')
    }
  }, [terminals, hosts, connectHostWithFallback])

  const handleSaveHost = useCallback(async (data: Partial<Host>) => {
    try {
      if (editingHost) {
        await api.updateHost(editingHost.id, data)
      } else {
        await api.addHost(data)
      }
      setModalOpen(false)
      setEditingHost(null)
      focusActiveTerminal()
      loadHosts()
    } catch (e) {
      alert('保存失败: ' + (e as Error).message)
    }
  }, [editingHost, loadHosts])

  const handleDeleteHost = useCallback(async (host: Host) => {
    if (!confirm(`确定删除主机「${host.name || host.host}」？`)) return
    try {
      await api.deleteHost(host.id)
      loadHosts()
    } catch (e) {
      alert('删除失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  const handleCopyConnection = useCallback((host: Host) => {
    const cmd = `ssh ${host.username}@${host.host} -p ${host.port}`
    const text = host.password
      ? `${cmd}\npassword: ${host.password}`
      : cmd
    // 统一走 writeClipboardText 三级兜底：pywebview 桥 → navigator.clipboard → execCommand
    // （WebView2 下 navigator.clipboard.writeText 常被安全策略/焦点要求拒绝而静默失败）
    writeClipboardText(text).then((ok) => alert(ok ? '已复制连接信息' : '复制失败：剪贴板不可用'))
  }, [])

  // 复制主机
  const handleDuplicateHost = useCallback(async (host: Host) => {
    try {
      await api.duplicateHost(host.id)
      loadHosts()
    } catch (e) {
      alert('复制失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  // 分组管理
  const [groupManagerOpen, setGroupManagerOpen] = useState(false)

  const handleAddGroup = useCallback(async (name: string) => {
    try {
      await api.addGroup(name)
      loadHosts()
    } catch (e) {
      alert('添加分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  const handleRenameGroup = useCallback(async (oldName: string, newName: string) => {
    try {
      await api.renameGroup(oldName, newName)
      loadHosts()
    } catch (e) {
      alert('重命名分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  const handleDeleteGroup = useCallback(async (name: string) => {
    try {
      await api.deleteGroup(name)
      loadHosts()
    } catch (e) {
      alert('删除分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  // 复制分组（含组内主机）
  const handleDuplicateGroup = useCallback(async (name: string) => {
    try {
      const r = await api.duplicateGroup(name)
      loadHosts()
      alert(`已复制分组"${name}" -> "${r.name}"（含 ${r.copied_hosts.length} 台主机）`)
    } catch (e) {
      alert('复制分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  // 分组/主机拖拽排序后保存
  const handleReorderGroups = useCallback(async (names: string[]) => {
    try {
      await api.reorderGroups(names)
      loadHosts()
    } catch (e) {
      console.error('保存分组排序失败', e)
    }
  }, [loadHosts])

  const handleReorderHosts = useCallback(async (ids: string[]) => {
    try {
      await api.reorderHosts(ids)
    } catch (e) {
      console.error('保存主机排序失败', e)
    }
  }, [])

  const handleSendQuickCommand = useCallback((qc: QuickCommand) => {
    executeQuickCommand(qc, null)
  }, [terminals])


  // 依次执行预操作（上传 → chmod → env），返回是否全部成功；
  // direct 直接执行 与 param/编辑后执行 共用：先完成预操作再发送命令。
  // session_id 显式传入（默认当前活动终端），供非活动会话定向执行
  const runPreOps = useCallback(async (qc: QuickCommand, session_id?: string): Promise<boolean> => {
    const sid = session_id ?? terminals.activeId
    if (!sid) return false
    const inst = terminals.terminals.get(sid)
    if (!inst || !inst.ws || inst.ws.readyState !== WebSocket.OPEN) return false
    for (const op of qc.pre_ops || []) {
      if (op.type === 'upload') {
        const remote = (op.remote || '').trim()
        const source = (op.source || '').trim()
        const sourceType = op.source_type === 'path' ? 'path' : 'script'
        if (!remote || !source) continue
        setStatus(`上传中 ${source} -> ${remote}...`)
        try {
          await api.preopUpload(sid, source, sourceType, remote)
          setStatus(`已上传 ${source} -> ${remote}`)
        } catch (e) {
          setStatus('上传失败，命令未执行')
          alert('预操作失败（上传文件）: ' + (e as Error).message)
          return false
        }
      } else if (op.type === 'chmod') {
        if (!op.mode || !op.path) continue
        terminals.sendCommandTo(sid, `chmod ${op.mode} ${op.path}`, true)
      } else if (op.type === 'exec') {
        // 先执行的命令：自由 shell 语句（export/cd/任意命令），逐条发送
        const cmd = (op.cmd || '').trim()
        if (!cmd) continue
        terminals.sendCommandTo(sid, cmd, true)
      }
    }
    return true
  }, [terminals, setStatus])


  // 执行快捷指令（含预操作流水线：上传文件 → chmod → 先执行命令 → 命令本体）。
  // session_id 显式传入（默认当前活动终端）
  const executeQuickCommand = useCallback(async (qc: QuickCommand, param: string | null, session_id?: string) => {
    if (!(await runPreOps(qc, session_id))) return

    // 参数替换：{args} 占位符替换，无占位符则追加到末尾
    let cmd = qc.command
    if (param !== null) {
      cmd = cmd.includes('{args}')
        ? cmd.split('{args}').join(param)
        : (cmd + ' ' + param).trim()
    }

    terminals.sendCommandTo(session_id ?? terminals.activeId ?? '', cmd, true)
    setStatus(`已执行: ${cmd.slice(0, 60)}`)
  }, [runPreOps, terminals, setStatus])

  // 检查配置/脚本更新：扫描 config/data/scripts 后刷新主机与快捷指令列表
  const handleReloadConfig = useCallback(async () => {
    setStatus('正在扫描配置与脚本...')
    try {
      const res = await api.reloadConfig()
      await Promise.all([loadHosts(), loadQuickCommands()])
      const scripts = (res.scripts as string[]) || []
      setStatus(`配置已刷新：主机 ${(res.data as any)?.hosts ?? '?'} 台 · 快捷指令 ${(res.data as any)?.quick_commands ?? '?'} 条 · scripts ${scripts.length} 个`)
    } catch (e) {
      setStatus('刷新配置失败')
      alert('刷新配置失败: ' + (e as Error).message)
    }
  }, [loadHosts, loadQuickCommands])

  // 编辑后执行：先执行预操作（上传/chmod/env），再把命令输入到终端不执行，用户可编辑 {args} 后手动执行
  const handleEditExecuteQuickCommand = useCallback(async (qc: QuickCommand) => {
    if (!(await runPreOps(qc))) return
    terminals.sendCommand(qc.command, false)
  }, [runPreOps, terminals])

  // 打开编辑快捷指令弹窗
  const handleEditQuickCommand = useCallback((qc: QuickCommand) => {
    setEditingQuickCommand(qc)
    setQuickCommandModalOpen(true)
  }, [])

  // 更新快捷指令
  const handleUpdateQuickCommand = useCallback(async (id: string, data: Partial<QuickCommand>) => {
    try {
      await api.updateQuickCommand(id, data)
      loadQuickCommands()
    } catch (e) {
      alert('更新失败: ' + (e as Error).message)
    }
  }, [loadQuickCommands])

  const handleAddQuickCommand = useCallback(async (data: Partial<QuickCommand>) => {
    try {
      await api.addQuickCommand(data)
      loadQuickCommands()
    } catch (e) {
      alert('添加失败: ' + (e as Error).message)
    }
  }, [loadQuickCommands])

  const handleDeleteQuickCommand = useCallback(async (id: string) => {
    if (!confirm('确定删除这个快捷指令吗？')) return
    try {
      await api.deleteQuickCommand(id)
      loadQuickCommands()
    } catch (e) {
      alert('删除失败: ' + (e as Error).message)
    }
  }, [loadQuickCommands])

  // 拖拽排序：本地先更新（拖拽立即生效），再保存到后端
  const handleReorderQuickCommands = useCallback(async (ids: string[]) => {
    const orderMap = new Map(quickCommands.map((q) => [q.id, q]))
    const next = ids.map((id) => orderMap.get(id)!).filter(Boolean)
    setQuickCommands(next)
    try {
      await api.reorderQuickCommands(ids)
    } catch (e) {
      alert('排序保存失败: ' + (e as Error).message)
      loadQuickCommands()
    }
  }, [quickCommands, loadQuickCommands])

  const sftpTabBlocked = (() => {
    const inst = terminals.activeId ? terminals.terminals.get(terminals.activeId) : null
    // 本机终端/未连接会话没有 SFTP 可言：tab 直接禁用（type 是业务分类不可靠，看 shell_type）
    return !inst || inst.shell_type === 'local' || inst.disconnected
  })()

  const renderPanelContent = () => {
    switch (panelTab) {
      case 'sessions':
        // 会话列表并入工具面板 tab：按主机组聚合，点击切换、支持拖拽排序
        return (
          <SessionPanel
            terminals={terminals.terminals}
            hosts={hosts}
            activeId={terminals.activeId}
            customOrder={customOrder}
            onOrderChange={setCustomOrder}
            onSwitch={handleSwitchTerminal}
            onClose={handleCloseTerminal}
          />
        )
      case 'quick':
        return (
          <QuickCommands
            commands={quickCommands}
            onExecute={handleSendQuickCommand}
            onEditExecute={handleEditExecuteQuickCommand}
            onEdit={handleEditQuickCommand}
            onDelete={handleDeleteQuickCommand}
            onOpenAddModal={() => { setEditingQuickCommand(null); setQuickCommandModalOpen(true) }}
            onReorder={handleReorderQuickCommands}
            disabled={!terminals.activeId}
          />
        )
      case 'sftp': {
        const sftpInst = terminals.activeId ? terminals.terminals.get(terminals.activeId) : null
        // 仅 SSH/远程会话可用 SFTP：本地终端、断开会话一律显示"请先连接 SSH"
        const sftpSid = sftpInst && sftpInst.shell_type !== 'local' && !sftpInst.disconnected ? terminals.activeId : null
        return <SftpPanel sessionId={sftpSid} />
      }
      case 'transfers':
        // 全部会话的上传/下载任务（进度/速度/取消 + 历史）
        return <TransferList />
    }
  }

  return (
    <div className="app">
      {/* 顶部栏 */}
      <div className="topbar">
        <button className="btn btn-secondary btn-sm" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} title="显示/折叠主机列表（Ctrl+B）">☰</button>
        <span className="logo">SSH Web Tool</span>
        {terminals.activeId && (() => {
          const activeInst = terminals.terminals.get(terminals.activeId!)
          // 占位连接中的 tab：无连接可断开，不显示操作按钮（状态在 tab/终端遮罩内呈现）
          if (activeInst?.pending) {
            return null
          }
          // 断开或 SSH exit 后自动切换本地终端：显示重连按钮（重连到原 SSH 主机）
          if (activeInst?.disconnected || activeInst?.ssh_exited) {
            return (
              <button
                className="btn btn-primary btn-sm conn-action-btn"
                onClick={() => handleReconnectTerminal(terminals.activeId!)}
                title={activeInst.reconnecting ? '正在重新连接…' : '重新连接当前主机'}
                disabled={activeInst.reconnecting}
              >{activeInst.reconnecting ? '⏳ 连接中…' : '🔗 重连'}</button>
            )
          }
          // 本地终端不显示断开按钮（本地终端没有"断开"的概念）
          if (activeInst?.type === 'local') {
            return null
          }
          return (
            <button
              className="btn btn-secondary btn-sm conn-action-btn"
              onClick={() => handleDisconnectTerminal(terminals.activeId!)}
              title="断开当前终端连接（保留标签，可重新连接）"
            >⛓️‍💥 断开</button>
          )
        })()}
        {/* 分屏：左右/上下固定均分两窗格；再次点击当前模式按钮退出 */}
        <div className="topbar-divider" />
        <button
          className={`btn btn-sm ${splitMode === 'h' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => (splitMode === 'h' ? exitSplit() : enterSplit('h'))}
          title="左右分屏：两个窗口各带标签栏，标签可拖拽跨窗口（再次点击退出）"
          style={{ whiteSpace: 'nowrap' }}
        >◫ 左右</button>
        <button
          className={`btn btn-sm ${splitMode === 'v' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => (splitMode === 'v' ? exitSplit() : enterSplit('v'))}
          title="上下分屏：两个窗口各带标签栏，标签可拖拽跨窗口（再次点击退出）"
          style={{ whiteSpace: 'nowrap' }}
        >⬒ 上下</button>
        <div style={{ flex: 1 }} />
        {/* 终端设置 */}
        <div className="topbar-divider" />
        <div className="topbar-settings">
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={() => setFontSize(settings.fontSize - 1)}
            title="减小字体"
            disabled={settings.fontSize <= 8}
          >
            A-
          </button>
          <span className="settings-font-size" title="当前字体大小">{settings.fontSize}px</span>
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={() => setFontSize(settings.fontSize + 1)}
            title="增大字体"
            disabled={settings.fontSize >= 32}
          >
            A+
          </button>
          <select
            className="settings-font-select"
            value={settings.fontFamily}
            onChange={(e) => setFontFamily(e.target.value)}
            title="选择终端字体"
          >
            {FONT_OPTIONS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={toggleTheme}
            title={settings.theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
          >
            {settings.theme === 'dark' ? '☀️' : '🌙'}
          </button>
        </div>
        <div className="topbar-divider" />
        <button
          className={`btn btn-sm ${rightMode === 'tools' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setRightMode((m) => (m === 'tools' ? 'none' : 'tools'))}
          title="显示/折叠右侧工具面板（会话/快速指令/SFTP/传输）"
        >📋 面板</button>
        <button
          className={`btn btn-sm ${editorOpen ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => { setEditorOpen(!editorOpen); if (editorOpen) focusActiveTerminal() }}
          title="文本编辑器：查看/编辑本地文件（日志、.zs 脚本），支持关键字高亮"
        >
          📝 编辑器
        </button>
        {/* SFTP 双窗工作台：本地/远程双栏 Xftp 风格，拖拽跨窗传输（需已连接 SSH 会话） */}
        <button
          className="btn btn-secondary btn-sm"
          onClick={async () => {
            const inst = terminals.activeId ? terminals.terminals.get(terminals.activeId) : null
            // type 是主机业务分类（web/db/other…）不可靠；shell_type='local' 才是本地终端
            if (inst && inst.shell_type === 'local') {
              alert('本机终端会话不支持 SFTP，请连接到远程主机后再打开 SFTP 工作台')
              return
            }
            if (!inst || inst.disconnected) {
              alert('请先连接一个 SSH 主机，再打开 SFTP 工作台')
              return
            }
            // 打开时定位到当前终端所在目录（cd 跟踪；失败/未知回退默认目录）
            let cwd: string | null = null
            try {
              const r = await api.getSessionCwd(terminals.activeId!)
              cwd = r.cwd
            } catch { /* 忽略，走默认目录 */ }
            setSftpInitialRemote(cwd)
            setSftpWbOpen(true)
          }}
          title="SFTP 双窗工作台：本地/远程双栏浏览，拖拽文件上传/下载"
        >
          🗂 SFTP
        </button>
        {/* 应用级入口（设置）与工具开关分组隔开 */}
        <div className="topbar-divider" />
        {/* 检查配置入口已移入设置页「本机终端」分区 */}
        {/* 设置入口：顶栏最右上角，打开整页设置 */}
        <button
          className="btn btn-secondary btn-sm settings-btn"
          onClick={() => setSettingsOpen(true)}
          title="设置（本机终端 / 命令块 / 关键字高亮 / 缓存清理等）"
        >
          ⚙️
        </button>
      </div>

      {/* 主体 */}
      <div className="main">
        {/* 左侧主机列表（宽度可拖拽调整） */}
        <div
          className={`sidebar${sidebarCollapsed ? ' collapsed' : ''}`}
          style={{ width: sidebarWidth, marginLeft: sidebarCollapsed ? -sidebarWidth : 0 }}
        >
          {!sidebarCollapsed && <div className="resizer sidebar-resizer" onMouseDown={startResize('sidebar')} title="拖拽调整宽度" />}
          <div className="sidebar-header">
            <span className="title">主机列表</span>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => { setEditingHost(null); setModalOpen(true) }}
              title="新建主机"
            >＋</button>
            <button className="btn btn-secondary btn-sm" onClick={loadHosts} title="刷新主机列表">🔄</button>
          </div>
          <HostList
            hosts={hosts}
            groups={groups}
            activeHostId={activeHostId}
            defaultShell={fallbackShell}
            onOpenDefaultTerminal={() => {
              terminals.openLocalTerminal(fallbackShell)
                .then(() => { setTimeout(() => loadHosts(), 500) })
                .catch((e) => alert('本机终端打开失败: ' + (e as Error).message))
            }}
            onLocalClick={handleLocalClick}
            onHostClick={handleHostClick}
            onHostDoubleClick={handleHostDoubleClick}
            onEdit={(h) => { setEditingHost(h); setModalOpen(true) }}
            onDelete={handleDeleteHost}
            onCopy={handleCopyConnection}
            onDuplicate={handleDuplicateHost}
            onManageGroups={() => setGroupManagerOpen(true)}
            onReorderHosts={handleReorderHosts}
          />
        </div>

        {/* 中间终端区域（窗口模型：每个窗口 = 自己的 tab 栏 + 终端区，tab 可跨窗口拖拽） */}
        <div className="terminal-area">
          <TerminalView
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            splitMode={splitMode}
            windowSessions={windowSessions}
            windowActive={windowActive}
            focusedPane={focusedPane}
            hosts={hosts}
            order={sortedSessionIds}
            hostTypes={hostTypes}
            registerContainer={terminals.registerContainer}
            onSwitch={handleSwitchTerminal}
            onClose={handleCloseTerminal}
            onNew={handleNewTerminal}
            onMoveTabToWindow={handleMoveTabToWindow}
            onFocusWindow={handleFocusWindow}
            onTerminalContextMenu={handleTerminalContextMenu}
            onPaneClick={(idx) => setFocusedPane(idx)}
          />
          {/* 终端内容搜索条（Ctrl+F）：浮在活动终端右上角；切换活动终端时跟随显示对应实例 */}
          {terminals.searchOpenId && (() => {
            const inst = terminals.terminals.get(terminals.searchOpenId)
            return inst ? (
              <SearchBar
                key={inst.session_id}
                instance={inst}
                onClose={() => terminals.setSearchOpenId(null)}
              />
            ) : null
          })()}
          {/* 浮动消息提示堆叠（fixed 定位脱离终端区，SFTP 工作台等全屏覆盖层之上也可见） */}
          {toasts.length > 0 && (
            <div className="toast-stack">
              {toasts.map((t) => (
                <div key={t.id} className="toast-item">{t.msg}</div>
              ))}
            </div>
          )}
          {/* 编辑器覆盖层：盖住终端区（终端保持挂载，切回时无需重建 xterm） */}
          {editorOpen && (
            <div className="editor-overlay">
              <FileEditor
                fontFamily={settings.fontFamily}
                fontSize={settings.fontSize}
                highlightRules={settings.highlightRules}
                quickCommands={quickCommands}
                sessions={editorSessions}
                sendToSession={(sid, cmd, exec) => terminals.sendCommandTo(sid, cmd, exec, false)}
                readBufferTail={terminals.readBufferTail}
                recentPaths={settings.editorRecentPaths}
                onUpdateRecentPaths={(paths) => updateSettings({ editorRecentPaths: paths })}
                onStatus={setStatus}
                onClose={() => { setEditorOpen(false); focusActiveTerminal() }}
              />
            </div>
          )}
        </div>

        {/* 右侧工具面板（宽度可拖拽调整；显示/关闭走顶栏「📋 面板」按钮）
            标题栏已去除：tab 栏（会话/快速指令/SFTP/传输）本身即导航，避免与标题重复 */}
        {rightMode === 'tools' && (
          <div className="panel" style={{ width: panelWidth }}>
            <div className="resizer panel-resizer" onMouseDown={startResize('panel')} title="拖拽调整宽度" />
            <div className="panel-tabs">
              <div className={`panel-tab${panelTab === 'sessions' ? ' active' : ''}`} onClick={() => setPanelTab('sessions')}>🗂 会话</div>
              <div className={`panel-tab${panelTab === 'quick' ? ' active' : ''}`} onClick={() => setPanelTab('quick')}>⚡ 快速指令</div>
              <div
                className={`panel-tab${panelTab === 'sftp' ? ' active' : ''}${sftpTabBlocked ? ' panel-tab-disabled' : ''}`}
                onClick={() => { if (!sftpTabBlocked) setPanelTab('sftp') }}
                title={sftpTabBlocked ? '本机终端不支持 SFTP，请先连接到远程主机' : '远程文件管理（SFTP）'}
              >📁 SFTP</div>
              <div className={`panel-tab${panelTab === 'transfers' ? ' active' : ''}`} onClick={() => setPanelTab('transfers')}>⇅ 传输</div>
            </div>
            {/* 会话 tab 内容自带 padding/滚动，外层去掉内边距避免双重留白 */}
            <div className={`panel-body${panelTab === 'sessions' ? ' panel-body-flush' : ''}`}>{renderPanelContent()}</div>
          </div>
        )}
      </div>

      {/* 主机弹窗 */}
      <HostModal
        open={modalOpen}
        host={editingHost}
        groups={groups}
        hostTypes={hostTypes}
        onClose={() => { setModalOpen(false); setEditingHost(null); focusActiveTerminal() }}
        onSave={handleSaveHost}
        onAddGroup={handleAddGroup}
      />

      {/* 分组管理弹窗 */}
      <GroupManager
        open={groupManagerOpen}
        groups={groups}
        onClose={() => { setGroupManagerOpen(false); focusActiveTerminal() }}
        onAdd={handleAddGroup}
        onRename={handleRenameGroup}
        onDelete={handleDeleteGroup}
        onDuplicate={handleDuplicateGroup}
        onReorder={handleReorderGroups}
      />

      {/* 历史搜索弹窗 */}
      {historySearchOpen && (
        <HistorySearchModal
          onClose={() => { setHistorySearchOpen(false); focusActiveTerminal() }}
          onExecuteQuick={handleSendQuickCommand}          // 快捷指令(direct)：与面板点击同一链路（含预操作）
          onEditExecuteQuick={handleEditExecuteQuickCommand}  // 快捷指令(param)：预操作 + 输入终端
          onInputToTerminal={(cmd) => terminals.sendCommand(cmd, false)}
          onRefreshQuickCommands={loadQuickCommands}
        />
      )}

      {/* 添加/编辑快捷指令弹窗 */}
      {quickCommandModalOpen && (
        <QuickCommandModal
          onClose={() => { setQuickCommandModalOpen(false); setEditingQuickCommand(null); focusActiveTerminal() }}
          onAdd={handleAddQuickCommand}
          onUpdate={handleUpdateQuickCommand}
          editing={editingQuickCommand}
        />
      )}


      {/* 重连密码弹窗：未保存密码且无私钥时，统一重连流程先取密码再建连 */}
      {pwPromptTitle && (
        <PasswordModal title={pwPromptTitle} onSubmit={handlePwSubmit} onCancel={handlePwCancel} />
      )}

      {/* SFTP 双窗工作台（Xftp 风格）：本地/远程双栏 + 拖拽传输，跟随当前活跃 SSH 会话 */}
      {sftpWbOpen && terminals.activeId && (
        <SftpWorkbench
          sessionId={terminals.activeId}
          initialRemote={sftpInitialRemote}
          onNotify={setStatus}
          onClose={() => { setSftpInitialRemote(null); setSftpWbOpen(false) }}
        />
      )}

      {/* 开启日志记录的目录选择弹窗：未勾选「不再询问」时右键「开始记录日志」先选目录 */}
      {recordDirModal && (
        <DirPickerModal
          title="选择日志保存目录"
          initialDir={settings.logRecordDir}
          onConfirm={(dir) => {
            setRecordDirModal(null)
            startRecording(recordDirModal.sessionId, dir)
          }}
          onCancel={() => setRecordDirModal(null)}
        />
      )}

      {/* 设置页（整页覆盖）：本机终端 / 命令块 / 字体主题 / 关键字高亮 / 缓存清理 */}
      {settingsOpen && (
        <SettingsModal
          settings={settings}
          onUpdate={updateSettings}
          fallbackShell={fallbackShell}
          shellChoices={shellChoices}
          onSetFallbackShell={handleSetFallbackShell}
          connectTimeout={connectTimeout}
          onSetConnectTimeout={handleSetConnectTimeout}
          configFile={configFile}
          onReloadConfig={handleReloadConfig}
          events={events.events}
          onClearEvents={events.clearEvents}
          onClose={() => { setSettingsOpen(false); focusActiveTerminal() }}
        />
      )}

      {/* 终端窗口右键菜单：块操作（右键命中色条时）+ 复制/粘贴/全选/搜索，与主机列表菜单各自独立 */}
      {terminalMenu && (() => {
        const inst = terminals.terminals.get(terminalMenu.sessionId)
        if (!inst) return null
        const bb = inst.blockBar
        const sel = bb?.getSelection() ?? []
        // 右键命中的块已在多选集合中 → 操作整组；否则只操作命中块
        const ids = terminalMenu.blockId != null && sel.length > 1 && sel.includes(terminalMenu.blockId)
          ? sel
          : terminalMenu.blockId != null ? [terminalMenu.blockId] : []
        const sections: CtxMenuItem[][] = []
        if (bb && ids.length > 0) {
          const nTag = ids.length > 1 ? `（${ids.length} 块）` : ''
          sections.push([
            { label: bb.isFolded(ids[0]) ? `展开块${nTag}` : `折叠块${nTag}`, onClick: () => ids.forEach((id) => bb.toggleFold(id)) },
            { label: `复制块内容${nTag}`, onClick: () => bb.copyBlocks(ids) },
          ])
        }
        sections.push([
          {
            label: terminalRecording === false ? '开始记录日志' : '暂停记录日志',
            onClick: () => {
              const recording = terminalRecording !== false // 加载中未知状态时按"正在记录"处理
              if (recording) {
                // 暂停：丢弃未落盘缓冲，已落盘内容保留
                api.setSessionRecord(terminalMenu.sessionId, false)
                  .then(() => {
                    setTerminalRecording(false)
                    setStatus('已暂停本会话日志记录（已落盘内容保留）')
                  })
                  .catch((e) => setStatus('设置日志记录失败: ' + (e as Error).message))
                return
              }
              // 开始记录：勾选「不再询问」直接用默认目录，否则先弹目录选择
              if (settings.logRecordNoAsk) {
                startRecording(terminalMenu.sessionId, settings.logRecordDir)
              } else {
                setRecordDirModal({ sessionId: terminalMenu.sessionId })
              }
            },
          },
        ])
        sections.push([
          {
            label: '复制',
            disabled: !inst.term.hasSelection(),
            onClick: () => { const text = inst.term.getSelection(); if (text) writeClipboardText(text) },
          },
          { label: '粘贴', onClick: () => pasteToTerminal(inst) },
          { label: '全选', onClick: () => inst.term.selectAll() },
          { label: '搜索内容', shortcut: 'Ctrl+F', onClick: () => terminals.setSearchOpenId(terminalMenu.sessionId) },
        ])
        return (
          <ContextMenu
            x={terminalMenu.x}
            y={terminalMenu.y}
            sections={sections}
            onClose={() => { setTerminalMenu(null); focusActiveTerminal() }}
          />
        )
      })()}

      {/* 拖拽提示 */}
      <div id="dropHint">释放文件以上传到当前 SFTP 目录</div>
    </div>
  )
}

export default App
