/**
 * 终端历史输出按当前列数重新折行（reflow）。
 *
 * 背景：历史日志是按 shell 启动时 pty 宽度（如 120 列）换行的（\r\n 已固化）。
 * 如果前端 xterm 实际宽度更窄（如 60 列），直接写入会触发 xterm 的 soft-wrap，
 * 出现"句子被腰斩、碎片化隔行"的错乱显示。
 *
 * 本函数把每行超过 cols 的可见字符在 cols 处插入 \r\n，使显示与真实宽度一致。
 * - ANSI 转义序列（CSI/OSC/字符集/单字符 ESC）按 0 宽度计，原样保留
 * - CJK 等宽字符按 2 列计
 * - 原有 \n 统一转为 \r\n（xterm 语义）
 * - cols <= 0 时原样返回（未就绪）
 */
export function charWidth(ch: string): number {
  const c = ch.codePointAt(0)!
  // 宽字符（East Asian Wide / Fullwidth）：CJK 统一表意、假名、谚文、全角符号、CJK 扩展等
  if (
    (c >= 0x1100 && c <= 0x115f) || // Hangul Jamo
    c === 0x2329 || c === 0x232a ||
    (c >= 0x2e80 && c <= 0xa4cf && c !== 0x303f) || // CJK Radicals .. Yi
    (c >= 0xac00 && c <= 0xd7a3) || // Hangul Syllables
    (c >= 0xf900 && c <= 0xfaff) || // CJK Compatibility Ideographs
    (c >= 0xfe10 && c <= 0xfe19) || // Vertical Forms
    (c >= 0xfe30 && c <= 0xfe6f) || // CJK Compatibility Forms
    (c >= 0xff00 && c <= 0xff60) || // Fullwidth Forms
    (c >= 0xffe0 && c <= 0xffe6) || // Fullwidth Signs
    (c >= 0x20000 && c <= 0x2fffd) // CJK Ext B..F
  ) {
    return 2
  }
  return 1
}

/**
 * 按列数重新折行。返回的字符串可直接 term.write。
 */
export function reflowForCols(text: string, cols: number): string {
  if (!(cols > 0) || !text) return text
  const out: string[] = []
  let w = 0
  let i = 0
  const n = text.length
  while (i < n) {
    const ch = text[i]
    if (ch === '\n') {
      out.push('\r\n')
      w = 0
      i++
      continue
    }
    if (ch === '\r') {
      // pty 行覆盖（如 Tab 补全重写整行）：回到行首
      w = 0
      out.push(ch)
      i++
      continue
    }
    if (ch === '\x1b') {
      // ANSI 转义序列整体复制，不计数
      let j = i + 1
      if (text[j] === '[') {
        // CSI: ESC [ 参数(数字/;/?/</=/>) 最终字节(0x40-0x7E)
        j++
        while (j < n && !(text.charCodeAt(j) >= 0x40 && text.charCodeAt(j) <= 0x7e)) j++
        if (j < n) j++ // 含最终字节
      } else if (text[j] === ']') {
        // OSC: ESC ] ... 到 BEL(0x07) 或 ST(ESC \)
        j++
        while (j < n && text[j] !== '\x07') {
          if (text[j] === '\x1b' && text[j + 1] === '\\') {
            j += 2
            break
          }
          j++
        }
        if (j < n) j++ // 越过 BEL
      } else if (
        text[j] === '(' || text[j] === ')' || text[j] === '#' ||
        text[j] === '%' || text[j] === '*' || text[j] === '+'
      ) {
        // 字符集等两字节序列
        j += 2
      } else {
        // 单字符 ESC 序列（ESC c / ESC 7 / ESC = 等）
        j++
      }
      out.push(text.slice(i, j))
      i = j
      continue
    }
    // 常规字符（含 tab——历史日志中罕见，按 1 列处理）
    out.push(ch)
    w += charWidth(ch)
    if (w >= cols) {
      out.push('\r\n')
      w = 0
    }
    i++
  }
  return out.join('')
}
