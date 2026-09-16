// 终端实例统一创建工厂（P0-③）：把 useTerminals.ts 中 createTerminal 与
// restoreTerminals 重复的装配逻辑（xterm 初始化 / WebSocket 握手 / 消息路由 /
// 洪水供给器）收敛到一个入口。行为与拆分前完全一致。

import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import type { RefObject, Dispatch, SetStateAction } from 'react'
import type { TerminalSettings } from './useSettings'
import type { BlockBarController } from './blockBar'
import type { WsServerMessage } from '../types/ws'
import { api } from './api'
import { reflowForCols } from './reflow'
import { setupTerminalCopy } from './terminalCopy'
import { createOutputFeeder } from './outputFeeder'
import { markDisconnected } from './terminalDisconnect'

// 终端回滚行数（单一定义源）：folds.ts 的 savedLines 缓存预算按
// 5000 − blockMaxLines − 1 计算，保证 visible + cached < scrollback
export const TERMINAL_SCROLLBACK_LINES = 5000

// 内容搜索装饰（addon-search 0.16 的 decorations 是每次 findNext/findPrevious
// 的 ISearchOptions，非构造参数）：总览标尺匹配标记，同时启用 onDidChangeResults 计数
export const SEARCH_DECORATIONS = {
  matchOverviewRuler: '#8892b0',
  activeMatchColorOverviewRuler: '#e94560',
} as const

// 会话内保存的 SSH 连接信息（本地终端拦截 ssh 命令后建立，无已保存主机，
// 供"重连"按钮直接用原始凭据重建会话——否则重连会报"找不到该主机信息"）
export interface SshConnInfo {
  host: string
  port: number
  username: string
  password: string
}

export interface TerminalInstance {
  session_id: string
  host_id: string
  host_name: string
  terminal_name: string
  type: string
  shell_type: string  // 当前 shell 类型：shell / python / mysql / other
  term: Terminal
  fitAddon: FitAddon
  ws: WebSocket | null
  container: HTMLDivElement | null
  state_timer: ReturnType<typeof setInterval> | null
  input_buffer: string  // 当前输入行缓冲区（用于记录命令历史）
  input_cursor: number  // 输入行光标位置（行编辑用，精确跟踪命令内容）
  disconnected: boolean  // 主动断开/意外断开标记（断开后可手动重连）
  reconnecting: boolean  // 重连建立中标记（Tab/终端遮罩显示"连接中"，期间禁止重复点击）
  ssh_exited: boolean  // SSH exit 退出后自动切换到本地终端，可点重连回到原 SSH 主机
  local_starting: boolean  // 本机终端创建后等待首批输出（开启启动提示）；收到第一条 output 后变 false
  feeder: ReturnType<typeof createOutputFeeder> | null
  ssh_conn: SshConnInfo | null  // 本地拦截 SSH 会话的连接信息（重连凭据）；已保存主机会话为 null
  // 命令块渲染层（左侧色条 + 真折叠）：容器挂载（registerContainer → term.open）后创建
  blockBar: BlockBarController | null
  // 终端内容搜索（Ctrl+F）：真折叠内容不在 buffer，打开搜索时先 unfoldAll（SearchBar 负责）
  search: SearchAddon
}

// 洪水输出内存上限：xterm write 缓冲无上限（超 50MB 抛错），这里限 2MB 积压，
// 超限丢最旧整块（用户正打算 Ctrl+C 的洪水内容，丢一行可接受）
export const MAX_PENDING_BYTES = 2 * 1024 * 1024

// 终端主题（settings.theme → xterm options.theme）
// xterm 6.0 起：滚动条为自绘组件（宽度由 options.overviewRuler.width 决定，默认 14px），
// 滑块颜色走 theme.scrollbarSlider*；overviewRulerBorder 不钉透明会在滚动条旁
// 画出一条前景色竖线（rssh 同款处理）
export function getTerminalTheme(settings: TerminalSettings) {
  if (settings.theme === 'light') {
    return {
      background: '#fafafa',
      foreground: '#1a1a2e',
      cursor: '#e94560',
      selectionBackground: 'rgba(233, 69, 96, 0.3)',
      // 滚动条滑块：沿用 6.0 之前 thumb 的边框系配色，hover/active 用次级文字色
      scrollbarSliderBackground: '#cbd5e0',
      scrollbarSliderHoverBackground: '#a0aec0',
      scrollbarSliderActiveBackground: '#718096',
      overviewRulerBorder: 'rgba(0,0,0,0)',
    }
  }
  return {
    background: '#0a0e1a',
    foreground: '#c8d0e0',
    cursor: '#e94560',
    selectionBackground: 'rgba(233, 69, 96, 0.3)',
    scrollbarSliderBackground: '#2a3040',
    scrollbarSliderHoverBackground: '#5a6a8a',
    scrollbarSliderActiveBackground: '#8892b0',
    overviewRulerBorder: 'rgba(0,0,0,0)',
  }
}

export interface TerminalInstanceSpec {
  session_id: string
  host_id: string
  host_name: string
  terminal_name: string
  type: string
  shell_type: string
  local_starting: boolean
  // 历史回放：重连时由 App 预先读取旧会话历史传入；否则按 historyLimit 从后端加载
  historyContent?: string
  historyLimit: number
  // 连接成功提示（重连用）：ws.onopen 写入新终端，如「已连接 user@host」
  connectMessage?: string
  ssh_conn: SshConnInfo | null
  settings: TerminalSettings
  // ---- hook 注入（useTerminals 持有状态/回调，经此注入，避免与 React 生命周期耦合）----
  containersRef: RefObject<Map<string, HTMLDivElement>>
  /** 终端输入回调：行编辑 + 发送（handleTerminalInput + sendInput 的宿主实现） */
  onData: (session_id: string, data: string) => void
  copyShortcut: () => void
  /** 终端内 Ctrl+F：打开内容搜索条（App 经 useTerminals 的 setSearchHandler 接线） */
  onCtrlF: (session_id: string) => void
  setTerminals: Dispatch<SetStateAction<Map<string, TerminalInstance>>>
  /** 收到 closed 消息后延迟关闭标签/连接（closeTerminal） */
  requestClose: (session_id: string) => Promise<void>
  /** 容器挂载后重同步终端（resyncTerminal）。带 session_id：clean 分支需经
   *  terminalsRef 找到实例调 blockBar.handleTerminalReset（term.reset 后折叠记录全 stale） */
  resync: (session_id: string, term: Terminal, ws: WebSocket | null, clean: boolean) => void
  /** 写入历史前确保 fit 完成（ensureFit） */
  ensureFit: (session_id: string, term: Terminal, fitAddon: FitAddon, ws: WebSocket | null) => Promise<void>
}

/**
 * 创建完整终端实例：xterm + FitAddon + WebSocket + 全部事件处理。
 * 返回的实例会经调用方登记进 terminals Map（此前的行为保持不变）。
 */
export function createTerminalInstance(spec: TerminalInstanceSpec): TerminalInstance {
  const {
    session_id,
    host_id,
    host_name,
    terminal_name,
    type,
    shell_type,
    local_starting,
    historyContent,
    historyLimit,
    connectMessage,
    ssh_conn,
    settings,
    containersRef,
    onData,
    copyShortcut,
    onCtrlF,
    setTerminals,
    requestClose,
    resync,
    ensureFit,
  } = spec

  const term = new Terminal({
    cursorBlink: true,
    theme: getTerminalTheme(settings),
    fontFamily: settings.fontFamily,
    fontSize: settings.fontSize,
    // 中文（微软雅黑）字形顶部超出 Consolas 度量的单元格，lineHeight=1 时顶部被裁切，
    // 给行高留出余量（1.2）确保 CJK 字形完整显示
    lineHeight: 1.2,
    // 保留足够滚动历史：clear/连接时不丢之前内容（只能滚动回看）
    scrollback: TERMINAL_SCROLLBACK_LINES,
    // xterm 6.0：自绘滚动条宽度（收窄到 6px；默认 14px）。滑块颜色在 getTerminalTheme
    overviewRuler: { width: 6 },
    // addon-search 的 decorations（总览标尺匹配标记 + onDidChangeResults 计数）依赖
    // proposed API（registerDecoration），必须显式开启，否则 findNext 直接抛异常
    allowProposedApi: true,
  })
  const fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  // 内容搜索（Ctrl+F）：装饰选项在每次 find 调用时传入（SEARCH_DECORATIONS）
  const searchAddon = new SearchAddon()
  term.loadAddon(searchAddon)

  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${wsProto}//${location.host}/ws/ssh/${session_id}`)

  term.onData((data) => {
    // 逐字符更新命令历史缓冲（保持 ESC 序列完整）
    onData(session_id, data)
  })
  setupTerminalCopy(term, copyShortcut, () => onCtrlF(session_id))

  // 采用"先声明后赋值"：handlers 异步执行时 instance 已就绪
  let instance!: TerminalInstance

  ws.onopen = () => {
    // 不做 term.reset()：历史加载会显示主机登录 banner，reset 会把它清掉
    term.focus()
    console.log('[Terminal] ws.onopen, initial size:', term.cols, 'x', term.rows)
    const doResize = () => {
      const container = containersRef.current?.get(session_id)
      if (container) {
        try { fitAddon.fit() } catch (e) { console.error('fit failed', e) }
        console.log('[Terminal] after fit, size:', term.cols, 'x', term.rows)
        // resyncTerminal 传 false：不清屏不重置（历史加载负责显示 banner/MOTD）
        // 只启用自动换行 + 发送 resize，确保输入到行尾时光标正常换行
        resync(session_id, term, ws, false)
      } else {
        console.log('[Terminal] container not found, skip resize')
      }
    }
    doResize()
    // 延迟再次调用，确保容器已注册且 xterm.js 渲染完成
    setTimeout(doResize, 50)
    setTimeout(doResize, 200)
    // 加载历史：先确保 fit（宽高就绪）再写入，避免历史内容按默认宽度折行导致
    // 显示错乱；不追加"加载完成"标记（无实际意义）
    if (historyContent) {
      // 立即写入旧历史（不等待 ensureFit）：新连接输出（banner）随后自然追加在
      // 历史之后，顺序正确（历史在上、新输出在下）；xterm 后续 fit/resize 会自动
      // reflow 折行，不会因当前 cols 未 fit 而 soft-wrap 碎片化
      term.write(reflowForCols(historyContent, term.cols))
    } else {
      // limit：完整回放同会话历史（同一终端 = 同一份记录）
      api.getHistoryLogs(session_id, 999999, historyLimit).then(async (res) => {
        if (res.content) {
          await ensureFit(session_id, term, fitAddon, ws)
          // 历史日志按 pty 当时宽度换行；窄窗口直接写入会 soft-wrap 碎片化，
          // 写入前按当前 cols 重新折行（ANSI 0 宽度计）
          term.write(reflowForCols(res.content, term.cols))
        }
      }).catch(() => {})
    }
    // 连接成功提示（重连流程）：绿色写入新终端，随后是主机 banner/回显
    if (connectMessage) term.write(`\r\n\x1b[32m[${connectMessage}]\x1b[0m\r\n`)
  }

  ws.onmessage = (event) => {
    try {
      // 断言为服务端消息判别联合（types/ws.ts）：msg.type 比较与字段访问获得编译期校验，
      // 后端消息类型/字段变更而此处未同步时，字面量比较或属性访问会直接报错
      const msg = JSON.parse(event.data) as WsServerMessage
      if (msg.type === 'output') {
        // 洪水输出走供给器：一次一块 + 内存上限，防 xterm 解析不过来卡 UI
        instance.feeder?.push(msg.data)
        // 首批输出已到：清除"正在启动"提示
        setTerminals((prev) => {
          const cur = prev.get(session_id)
          if (cur && cur.local_starting) {
            const next = new Map(prev)
            next.set(session_id, { ...cur, local_starting: false })
            return next
          }
          return prev
        })
      }
      else if (msg.type === 'ssh_connected') {
        // 本地终端拦截 SSH 后切换为远端终端：更新标签信息。
        // 同时保存连接信息（host/port/username/password）：此类会话没有已保存
        // 主机（host_id 为空），重连按钮需用这份凭据重建会话，否则报"无主机信息"
        setTerminals((prev) => {
          const next = new Map(prev)
          const cur = next.get(session_id)
          if (cur) {
            next.set(session_id, {
              ...cur,
              host_name: msg.terminal_name || `${msg.username}@${msg.host}`,
              terminal_name: msg.terminal_name || cur.terminal_name,
              type: 'ssh',
              shell_type: 'shell',
              host_id: '',
              ssh_conn: {
                host: msg.host,
                port: msg.port,
                username: msg.username,
                password: msg.password || '',
              },
            })
          }
          return next
        })
      }
      else if (msg.type === 'switched_to_local') {
        // SSH 退出/断开后后端自动切换到本机终端：标记 ssh_exited，保留原 host_id 供重连
        setTerminals((prev) => {
          const next = new Map(prev)
          const cur = next.get(session_id)
          if (cur) {
            next.set(session_id, {
              ...cur,
              ssh_exited: true,
              shell_type: 'local',
            })
          }
          return next
        })
      }
      else if (msg.type === 'error') term.write(`\r\n\x1b[31m[错误] ${msg.data}\x1b[0m\r\n`)
      else if (msg.type === 'info') term.write(`\r\n\x1b[33m[${msg.data}]\x1b[0m\r\n`)
      else if (msg.type === 'closed') {
        // 本机终端 exit 等：后端已移除会话，写入提示后延迟关闭标签与连接
        term.write(`\r\n\x1b[33m[${msg.data || '连接已关闭'}]\x1b[0m\r\n`)
        setTimeout(() => { requestClose(session_id).catch(() => {}) }, 800)
      }
    } catch {
      term.write(event.data)
    }
  }

  // WebSocket 意外断开（网络中断/后端主动关闭）：标记断开，不自动重连
  ws.onclose = () => markDisconnected(session_id, setTerminals)

  instance = {
    session_id,
    host_id,
    host_name,
    terminal_name,
    type,
    shell_type,
    term,
    fitAddon,
    ws,
    container: null,
    state_timer: null,
    input_buffer: '',
    input_cursor: 0,
    disconnected: false,
    reconnecting: false,
    ssh_exited: false,
    local_starting,
    feeder: createOutputFeeder({
      write: (data, cb) => term.write(data, cb),
      maxPendingBytes: MAX_PENDING_BYTES,
    }),
    ssh_conn,
    blockBar: null,
    search: searchAddon,
  }
  return instance
}