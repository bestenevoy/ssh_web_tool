// 关键字高亮（移植自 rssh src/lib/terminal/highlight.ts）
// 纯函数层：规则校验 / 编译 / 匹配 / 布局规划，不触碰 xterm —— 单元可测。
// xterm 装饰层（HighlightDecorator）在 highlightDecorations.ts。

// 高亮规则（与后端 ui_settings.highlight_rules / HighlightRuleItem 同构：
// keyword 即正则源，也是规则身份键——去重与保存同步均以它为准）
export interface HighlightRule {
  keyword: string
  name: string
  color: string // #RRGGBB
  enabled: boolean
  is_case_sensitive: boolean
}

export interface CompiledHighlightRule {
  keyword: string
  color: string
  enabled: boolean
  is_case_sensitive: boolean
  source: string
  regex: RegExp | null
}

export type HighlightValidationError =
  | { kind: 'invalid'; message: string }
  | { kind: 'zero_width' }
  | { kind: 'name_required' }
  | { kind: 'name_too_long' }
  | { kind: 'keyword_too_long' }

// 上限与后端 config.py 常量保持一致
export const MAX_HIGHLIGHT_RULES = 50
export const MAX_HIGHLIGHT_KEYWORD_LENGTH = 200
const MAX_HIGHLIGHT_NAME = 100

// 默认规则（与后端 config.py _DEFAULT_HIGHLIGHT_RULES 同源；「恢复默认」按钮用）
export const DEFAULT_HIGHLIGHT_RULES: HighlightRule[] = [
  { keyword: '\\bINFO\\b', name: 'Info', color: '#6EDAA0', enabled: true, is_case_sensitive: false },
  { keyword: '\\bDEBUG\\b', name: 'Debug', color: '#40C8E0', enabled: true, is_case_sensitive: false },
  {
    keyword: '\\b(?:error|fail(?:s|ed|ing|ures?)?|denied|refused|fatal|timed out|timeout|invalid)\\b',
    name: 'Errors',
    color: '#FF6B6B',
    enabled: true,
    is_case_sensitive: false,
  },
  {
    keyword: '\\b(?:success(?:ful)?|succeeded|passed|completed)\\b',
    name: 'Success',
    color: '#6EDAA0',
    enabled: true,
    is_case_sensitive: false,
  },
  { keyword: '\\bwarn(?:ing)?s?\\b', name: 'Warnings', color: '#FFD060', enabled: true, is_case_sensitive: false },
  {
    keyword: '\\b\\d{4}-\\d{2}-\\d{2}(?:[ T]\\d{2}:\\d{2}(?::\\d{2})?)?\\b',
    name: 'Date/Time',
    color: '#82AAFF',
    enabled: true,
    is_case_sensitive: false,
  },
  { keyword: '\\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\\b', name: 'MAC', color: '#D86BFF', enabled: true, is_case_sensitive: false },
  {
    keyword: '\\b(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)(?:\\.(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)){3}\\b',
    name: 'IPv4',
    color: '#D86BFF',
    enabled: true,
    is_case_sensitive: false,
  },
  {
    keyword: '\\b\\d+(?:\\.\\d+)?(?:[KMGTPE]i?B|[KMGTPE]|B)\\b',
    name: 'File sizes',
    color: '#E8A87C',
    enabled: true,
    is_case_sensitive: false,
  },
]

/**
 * 检测"纯零宽断言"正则（锚点/词边界/环视）：这类模式匹配成功但不占字符，
 * 无法可见地高亮任何文本。尽力而为的检查；运行时另有零宽匹配防御防死循环。
 */
function isPureZeroWidth(pattern: string): boolean {
  const s = pattern.trim()
  if (!s) return true
  let i = 0
  while (i < s.length) {
    const c = s[i]
    if (c === '^' || c === '$') {
      i++
      continue
    }
    if (c === '\\' && (s[i + 1] === 'b' || s[i + 1] === 'B')) {
      i += 2
      continue
    }
    if (c === '(' && s[i + 1] === '?') {
      if (s.slice(i, i + 3) === '(?=' || s.slice(i, i + 3) === '(?!') {
        i += 3
      } else if (s.slice(i, i + 4) === '(?<=' || s.slice(i, i + 4) === '(?<!') {
        i += 4
      } else {
        return false
      }
      let depth = 1
      while (i < s.length && depth > 0) {
        const ch = s[i]
        if (ch === '\\') {
          i += 2
          continue
        }
        if (ch === '(') depth++
        if (ch === ')') depth--
        i++
      }
      if (depth !== 0) return false
      continue
    }
    return false
  }
  return true
}

/**
 * 校验单条高亮规则（keyword 即正则源，必须做语法检查）。
 * 合法返回 null；否则返回错误类别供 UI 映射提示文案。
 */
export function validateHighlightRule(rule: HighlightRule): HighlightValidationError | null {
  if (!rule.name.trim()) {
    return { kind: 'name_required' }
  }
  if (rule.name.length > MAX_HIGHLIGHT_NAME) {
    return { kind: 'name_too_long' }
  }
  if (rule.keyword.length > MAX_HIGHLIGHT_KEYWORD_LENGTH) {
    return { kind: 'keyword_too_long' }
  }
  if (!rule.keyword) return null
  const flags = rule.is_case_sensitive ? 'g' : 'gi'
  if (isPureZeroWidth(rule.keyword)) {
    return { kind: 'zero_width' }
  }
  try {
    const re = new RegExp(rule.keyword, flags)
    if (re.test('')) {
      return { kind: 'zero_width' }
    }
  } catch (e) {
    return { kind: 'invalid', message: e instanceof Error ? e.message : String(e) }
  }
  return null
}

/** 预编译高亮规则为可复用的 RegExp 对象；非法规则标记为 regex=null（跳过高亮） */
export function compileHighlightRules(rules: HighlightRule[]): CompiledHighlightRule[] {
  return rules.map((rule) => {
    if (!rule.enabled || !rule.keyword) {
      return { ...rule, source: '', regex: null }
    }
    // keyword 即正则源（与 rssh 一致，文本/正则双模式已废弃）
    const source = rule.keyword
    const flags = rule.is_case_sensitive ? 'g' : 'gi'
    try {
      const regex = new RegExp(source, flags)
      return { ...rule, source, regex }
    } catch (e) {
      console.warn('[highlight] invalid regex, skipping:', rule.keyword, e)
      return { ...rule, source, regex: null }
    }
  })
}

export interface HighlightMatch {
  start: number
  end: number
  color: string
}

interface RawMatch extends HighlightMatch {
  index: number // 规则列表位置，仅用于消解重叠优先级
}

/**
 * 在纯文本中找出全部高亮匹配。返回按 start 排序、重叠已消解（列表中先出现的
 * 规则优先）的匹配列表。调用方把区间转成 xterm 装饰——本函数绝不改写字节流，
 * 因此不可能破坏程序自身的 SGR 状态（rssh issue #114 的教训）。
 */
export function findMatches(text: string, compiled: CompiledHighlightRule[]): HighlightMatch[] {
  const enabled = compiled.filter((c) => c.enabled && c.keyword && c.regex)
  if (!enabled.length) return []

  const raw: RawMatch[] = []
  for (let i = 0; i < enabled.length; i++) {
    const re = enabled[i].regex!
    re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(text)) !== null) {
      const start = m.index
      const end = start + m[0].length
      if (end === start) {
        // 零宽匹配：前进一字符防死循环（防御性）
        re.lastIndex = start + 1
        continue
      }
      raw.push({ start, end, color: enabled[i].color, index: i })
    }
  }

  raw.sort((a, b) => (a.start !== b.start ? a.start - b.start : a.index - b.index))

  const out: HighlightMatch[] = []
  let pos = 0
  // 匹配已按 start 排序；pos 是上一个保留匹配的终点，
  // 起点落在它之前的匹配与更早（更高优先级）的匹配重叠——跳过
  for (const m of raw) {
    if (m.start < pos) continue
    out.push({ start: m.start, end: m.end, color: m.color })
    pos = m.end
  }
  return out
}

/**
 * 一条 buffer 行归约成高亮层所需的信息：
 *   - `text`：行的可见文本（UTF-16 code units），findMatches/RegExp 的索引基准
 *   - `cellAt`：cellAt[i] 是 UTF-16 单元 text[i] 所在的 cell 列；长度为
 *     text.length+1，cellAt[text.length] 是行尾列。字符偏移与 cell 列在两种
 *     情况下会错位：宽字符（CJK）占 2 列；一个字形（emoji/组合记号）可由多个
 *     UTF-16 单元组成且都映射到同一列——该映射保证装饰落在真实 cell 上。
 */
export interface LineCells {
  text: string
  cellAt: number[]
}

/** 单行上要放置的一条装饰：cell 列、占宽（cell 数）、颜色 */
export interface LineDecoration {
  x: number
  width: number
  color: string
}

/**
 * 把一行的匹配结果转换成按 cell 定位的装饰。纯函数：xterm 耦合
 * （读 buffer 成 LineCells、注册装饰）留在调用方，保证本函数可单元测试。
 */
export function planLine(cells: LineCells, compiled: CompiledHighlightRule[]): LineDecoration[] {
  return findMatches(cells.text, compiled)
    .map((m) => ({
      x: cells.cellAt[m.start],
      width: cells.cellAt[m.end] - cells.cellAt[m.start],
      color: m.color,
    }))
    // 0 宽区间（匹配没落到任何完整 cell 上）不是合法装饰；
    // xterm 的 width 默认为 1，直接丢弃
    .filter((d) => d.width > 0)
}
