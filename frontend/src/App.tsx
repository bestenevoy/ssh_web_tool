import { useState, useEffect, useCallback, useRef } from 'react'
import '@xterm/xterm/css/xterm.css'
import './App.css'
import type { Host, HostType, QuickCommand } from './types'
import type { SshConnInfo, TerminalInstance } from './lib/useTerminals'
import { api } from './lib/api'
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
import { QuickCommands } from './components/QuickCommands'
import { SftpPanel } from './components/SftpPanel'
import { EventLog } from './components/EventLog'
import { ApiDocs } from './components/ApiDocs'
import { HostModal } from './components/HostModal'
import { QuickCommandModal } from './components/QuickCommandModal'
import { PasswordModal } from './components/PasswordModal'
import { SettingsModal } from './components/SettingsModal'
import { SearchBar } from './components/SearchBar'
import HistorySearchModal from './components/HistorySearchModal'

type PanelTab = 'quick' | 'sftp' | 'events' | 'api'

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
  // 布局宽度（侧栏/右面板可拖拽调宽，localStorage 持久化；终端是核心区，宽度自适应）
  const [sidebarWidth, setSidebarWidth] = useState(() => loadLayoutWidth('sidebar', 240, 180, 440))
  const [panelWidth, setPanelWidth] = useState(() => loadLayoutWidth('panel', 320, 260, 600))
  const [panelTab, setPanelTab] = useState<PanelTab>('quick')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingHost, setEditingHost] = useState<Host | null>(null)
  const [status, setStatus] = useState('就绪')
  const [historySearchOpen, setHistorySearchOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [quickCommandModalOpen, setQuickCommandModalOpen] = useState(false)
  const [editingQuickCommand, setEditingQuickCommand] = useState<QuickCommand | null>(null)
  // 终端窗口右键菜单（与主机列表右键菜单各自独立）：坐标 + 会话 + 命中的色块 id
  const [terminalMenu, setTerminalMenu] = useState<{
    x: number; y: number; sessionId: string; blockId: number | null
  } | null>(null)

  const { settings, toggleTheme, setFontFamily, setFontSize, updateSettings } = useSettings()
  const terminals = useTerminals(settings)
  const { focusActiveTerminal } = terminals
  const events = useEvents()

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
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [terminals.activeId])

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
    if (terminals.activeId) {
      const inst = terminals.terminals.get(terminals.activeId)
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
      }
    } else {
      alert('请先选择一个主机')
    }
  }, [terminals, hosts, loadHosts])

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
  // direct 直接执行 与 param/编辑后执行 共用：先完成预操作再发送命令
  const runPreOps = useCallback(async (qc: QuickCommand): Promise<boolean> => {
    if (!terminals.activeId) return false
    const inst = terminals.terminals.get(terminals.activeId)
    if (!inst || !inst.ws || inst.ws.readyState !== WebSocket.OPEN) return false
    const session_id = inst.session_id
    for (const op of qc.pre_ops || []) {
      if (op.type === 'upload') {
        const remote = (op.remote || '').trim()
        const source = (op.source || '').trim()
        const sourceType = op.source_type === 'path' ? 'path' : 'script'
        if (!remote || !source) continue
        setStatus(`上传中 ${source} -> ${remote}...`)
        try {
          await api.preopUpload(session_id, source, sourceType, remote)
          setStatus(`已上传 ${source} -> ${remote}`)
        } catch (e) {
          setStatus('上传失败，命令未执行')
          alert('预操作失败（上传文件）: ' + (e as Error).message)
          return false
        }
      } else if (op.type === 'chmod') {
        if (!op.mode || !op.path) continue
        terminals.sendCommand(`chmod ${op.mode} ${op.path}`, true)
      } else if (op.type === 'env') {
        if (!op.key) continue
        terminals.sendCommand(`export ${op.key}=${op.value ?? ''}`, true)
      }
    }
    return true
  }, [terminals, setStatus])


  // 执行快捷指令（含预操作流水线：上传文件 → chmod → env → 命令本体）
  const executeQuickCommand = useCallback(async (qc: QuickCommand, param: string | null) => {
    if (!(await runPreOps(qc))) return

    // 参数替换：{args} 占位符替换，无占位符则追加到末尾
    let cmd = qc.command
    if (param !== null) {
      cmd = cmd.includes('{args}')
        ? cmd.split('{args}').join(param)
        : (cmd + ' ' + param).trim()
    }

    terminals.sendCommand(cmd, true)
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
      case 'events':
        return <EventLog events={events.events} onClear={events.clearEvents} />
      case 'api':
        return <ApiDocs />
    }
  }

  return (
    <div className="app">
      {/* 顶部栏 */}
      <div className="topbar">
        <button className="btn btn-secondary btn-sm" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} title="折叠主机列表">☰</button>
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
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={() => setSettingsOpen(true)}
            title="设置（本机终端 / 命令块 / 缓存清理等）"
          >
            ⚙️
          </button>
        </div>
        <div className="topbar-divider" />
        <button className="btn btn-secondary btn-sm" onClick={() => setPanelCollapsed(!panelCollapsed)}>
          📋 面板
        </button>
        <button className="btn btn-secondary btn-sm" onClick={handleReloadConfig} title="扫描配置文件与 scripts 脚本目录并刷新">
          🔄 检查配置
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

        {/* 中间终端区域 */}
        <div className="terminal-area">
          <TerminalTabs
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            hostTypes={hostTypes}
            hosts={hosts}
            groups={groups}
            onSwitch={terminals.switchTerminal}
            onClose={handleCloseTerminal}
            onNew={handleNewTerminal}
          />
          <TerminalView
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            registerContainer={terminals.registerContainer}
            onTerminalContextMenu={handleTerminalContextMenu}
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
        </div>

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
              <div className={`panel-tab${panelTab === 'events' ? ' active' : ''}`} onClick={() => setPanelTab('events')}>📊 活动日志</div>
              <div className={`panel-tab${panelTab === 'api' ? ' active' : ''}`} onClick={() => setPanelTab('api')}>🔌 API</div>
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

      {/* 设置弹窗：本机终端 / 命令块 / 字体主题 / 缓存清理 */}
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
