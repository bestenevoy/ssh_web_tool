// 终端行编辑纯函数：维护输入行缓冲区（光标移动/退格/清行/插入/Ctrl 快捷键）
// 与 React / WebSocket 解耦，便于单元测试；历史记录、洪水丢弃等副作用由调用方处理
// 原逻辑在 useTerminals.ts 的 handleTerminalInput 中内联（P0-③ 抽取）

export interface LineEditState {
  buffer: string
  cursor: number
}

/** 输入行起点锚：首字符按下时光标所在终端物理行/列（= 提示符结束位置） */
export interface LineAnchor {
  row: number
  col: number
}

/** captureTypedLine 所需的最小终端面（xterm buffer.active 子集，可 mock 单测） */
export interface BufferSource {
  buffer: {
    active: {
      cursorY: number
      getLine(y: number): { translateToString(trimRight?: boolean): string } | undefined
    }
  }
}

/**
 * 从终端缓冲捕获"用户实际看到的输入行全文"（Tab 补全识别主路径）。
 *
 * 原理：补全文本由远端回显直接渲染进 xterm 缓冲区；按下回车瞬间，输入行内容 =
 * 提示符结束列（anchor）到光标行的全部已显示文本。与 readline 重绘字节序
 * （\r / 光标移动 / 候选列表清行）和 PS1 形态完全无关——键盘缓冲只见按键、
 * 回显解析依赖重绘，二者在复杂补全下都会丢失补全结果，本函数不会。
 * 行宽折行的续行按物理行顺序拼接（PTY 层并无真实换行）。
 */
export function captureTypedLine(term: BufferSource, anchor: LineAnchor): string {
  const active = term.buffer.active
  const last = active.cursorY
  if (last < anchor.row || last - anchor.row > 20) return '' // 行已被推/scrolled 出可视区：放弃
  const parts: string[] = []
  for (let r = anchor.row; r <= last; r++) {
    const line = active.getLine(r)
    if (!line) break
    let s = line.translateToString(true)
    if (r === anchor.row) s = s.slice(anchor.col)
    parts.push(s)
  }
  return parts.join('').trim()
}

const ENTER_SEQS = ['\r', '\n', '\r\n']

/** 是否回车（成行触发） */
export function isEnter(data: string): boolean {
  return ENTER_SEQS.includes(data)
}

/**
 * 应用一次键盘输入，返回新的缓冲区状态（纯函数，不修改入参）
 *
 * 若输入消费后状态无变化（如方向键到行首继续左移），返回原引用。
 */
export function editLineBuffer(state: LineEditState, data: string): LineEditState {
  const { buffer, cursor } = state

  // 回车：清空行缓冲（由调用方记录历史）
  if (isEnter(data)) return { buffer: '', cursor: 0 }

  // 退格：删除光标前一个字符
  if (data === '\x7f' || data === '\b') {
    if (cursor <= 0) return state
    return { buffer: buffer.slice(0, cursor - 1) + buffer.slice(cursor), cursor: cursor - 1 }
  }

  // Ctrl+C / Ctrl+D：清空缓冲区（Ctrl+C 洪水丢弃由调用方处理）
  if (data === '\x03' || data === '\x04') return { buffer: '', cursor: 0 }

  // ESC 序列（方向键/Home/End/Delete 等）：整段解析，绝不写入缓冲区
  // （之前只过滤 \x1b 字符，导致 [D/[C 这类残留被当成可打印字符混入命令历史）
  if (data.startsWith('\x1b')) {
    switch (data) {
      case '\x1b[D': return { buffer, cursor: Math.max(0, cursor - 1) }  // ← 光标左移
      case '\x1b[C': return { buffer, cursor: Math.min(buffer.length, cursor + 1) }  // → 光标右移
      case '\x1b[H': return { buffer, cursor: 0 }  // Home 行首
      case '\x1b[F': return { buffer, cursor: buffer.length }  // End 行尾
      case '\x1b[3~':  // Delete：删除光标处字符
        if (cursor >= buffer.length) return state
        return { buffer: buffer.slice(0, cursor) + buffer.slice(cursor + 1), cursor }
      case '\x1b[1;5D':  // Ctrl+←：跳到上一个词
        return { buffer, cursor: Math.max(0, buffer.lastIndexOf(' ', Math.max(0, cursor - 1))) }
      case '\x1b[1;5C': {  // Ctrl+→：跳到下一个词
        const np = buffer.indexOf(' ', cursor)
        return { buffer, cursor: np === -1 ? buffer.length : np + 1 }
      }
      default:
        return state  // 其他序列（上/下箭头、功能键等）：忽略，不影响缓冲区
    }
  }

  // Ctrl+A 行首 / Ctrl+E 行尾 / Ctrl+U 清行
  if (data === '\x01') return { buffer, cursor: 0 }
  if (data === '\x05') return { buffer, cursor: buffer.length }
  if (data === '\x15') return { buffer: '', cursor: 0 }

  // 可打印字符：插入到光标位置（支持多字符粘贴和非 ASCII 字符）
  if (data.length > 0 && data !== '\x00') {
    const printable = data.split('').filter(c => c >= ' ' || c.charCodeAt(0) > 127).join('')
    if (printable) {
      return { buffer: buffer.slice(0, cursor) + printable + buffer.slice(cursor), cursor: cursor + printable.length }
    }
  }
  return state
}