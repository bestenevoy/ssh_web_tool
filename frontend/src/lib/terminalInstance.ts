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
import type { HighlightDecorator } from './highlightDecorations'

// 终端回滚行数（单一定义源）：folds.ts 的 savedLines 缓存预算按
// 5000 − blockMaxLines − 1 计算，保证 visible + cached < scrollback
export const TERMINAL_SCROLLBACK_LINES = 5000

// 强调色（与 index.css 的 --accent / --accent-rgb 保持一致；
// xterm 走 canvas 渲染，无法读取 CSS 变量，只能在此集中定义字面量）
const ACCENT = '#4d6bfe'
const ACCENT_SELECTION = 'rgba(77, 107, 254, 0.3)'

// 内容搜索装饰（addon-search 0.16 的 decorations 是每次 findNext/findPrevious
// 的 ISearchOptions，非构造参数）：普通匹配淡蓝灰背景、当前匹配红色背景+红边框，
// 两者颜色明显区分；总览标尺同色系。背景用 #RRGGBBAA 带透明度（纯色会盖住终端文字）
export const SEARCH_DECORATIONS = {
  matchBackground: '#94a0ba40',
  matchOverviewRuler: '#94a0ba',
  activeMatchBackground: `${ACCENT}90`,
  activeMatchBorder: ACCENT,
  activeMatchColorOverviewRuler: ACCENT,
} as const

/**
 * 输入法（IME）字符吞字补丁——xterm 5.5~6.x 未修复的上游缺陷（xtermjs/xterm.js#5887）。
 *
 * 微软拼音等 IME 在"中文切英文"后仍对所有键上报 keyCode=229：keydown 置
 * _keyDownSeen=true，但按键不产生 keypress 投递；随后 input 事件（composed=true、
 * insertText）被 `_keyDownSeen` 门控丢弃——表现为切到英文后敲的字符进不了终端。
 * 这里在实例上遮蔽原型方法 _inputEvent：insertText 且门控被 _keyDownSeen 卡住时
 * 临时放行，让原实现走 triggerDataEvent 投递；原实现自带的 _keyPressHandled
 * 检查保证普通按键（keypress 已处理）不会二次发送。
 */
function patchImeKeyDownGate(term: Terminal) {
  type Gate = {
    _inputEvent?: (ev: Event) => boolean
    _keyDownSeen?: boolean
  }
  const core = term as unknown as Gate
  const orig = core._inputEvent
  if (typeof orig !== 'function') return
  core._inputEvent = function (this: Gate, ev: Event): boolean {
    const ie = ev as InputEvent
    if (ie.data && ie.inputType === 'insertText' && this._keyDownSeen) {
      const saved = this._keyDownSeen
      this._keyDownSeen = false
      try {
        return orig.call(this, ev)
      } finally {
        this._keyDownSeen = saved
      }
    }
    return orig.call(this, ev)
  }
}

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
  local_shell?: string  // 本地终端的具体 shell（cmd / powershell / pwsh），用于特性档案标注
  term: Terminal
  fitAddon: FitAddon
  ws: WebSocket | null
  container: HTMLDivElement | null
  state_timer: ReturnType<typeof setInterval> | null
  input_buffer: string  // 当前输入行缓冲区（用于记录命令历史）
  input_cursor: number  // 输入行光标位置（行编辑用，精确跟踪命令内容）
  disconnected: boolean  // 主动断开/意外断开标记（断开后可手动重连）
  reconnecting: boolean  // 重连建立中标记（Tab/终端遮罩显示"连接中"，期间禁止重复点击）
  pending: boolean  // 初始连接建立中标记（先建 tab 后连接的占位阶段，Tab 闪烁 + 终端遮罩）
  ssh_exited: boolean  // SSH exit 退出后自动切换到本地终端，可点重连回到原 SSH 主机
  local_starting: boolean  // 本机终端创建后等待首批输出（开启启动提示）；收到第一条 output 后变 false
  feeder: ReturnType<typeof createOutputFeeder> | null
  ssh_conn: SshConnInfo | null  // 本地拦截 SSH 会话的连接信息（重连凭据）；已保存主机会话为 null
  // 命令块渲染层（左侧色条 + 真折叠）：容器挂载（registerContainer → term.open）后创建
  blockBar: BlockBarController | null
  // 关键字高亮装饰层（rssh 同款）：容器挂载后创建（同 blockBar 模式），规则经
  // updateTerminalSettings 同步；dispose 走 closeTerminal
  highlight: HighlightDecorator | null
  // 终端内容搜索（Ctrl+F）：真折叠内容不在 buffer，打开搜索时先 unfoldAll（SearchBar 负责）
  search: SearchAddon
  // 容器尺寸观察器（registerContainer 挂载）：pywebview/WebView2 最大化、还原、
  // 拖边框时 window.resize 不一定派发，容器 ResizeObserver 是唯一可靠的重 fit 信号
  resizeObserver?: ResizeObserver | null
  // 观察器当前绑定的容器元素：React 行内 ref 每次重渲染会重挂同一元素，
  // 据此判断无需销毁重建观察器（重建会让 observe 初始回调对所有终端重 fit）
  resizeObservedEl?: HTMLDivElement | null
  // 重连后重建 WebSocket（断开时 ws 被置空/关闭，重建以恢复终端输出）
  reconnectWs?: () => void
}

// 洪水输出内存上限：xterm write 缓冲无上限（超 50MB 抛错），这里限 2MB 积压，
// 超限丢最旧整块（用户正打算 Ctrl+C 的洪水内容，丢一行可接受）
export const MAX_PENDING_BYTES = 2 * 1024 * 1024

// 终端主题（settings.theme → xterm options.theme）
// xterm 6.0 起：滚动条为自绘组件（宽度由 options.overviewRuler.width 决定，默认 14px），
// 滑块颜色走 theme.scrollbarSlider*；overviewRulerBorder 不钉透明会在滚动条旁
// 画出一条前景色竖线（rssh 同款处理）
export function getTerminalTheme(settings: TerminalSettings) {
  // 背景/前景色与 index.css --term-bg / --text-primary 手工同步（xterm 读不了 CSS 变量）
  if (settings.theme === 'light') {
    return {
      background: '#f7f9fc',
      foreground: '#1b2231',
      cursor: ACCENT,
      selectionBackground: ACCENT_SELECTION,
      // 滚动条滑块：沿用 6.0 之前 thumb 的边框系配色，hover/active 用次级文字色
      scrollbarSliderBackground: '#c4cedd',
      scrollbarSliderHoverBackground: '#9daabf',
      scrollbarSliderActiveBackground: '#77839c',
      overviewRulerBorder: 'rgba(0,0,0,0)',
    }
  }
  return {
    background: '#0d1017',
    foreground: '#dee5f2',
    cursor: ACCENT,
    selectionBackground: ACCENT_SELECTION,
    scrollbarSliderBackground: '#2f3a4e',
    scrollbarSliderHoverBackground: '#57647e',
    scrollbarSliderActiveBackground: '#94a0ba',
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
  local_shell?: string  // 本地终端的具体 shell（cmd/powershell/pwsh）；远端/临时会话不传
  local_starting: boolean
  // 延迟连接：true 时不立即创建 WebSocket（先建 tab 显示"连接中"，HTTP 连接成功后
  // 由调用方关闭占位 tab 并另建真实实例）；省略/false 保持立即建连
  deferConnect?: boolean
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
  /** 终端内 Ctrl+B：显示/折叠主机列表（App 经 useTerminals 的 setSidebarToggleHandler 接线） */
  onCtrlB: (session_id: string) => void
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
    local_shell,
    local_starting,
    deferConnect,
    historyContent,
    historyLimit,
    connectMessage,
    ssh_conn,
    settings,
    containersRef,
    onData,
    copyShortcut,
    onCtrlF,
    onCtrlB,
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
    // xterm 6：ED2（\x1b[2J，clear/cls/Ctrl+L）把被擦除的视口内容推入
    // scrollback 而非直接清空（PuTTY 式"窗口上移"），历史可滚动回看
    scrollOnEraseInDisplay: true,
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
  patchImeKeyDownGate(term)

  // 建立后端 WebSocket 并绑定全部消息处理。deferConnect=false 时在工厂末尾立即调用；
  // deferConnect=true 的占位实例不建连（"先建 tab 显示连接中，连接成功后另建真实实例"）
  const connectWs = () => {

    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/ssh/${session_id}`)

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
                // 后端按 host+port+user 匹配结果下发：匹配到新主机=新 id，未匹配=""（换挂后
                // 必须清掉旧挂载，不能让断开重连到别的主机的会话留在旧主机组）
                host_id: msg.host_id !== undefined ? msg.host_id : cur.host_id || '',
                ssh_exited: false, // 断开后曾退到本机 shell 的会话重新连上：清除离线标记
                // 换挂新 shell：旧连接上未回车的半截输入作废（防止串进新会话首条记录）
                input_buffer: '',
                input_cursor: 0,
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
                // 切到本机 shell：远端未回车的半截输入作废
                input_buffer: '',
                input_cursor: 0,
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

    instance.ws = ws
  }

  // 绑定终端键盘输入 + 复制/粘贴（与 WebSocket 无关，占位实例也需要）
  term.onData((data) => {
    // 逐字符更新命令历史缓冲（保持 ESC 序列完整）
    onData(session_id, data)
  })
  setupTerminalCopy(term, copyShortcut, () => onCtrlF(session_id), () => onCtrlB(session_id))

  // 采用"先声明后赋值"：connectWs 异步执行时 instance 已就绪
  // 跨块缓冲：clear 序列（\x1b[2J / \x1b[3J）可能被 feeder 分块拆分，
  // 保留块尾的不完整 CSI 序列，与下一块拼接后再做替换
  let pendingTail = ''
  const instance: TerminalInstance = {
    session_id,
    host_id,
    host_name,
    terminal_name,
    type,
    shell_type,
    local_shell,
    term,
    fitAddon,
    ws: null,
    container: null,
    state_timer: null,
    input_buffer: '',
    input_cursor: 0,
    disconnected: false,
    reconnecting: false,
    pending: false,
    ssh_exited: false,
    local_starting,
    feeder: createOutputFeeder({
      // clear/cls/Ctrl+L：先展开全部折叠（fold 的 savedLines 在内存里，滚动条
      // 不可见），让历史回到 buffer/scrollback；再由 scrollOnEraseInDisplay
      // 把视口内容推入 scrollback——clear 只是窗口上移、内容可滚动回看。
      // CSI 3 J 会清空整个 scrollback，与"保留历史"冲突，直接剥掉。
      // 跨块缓冲处理 feeder 分块拆分 clear 序列。
      write: (data, cb) => {
        const full = pendingTail + data
        if (full.includes('\x1b[2J')) {
          // clear：先展开全部折叠（fold 的 savedLines 在内存里、滚动条不可见），
          // 并暂停自动折叠直到下次输入；随后 scrollOnEraseInDisplay 把视口内容
          // 推入 scrollback——clear 只是窗口上移、历史可滚动回看
          instance.blockBar?.unfoldAll()
          instance.blockBar?.suppressAutoFoldUntilInput()
        }
        const cleaned = full.replace(/\x1b\[\s*3\s*J/g, '')
        const tailMatch = cleaned.match(/\x1b\[[0-9;?]*$/)
        pendingTail = tailMatch ? tailMatch[0] : ''
        const toWrite = tailMatch ? cleaned.slice(0, cleaned.length - tailMatch[0].length) : cleaned
        term.write(toWrite, cb)
      },
      maxPendingBytes: MAX_PENDING_BYTES,
    }),
    ssh_conn,
    blockBar: null,
    highlight: null,
    search: searchAddon,
    reconnectWs: connectWs,
  }
  if (!deferConnect) connectWs()
  return instance
}
