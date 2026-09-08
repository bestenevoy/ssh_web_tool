/**
 * reflowForCols 单元测试。
 *
 * 回归背景：历史日志按 pty 当时宽度（120 列）换行，窄 xterm（如 60 列）直接写入
 * 会 soft-wrap 碎片化（"句子被腰斩"）。reflow 后显示应与真实宽度一致。
 */
import { describe, expect, it } from 'vitest'
import { charWidth, reflowForCols } from './reflow'

describe('reflowForCols', () => {
  it('超宽行在 cols 处插入换行', () => {
    // 62 字符内容按 60 列 → 两行
    const line = 'Expanded Security Maintenance for Applications is not enabled.'
    const out = reflowForCols(line, 60)
    expect(out.split('\r\n').map(s => s.length)).toEqual([60, 2])
  })

  it('不超宽的行保持不变', () => {
    const line = 'Last login: Tue Sep 8 22:28:29 2026 from 112.193.118.195'
    expect(reflowForCols(line, 60)).toBe(line)
  })

  it('原有换行保留（\n 转 \r\n）', () => {
    const text = 'line one\nline two'
    expect(reflowForCols(text, 60)).toBe('line one\r\nline two')
  })

  it('ANSI 转义序列按 0 宽度计且原样保留', () => {
    // \x1b[32m 绿色 + 62 可见字符：折行位置仍按 60 可见字符
    const line = '\x1b[32m' + 'A'.repeat(62) + '\x1b[0m'
    const out = reflowForCols(line, 60)
    const parts = out.split('\r\n')
    expect(parts).toHaveLength(2)
    // 第一行 = CSI + 60 个 A，第二行 = 2 个 A + 重置序列
    expect(parts[0]).toBe('\x1b[32m' + 'A'.repeat(60))
    expect(parts[1]).toBe('AA\x1b[0m')
  })

  it('CJK 宽字符按 2 列计', () => {
    // 30 个中文字符 = 60 列，恰好一行；再加一个字符就折行
    const text = '中'.repeat(30) + 'x'
    const out = reflowForCols(text, 60)
    const parts = out.split('\r\n')
    expect(parts).toHaveLength(2)
    expect(parts[0]).toBe('中'.repeat(30))
    expect(parts[1]).toBe('x')
  })

  it('cols<=0 或空文本原样返回', () => {
    expect(reflowForCols('abc', 0)).toBe('abc')
    expect(reflowForCols('', 60)).toBe('')
  })

  it('CR 重置行宽（Tab 补全覆盖场景）', () => {
    // 行首 20 字符 + \r 后重写 70 字符：\r 后从行首计宽，70 列在 60 处折行
    const text = 'A'.repeat(20) + '\r' + 'B'.repeat(70)
    const out = reflowForCols(text, 60)
    const parts = out.split('\r\n')
    expect(parts[0]).toBe('A'.repeat(20) + '\r' + 'B'.repeat(60))
    expect(parts[1]).toBe('B'.repeat(10))
  })
})

describe('charWidth', () => {
  it('ASCII 为 1', () => {
    expect(charWidth('a')).toBe(1)
    expect(charWidth('1')).toBe(1)
  })
  it('CJK 为 2', () => {
    expect(charWidth('中')).toBe(2)
    expect(charWidth('文')).toBe(2)
    expect(charWidth('，')).toBe(2)
  })
})
