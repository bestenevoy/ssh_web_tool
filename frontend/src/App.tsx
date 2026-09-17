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
import { TerminalTabs } from './components/TerminalTabs'
import { TerminalView } from './components/TerminalView'
import type { SplitMode, PaneIndex } from './components/TerminalView'
import { SessionPanel } from './components/SessionPanel'
import { QuickCommands } from './components/QuickCommands'
import { SftpPanel } from './components/SftpPanel'
import { HostModal } from './components/HostModal'
import { QuickCommandModal } from './components/QuickCommandModal'
import { PasswordModal } from './components/PasswordModal'
import { SettingsModal } from './components/SettingsModal'
import { DirPickerModal } from './components/DirPickerModal'
import { SearchBar } from './components/SearchBar'
import { FileEditor } from './components/FileEditor'
import HistorySearchModal from './components/HistorySearchModal'

type PanelTab = 'quick' | 'sftp'

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
  const [panelCollapsed, setPanelCollapsed] = useState(false)
  // 分屏：none 单屏 / h 左右均分 / v 上下均分（固定 2 窗格）；
  // panes 为各窗格显示的会话 id（null=空窗格占位），activeId 始终等于焦点窗格的会话
  const [splitMode, setSplitMode] = useState<SplitMode>('none')
  const [panes, setPanes] = useState<[string | null, string | null]>([null, null])
  const [focusedPane, setFocusedPane] = useState<PaneIndex>(0)
  // 右侧会话列表面板（按主机 ip 聚合分组，与顶部标签栏并存）
  const [sessionPanelCollapsed, setSessionPanelCollapsed] = useState(false)
  // 布局宽度（侧栏/右面板可拖拽调宽，localStorage 持久化；终端是核心区，宽度自适应）
  const [sidebarWidth, setSidebarWidth] = useState(() => loadLayoutWidth('sidebar', 240, 180, 440))
  const [panelWidth, setPanelWidth] = useState(() => loadLayoutWidth('panel', 320, 260, 600))
  const [sessionPanelWidth, setSessionPanelWidth] = useState(() => loadLayoutWidth('sessionPanel', 240, 200, 480))
  const [panelTab, setPanelTab] = useState<PanelTab>('quick')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingHost, setEditingHost] = useState<Host | null>(null)
  const [status, setStatus] = useState('就绪')
  const [historySearchOpen, setHistorySearchOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 编辑器页：打开后替换中间终端区（终端实例保持挂载，仅隐藏容器）
  const [editorOpen, setEditorOpen] = useState(false)
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

  // 拖拽调宽：sidebar 向右拖变宽，panel/sessionPanel 向左拖变宽；松开时持久化
  const startResize = useCallback((side: 'sidebar' | 'panel' | 'sessionPanel') => (e: React.MouseEvent) => {
    e.preventDefault()
    const isSidebar = side === 'sidebar'
    const isSession = side === 'sessionPanel'
    const startX = e.clientX
    const startW = isSidebar ? sidebarWidth : isSession ? sessionPanelWidth : panelWidth
    const min = isSidebar ? 180 : isSession ? 200 : 260
    const max = isSidebar ? 440 : isSession ? 480 : 600
    const dir = isSidebar ? 1 : -1
    let last = startW
    const onMove = (ev: MouseEvent) => {
      last = Math.min(max, Math.max(min, startW + dir * (ev.clientX - startX)))
      if (isSidebar) setSidebarWidth(last)
      else if (isSession) setSessionPanelWidth(last)
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
  }, [sidebarWidth, panelWidth, sessionPanelWidth])

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

  const handleHostClick = useCallback((host: Host) => {
    // 连接防抖：已有连接正在建立时忽略新点击，避免并发连接
    if (terminals.connecting) {
      setStatus('正在连接其他主机，请稍候...')
      return
    }
    const existing = Array.from(terminals.terminals.values()).find((t) => t.host_id === host.id && !t.disconnected)
    if (existing) {
      terminals.switchTerminal(existing.session_id)
      setStatus(`已连接 ${host.host} [${existing.session_id}]`)
    } else {
      setStatus(`正在连接 ${host.host}...`)
      terminals.createTerminal(host)
        .then(() => {
          setStatus(`已连接 ${host.host}`)
          // 立即刷新 + 延迟刷新（确保 WebSocket 和 shell 启动完成，状态完全同步）
          loadHosts()
          setTimeout(() => loadHosts(), 800)
        })
        .catch((e) => {
          if ((e as any)?.isConnecting) {
            setStatus('正在连接其他主机，请稍候...')
            return
          }
          setStatus('连接失败')
          alert('SSH连接失败: ' + (e as Error).message)
        })
    }
  }, [terminals, loadHosts])

  const handleNewTerminal = useCallback(() => {
    // 分屏焦点是空窗格（activeId=null）时回退：取任一存活会话的主机
    const inst = (terminals.activeId ? terminals.terminals.get(terminals.activeId) : null)
      ?? Array.from(terminals.terminals.values()).find((t) => !t.disconnected)
      ?? null
    if (inst) {
      const host = hosts.find((h) => h.id === inst.host_id)
      if (host) {
        terminals.createTerminal(host)
          .then(() => {
            loadHosts()
            setTimeout(() => loadHosts(), 800)
          })
          .catch((e) => alert('创建终端失败: ' + (e as Error).message))
      }
    } else {
      alert('请先选择一个主机')
    }
  }, [terminals, hosts, loadHosts])

  // 分屏窗格中的会话集合（顶部 tab 分屏标记用）
  const splitSessions = useMemo(() => new Set(panes.filter((id): id is string => !!id)), [panes])

  // 分屏模式下的会话切换（顶部 tab / 右侧会话列表共用）：
  // 目标已在某窗格 → 聚焦该窗格；否则放进焦点窗格显示。单屏 = 普通切换
  const handleSwitchTerminal = useCallback((id: string) => {
    if (splitMode === 'none') {
      terminals.switchTerminal(id)
      return
    }
    const idx = panes.indexOf(id)
    if (idx >= 0) {
      setFocusedPane(idx as PaneIndex)
      return
    }
    setPanes((prev) => {
      const next: [string | null, string | null] = [prev[0], prev[1]]
      next[focusedPane] = id
      return next
    })
  }, [splitMode, panes, focusedPane, terminals])

  // 进入分屏：窗格0 = 当前会话，窗格1 = 另一个会话（无则空窗格占位，之后点标签填入）
  const enterSplit = useCallback((mode: 'h' | 'v') => {
    if (terminals.terminals.size === 0) { setStatus('请先打开一个终端会话'); return }
    if (window.innerWidth < 800) { setStatus('窗口太窄，无法分屏'); return }
    const ids = Array.from(terminals.terminals.keys())
    const first = terminals.activeId && ids.includes(terminals.activeId) ? terminals.activeId : ids[0]
    const second = ids.find((id) => id !== first) ?? null
    setSplitMode(mode)
    setPanes([first, second])
    setFocusedPane(0)
    if (terminals.activeId !== first) terminals.switchTerminal(first)
  }, [terminals])

  // 退出分屏：回到单屏，显示退出前焦点窗格的会话
  const exitSplit = useCallback(() => {
    const sid = panes[focusedPane] ?? panes[0] ?? panes[1]
    setSplitMode('none')
    setPanes([null, null])
    setFocusedPane(0)
    if (sid && terminals.terminals.has(sid)) terminals.switchTerminal(sid)
  }, [panes, focusedPane, terminals])

  // 分屏状态自愈（每次渲染收敛检查，守卫保证只跑一次有效分支）：
  // ① 清理窗格里已被关闭的会话；② 两窗格全空则退出分屏；
  // ③ activeId 指向的新会话（新建/重连/自动恢复）进入焦点窗格；
  // ④ 焦点窗格会话与 activeId 不一致时同步（点击窗格/空占位切换焦点的收尾）
  useEffect(() => {
    if (splitMode === 'none') return
    const liveOf = (sid: string | null): string | null =>
      sid && terminals.terminals.has(sid) ? sid : null
    const l0 = liveOf(panes[0])
    const l1 = liveOf(panes[1])
    if (l0 !== panes[0] || l1 !== panes[1]) { setPanes([l0, l1]); return }
    if (!l0 && !l1) { setSplitMode('none'); return }
    const aid = terminals.activeId
    if (aid && aid !== l0 && aid !== l1) {
      setPanes((prev) => {
        const next: [string | null, string | null] = [prev[0], prev[1]]
        next[focusedPane] = aid
        return next
      })
      return
    }
    const focusedLive = focusedPane === 0 ? l0 : l1
    if (focusedLive !== aid) {
      if (focusedLive) terminals.switchTerminal(focusedLive)
      else terminals.setActiveId(null)
    }
  }, [splitMode, focusedPane, panes, terminals])

  // 分屏布局变化后重新 fit 可见窗格（等 grid 生效后的下一帧再量尺寸）
  const refitSplit = terminals.refitTerminals
  useEffect(() => {
    if (splitMode === 'none') return
    const ids = panes.filter((id): id is string => !!id)
    const raf = requestAnimationFrame(() => refitSplit(ids))
    return () => cancelAnimationFrame(raf)
  }, [splitMode, panes, refitSplit])

  // 分屏模式下窗口尺寸变化：两个窗格都要重新 fit（单屏场景由 useTerminals 内部处理 activeId）
  useEffect(() => {
    if (splitMode === 'none') return
    const handler = () => refitSplit(panes.filter((id): id is string => !!id))
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [splitMode, panes, refitSplit])

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
      pwResolveRef.current?.(null)  // 理论不会并发：防抖已保证单连接流程
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
    if (terminals.connecting) { setStatus('正在连接其他主机，请稍候...'); return }
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
      setStatus('重新连接失败：找不到主机连接信息')
      return
    }

    // ① 置为连接中（Tab 闪烁 + 终端遮罩）+ 终端显示「连接到 ip:port」信息
    const port = target.kind === 'raw' ? target.conn.port : target.host.port
    terminals.setReconnecting(session_id, true)
    inst.term.write(`\r\n\x1b[33m[重连] 正在连接 ${target.display}:${port} ...\x1b[0m\r\n`)
    setStatus(`正在重新连接 ${target.display}...`)

    try {
      // ② 读取旧会话完整历史（重连不清空：断开时后端会话已删，须走 /api/logs 按文件读）
      let oldHistory = ''
      try {
        const res = await api.getLogsByFile(inst.session_id)
        oldHistory = res?.content || ''
      } catch { /* 旧会话无历史（如本地 shell）则跳过 */ }

      // ③ 密码解析：未保存密码且无私钥 → 弹窗输入（取消则按断开流程收尾）
      const storedPassword = target.kind === 'raw' ? (target.conn.password || '') : (target.host.password || '')
      const hasKey = target.kind === 'host' && !!target.host.private_key
      let password = storedPassword
      let prompted = false
      if (!password && !hasKey) {
        const pw = await askPassword(`连接 ${target.display}`)
        if (pw === null) {
          inst.term.write('\r\n\x1b[33m[重连] 已取消连接\x1b[0m\r\n')
          setStatus('已取消重连')
          return
        }
        password = pw
        prompted = true
      }

      // ④ 建连：成功后新终端显示「已连接」（ws.onopen 写入），随后关闭旧标签
      const successMsg = `已连接 ${target.display}`
      if (target.kind === 'raw') {
        // 弹窗输入的密码随新实例 ssh_conn 记忆，后续重连不再询问
        await terminals.createTerminal(null, inst.terminal_name, 'ssh', 'cmd', oldHistory, { ...target.conn, password }, undefined, successMsg)
      } else {
        await terminals.createTerminal(target.host, inst.terminal_name, 'ssh', 'cmd', oldHistory, undefined, password || undefined, successMsg)
      }
      // 弹窗输入的密码连接成功后持久化到主机配置（PUT 为整体替换语义，须带完整主机字段）
      if (target.kind === 'host' && prompted && password) {
        api.updateHost(target.host.id, { ...target.host, password }).catch(() => {})
      }
      await terminals.closeTerminal(session_id, true)
      loadHosts()
      setTimeout(() => loadHosts(), 800)
    } catch (e) {
      if ((e as any)?.isConnecting) {
        inst.term.write('\r\n\x1b[33m[重连] 已有连接正在建立，请稍候再试\x1b[0m\r\n')
        setStatus('正在连接其他主机，请稍候...')
        return
      }
      // ⑤ 失败统一走断开流程：终端写失败原因、保持断开态（重连按钮可用），不弹 alert
      const msg = (e as Error)?.message || '未知错误'
      inst.term.write(`\r\n\x1b[31m[重连] 连接失败：${msg}\x1b[0m\r\n`)
      setStatus(`重新连接失败：${msg}`)
    } finally {
      terminals.setReconnecting(session_id, false)
    }
  }, [terminals, hosts, loadHosts, askPassword])

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

  const renderPanelContent = () => {
    switch (panelTab) {
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
      case 'sftp':
        return <SftpPanel sessionId={terminals.activeId} />
    }
  }

  return (
    <div className="app">
      {/* 顶部栏 */}
      <div className="topbar">
        <button className="btn btn-secondary btn-sm" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} title="显示/折叠主机列表（Ctrl+B）">☰</button>
        <span className="logo">SSH Web Tool</span>
        <span className="status">{status}</span>
        {terminals.activeId && (() => {
          const activeInst = terminals.terminals.get(terminals.activeId!)
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
          title="左右分屏（均分两栏，再次点击退出）"
          style={{ whiteSpace: 'nowrap' }}
        >◫ 左右</button>
        <button
          className={`btn btn-sm ${splitMode === 'v' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => (splitMode === 'v' ? exitSplit() : enterSplit('v'))}
          title="上下分屏（均分两行，再次点击退出）"
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
        <button className="btn btn-secondary btn-sm" onClick={() => setSessionPanelCollapsed(!sessionPanelCollapsed)} title="显示/折叠右侧会话列表">
          🗂 会话
        </button>
        <button className="btn btn-secondary btn-sm" onClick={() => setPanelCollapsed(!panelCollapsed)} title="显示/折叠右侧工具面板（快速指令/SFTP）">
          📋 面板
        </button>
        <button
          className={`btn btn-sm ${editorOpen ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => { setEditorOpen(!editorOpen); if (editorOpen) focusActiveTerminal() }}
          title="文本编辑器：查看/编辑本地文件（日志、.zs 脚本），支持关键字高亮"
        >
          📝 编辑器
        </button>
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
            hostTypes={hostTypes}
            groups={groups}
            activeHostId={activeHostId}
            defaultShell={fallbackShell}
            onOpenDefaultTerminal={() => {
              if (terminals.connecting) { setStatus('正在连接其他终端，请稍候...'); return }
              setStatus(`正在打开本机 ${fallbackShell}...`)
              terminals.openLocalTerminal(fallbackShell)
                .then(() => { setStatus(`本机 ${fallbackShell} 已打开`); setTimeout(() => loadHosts(), 500) })
                .catch((e) => { setStatus('打开本机终端失败'); alert('本机终端打开失败: ' + (e as Error).message) })
            }}
            onHostClick={handleHostClick}
            onEdit={(h) => { setEditingHost(h); setModalOpen(true) }}
            onDelete={handleDeleteHost}
            onCopy={handleCopyConnection}
            onDuplicate={handleDuplicateHost}
            onManageGroups={() => setGroupManagerOpen(true)}
            onReorderHosts={handleReorderHosts}
          />
        </div>

        {/* 中间终端区域（编辑器页以覆盖层形式呈现：终端实例保持挂载，避免 xterm 重建） */}
        <div className="terminal-area">
          <TerminalTabs
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            hostTypes={hostTypes}
            hosts={hosts}
            groups={groups}
            splitSessions={splitSessions}
            onSwitch={handleSwitchTerminal}
            onClose={handleCloseTerminal}
            onNew={handleNewTerminal}
          />
          <TerminalView
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            splitMode={splitMode}
            panes={panes}
            focusedPane={focusedPane}
            registerContainer={terminals.registerContainer}
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

        {/* 右侧会话列表（按主机 ip 聚合分组，宽度可拖拽调整） */}
        {!sessionPanelCollapsed && (
          <div className="session-panel" style={{ width: sessionPanelWidth }}>
            <div className="resizer session-panel-resizer" onMouseDown={startResize('sessionPanel')} title="拖拽调整宽度" />
            <div className="session-panel-header">
              <span className="title">会话列表</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setSessionPanelCollapsed(true)}>✕</button>
            </div>
            <SessionPanel
              terminals={terminals.terminals}
              hosts={hosts}
              activeId={terminals.activeId}
              onSwitch={handleSwitchTerminal}
              onClose={handleCloseTerminal}
            />
          </div>
        )}

        {/* 右侧面板（宽度可拖拽调整） */}
        {!panelCollapsed && (
          <div className="panel" style={{ width: panelWidth }}>
            <div className="resizer panel-resizer" onMouseDown={startResize('panel')} title="拖拽调整宽度" />
            <div className="panel-header">
              <span className="title">工具面板</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setPanelCollapsed(true)}>✕</button>
            </div>
            <div className="panel-tabs">
              <div className={`panel-tab${panelTab === 'quick' ? ' active' : ''}`} onClick={() => setPanelTab('quick')}>⚡ 快速指令</div>
              <div className={`panel-tab${panelTab === 'sftp' ? ' active' : ''}`} onClick={() => setPanelTab('sftp')}>📁 SFTP</div>
            </div>
            <div className="panel-body">{renderPanelContent()}</div>
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
