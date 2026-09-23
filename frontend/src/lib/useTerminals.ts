// 终端状态管理 hook
import { useState, useCallback, useRef, useEffect } from 'react'
import type { Terminal } from '@xterm/xterm'
import type { FitAddon } from '@xterm/addon-fit'
import type { Host } from '../types'
import { api } from './api'
import type { TerminalSettings } from './useSettings'
import { editLineBuffer, isEnter, captureTypedLine } from './lineEditor'
import { normalizeInvisible } from './invisibleChars'
import { createTerminalInstance, getTerminalTheme } from './terminalInstance'
import type { TerminalInstance, SshConnInfo } from './terminalInstance'
import { attachBlockBar } from './blockBar'
import { HighlightDecorator } from './highlightDecorations'
import { compileHighlightRules } from './highlight'
import type { WsClientMessage } from '../types/ws'

// 类型从 terminalInstance.ts 重导出（组件导入路径不变）
export type { TerminalInstance, SshConnInfo } from './terminalInstance'

// 上行消息统一构造出口：type 受 WsClientMessage 判别联合约束（types/ws.ts，与后端
// ws_protocol.py 一一对应），消息类型/字段拼错在编译期报错，而不是悄悄漂移
function sendClient(ws: WebSocket | null, msg: WsClientMessage): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg))
  }
}

// 获取 shell 类型标签
function getShellTypeLabel(state: any): string {
  if (state?.in_python) return 'python'
  if (state?.in_mysql) return 'mysql'
  if (state?.in_pager) return 'pager'
  if (state?.in_shell) return 'shell'
  return 'other'
}

// 页面生命周期内已在前端建过实例的会话 id（createTerminal / restoreTerminals 均登记）。
// 用于 restoreTerminals 防重：React state/ref 存在时序窗口（setState 后到渲染前 ref 未同步），
// 仅靠 terminalsRef.has() 会漏判，导致同一会话被重复恢复（重复 ws → 后端关闭旧连接 → 误标断开）。
const knownTerminalSessions = new Set<string>()

export function useTerminals(settings: TerminalSettings) {
  const [terminals, setTerminalsRaw] = useState<Map<string, TerminalInstance>>(new Map())
  const [activeId, setActiveId] = useState<string | null>(null)
  // 终端内内容搜索（Ctrl+F）打开中的会话 id；null = 未打开
  const [searchOpenId, setSearchOpenId] = useState<string | null>(null)
  const containersRef = useRef<Map<string, HTMLDivElement>>(new Map())
  // 快捷键处理函数（外部设置，用于打开搜索弹窗等）
  const shortcutHandlerRef = useRef<(() => void) | null>(null)
  // 终端内 Ctrl+F 处理函数（App 接线：打开内容搜索条）
  const searchHandlerRef = useRef<((session_id: string) => void) | null>(null)
  // 终端内 Ctrl+B 处理函数（App 接线：显示/折叠主机列表）
  const sidebarToggleRef = useRef<(() => void) | null>(null)
  // 保存最新的 settings 引用，用于回调中
  const settingsRef = useRef(settings)
  settingsRef.current = settings
  // 保存最新的 terminals 引用，用于回调中（解决闭包捕获旧状态导致 input_buffer 不记录的问题）
  const terminalsRef = useRef(terminals)
  terminalsRef.current = terminals
  // 包装 setTerminals：同步维护 terminalsRef，防止 restoreTerminals 在 setState 后
  // 下次渲染前读到过期 ref，对同一 session_id 重复创建实例（重复 ws → 后端关闭旧
  // 连接 → 前端误标 disconnected，顶栏误显重连、输入失效）
  const setTerminals = useCallback(
    (arg: React.SetStateAction<Map<string, TerminalInstance>>) => {
      setTerminalsRaw((prev) => {
        const next = typeof arg === 'function' ? (arg as (p: Map<string, TerminalInstance>) => Map<string, TerminalInstance>)(prev) : arg
        terminalsRef.current = next
        return next
      })
    },
    []
  )
  // 保存最新的 activeId 引用，避免 restoreTerminals 依赖 activeId 导致定时器频繁重建
  const activeIdRef = useRef<string | null>(null)
  activeIdRef.current = activeId
  // closeTerminal 经 ref 转发给终端工厂：closeTerminal 随 terminals 每次渲染重建，
  // 工厂回调若直接捕获会拿到创建时（首次渲染、空 Map）的过期闭包，导致后端
  // closed 消息到来时 terminals.get() 找不到实例而提前 return（标签无法自动关闭清理）
  const closeTerminalRef = useRef<((session_id: string, keepActive?: boolean) => Promise<void>) | null>(null)

  // 直接发送输入到后端，不做任何延迟/合并
  const sendInput = useCallback((session_id: string, data: string) => {
    const inst = terminalsRef.current.get(session_id)
    sendClient(inst?.ws ?? null, { type: 'input', data })
  }, [])


  // 后端所有活跃终端列表（包括 CLI/Python 包创建的，Web 页面统一显示）
  const [activeSessions, setActiveSessions] = useState<any[]>([])

  // 同步后端活跃终端列表
  const syncActiveSessions = useCallback(async () => {
    try {
      const res = await api.listActiveTerminals()
      setActiveSessions(res.terminals || [])
    } catch (e) {
      // 同步失败不影响主流程
    }
  }, [])

  // 使用官方 FitAddon 自动计算终端尺寸，确保 cols/rows 准确
  // 附加溢出修正：字体放大后渲染行高可能略大于计算值，导致最后一行被容器裁切，
  // 检测到滚动高度溢出时减少一行，保证底部内容完整可见
  // 真折叠互操作（folds.ts）：savedLines 是旧列宽快照，必须在 fit 触发 reflow
  // 之前 unfoldAll；fit 完成后 afterFit 按当前阈值重新应用自动折叠
  const fitTerminal = useCallback((inst: TerminalInstance) => {
    if (inst.container) {
      try {
        inst.blockBar?.unfoldAll()
        inst.fitAddon.fit()
        inst.blockBar?.afterFit()
        // 强制重绘可见区：分屏切换/跨窗口拖拽后 DOM renderer 可能停留在空白帧
        // （尺寸未变时 fit 不触发重绘，内容行不刷新）
        try { inst.term.refresh(0, inst.term.rows - 1) } catch { /* 忽略 */ }
        const el = inst.term.element
        if (el && el.scrollHeight > el.clientHeight + 2 && inst.term.rows > 5) {
          inst.term.resize(inst.term.cols, inst.term.rows - 1)
        }
        // 右缘溢出修正：FitAddon 把容器 padding-left（色条/折叠槽 14px）也算进
        // 可用宽度，列宽合计可能超出 .xterm 实际宽度，最后一列被右缘裁切/压到
        // 滚动条下。按当前格宽回退整列数直到不再溢出（算术求解，单次 resize）
        const screenEl = el?.querySelector('.xterm-screen') as HTMLElement | null
        if (el && screenEl && inst.term.cols > 2) {
          const screenW = screenEl.getBoundingClientRect().width
          const availW = el.getBoundingClientRect().width
          if (screenW > availW + 1) {
            const cellW = screenW / inst.term.cols
            let target = inst.term.cols
            while (target > 2 && cellW * target > availW + 1) target -= 1
            if (target !== inst.term.cols) inst.term.resize(target, inst.term.rows)
          }
        }
      } catch (e) {
        console.error('FitAddon fit failed', e)
      }
    }
  }, [])

  const sendResize = useCallback((term: Terminal, ws: WebSocket | null) => {
    if (term) {
      sendClient(ws, { type: 'resize', cols: term.cols, rows: term.rows })
    }
  }, [])

  // 等待容器注册且尺寸就绪后 fit（最多约 600ms），fit 成功后发送 resize 到后端。
  // 用于写入历史输出前确保终端宽高正确——否则历史内容按默认 80 列折行，
  // fit 到实际宽度后已写入的行不会重新折行，导致 banner/MOTD 显示错乱
  const ensureFit = useCallback((session_id: string, term: Terminal, fitAddon: FitAddon, ws: WebSocket | null, maxRetry = 12): Promise<void> => {
    return new Promise((resolve) => {
      const attempt = (left: number) => {
        const container = containersRef.current.get(session_id)
        if (container && container.clientWidth > 0) {
          try {
            fitAddon.fit()
            sendResize(term, ws)
            resolve()
            return
          } catch { /* 容器未完全就绪，继续重试 */ }
        }
        if (left <= 0) resolve()
        else setTimeout(() => attempt(left - 1), 50)
      }
      attempt(maxRetry)
    })
  }, [sendResize])

  // 终端重同步：清除缓冲区 + 发送 Ctrl+L 让 shell 重绘 + 发送 resize
  // 用于初始连接时，确保 shell 第一帧输出使用正确的终端尺寸
  // （终端复制/粘贴逻辑见 terminalCopy.ts：选中复制、Ctrl+C 复制、Ctrl+V 单次粘贴）

const resyncTerminal = useCallback((session_id: string, term: Terminal, ws: WebSocket | null, clean_screen: boolean = true) => {
    if (ws && ws.readyState === WebSocket.OPEN && term) {
      // clean_screen=true（默认）：清除 xterm 缓冲区 + 发送 Ctrl+L 让 shell 清屏重绘
      // 用于恢复/重连场景；首次连接传 false，避免清掉主机 banner/MOTD 等登录信息
      if (clean_screen) {
        // 1. 清除 xterm.js 缓冲区（丢弃可能使用错误尺寸渲染的内容）
        term.reset()
        // 2. reset 后 buffer 已清空，折叠 savedLines 全部 stale——经 tracker.onReset
        //    链触发 foldStore.discardAll（只清记录禁止塞回，塞回会写进别人的行）
        terminalsRef.current.get(session_id)?.blockBar?.handleTerminalReset()
        // 3. 发送 Ctrl+L（换页符），告诉 shell 清屏并重绘提示符
        sendClient(ws, { type: 'input', data: '\x0c' })
      }
      // xterm.js 默认已启用自动换行（DECSET 7），不需要显式写入 ESC 序列
      // 发送 resize 消息，应用新尺寸
      sendClient(ws, { type: 'resize', cols: term.cols, rows: term.rows })
      console.log('[Terminal] resync size:', term.cols, 'x', term.rows)
    }
  }, [])

  // 更新单个终端的设置（字体、大小、主题）
  const updateTerminalSettings = useCallback((inst: TerminalInstance) => {
    const s = settingsRef.current
    inst.term.options.fontFamily = s.fontFamily
    inst.term.options.fontSize = s.fontSize
    inst.term.options.theme = getTerminalTheme(s)
    // 命令块设置（开关/自动折叠/行数）实时同步到渲染层
    inst.blockBar?.applySettings(s)
    // 关键字高亮规则实时同步（重编译全部规则并全量重画装饰层）
    inst.highlight?.setRules(compileHighlightRules(s.highlightRules))
    // 重新计算终端尺寸
    if (inst.container) {
      fitTerminal(inst)
      sendResize(inst.term, inst.ws)
    }
  }, [fitTerminal, sendResize])

  // 设置变化时更新所有终端
  // 优化：去掉 terminals 依赖，只在 settings 变化时执行（原依赖含 terminals
  // 导致每次 terminals Map 变化都重新遍历所有终端做设置更新）
  useEffect(() => {
    terminals.forEach((inst) => {
      updateTerminalSettings(inst)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings, updateTerminalSettings])

  // 处理终端输入：维护输入行缓冲区（精确行编辑，跟踪光标位置），回车时记录完整命令到后端
  // 注意：使用 terminalsRef.current 而不是 terminals，避免闭包捕获旧状态导致新终端的 input_buffer 不记录
  // 行编辑逻辑已抽取为纯函数 editLineBuffer（lineEditor.ts，可单测）；此处仅保留副作用
  const handleTerminalInput = useCallback((session_id: string, data: string) => {
    const inst = terminalsRef.current.get(session_id)
    if (!inst) return
    // 连接未就绪（已断开/重连中/占位实例）：输入到不了任何 shell——不记录、
    // 不积累盲打文本，并清掉残留缓冲（防止断线前没回车的半截输入串进
    // 重连后的第一条命令，产出错乱的"不完整"历史记录）
    if (inst.reconnecting || !inst.ws || inst.ws.readyState !== WebSocket.OPEN) {
      inst.input_buffer = ''
      inst.input_cursor = 0
      inst.saw_tab = false
      inst.cmd_anchor = null
      return
    }

    // 回车：先由前端按键盘输入即时记录（兜底，保证任何 PS1 环境下都有历史），
    // 后端随后解析终端回显行（含 Tab 补全/历史翻查后的真实命令）：
    // record_echo_command 会清理 3 秒内的前缀残留（如 cd /va）并去重，以后端为准。
    // 例外——本行按过 Tab：补全文本从输出侧回来，键盘缓冲要么残缺、要么在
    // "补全后又输入"时交错成非前缀乱串（前缀/超串去重都拦不住，即"补全的命令
    // 没记录、只留半截"的根因）。三级取权威：
    // ① 终端缓冲捕获输入行全文（补全后所见即所得，与重绘字节序/PS1 形态无关）
    // ② 缓冲不可用（行被后台输出推挤等）→ 延迟兜底：等 2s 让回显解析先落库
    // ③ 回显也没记到才落键盘版——任何路径下至少一条，且不再残留补全前半截
    // 回车记录（一期：输入快照主通道化）。键盘缓冲只在"纯键入无补全"时可信：
    // - 可信行（有键入内容且本行无 Tab）→ 键入版即时记录（原行为，最快最稳）
    // - 含 Tab 补全行 / 缓冲空但有输入锚行（↑ 历史召回、整段粘贴、自研 shell
    //   下行回显）→ captureTypedLine 从 xterm 缓冲捕获屏幕实际显示的命令行
    //   （所见即所得，不依赖提示符格式/重绘方式，S2 主通道）
    // - 捕获不可用（行被推挤/锚失效）且缓冲非空 → 延迟兜底：等 2s 让后端回显
    //   解析先落库，没记到才落键盘版——任何 shell 至少一条且不留补全前半截
    if (isEnter(data)) {
      const typed = inst.input_buffer.trim()
      const viaTab = inst.saw_tab
      const anchor = inst.cmd_anchor
      inst.saw_tab = false
      inst.cmd_anchor = null
      if (typed && !viaTab) {
        console.log('[history] 回车记录·键入版:', JSON.stringify(typed))
        api.recordCommand(typed).catch(() => {})
      } else {
        const cap = anchor ? captureTypedLine(inst.term, anchor) : ''
        if (cap && cap.length >= typed.length) {
          console.log('[history] 回车记录·缓冲捕获:', JSON.stringify(cap))
          api.recordCommand(cap).catch(() => {})
        } else if (typed) {
          console.log('[history] 回车记录·延迟兜底(回显优先):', JSON.stringify(typed))
          api.recordCommand(typed, session_id, 2).catch(() => {})
        }
      }
    } else if (data.includes('\t')) {
      // 行内任何 Tab（补全/菜单选择）都使键盘版缓冲不可信；整段粘贴含 Tab 同样命中
      inst.saw_tab = true
    }
    // Ctrl+C：除清空行缓冲外，打断洪水输出（cat 大文件/grep -r）时立即丢弃
    // 尚未渲染的积压。否则后端已中断命令，但积压块仍会持续灌入 xterm 解析绘制，
    // 界面看起来像"卡死/没反应"（这是用户最常把 Ctrl+C 误判为失效的场景）
    // Ctrl+C / Ctrl+D：行被作废，输入缓冲与 Tab 标记一并由 editLineBuffer 清空路径处理
    // （此处显式重置 saw_tab，防补全标记串到下一行导致误走延迟兜底）
    if (data === '\x03' || data === '\x04') {
      inst.saw_tab = false
      inst.cmd_anchor = null
      if (inst.feeder && inst.feeder.pendingBytes() > 0) {
        inst.feeder.dropPending()
        // 本地 shell（cmd/powershell）：Ctrl+C 后 ^C 回显+新提示符几十毫秒就到，
        // 静默丢弃会把提示符吃掉，界面反而看起来"没反应"。SSH 远端洪水残液在途
        // 时间更长（网络 + 事件队列），用短窗口吞掉残液、放行之后的新提示符
        if (inst.type !== 'local') {
          inst.feeder.armQuiescentDrop(80)
        }
      }
    }

    const prevBuffer = inst.input_buffer
    const next = editLineBuffer({ buffer: inst.input_buffer, cursor: inst.input_cursor }, data)
    if (next.buffer !== inst.input_buffer || next.cursor !== inst.input_cursor) {
      inst.input_buffer = next.buffer
      inst.input_cursor = next.cursor
    }
    // 输入行锚点：行空闲（键盘缓冲空）时的任何首次输入都设锚 = 当前光标位置
    // （远端回显异步未到，光标仍在提示符结束处）。覆盖三类缓冲记不到的行：
    // ↑ 历史召回（ESC 序列）、整段粘贴（ESC 200~ 段）、Tab 菜单选择——它们
    // 键盘缓冲留空，回车时靠此锚从 xterm 缓冲捕获屏幕命令行
    if (!isEnter(data) && !prevBuffer && data !== '\x03' && data !== '\x04' && data !== '\x15') {
      const b = inst.term.buffer.active
      inst.cmd_anchor = { row: b.cursorY, col: b.cursorX }
    }
  }, [])

  const createTerminal = useCallback(async (
    host: Host | null,
    terminal_name?: string,
    kind: 'ssh' | 'local' = 'ssh',
    localShell = 'powershell',
    history_content?: string,
    rawConn?: SshConnInfo,  // 本地拦截 SSH 会话重连：用原始凭据建会话（无已保存主机）
    password?: string,      // 已保存主机连接的密码覆盖（重连弹窗输入）；raw 会话密码带在 rawConn 内
    connect_message?: string,  // 连接成功后写入新终端的提示（如「已连接 user@host」）
  ) => {
    const s = settingsRef.current
    // 工厂共用回调（占位实例 / 真实实例 / 恢复实例行为一致）
    const factoryCommon = {
      onData: (sid: string, data: string) => {
        handleTerminalInput(sid, data)
        sendInput(sid, data)
      },
      copyShortcut: () => shortcutHandlerRef.current?.(),
      onCtrlF: (sid: string) => searchHandlerRef.current?.(sid),
      onCtrlB: () => sidebarToggleRef.current?.(),
      setTerminals,
      requestClose: (sid: string) => closeTerminalRef.current!(sid),
      resync: resyncTerminal,
      ensureFit,
    } as const

    // ---- 本机终端 / rawConn 重连：后端会话先行，直接建连（无占位阶段）----
    if (kind === 'local' || rawConn) {
      let session_id: string
      let tname: string
      let hostLike: { id: string; host: string; name: string; type: string }
      if (kind === 'local') {
        // 本机终端（cmd/powershell，WinPTY，不经过 SSH）
        const r = await api.createLocalSession(localShell)
        session_id = r.session_id
        tname = r.terminal_name || terminal_name || (localShell === 'cmd' ? '本机 cmd' : localShell === 'powershell' ? '本机 PowerShell' : '本机 pwsh')
        hostLike = { id: 'local', host: 'localhost', name: tname, type: 'local' }
      } else {
        const conn = rawConn as SshConnInfo  // 外层分支条件已保证 rawConn 存在
        const result = await api.connectRaw(conn, terminal_name)
        session_id = result.session_id
        tname = result.terminal_name || terminal_name || '终端1'
        hostLike = { id: '', host: conn.host, name: `${conn.username}@${conn.host}`, type: 'ssh' }
      }

      // 统一工厂（P0-③）：xterm/WebSocket/消息路由/洪水供给器在此一次装配
      const instance = createTerminalInstance({
        session_id,
        host_id: hostLike.id,
        host_name: hostLike.name || hostLike.host,
        terminal_name: tname,
        type: hostLike.type,
        shell_type: kind === 'local' ? 'local' : 'shell',
        local_shell: kind === 'local' ? localShell : undefined,
        local_starting: kind === 'local',
        // history_content：重连时由 App 预先读取旧会话历史传入（旧会话在读取后才关闭），
        // 直接写入新终端，保证重连后之前的内容（banner/命令输出）仍然可看（用户要求：重连不清空）
        historyContent: history_content,
        // limit 200000：完整回放同会话历史（同一终端 = 同一份记录）
        historyLimit: 200000,
        connectMessage: connect_message,
        ssh_conn: rawConn ? { ...rawConn } : null,
        settings: s,
        containersRef,
        ...factoryCommon,
      })

      setTerminals((prev) => {
        const next = new Map(prev)
        next.set(session_id, instance)
        return next
      })
      knownTerminalSessions.add(session_id)
      setActiveId(session_id)
      return session_id
    }

    // ---- 已保存主机 SSH：先建占位 tab（显示"连接中"），HTTP 连接成功后关闭占位、
    //      另建真实实例接管（并发友好：不同主机可同时建立连接，状态在各自 tab 内呈现）----
    if (!host) throw new Error('缺少主机连接信息')
    // 建占位前的活动会话：连接失败关闭占位后焦点回退到它（同窗口原位回退，不跳转）
    const prevActiveId = activeIdRef.current
    const pendingId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const pending = createTerminalInstance({
      session_id: pendingId,
      host_id: host.id,
      host_name: host.name || host.host,
      terminal_name: '连接中…',
      type: host.type,
      shell_type: 'shell',
      local_starting: false,
      deferConnect: true,  // 占位实例不建 ws，只做"连接中"状态展示
      historyLimit: 200000,
      ssh_conn: null,
      settings: s,
      containersRef,
      ...factoryCommon,
    })
    pending.pending = true
    setTerminals((prev) => {
      const next = new Map(prev)
      next.set(pendingId, pending)
      return next
    })
    setActiveId(pendingId)

    try {
      const result = await api.connectHost(host.id, terminal_name, password)
      // 先登记会话防重（restore 定时可能在 closeTerminalRef 期间读到 active 列表而重复创建）
      knownTerminalSessions.add(result.session_id)
      // 连接成功：关闭占位 tab（keepActive 防止 activeId 闪跳到其他标签），另建真实实例
      await closeTerminalRef.current!(pendingId, true)
      const instance = createTerminalInstance({
        session_id: result.session_id,
        host_id: host.id,
        host_name: host.name || host.host,
        terminal_name: result.terminal_name || terminal_name || '终端1',
        type: host.type,
        shell_type: 'shell',
        local_starting: false,
        historyContent: history_content,
        historyLimit: 200000,
        connectMessage: connect_message,
        ssh_conn: null,
        settings: s,
        containersRef,
        ...factoryCommon,
      })
      setTerminals((prev) => {
        const next = new Map(prev)
        next.set(result.session_id, instance)
        return next
      })
      knownTerminalSessions.add(result.session_id)
      setActiveId(result.session_id)
      return result.session_id
    } catch (e) {
      // 连接失败：直接关闭占位 tab（失败原因由调用方 toast 呈现），焦点回退到建占位前
      // 的活动会话；不再保留不可操作的"连接失败"tab，也不跳转/新建其他终端
      await closeTerminalRef.current!(pendingId, true)
      if (prevActiveId && terminalsRef.current.has(prevActiveId)) setActiveId(prevActiveId)
      else if (activeIdRef.current === pendingId) setActiveId(null)
      throw e
    }
  }, [fitTerminal, sendResize, resyncTerminal, handleTerminalInput, sendInput, ensureFit])

  const restoreTerminals = useCallback(async () => {
    if (typeof document !== 'undefined' && document.hidden) return
    try {
      const { terminals: active } = await api.listActiveTerminals()
      const s = settingsRef.current
      // 并行恢复所有终端（原 for...of 串行创建，3 个终端延迟累加）
      const toRestore = active.filter(t => {
        if (t.ws_active) return false
        return !knownTerminalSessions.has(t.session_id) && !terminalsRef.current.has(t.session_id)
      })
      await Promise.all(toRestore.map(async (t) => {
        knownTerminalSessions.add(t.session_id)  // 立即登记，防并发 restore 重复建实例
        // 统一工厂（P0-③）：与 createTerminal 同一套装配（xterm/WS/消息路由/洪水供给器），
        // restore 与新建行为一致；历史由 factory 在 ws.onopen 中按 historyLimit 回放
        const instance = createTerminalInstance({
          session_id: t.session_id,
          host_id: t.host_id,
          host_name: t.host_name || t.host,
          terminal_name: t.terminal_name || '终端',
          type: t.host_type,
          shell_type: 'shell',
          local_starting: false,
          // limit 200000：完整回放同会话历史（同一终端 = 同一份记录）
          historyLimit: 200000,
          ssh_conn: t.ssh_conn ?? null,  // 拦截 SSH 会话：从活跃列表恢复重连凭据；已保存主机会话为 null
          settings: s,
          containersRef,
          onData: (sid, data) => {
            handleTerminalInput(sid, data)
            sendInput(sid, data)
          },
          copyShortcut: () => shortcutHandlerRef.current?.(),
          onCtrlF: (sid) => searchHandlerRef.current?.(sid),
          onCtrlB: () => sidebarToggleRef.current?.(),
          setTerminals,
          requestClose: (sid) => closeTerminalRef.current!(sid),
          resync: resyncTerminal,
          ensureFit,
        })

        setTerminals((prev) => {
          const next = new Map(prev)
        knownTerminalSessions.add(t.session_id)
          next.set(t.session_id, instance)
          return next
        })
        if (!activeIdRef.current) setActiveId(t.session_id)
        return t.session_id
      }))
    } catch (e) {
      console.error('恢复终端失败', e)
    }
  }, [fitTerminal, sendResize, handleTerminalInput, sendInput, ensureFit])

  // 定期同步活跃终端列表，并自动恢复新创建的终端（CLI/Python 包创建的）
  // 优化：App.tsx 已有主机列表低频轮询（15s），这里负责发现新终端并恢复；
  //       连接/断开等操作已在事件回调中即时同步，低频兜底即可
  useEffect(() => {
    syncActiveSessions()
    restoreTerminals()
    const timer = setInterval(() => {
      syncActiveSessions()
      if (document.hidden) return
      restoreTerminals()
    }, 10000)
    return () => clearInterval(timer)
  }, [syncActiveSessions, restoreTerminals])

  // 批量终端状态轮询：所有终端合并为一次 HTTP 请求（每 5 秒）
  // 优化：原每个终端独立 setInterval 3 秒轮询，5 个终端 = 每秒 ~1.7 次 HTTP 请求
  //       批量合并后仅 1 次请求/5s；shell 类型变化不频繁，5s 轮询足够
  useEffect(() => {
    const timer = setInterval(() => {
      const ids = Array.from(terminalsRef.current.keys())
      if (ids.length === 0) return
      api.getBatchSessionStates(ids).then((res) => {
        const states = res.states
        setTerminals((prev) => {
          let changed = false
          const next = new Map(prev)
          for (const [sid, state] of Object.entries(states)) {
            const inst = next.get(sid)
            if (inst) {
              const newType = getShellTypeLabel(state)
              if (inst.shell_type !== newType) {
                next.set(sid, { ...inst, shell_type: newType })
                changed = true
              }
            }
          }
          return changed ? next : prev
        })
      }).catch(() => {})
    }, 5000)
    return () => clearInterval(timer)
  }, [])

  const switchTerminal = useCallback((session_id: string) => {
    setActiveId(session_id)
    setTimeout(() => {
      const inst = terminals.get(session_id)
      const container = containersRef.current.get(session_id)
      if (inst && container) {
        fitTerminal(inst)
        // 切换终端后也 resync，确保 xterm.js 状态正确
        requestAnimationFrame(() => {
          if (inst.container) {
            fitTerminal(inst)
            sendResize(inst.term, inst.ws)
          }
        })
        inst.term.focus()
      }
    }, 50)
  }, [terminals, fitTerminal, sendResize])

  // 重新 fit 指定会话（分屏布局/窗口尺寸变化后调用；仅处理已挂载容器的会话）
  const refitTerminals = useCallback((session_ids: string[]) => {
    for (const id of session_ids) {
      const inst = terminalsRef.current.get(id)
      if (inst?.container) {
        fitTerminal(inst)
        sendResize(inst.term, inst.ws)
      }
    }
  }, [fitTerminal, sendResize])

  // 弹窗/面板关闭后把光标还给当前终端
  const focusActiveTerminal = useCallback(() => {
    const inst = activeId ? terminals.get(activeId) : undefined
    if (inst && inst.term) inst.term.focus()
  }, [terminals, activeId])

  // 关闭终端标签：同时删除后端会话（不保留"仅关闭标签"的连接），
  // 这样重开页面不会恢复已关闭的会话；只有后端仍真实存活的连接（整关页面但程序未退）才会被恢复
  const closeTerminal = useCallback(async (session_id: string, keepActive: boolean = false) => {
    const inst = terminals.get(session_id)
    if (!inst) return
    if (inst.ws) inst.ws.close()
    inst.feeder?.dispose()
    inst.resizeObserver?.disconnect()
    inst.resizeObserver = null
    inst.resizeObservedEl = null
    inst.blockBar?.dispose()
    inst.highlight?.dispose()
    // state_timer 已改为批量轮询，不再需要单独清理
    try { await api.closeSession(session_id) } catch {}
    setTerminals((prev) => {
      const next = new Map(prev)
      next.delete(session_id)
      return next
    })
    // keepActive=true：关闭当前标签时不切换 activeId（用于重连流程，新会话已激活）
    if (!keepActive && activeId === session_id) {
      const remaining = Array.from(terminals.keys()).filter((id) => id !== session_id)
      setActiveId(remaining.length > 0 ? remaining[0] : null)
    }
  }, [terminals, activeId])
  closeTerminalRef.current = closeTerminal

  // 手动断开：关闭 WebSocket + 后端会话（后端删除后不会再被自动恢复），保留标签等待重连
  const disconnectTerminal = useCallback(async (session_id: string) => {
    const inst = terminals.get(session_id)
    if (!inst || inst.disconnected) return
    try { await api.disconnectToLocal(session_id) } catch {}
    // 不关 ws、不标断态：后端 _auto_switch_to_local 关闭 SSH 通道并推送
    // switched_to_local 通知，前端收到后把当前终端切回本机 shell
    // （ssh_exited: true, shell_type: 'local'，保留 host_id 供重连复用会话）。
    // 会话不删除，重连时复用同一 session 保留全部历史。
  }, [terminals])

  // 重连中状态标记：统一重连流程入口置 true，成功/失败后置 false
  // （Tab/终端遮罩显示"连接中"，期间重复点击重连被 App 层拦截）
  // 重连后恢复：清除断开/exit 标记，ws 已死（意外断开置空）时重建 WebSocket 连接。
  // ws 仍存活（手动断开切本地，通道复用）则仅重置状态；App 重连成功后调用。
  const reconnectWs = useCallback((session_id: string) => {
    setTerminals((prev) => {
      const cur = prev.get(session_id)
      if (!cur) return prev
      const next = new Map(prev)
      next.set(session_id, { ...cur, disconnected: false, ssh_exited: false, reconnecting: false })
      return next
    })
    const inst = terminalsRef.current.get(session_id)
    if (inst && (!inst.ws || inst.ws.readyState === WebSocket.CLOSED) && typeof inst.reconnectWs === 'function') {
      inst.reconnectWs()
    }
  }, [])

  const setReconnecting = useCallback((session_id: string, value: boolean) => {
    // 重连期间该会话只读：终端不盖遮罩（历史保持可见），键盘/粘贴输入全部屏蔽，
    // 进度与结果由终端内 [重连] 提示呈现
    const inst = terminalsRef.current.get(session_id)
    if (inst) {
      inst.reconnecting = value
      inst.term.options.disableStdin = value
    }
    setTerminals((prev) => {
      const cur = prev.get(session_id)
      if (!cur || cur.reconnecting === value) return prev
      const next = new Map(prev)
      // 重连结束（成功落到新 shell 或放弃）：旧连接上没回车的半截输入已无意义，清空
      next.set(session_id, value ? { ...cur, reconnecting: true } : { ...cur, reconnecting: false, input_buffer: '', input_cursor: 0, saw_tab: false, cmd_anchor: null })
      return next
    })
  }, [])

  // 定向发送命令到指定会话（sendCommand 的多会话版本）：返回是否发送成功。
  // focus=false 用于 .zs 广播播放——编辑器打开时光标应留在编辑器，不能抢焦点
  const sendCommandTo = useCallback((session_id: string, command: string, execute: boolean = true, focus: boolean = true): boolean => {
    const inst = terminalsRef.current.get(session_id)
    if (!inst || !inst.ws || inst.ws.readyState !== WebSocket.OPEN) return false
    // 隐形字符防御：快捷指令/脚本下发的字符串不经终端粘贴路径，历史库或指令卡
    // 若混入 NBSP/零宽字符会原样喂给 shell（创建出带脏字符的文件名）——发送前归一
    const cmd = normalizeInvisible(command)
    if (execute) {
      sendClient(inst.ws, { type: 'input', data: cmd + '\n' })
      console.log('[history] 回车记录·快捷指令注入:', JSON.stringify(cmd))
      api.recordCommand(cmd).catch(() => {})
      inst.input_buffer = ''
      // 程序注入绕过 term.onData（只对真实键盘触发），显式走一次 Enter 语义，
      // 否则 enter 模式下快捷指令不会开启新命令块（色块不划分）。
      // 多行指令（一次下发多条命令）按行数通知：首行即时开块，其余行等
      // 各自执行完的提示符行出现时逐个补切块——见 commandBlocks.ts onEnter
      inst.blockBar?.notifySubmit(cmd.split('\n').length)
    } else {
      // 只输入命令文本，不发送换行，用户可以编辑后手动执行
      sendClient(inst.ws, { type: 'input', data: cmd })
      inst.input_buffer = cmd
      inst.input_cursor = cmd.length
    }
    if (focus) inst.term.focus()
    return true
  }, [])

  const sendCommand = useCallback((command: string, execute: boolean = true) => {
    if (!activeId) return
    sendCommandTo(activeId, command, execute)
  }, [activeId, sendCommandTo])

  // 读取指定会话 xterm buffer 的最后 N 行纯文本（监控抽屉 / hover 预览用，
  // 一期零后端改动）。会话不存在返回 null；尾部的空行会被裁掉。
  const readBufferTail = useCallback((session_id: string, lines: number): string | null => {
    const inst = terminalsRef.current.get(session_id)
    if (!inst) return null
    const buf = inst.term.buffer.active
    let end = buf.length - 1
    while (end >= 0 && !(buf.getLine(end)?.translateToString(true) ?? '').trim()) end--
    const start = Math.max(0, end - lines + 1)
    const out: string[] = []
    for (let i = start; i <= end; i++) out.push(buf.getLine(i)?.translateToString(true) ?? '')
    return out.join('\n')
  }, [])

  // 设置快捷键处理函数（Alt+R 打开搜索弹窗）
  const setShortcutHandler = useCallback((handler: (() => void) | null) => {
    shortcutHandlerRef.current = handler
  }, [])

  // 设置终端内 Ctrl+F 处理函数（打开内容搜索条）
  const setSearchHandler = useCallback((handler: ((session_id: string) => void) | null) => {
    searchHandlerRef.current = handler
  }, [])

  // 设置终端内 Ctrl+B 处理函数（显示/折叠主机列表）
  const setSidebarToggleHandler = useCallback((handler: (() => void) | null) => {
    sidebarToggleRef.current = handler
  }, [])

  const registerContainer = useCallback((session_id: string, el: HTMLDivElement | null) => {
    if (el) {
      containersRef.current.set(session_id, el)
      // 经 terminalsRef 读取（而非 terminals state）：registerContainer 身份保持稳定，
      // 下游 TerminalView 的行内 ref 不再随每次轮询/toast 重渲染而反复 detach/attach
      const inst = terminalsRef.current.get(session_id)
      // 容器尺寸观察器：window.resize 在 pywebview 窗口最大化/还原时不一定派发，
      // 改为观察容器自身尺寸（rAF 防抖）。仅容器元素变化时重建（避免 React ref
      // 重挂导致观察器销毁重建 + observe 初始回调对全部终端重 fit 的风暴）；
      // 以当前尺寸为基线，尺寸真实变化才执行 fit + 上报 cols/rows
      if (inst && (!inst.resizeObserver || inst.resizeObservedEl !== el)) {
        inst.resizeObserver?.disconnect()
        let raf = 0
        let lastW = el.clientWidth
        let lastH = el.clientHeight
        const ro = new ResizeObserver(() => {
          if (raf) return
          raf = requestAnimationFrame(() => {
            raf = 0
            const w = el.clientWidth
            const h = el.clientHeight
            if (w === lastW && h === lastH) return
            lastW = w
            lastH = h
            if (inst.container && w > 0) {
              fitTerminal(inst)
              sendResize(inst.term, inst.ws)
            }
          })
        })
        ro.observe(el)
        inst.resizeObserver = ro
        inst.resizeObservedEl = el
      }
      if (inst && !inst.container) {
        inst.term.open(el)
        inst.container = el
        // 命令块渲染层（左侧色条 + 遮罩折叠）：xterm 打开后才有几何信息可测
        if (!inst.blockBar) inst.blockBar = attachBlockBar(inst.term, el, settingsRef.current)
        // 关键字高亮装饰层（rssh 同款）：xterm 打开后创建（同 blockBar 模式），
        // 创建即应用当前规则集，后续规则变化走 updateTerminalSettings
        if (!inst.highlight) {
          inst.highlight = new HighlightDecorator(inst.term)
          inst.highlight.setRules(compileHighlightRules(settingsRef.current.highlightRules))
        }
        // 终端真正打开后，使用 resyncTerminal 确保 xterm.js 状态正确
        // 关键：先 fit 计算正确的 cols/rows，再 reset 清除可能错误的状态，再发送 resize 和 Ctrl+L
        fitTerminal(inst)
        // 延迟到下一帧，确保 xterm.js 完成渲染后再 resync
        requestAnimationFrame(() => {
          if (inst.container) {
            fitTerminal(inst)
            // 首次打开不清屏不重置：保留主机登录 banner/MOTD 等连接信息
            resyncTerminal(session_id, inst.term, inst.ws, false)
          }
        })
        // 延迟再次调用，确保字体加载完成
        setTimeout(() => {
          if (inst.container) {
            fitTerminal(inst)
            sendResize(inst.term, inst.ws)
          }
        }, 200)
        // 等待字体加载完成后重新计算尺寸
        if (document.fonts && document.fonts.ready) {
          document.fonts.ready.then(() => {
            if (inst.container) {
              fitTerminal(inst)
              sendResize(inst.term, inst.ws)
            }
          })
        }
      }
    } else {
      containersRef.current.delete(session_id)
    }
  }, [fitTerminal, sendResize, resyncTerminal])

  // 窗口大小变化时调整终端
  useEffect(() => {
    const handler = () => {
      if (activeId) {
        const inst = terminals.get(activeId)
        const container = containersRef.current.get(activeId)
        if (inst && container) {
          fitTerminal(inst)
          sendResize(inst.term, inst.ws)
        }
      }
    }
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [activeId, terminals, fitTerminal, sendResize])

  return {
    terminals,
    activeId,
    activeSessions,
    searchOpenId,
    setSearchOpenId,
    // 按会话 id 读取最新实例（terminalsRef，绕过闭包过期问题；不存在返回 undefined）
    getTerminal: (session_id: string) => terminalsRef.current.get(session_id),
    createTerminal,
    // 打开本机终端（shell 为短标识或检测列表里的完整路径，WinPTY，不经过 SSH）
    openLocalTerminal: (shell: string) => createTerminal({} as Host, undefined, 'local', shell),
    restoreTerminals,
    switchTerminal,
    setActiveId,
    refitTerminals,
    closeTerminal,
    disconnectTerminal,
    setReconnecting,
    reconnectWs,
    sendCommand,
    sendCommandTo,
    readBufferTail,
    registerContainer,
    focusActiveTerminal,
    setShortcutHandler,
    setSearchHandler,
    setSidebarToggleHandler,
  }
}
