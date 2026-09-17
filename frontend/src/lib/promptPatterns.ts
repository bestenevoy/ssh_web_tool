/**
 * Shell 提示符识别（移植自 rssh prompt.ts）。
 *
 * 采用 WindTerm 的务实路线：shell 家族正则，而非 OCR 或第二套命令块协议。
 * 检测刻意锚定第 0 列。调用方必须提供受约束的边界候选行——命令块的首个
 * 逻辑行，或 tracker 等待提示符时的光标逻辑行——严禁用这些模式扫描任意
 * 输出历史（否则输出内容里的 "foo$" 之类文本会被误判为提示符）。
 */

export interface PromptMatch {
  /** 提示符标记之后的 UTF-16 偏移（命令空格之前）。 */
  end: number
}

const PROMPT_PATTERNS: ReadonlyArray<RegExp> = [
  // PowerShell："PS C:\\Users\\alice>" 与跨平台 "PS /home/alice>"
  /^PS(?:\s+[^>\r\n]{0,200})?>/i,
  // cmd.exe：盘符路径与 UNC 路径
  /^(?:[A-Za-z]:\\|\\\\)[^>\r\n]{0,200}>/,
  // Unix user@host 提示符，可选前置 venv/上下文括号，后跟 :path 或 fish 风格 " path"
  /^(?:(?:\([^)\r\n]{1,80}\)|\[[^\]\r\n]{1,120}\])\s*)*[^\s@:\r\n]+@[^\s:\r\n]+(?:(?::|\s+)[^#$%>\r\n]{0,160})?[#$%>]/,
  // 传统方括号提示符："[root@host path]#" 或 "[ctx]$"
  /^\[[^\]\r\n]{1,160}\][#$%>]/,
  // macOS 历史默认："host:directory user$"
  /^[A-Za-z0-9._-]{1,80}:[^#$%>\r\n]{0,120}\s+[A-Za-z0-9._-]{1,80}[#$%>]/,
  // Powerline：最后一段分隔符即提示符结尾（保持贪婪以完整覆盖多段提示符）
  /^[^\r\n]{0,200}[\uE0B0\uE0B1]/,
  // Starship / oh-my-zsh 符号提示符（单独出现或跟在简短状态/路径后）
  /^(?:[^❯➜➤λ\r\n]{1,160}\s)?[❯➜➤λ](?=\s|$)/,
  // 带版本号的 shell 提示符，如 "bash-5.2$"
  /^[A-Za-z][A-Za-z0-9._-]{0,60}[#$%>](?=\s|$)/,
  // 最简 POSIX 提示符
  /^[#$%>](?=\s|$)/,
]

export function detectPrompt(text: string, extra?: ReadonlyArray<RegExp>): PromptMatch | null {
  // 自定义正则优先于内置（用户明确声明的提示符形态最可信）
  if (extra) {
    for (const pattern of extra) {
      const match = pattern.exec(text)
      if (match) return { end: match[0].length }
    }
  }
  for (const pattern of PROMPT_PATTERNS) {
    const match = pattern.exec(text)
    if (match) return { end: match[0].length }
  }
  return null
}

export interface PromptTailMatch extends PromptMatch {
  /** 提示符在本行中的起点（>0 表示提示符前还有上一命令未换行的输出残留）。 */
  start: number
}

/**
 * 行尾提示符检测：上一命令输出末行无换行符时，新提示符直接接在输出后面
 * （如 `echo -n abc` 后光标行变成 "abcuser@host:~$"），行首锚定的
 * detectPrompt 无法正确处理。
 *
 * 做法：去掉 ^ 锚 + 追加 $ 锚（匹配必须延伸到行尾），对整行 exec——
 * match.index 即提示符最早起点（正则自身字符类会自然停在空白/结构字符
 * 处，输出残留通常能完整留在上一块）；附加约束 match[0].length >= 3，
 * 防输出行尾 "$"/"%" 之类单字符撞车（"$"/"%"/">" 单字符提示符前有输出
 * 残留时宁可漏切，等下一个整行提示符，也不误切输出）。
 *
 * 代价：每次输出解析批回调对单行跑一遍模式搜索，开销可忽略。
 * 误报面与 detectPrompt 相同（仅在等待提示符期间调用，不会扫描任意输出）。
 */
export function detectPromptTail(text: string, extra?: ReadonlyArray<RegExp>): PromptTailMatch | null {
  const patterns = extra ? [...extra, ...PROMPT_PATTERNS] : PROMPT_PATTERNS
  for (const pattern of patterns) {
    const tail = TAIL_CACHE.get(pattern) ?? new RegExp(pattern.source.replace(/^\^/, ''), pattern.flags)
    TAIL_CACHE.set(pattern, tail)
    const match = tail.exec(text)
    if (match && match[0].length >= 3 && match.index + match[0].length === text.length) {
      return { start: match.index, end: text.length }
    }
  }
  return null
}

// tail 正则缓存（源码去 ^ 锚；每个内置/自定义模式只编译一次，保留原 flags）
const TAIL_CACHE = new Map<RegExp, RegExp>()

// 单条自定义正则的最大长度与总条数上限（防超宽正则拖慢检测/误判面扩大）
export const MAX_PROMPT_PATTERN_LENGTH = 200
export const MAX_PROMPT_PATTERN_COUNT = 20

/**
 * 编译用户自定义提示符正则（一行一条的字符串列表 → RegExp[]）。
 * 自动包裹 `^(?:...)` 锚定行首（与内置正则同规则，防输出内容误判）；
 * 空/超长/语法非法的条目静默丢弃，调用方无需处理异常。
 */
export function compilePromptPatterns(list: ReadonlyArray<string>): RegExp[] {
  const out: RegExp[] = []
  for (const body of list) {
    if (typeof body !== 'string') continue
    const trimmed = body.trim()
    if (!trimmed || trimmed.length > MAX_PROMPT_PATTERN_LENGTH) continue
    try {
      out.push(new RegExp(`^(?:${trimmed})`))
    } catch {
      // 非法正则丢弃（UI 层已提前校验提示，此处兜底）
    }
  }
  return out
}
