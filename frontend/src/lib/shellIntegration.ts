/**
 * S1 shell 集成（OSC 133/633 事件解析）。
 *
 * 后端在 SSH 交互 shell 建立后注入 precmd 片段（sessions.py
 * SHELL_INTEGRATION_SNIPPET），远端 shell 在每个提示符周期向输出流写
 * OSC 转义上报权威事件，本模块挂在 xterm parser 上拦截解析：
 *   OSC 133;A          提示符开始 → 命令块切块锚点（权威，替代猜测）
 *   OSC 133;D;<rc>     上一条命令退出码 → 块失败标记
 *   OSC 633;E;<b64>    实际执行的命令全文（base64/UTF-8，Tab 补全后的定稿）→ 历史
 *   OSC 633;P;Cwd=<p>  当前工作目录 → cd 跟踪权威源
 *   OSC 633;SI;ready   集成生效回执
 *
 * 协议与 Ghostty / VSCode shell integration 同源；只注册 133/633 两个
 * OSC 码，未知子类型返回 false 放行给 xterm 默认处理。
 */
import type { Terminal, IDisposable } from '@xterm/xterm'

export type ShellIntegrationEvent =
  | { kind: 'ready' }
  | { kind: 'prompt' }
  | { kind: 'exit'; exitCode: number }
  | { kind: 'command'; command: string }
  | { kind: 'cwd'; cwd: string }

/** base64 → UTF-8 文本；非法输入返回 null（宁丢一条不误记）。 */
export function decodeB64Utf8(b64: string): string | null {
  try {
    const bin = atob(b64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
    return new TextDecoder('utf-8').decode(bytes)
  } catch {
    return null
  }
}

/**
 * 纯函数解析：OSC 码 + payload → 事件（不识别返回 null，便于单测）。
 * payload 为 OSC 数字前缀之后、终止符（BEL/ST）之前的原文。
 */
export function parseShellIntegrationOsc(code: number, payload: string): ShellIntegrationEvent | null {
  if (code === 133) {
    if (payload === 'A' || payload.startsWith('A;')) return { kind: 'prompt' }
    if (payload.startsWith('D;')) {
      const rc = Number.parseInt(payload.slice(2), 10)
      return Number.isFinite(rc) ? { kind: 'exit', exitCode: rc } : null
    }
    return null
  }
  if (code === 633) {
    if (payload === 'SI' || payload.startsWith('SI;')) return { kind: 'ready' }
    if (payload.startsWith('E;')) {
      const cmd = decodeB64Utf8(payload.slice(2).trim())
      return cmd === null ? null : { kind: 'command', command: cmd }
    }
    // P;Cwd=/path——路径可含空格，取等号后全部
    if (payload.startsWith('P;Cwd=')) return { kind: 'cwd', cwd: payload.slice(6) }
    return null
  }
  return null
}

export interface ShellIntegrationHandlers {
  /** 633;SI;ready 或首个权威事件：上层据此置位"集成已生效" */
  onReady?(): void
  onPrompt?(): void
  onExit?(exitCode: number): void
  onCommand?(command: string): void
  onCwd?(cwd: string): void
}

export function attachShellIntegration(term: Terminal, h: ShellIntegrationHandlers): IDisposable {
  const dispatch = (code: number, payload: string): boolean => {
    const ev = parseShellIntegrationOsc(code, payload)
    if (!ev) return false
    switch (ev.kind) {
      case 'ready':
        h.onReady?.()
        return true
      case 'prompt':
        // 部分终端主题/插件不发 SI（如自带集成），A 同样代表集成在生效
        h.onReady?.()
        h.onPrompt?.()
        return true
      case 'exit':
        h.onExit?.(ev.exitCode)
        return true
      case 'command':
        h.onCommand?.(ev.command)
        return true
      case 'cwd':
        h.onReady?.()
        h.onCwd?.(ev.cwd)
        return true
    }
  }
  const d133 = term.parser.registerOscHandler(133, (data) => dispatch(133, data))
  const d633 = term.parser.registerOscHandler(633, (data) => dispatch(633, data))
  return {
    dispose: () => {
      d133.dispose()
      d633.dispose()
    },
  }
}
