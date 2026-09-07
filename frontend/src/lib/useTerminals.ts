// 终端状态管理 hook
import { useState, useCallback, useRef, useEffect } from 'react'
import { Terminal } from 'xterm'
import { FitAddon } from '@xterm/addon-fit'
import type { Host } from '../types'
import { api } from './api'
import type { TerminalSettings } from './useSettings'

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
  const containersRef = useRef<Map<string, HTMLDivElement>>(new Map())
  // 快捷键处理函数（外部设置，用于打开搜索弹窗等）
  const shortcutHandlerRef = useRef<(() => void) | null>(null)
  // 保存最新的 settings 引用，用于回调中
  const settingsRef = useRef(settings)
  settingsRef.current = settings
  // 保存最新的 terminals 引用，用于回调中（解决闭包捕获旧状态导致 input_buffer 不记录的问题）
  const terminalsRef = useRef(terminals)
  terminalsRef.current = terminals

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

  // 终端重同步：清除缓冲区 + 启用自动换行 + 发送 Ctrl+L 让 shell 重绘 + 发送 resize
  // 用于初始连接时，确保 shell 第一帧输出使用正确的终端尺寸
  // 终端复制：选中内容立即复制；Ctrl+C 有选区时复制（不发送 SIGINT 到远端）
function copySelection(term: Terminal) {
  const sel = term.getSelection()
  if (!sel) return
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(sel).catch(() => {
      // clipboard API 被拒绝时兜底：用文本域 execCommand
      try {
        const ta = document.createElement('textarea')
        ta.value = sel
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      } catch { /* ignore */ }
    })
  } else {
    try {
      const ta = document.createElement('textarea')
      ta.value = sel
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    } catch { /* ignore */ }
  }
}

function setupTerminalCopy(term: Terminal) {
  // 1) 选中终端内容立即复制（无需 Ctrl+C）
  term.onSelectionChange(() => {
    if (term.hasSelection()) copySelection(term)
  })
  // 2) Ctrl+C：有选区时复制并阻止发送（避免打断远端正在运行的命令）
  term.attachCustomKeyEventHandler((e) => {
    if (e.ctrlKey && (e.key === 'c' || e.key === 'C')) {
      if (term.hasSelection()) {
        copySelection(term)
        return false  // 阻止 xterm 把 Ctrl+C 发送到终端
      }
    }
    return true
  })
}

const resyncTerminal = useCallback((term: Terminal, ws: WebSocket | null) => {
    if (ws && ws.readyState === WebSocket.OPEN && term) {
      // 1. 清除 xterm.js 缓冲区（丢弃可能使用错误尺寸渲染的内容）
      term.reset()
      // 2. 延迟到下一帧启用自动换行（DECSET 7），确保 term.reset() 完成后生效
      // reset 可能会重置自动换行模式，导致输入到行尾时光标回到行首覆盖内容
      requestAnimationFrame(() => {
        term.write('\x1b[?7h')  // 启用自动换行
        console.log('[Terminal] autowrap enabled, size:', term.cols, 'x', term.rows)
      })
      // 3. 发送 Ctrl+L（换页符），告诉 shell 清屏并重绘提示符
      ws.send(JSON.stringify({ type: 'input', data: '\x0c' }))
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
  useEffect(() => {
    terminals.forEach((inst) => {
      updateTerminalSettings(inst)
    })
  }, [settings, terminals, updateTerminalSettings])

  // 处理终端输入，维护输入行缓冲区，遇到回车时记录命令到后端
  // 注意：使用 terminalsRef.current 而不是 terminals，避免闭包捕获旧状态导致新终端的 input_buffer 不记录
  const handleTerminalInput = useCallback((session_id: string, data: string) => {
    const inst = terminalsRef.current.get(session_id)
    if (!inst) return
    // 回车：记录当前输入行到后端全局历史，清空缓冲区
    if (data === '\r' || data === '\n' || data === '\r\n') {
      if (inst.input_buffer.trim()) {
        api.recordCommand(inst.input_buffer).catch(() => {})
      }
      inst.input_buffer = ''
      return
    }
    // 退格：删除缓冲区最后一个字符
    if (data === '\x7f' || data === '\b') {
      inst.input_buffer = inst.input_buffer.slice(0, -1)
      return
    }
    // Ctrl+C / Ctrl+D：清空缓冲区
    if (data === '\x03' || data === '\x04') {
      inst.input_buffer = ''
      return
    }
    // 可打印字符：添加到缓冲区（支持多字符粘贴和非 ASCII 字符）
    if (data.length > 0 && data !== '\x00') {
      // 过滤掉纯控制字符（除了可打印字符和空格）
      const printable = data.split('').filter(c => c >= ' ' || c.charCodeAt(0) > 127).join('')
      if (printable) {
        inst.input_buffer += printable
      }
    }
    // 其他字符（方向键、Tab 等）：忽略，不影响缓冲区
  }, [])

  const createTerminal = useCallback(async (host: Host, terminal_name?: string) => {
    try {
      const result = await api.connectHost(host.id, terminal_name)
      const session_id = result.session_id
      const tname = result.terminal_name || terminal_name || '终端1'
      const s = settingsRef.current

      const term = new Terminal({
        cursorBlink: true,
        theme: getTerminalTheme(s),
        fontFamily: s.fontFamily,
        fontSize: s.fontSize,
      })
      const fitAddon = new FitAddon()
      term.loadAddon(fitAddon)

      const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${wsProto}//${location.host}/ws/ssh/${session_id}`)

      term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'input', data }))
        }
        handleTerminalInput(session_id, data)
      })
      setupTerminalCopy(term)

      // 检测快捷键 Alt+R（终端获得焦点时也能触发）
      term.onKey(({ domEvent }) => {
        if (domEvent.altKey && (domEvent.key === 'r' || domEvent.key === 'R')) {
          domEvent.preventDefault()
          if (shortcutHandlerRef.current) {
            shortcutHandlerRef.current()
          }
        }
      })

      ws.onopen = () => {
        term.reset()
        term.focus()
        console.log('[Terminal] ws.onopen, initial size:', term.cols, 'x', term.rows)
        const doResize = () => {
          const container = containersRef.current.get(session_id)
          if (container) {
            try { fitAddon.fit() } catch (e) { console.error('fit failed', e) }
            console.log('[Terminal] after fit, size:', term.cols, 'x', term.rows)
            // 使用 resyncTerminal：reset + 启用自动换行 + Ctrl+L + resize
            // 确保自动换行被启用，防止输入到行尾时光标回到行首覆盖
            resyncTerminal(term, ws)
          } else {
            console.log('[Terminal] container not found, skip resize')
          }
        }
        doResize()
        // 延迟再次调用，确保容器已注册且 xterm.js 渲染完成
        setTimeout(doResize, 50)
        setTimeout(doResize, 200)
        // 加载历史输出
        api.getHistoryLogs(session_id, 0, 3000).then((res) => {
          if (res.content) {
            term.write(res.content)
            term.write('\r\n\x1b[33m[--- 历史输出加载完成 ---]\x1b[0m\r\n')
          }
        }).catch(() => {})
      }

      // 定期检测终端状态（用于 tab 显示 shell 类型）
      const state_timer = setInterval(() => {
        api.getSessionState(session_id).then((state) => {
          setTerminals((prev) => {
            const inst = prev.get(session_id)
            if (inst) {
              const newType = getShellTypeLabel(state)
              if (inst.shell_type !== newType) {
                const next = new Map(prev)
                next.set(session_id, { ...inst, shell_type: newType })
                return next
              }
            }
            return prev
          })
        }).catch(() => {})
      }, 3000)

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

      const instance: TerminalInstance = {
        session_id,
        host_id: host.id,
        host_name: host.name || host.host,
        terminal_name: tname,
        type: host.type,
        shell_type: 'shell',
        term,
        fitAddon,
        ws,
        container: null,
        state_timer,
        input_buffer: '',
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
    }
  }, [fitTerminal, sendResize, resyncTerminal, handleTerminalInput])

  const restoreTerminals = useCallback(async () => {
    try {
      const { terminals: active } = await api.listActiveTerminals()
      const s = settingsRef.current
      for (const t of active) {
        // 跳过已连接的终端，避免重复连接
        if (terminalsRef.current.has(t.session_id)) {
          continue
        }
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
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'input', data }))
          }
          handleTerminalInput(t.session_id, data)
        })
        setupTerminalCopy(term)

        // 检测快捷键 Alt+R（终端获得焦点时也能触发）
        term.onKey(({ domEvent }) => {
          if (domEvent.altKey && (domEvent.key === 'r' || domEvent.key === 'R')) {
            domEvent.preventDefault()
            if (shortcutHandlerRef.current) {
              shortcutHandlerRef.current()
            }
          }
        })

        ws.onopen = () => {
          term.focus()
          console.log('[Terminal] restore ws.onopen, size:', term.cols, 'x', term.rows)
          const doResize = () => {
            const container = containersRef.current.get(t.session_id)
            if (container) {
              try { fitAddon.fit() } catch (e) { console.error('fit failed', e) }
              console.log('[Terminal] restore after fit, size:', term.cols, 'x', term.rows)
              // 恢复终端时也使用 resyncTerminal，确保自动换行被启用
              resyncTerminal(term, ws)
            }
          }
          doResize()
          setTimeout(doResize, 50)
          setTimeout(doResize, 200)
          // 加载历史输出
          api.getHistoryLogs(t.session_id, 0, 3000).then((res) => {
            if (res.content) {
              term.write(res.content)
              term.write('\r\n\x1b[33m[--- 历史输出加载完成 ---]\x1b[0m\r\n')
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

        // 定期检测终端状态
        const state_timer = setInterval(() => {
          api.getSessionState(t.session_id).then((state) => {
            setTerminals((prev) => {
              const inst = prev.get(t.session_id)
              if (inst) {
                const newType = getShellTypeLabel(state)
                if (inst.shell_type !== newType) {
                  const next = new Map(prev)
                  next.set(t.session_id, { ...inst, shell_type: newType })
                  return next
                }
              }
              return prev
            })
          }).catch(() => {})
        }, 3000)

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
        }

        setTerminals((prev) => {
          const next = new Map(prev)
          next.set(t.session_id, instance)
          return next
        })
        if (!activeId) setActiveId(t.session_id)
      }
    } catch (e) {
      console.error('恢复终端失败', e)
    }
  }, [activeId, fitTerminal, sendResize, handleTerminalInput])

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

  const closeTerminal = useCallback(async (session_id: string, closeBackend: boolean) => {
    const inst = terminals.get(session_id)
    if (!inst) return
    if (inst.ws) inst.ws.close()
    if (inst.state_timer) clearInterval(inst.state_timer)
    if (closeBackend) {
      try { await api.closeSession(session_id) } catch {}
    }
    setTerminals((prev) => {
      const next = new Map(prev)
      next.delete(session_id)
      return next
    })
    if (activeId === session_id) {
      const remaining = Array.from(terminals.keys()).filter((id) => id !== session_id)
      setActiveId(remaining.length > 0 ? remaining[0] : null)
    }
  }, [terminals, activeId])

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
            resyncTerminal(inst.term, inst.ws)
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
    activeSessions,
    createTerminal,
    restoreTerminals,
    switchTerminal,
    closeTerminal,
    sendCommand,
    registerContainer,
    focusActiveTerminal,
    setShortcutHandler,
  }
}
