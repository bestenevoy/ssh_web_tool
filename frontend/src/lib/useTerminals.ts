// 终端状态管理 hook
import { useState, useCallback, useRef, useEffect } from 'react'
import { Terminal } from 'xterm'
import { FitAddon } from '@xterm/addon-fit'
import type { Host } from '../types'
import { api } from './api'
import type { TerminalSettings } from './useSettings'
import { reflowForCols } from './reflow'
import { setupTerminalCopy } from './terminalCopy'

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
}

// 获取 shell 类型标签
function getShellTypeLabel(state: any): string {
  if (state?.in_python) return 'python'
  if (state?.in_mysql) return 'mysql'
  if (state?.in_pager) return 'pager'
  if (state?.in_shell) return 'shell'
  return 'other'
}

// 获取终端主题配置
function getTerminalTheme(settings: TerminalSettings) {
  if (settings.theme === 'light') {
    return {
      background: '#fafafa',
      foreground: '#1a1a2e',
      cursor: '#e94560',
      selectionBackground: 'rgba(233, 69, 96, 0.3)',
    }
  }
  return {
    background: '#0a0e1a',
    foreground: '#c8d0e0',
    cursor: '#e94560',
    selectionBackground: 'rgba(233, 69, 96, 0.3)',
  }
}

export function useTerminals(settings: TerminalSettings) {
  const [terminals, setTerminals] = useState<Map<string, TerminalInstance>>(new Map())
  const [activeId, setActiveId] = useState<string | null>(null)
  // 连接防抖：同一时间只允许一个连接建立中，避免快速点击多个主机并发连接
  const [connecting, setConnecting] = useState(false)
  const connectingRef = useRef(false)
  const containersRef = useRef<Map<string, HTMLDivElement>>(new Map())
  // 快捷键处理函数（外部设置，用于打开搜索弹窗等）
  const shortcutHandlerRef = useRef<(() => void) | null>(null)
  // 保存最新的 settings 引用，用于回调中
  const settingsRef = useRef(settings)
  settingsRef.current = settings
  // 保存最新的 terminals 引用，用于回调中（解决闭包捕获旧状态导致 input_buffer 不记录的问题）
  const terminalsRef = useRef(terminals)
  terminalsRef.current = terminals
  // 输入合并发送：键盘输入按 50ms 窗口合并后一次 WS 发送，避免逐字符通信
  // （存储阵列等慢速 SSH 服务在大量小包时可能挂死 channel，合并大幅减少 write 次数）
  const inputSendBufferRef = useRef<Map<string, string>>(new Map())
  const inputFlushTimerRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  // 合并发送 flush：把缓冲的输入一次性发给后端
  const flushInputBuffer = useCallback((session_id: string) => {
    const b = inputSendBufferRef.current.get(session_id) || ''
    inputSendBufferRef.current.delete(session_id)
    inputFlushTimerRef.current.delete(session_id)
    if (!b) return
    const inst = terminalsRef.current.get(session_id)
    if (inst && inst.ws && inst.ws.readyState === WebSocket.OPEN) {
      inst.ws.send(JSON.stringify({ type: 'input', data: b }))
    }
  }, [])

  // 把输入字符追加到合并缓冲（逐字符记录历史，合并后发送）
  const appendInput = useCallback((session_id: string, data: string) => {
    const buf = inputSendBufferRef.current.get(session_id) || ''
    inputSendBufferRef.current.set(session_id, buf + data)
    if (!inputFlushTimerRef.current.has(session_id)) {
      inputFlushTimerRef.current.set(session_id, setTimeout(() => flushInputBuffer(session_id), 50))
    }
  }, [flushInputBuffer])

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
  const fitTerminal = useCallback((inst: TerminalInstance) => {
    if (inst.container) {
      try {
        inst.fitAddon.fit()
        const el = inst.term.element
        if (el && el.scrollHeight > el.clientHeight + 2 && inst.term.rows > 5) {
          inst.term.resize(inst.term.cols, inst.term.rows - 1)
        }
      } catch (e) {
        console.error('FitAddon fit failed', e)
      }
    }
  }, [])

  const sendResize = useCallback((term: Terminal, ws: WebSocket | null) => {
    if (ws && ws.readyState === WebSocket.OPEN && term) {
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
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

  // 终端重同步：清除缓冲区 + 启用自动换行 + 发送 Ctrl+L 让 shell 重绘 + 发送 resize
  // 用于初始连接时，确保 shell 第一帧输出使用正确的终端尺寸
  // （终端复制/粘贴逻辑见 terminalCopy.ts：选中复制、Ctrl+C 复制、Ctrl+V 单次粘贴）

// 标记终端为断开状态（ws 关闭时调用）
function markDisconnected(term: Terminal, session_id: string, setTerminals: React.Dispatch<React.SetStateAction<Map<string, TerminalInstance>>>) {
  try { term.write('\r\n\x1b[31m[连接已断开，点击顶部「🔗 重连」重新连接]\x1b[0m\r\n') } catch {}
  setTerminals((prev) => {
    const next = new Map(prev)
    const cur = next.get(session_id)
    if (cur && !cur.disconnected) {
      // state_timer 已改为批量轮询，不再需要单独清理
      next.set(session_id, { ...cur, disconnected: true, ws: null })
    }
    return next
  })
}

const resyncTerminal = useCallback((term: Terminal, ws: WebSocket | null, clean_screen: boolean = true) => {
    if (ws && ws.readyState === WebSocket.OPEN && term) {
      // clean_screen=true（默认）：清除 xterm 缓冲区 + 发送 Ctrl+L 让 shell 清屏重绘
      // 用于恢复/重连场景；首次连接传 false，避免清掉主机 banner/MOTD 等登录信息
      if (clean_screen) {
        // 1. 清除 xterm.js 缓冲区（丢弃可能使用错误尺寸渲染的内容）
        term.reset()
        // 3. 发送 Ctrl+L（换页符），告诉 shell 清屏并重绘提示符
        ws.send(JSON.stringify({ type: 'input', data: '\x0c' }))
      }
      // 2. 延迟到下一帧启用自动换行（DECSET 7）
      requestAnimationFrame(() => {
        term.write('\x1b[?7h')  // 启用自动换行
        console.log('[Terminal] autowrap enabled, size:', term.cols, 'x', term.rows)
      })
      // 4. 发送 resize 消息，应用新尺寸
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      console.log('[Terminal] resync size:', term.cols, 'x', term.rows)
    }
  }, [])

  // 更新单个终端的设置（字体、大小、主题）
  const updateTerminalSettings = useCallback((inst: TerminalInstance) => {
    const s = settingsRef.current
    inst.term.options.fontFamily = s.fontFamily
    inst.term.options.fontSize = s.fontSize
    inst.term.options.theme = getTerminalTheme(s)
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
  const handleTerminalInput = useCallback((session_id: string, data: string) => {
    const inst = terminalsRef.current.get(session_id)
    if (!inst) return
    const buf = inst.input_buffer
    const cur = inst.input_cursor

    // 回车：先由前端按键盘输入即时记录（兜底，保证任何 PS1 环境下都有历史），
    // 后端随后解析终端回显行（含 Tab 补全/历史翻查后的真实命令）：
    // record_echo_command 会清理 3 秒内的前缀残留（如 cd /va）并去重，以后端为准
    if (data === '\r' || data === '\n' || data === '\r\n') {
      if (buf.trim()) {
        api.recordCommand(buf.trim()).catch(() => {})
      }
      inst.input_buffer = ''
      inst.input_cursor = 0
      return
    }
    // 退格：删除光标前一个字符
    if (data === '\x7f' || data === '\b') {
      if (cur > 0) {
        inst.input_buffer = buf.slice(0, cur - 1) + buf.slice(cur)
        inst.input_cursor = cur - 1
      }
      return
    }
    // Ctrl+C / Ctrl+D：清空缓冲区
    if (data === '\x03' || data === '\x04') {
      inst.input_buffer = ''
      inst.input_cursor = 0
      return
    }
    // ESC 序列（方向键/Home/End/Delete 等）：整段解析，绝不写入缓冲区
    // （之前只过滤 \x1b 字符，导致 [D/[C 这类残留被当成可打印字符混入命令历史）
    if (data.startsWith('\x1b')) {
      switch (data) {
        case '\x1b[D': inst.input_cursor = Math.max(0, cur - 1); return  // ← 光标左移
        case '\x1b[C': inst.input_cursor = Math.min(buf.length, cur + 1); return  // → 光标右移
        case '\x1b[H': inst.input_cursor = 0; return  // Home 行首
        case '\x1b[F': inst.input_cursor = buf.length; return  // End 行尾
        case '\x1b[3~':  // Delete：删除光标处字符
          if (cur < buf.length) inst.input_buffer = buf.slice(0, cur) + buf.slice(cur + 1)
          return
        case '\x1b[1;5D':  // Ctrl+←：跳到上一个词
          inst.input_cursor = Math.max(0, buf.lastIndexOf(' ', Math.max(0, cur - 1)))
          return
        case '\x1b[1;5C': {  // Ctrl+→：跳到下一个词
          const np = buf.indexOf(' ', cur)
          inst.input_cursor = np === -1 ? buf.length : np + 1
          return
        }
        default:
          return  // 其他序列（上/下箭头、功能键等）：忽略，不影响缓冲区
      }
    }
    // Ctrl+A 行首 / Ctrl+E 行尾 / Ctrl+U 清行
    if (data === '\x01') { inst.input_cursor = 0; return }
    if (data === '\x05') { inst.input_cursor = buf.length; return }
    if (data === '\x15') { inst.input_buffer = ''; inst.input_cursor = 0; return }
    // 可打印字符：插入到光标位置（支持多字符粘贴和非 ASCII 字符）
    if (data.length > 0 && data !== '\x00') {
      const printable = data.split('').filter(c => c >= ' ' || c.charCodeAt(0) > 127).join('')
      if (printable) {
        inst.input_buffer = buf.slice(0, cur) + printable + buf.slice(cur)
        inst.input_cursor = cur + printable.length
      }
    }
  }, [])

  const createTerminal = useCallback(async (host: Host, terminal_name?: string, kind: 'ssh' | 'local' = 'ssh', localShell = 'cmd') => {
    // 连接防抖：已有连接正在建立时拒绝新的连接请求
    if (connectingRef.current) {
      const err = new Error('已有连接正在建立中，请稍候再试')
      ;(err as any).isConnecting = true
      throw err
    }
    connectingRef.current = true
    setConnecting(true)
    try {
      let session_id: string
      let tname: string
      let hostLike: { id: string; host: string; name: string; type: string }
      if (kind === 'local') {
        // 本机终端（cmd/powershell，winpty ConPTY，不经过 SSH）
        const r = await api.createLocalSession(localShell)
        session_id = r.session_id
        tname = r.terminal_name || terminal_name || (localShell === 'powershell' ? '本机 PowerShell' : '本机 cmd')
        hostLike = { id: 'local', host: 'localhost', name: tname, type: 'local' }
      } else {
        const result = await api.connectHost(host.id, terminal_name)
        session_id = result.session_id
        tname = result.terminal_name || terminal_name || '终端1'
        hostLike = { id: host.id, host: host.host, name: host.name || host.host, type: host.type }
      }
      const s = settingsRef.current

      const term = new Terminal({
        cursorBlink: true,
        theme: getTerminalTheme(s),
        fontFamily: s.fontFamily,
        fontSize: s.fontSize,
        // 保留足够滚动历史：clear/连接时不丢之前内容（只能滚动回看）
        scrollback: 5000,
      })
      const fitAddon = new FitAddon()
      term.loadAddon(fitAddon)

      const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${wsProto}//${location.host}/ws/ssh/${session_id}`)

      term.onData((data) => {
        // 逐字符更新命令历史缓冲（保持 ESC 序列完整）
        handleTerminalInput(session_id, data)
        // 合并发送：15ms 窗口内积累后一次 WS 发送，减少 SSH 通信次数
        appendInput(session_id, data)
      })
      setupTerminalCopy(term, () => shortcutHandlerRef.current?.())

      ws.onopen = () => {
        // 不做 term.reset()：历史加载会显示主机登录 banner，reset 会把它清掉
        term.focus()
        console.log('[Terminal] ws.onopen, initial size:', term.cols, 'x', term.rows)
        const doResize = () => {
          const container = containersRef.current.get(session_id)
          if (container) {
            try { fitAddon.fit() } catch (e) { console.error('fit failed', e) }
            console.log('[Terminal] after fit, size:', term.cols, 'x', term.rows)
            // resyncTerminal 传 false：不清屏不重置（历史加载负责显示 banner/MOTD）
            // 只启用自动换行 + 发送 resize，确保输入到行尾时光标正常换行
            resyncTerminal(term, ws, false)
          } else {
            console.log('[Terminal] container not found, skip resize')
          }
        }
        doResize()
        // 延迟再次调用，确保容器已注册且 xterm.js 渲染完成
        setTimeout(doResize, 50)
        setTimeout(doResize, 200)
        // 加载完整历史日志（含主机登录 banner/MOTD）：先确保 fit（宽高就绪）再写入，
        // 避免历史内容按默认宽度折行导致显示错乱；不追加"加载完成"标记（无实际意义）
        // limit 200000：完整回放同会话历史（同一终端 = 同一份记录）
        api.getHistoryLogs(session_id, 999999, 200000).then(async (res) => {
          if (res.content) {
            await ensureFit(session_id, term, fitAddon, ws)
            // 历史日志按 pty 当时宽度换行；窄窗口直接写入会 soft-wrap 碎片化，
            // 写入前按当前 cols 重新折行（ANSI 0 宽度计）
            term.write(reflowForCols(res.content, term.cols))
          }
        }).catch(() => {})
      }

      // 定期检测终端状态（用于 tab 显示 shell 类型）
      // 已改为批量轮询（由外层 useEffect 统一处理，减少 HTTP 请求）
      const state_timer = null

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'output') term.write(msg.data)
          else if (msg.type === 'error') term.write(`\r\n\x1b[31m[错误] ${msg.data}\x1b[0m\r\n`)
          else if (msg.type === 'info') term.write(`\r\n\x1b[33m[${msg.data}]\x1b[0m\r\n`)
          else if (msg.type === 'closed') term.write('\r\n\x1b[33m[连接已关闭]\x1b[0m\r\n')
        } catch {
          term.write(event.data)
        }
      }

      // WebSocket 意外断开（网络中断/后端主动关闭）：标记断开，不自动重连
      ws.onclose = () => markDisconnected(term, session_id, setTerminals)

      const instance: TerminalInstance = {
        session_id,
        host_id: hostLike.id,
        host_name: hostLike.name || hostLike.host,
        terminal_name: tname,
        type: hostLike.type,
        shell_type: kind === 'local' ? 'local' : 'shell',
        term,
        fitAddon,
        ws,
        container: null,
        state_timer,
        input_buffer: '',
        input_cursor: 0,
        disconnected: false,
      }

      setTerminals((prev) => {
        const next = new Map(prev)
        next.set(session_id, instance)
        return next
      })
      setActiveId(session_id)
      return session_id
    } catch (e) {
      console.error('创建终端失败', e)
      throw e
    } finally {
      connectingRef.current = false
      setConnecting(false)
    }
  }, [fitTerminal, sendResize, resyncTerminal, handleTerminalInput, appendInput, ensureFit])

  const restoreTerminals = useCallback(async () => {
    try {
      const { terminals: active } = await api.listActiveTerminals()
      const s = settingsRef.current
      // 并行恢复所有终端（原 for...of 串行创建，3 个终端延迟累加）
      const toRestore = active.filter(t => !terminalsRef.current.has(t.session_id))
      await Promise.all(toRestore.map(t => {
        const term = new Terminal({
          cursorBlink: true,
          theme: getTerminalTheme(s),
          fontFamily: s.fontFamily,
          fontSize: s.fontSize,
        })
        const fitAddon = new FitAddon()
        term.loadAddon(fitAddon)

        const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
        const ws = new WebSocket(`${wsProto}//${location.host}/ws/ssh/${t.session_id}`)

        term.onData((data) => {
          handleTerminalInput(t.session_id, data)
          appendInput(t.session_id, data)
        })
        setupTerminalCopy(term, () => shortcutHandlerRef.current?.())

        ws.onopen = () => {
          term.focus()
          console.log('[Terminal] restore ws.onopen, size:', term.cols, 'x', term.rows)
          const doResize = () => {
            const container = containersRef.current.get(t.session_id)
            if (container) {
              try { fitAddon.fit() } catch (e) { console.error('fit failed', e) }
              console.log('[Terminal] restore after fit, size:', term.cols, 'x', term.rows)
              // 恢复终端时 resyncTerminal 传 false：不清屏不重置，保留历史日志（含 banner）
              resyncTerminal(term, ws, false)
            }
          }
          doResize()
          setTimeout(doResize, 50)
          setTimeout(doResize, 200)
          // 加载历史输出：先确保 fit 再写入（避免按默认宽度折行错乱），不追加"加载完成"标记
          api.getHistoryLogs(t.session_id, 999999, 8000).then(async (res) => {
            if (res.content) {
              await ensureFit(t.session_id, term, fitAddon, ws)
              term.write(reflowForCols(res.content, term.cols))
            }
          }).catch(() => {})
        }

        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data)
            if (msg.type === 'output') term.write(msg.data)
            else if (msg.type === 'error') term.write(`\r\n\x1b[31m[错误] ${msg.data}\x1b[0m\r\n`)
            else if (msg.type === 'info') term.write(`\r\n\x1b[33m[${msg.data}]\x1b[0m\r\n`)
            else if (msg.type === 'closed') term.write('\r\n\x1b[33m[连接已关闭]\x1b[0m\r\n')
          } catch {
            term.write(event.data)
          }
        }

        // 意外断开：标记断开，不自动重连
        ws.onclose = () => markDisconnected(term, t.session_id, setTerminals)

        // 终端状态检测已改为批量轮询（由外层 useEffect 统一处理）
        const state_timer = null

        const instance: TerminalInstance = {
          session_id: t.session_id,
          host_id: t.host_id,
          host_name: t.host_name || t.host,
          terminal_name: t.terminal_name || '终端',
          type: t.host_type,
          shell_type: 'shell',
          term,
          fitAddon,
          ws,
          container: null,
          state_timer,
          input_buffer: '',
          input_cursor: 0,
          disconnected: false,
        }

        setTerminals((prev) => {
          const next = new Map(prev)
          next.set(t.session_id, instance)
          return next
        })
        if (!activeId) setActiveId(t.session_id)
        return t.session_id
      }))
    } catch (e) {
      console.error('恢复终端失败', e)
    }
  }, [activeId, fitTerminal, sendResize, handleTerminalInput, appendInput, ensureFit])

  // 定期同步活跃终端列表，并自动恢复新创建的终端（CLI/Python 包创建的）
  useEffect(() => {
    syncActiveSessions()
    restoreTerminals()
    const timer = setInterval(() => {
      syncActiveSessions()
      restoreTerminals()
    }, 5000)
    return () => clearInterval(timer)
  }, [syncActiveSessions, restoreTerminals])

  // 批量终端状态轮询：所有终端合并为一次 HTTP 请求（每 3 秒）
  // 优化：原每个终端独立 setInterval 3 秒轮询，5 个终端 = 每秒 ~1.7 次 HTTP 请求
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
    }, 3000)
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
    // 清理输入合并缓冲与定时器
    inputSendBufferRef.current.delete(session_id)
    const t = inputFlushTimerRef.current.get(session_id)
    if (t) { clearTimeout(t); inputFlushTimerRef.current.delete(session_id) }
    if (inst.ws) inst.ws.close()
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

  // 手动断开：关闭 WebSocket + 后端会话（后端删除后不会再被自动恢复），保留标签等待重连
  const disconnectTerminal = useCallback(async (session_id: string) => {
    const inst = terminals.get(session_id)
    if (!inst || inst.disconnected) return
    try { await api.closeSession(session_id) } catch {}
    if (inst.ws) { try { inst.ws.close() } catch {} }
    // state_timer 已改为批量轮询，不再需要单独清理
    setTerminals((prev) => {
      const next = new Map(prev)
      const cur = next.get(session_id)
      if (cur) next.set(session_id, { ...cur, disconnected: true, ws: null })
      return next
    })
  }, [terminals])

  const sendCommand = useCallback((command: string, execute: boolean = true) => {
    if (!activeId) return
    const inst = terminals.get(activeId)
    if (inst && inst.ws && inst.ws.readyState === WebSocket.OPEN) {
      if (execute) {
        inst.ws.send(JSON.stringify({ type: 'input', data: command + '\n' }))
        api.recordCommand(command).catch(() => {})
        inst.input_buffer = ''
      } else {
        // 只输入命令文本，不发送换行，用户可以编辑后手动执行
        inst.ws.send(JSON.stringify({ type: 'input', data: command }))
        inst.input_buffer = command
        inst.input_cursor = command.length
      }
      // 输入/执行后光标聚焦到终端，方便直接继续输入
      inst.term.focus()
    }
  }, [activeId, terminals])

  // 设置快捷键处理函数（Alt+R 打开搜索弹窗）
  const setShortcutHandler = useCallback((handler: (() => void) | null) => {
    shortcutHandlerRef.current = handler
  }, [])

  const registerContainer = useCallback((session_id: string, el: HTMLDivElement | null) => {
    if (el) {
      containersRef.current.set(session_id, el)
      const inst = terminals.get(session_id)
      if (inst && !inst.container) {
        inst.term.open(el)
        inst.container = el
        // 终端真正打开后，使用 resyncTerminal 确保 xterm.js 状态正确
        // 关键：先 fit 计算正确的 cols/rows，再 reset 清除可能错误的状态，再发送 resize 和 Ctrl+L
        fitTerminal(inst)
        // 延迟到下一帧，确保 xterm.js 完成渲染后再 resync
        requestAnimationFrame(() => {
          if (inst.container) {
            fitTerminal(inst)
            // 首次打开不清屏不重置：保留主机登录 banner/MOTD 等连接信息
            resyncTerminal(inst.term, inst.ws, false)
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
  }, [terminals, fitTerminal, sendResize, resyncTerminal])

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
    connecting,
    activeSessions,
    createTerminal,
    // 打开本机终端（cmd / powershell，winpty ConPTY，不经过 SSH）
    openLocalTerminal: (shell: 'cmd' | 'powershell') =>
      createTerminal({} as Host, undefined, 'local', shell),
    restoreTerminals,
    switchTerminal,
    closeTerminal,
    disconnectTerminal,
    sendCommand,
    registerContainer,
    focusActiveTerminal,
    setShortcutHandler,
  }
}
