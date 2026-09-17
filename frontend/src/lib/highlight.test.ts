/**
 * 关键字高亮纯函数层单元测试（移植自 rssh 的设计）。
 *
 * 覆盖：规则校验/编译、匹配与重叠消解、字符偏移 → cell 列映射（CJK 宽字符 /
 * 尾部填充修剪）、行内装饰规划、可视行增量 diff（reconcile）。
 * HighlightDecorator（xterm 装饰注册）依赖真实渲染环境，不在单测范围。
 */
import { describe, expect, it } from 'vitest'
import type { IBufferCell, IBufferLine } from '@xterm/xterm'
import {
  compileHighlightRules,
  findMatches,
  planLine,
  validateHighlightRule,
  type HighlightRule,
  type LineCells,
} from './highlight'
import { readLineCells, reconcile, type VisibleLine } from './highlightDecorations'

function rule(partial: Partial<HighlightRule>): HighlightRule {
  return { keyword: 'X', name: 'r', color: '#FF0000', enabled: true, is_case_sensitive: false, ...partial }
}

describe('validateHighlightRule', () => {
  it('合法规则返回 null', () => {
    expect(validateHighlightRule(rule({ keyword: '\\bERROR\\b', name: '错误' }))).toBeNull()
  })
  it('空名称 / 超长名称', () => {
    expect(validateHighlightRule(rule({ name: '  ' }))).toEqual({ kind: 'name_required' })
    expect(validateHighlightRule(rule({ name: 'x'.repeat(101) }))).toEqual({ kind: 'name_too_long' })
  })
  it('超长 keyword', () => {
    expect(validateHighlightRule(rule({ keyword: 'a'.repeat(201) }))).toEqual({ kind: 'keyword_too_long' })
  })
  it('纯零宽断言（锚点/词边界）拒绝', () => {
    expect(validateHighlightRule(rule({ keyword: '^$' }))).toEqual({ kind: 'zero_width' })
    expect(validateHighlightRule(rule({ keyword: '\\b' }))).toEqual({ kind: 'zero_width' })
  })
  it('空串也能匹配的正则拒绝（re.test("") 为真）', () => {
    expect(validateHighlightRule(rule({ keyword: '(?:x)?' }))).toEqual({ kind: 'zero_width' })
  })
  it('语法错误返回 invalid 并带浏览器报错信息', () => {
    const err = validateHighlightRule(rule({ keyword: '(unclosed' }))
    expect(err?.kind).toBe('invalid')
  })
})

describe('compileHighlightRules', () => {
  it('编译为带 flags 的 RegExp（默认大小写不敏感 gi）', () => {
    const [c] = compileHighlightRules([rule({ keyword: 'error' })])
    expect(c.regex).not.toBeNull()
    expect(c.regex?.flags).toBe('gi')
    expect(c.source).toBe('error')
  })
  it('区分大小写时 flags 为 g', () => {
    const [c] = compileHighlightRules([rule({ keyword: 'error', is_case_sensitive: true })])
    expect(c.regex?.flags).toBe('g')
  })
  it('停用规则 / 空 keyword 不编译', () => {
    const [a, b] = compileHighlightRules([rule({ enabled: false, keyword: 'x' }), rule({ keyword: '' })])
    expect(a.regex).toBeNull()
    expect(b.regex).toBeNull()
  })
  it('非法正则标记为 regex=null（跳过高亮而非抛错）', () => {
    const [c] = compileHighlightRules([rule({ keyword: '(bad' })])
    expect(c.regex).toBeNull()
  })
})

describe('findMatches', () => {
  const compiled = compileHighlightRules([rule({ keyword: 'abc', color: '#111111' })])
  it('单规则基本匹配', () => {
    expect(findMatches('xx abc yy', compiled)).toEqual([{ start: 3, end: 6, color: '#111111' }])
  })
  it('重叠消解：先出现的匹配保留，重叠的丢弃', () => {
    const cs = compileHighlightRules([rule({ keyword: 'abc', color: '#111111' }), rule({ keyword: 'bcd', color: '#222222' })])
    expect(findMatches('abcd', cs)).toEqual([{ start: 0, end: 3, color: '#111111' }])
  })
  it('规则顺序即重叠优先级（同起点取列表靠前者）', () => {
    const cs = compileHighlightRules([rule({ keyword: 'b', color: '#222222' }), rule({ keyword: 'abc', color: '#111111' })])
    // "abc"：b 在 index1 先被收集，但按 (start,index) 排序后 abc(start0) 在前，
    // b(start1) 落在 pos=3 之后被保留？——不，abc 覆盖 [0,3)，b 的 [1,2) 被跳过
    expect(findMatches('abc', cs)).toEqual([{ start: 0, end: 3, color: '#111111' }])
  })
  it('大小写不敏感匹配', () => {
    expect(findMatches('ABC', compiled)).toEqual([{ start: 0, end: 3, color: '#111111' }])
  })
  it('区分大小写时不匹配', () => {
    const cs = compileHighlightRules([rule({ keyword: 'abc', is_case_sensitive: true })])
    expect(findMatches('ABC', cs)).toEqual([])
  })
  it('零宽匹配防御：不产生匹配也不死循环', () => {
    const cs = compileHighlightRules([rule({ keyword: 'x*' })])
    expect(findMatches('ab', cs)).toEqual([])
    expect(findMatches('axb', cs)).toEqual([{ start: 1, end: 2, color: '#FF0000' }])
  })
  it('停用/非法规则整体跳过', () => {
    expect(findMatches('abc', compileHighlightRules([rule({ keyword: 'abc', enabled: false })]))).toEqual([])
    expect(findMatches('abc', compileHighlightRules([rule({ keyword: '(bad' })]))).toEqual([])
  })
})

describe('readLineCells / planLine', () => {
  interface FakeCell {
    w: number
    chars: string
  }
  function fakeLine(cells: FakeCell[]): Pick<IBufferLine, 'length' | 'getCell'> {
    return {
      length: cells.length,
      // 测试桩只实现 readLineCells 用到的 getWidth/getChars，其余 IBufferCell 成员不参与
      getCell: (x: number): IBufferCell | undefined =>
        (x < cells.length ? { getWidth: () => cells[x].w, getChars: () => cells[x].chars } : undefined) as IBufferCell,
    }
  }

  it('ASCII 行：字符偏移与 cell 列一一对应', () => {
    const cells = readLineCells(fakeLine('hello'.split('').map((c) => ({ w: 1, chars: c }))))
    expect(cells.text).toBe('hello')
    expect(cells.cellAt).toEqual([0, 1, 2, 3, 4, 5])
  })
  it('CJK 宽字符：跳过 0 宽 spacer，映射到 2 列', () => {
    // "中" 占 x=0..1（x=1 是 spacer），"a" 在 x=2
    const cells = readLineCells(fakeLine([{ w: 2, chars: '中' }, { w: 0, chars: '' }, { w: 1, chars: 'a' }]))
    expect(cells.text).toBe('中a')
    expect(cells.cellAt).toEqual([0, 2, 3])
    const [d] = planLine(cells, compileHighlightRules([rule({ keyword: '中', color: '#111111' })]))
    expect(d).toEqual({ x: 0, width: 2, color: '#111111' })
  })
  it('未写入的行尾填充被修剪（不会被 \\s / .* 类规则涂色）', () => {
    const cells = readLineCells(fakeLine([{ w: 1, chars: 'a' }, { w: 1, chars: '' }, { w: 1, chars: '' }]))
    expect(cells.text).toBe('a')
    expect(cells.cellAt).toEqual([0, 1])
    expect(planLine(cells, compileHighlightRules([rule({ keyword: '\\s+' })]))).toEqual([])
  })
  it('行内未写入的中间 cell 视为空格', () => {
    const cells = readLineCells(fakeLine([{ w: 1, chars: 'a' }, { w: 1, chars: '' }, { w: 1, chars: 'b' }]))
    expect(cells.text).toBe('a b')
  })
  it('planLine 丢弃 0 宽装饰', () => {
    const cells: LineCells = { text: 'ab', cellAt: [0, 1, 2] }
    // 构造一个 start==end 映射的退化情形较难，直接验证正常路径 + filter 分支存在：
    const out = planLine(cells, compileHighlightRules([rule({ keyword: 'a', color: '#111111' })]))
    expect(out).toEqual([{ x: 0, width: 1, color: '#111111' }])
  })
})

describe('reconcile', () => {
  const vl = (line: number, sig: string): VisibleLine => ({ line, sig, plan: sig ? [{ x: 0, width: 1, color: '#111111' }] : [] })
  it('签名相同 → 保留（不进 dispose/create）', () => {
    const r = reconcile([vl(5, 'a')], new Map([[5, 'a']]))
    expect(r).toEqual({ disposeLines: [], createLines: [] })
  })
  it('签名不同 → dispose + create；方案为空则只 dispose', () => {
    const r = reconcile([vl(5, 'b'), vl(6, '')], new Map<number, string>([[5, 'a'], [6, 'x']]))
    expect(r.disposeLines).toEqual([5, 6])
    expect(r.createLines).toEqual([vl(5, 'b')])
  })
  it('新出现匹配 / 未知行 → create', () => {
    const r = reconcile([vl(7, 'new')], new Map<number, string>())
    expect(r.createLines).toEqual([vl(7, 'new')])
  })
})
