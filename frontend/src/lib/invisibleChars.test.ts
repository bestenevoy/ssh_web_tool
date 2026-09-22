/**
 * 隐形字符防御单测：normalizeInvisible（零宽删除/异形空白转普通空格、幂等）、
 * scanHiddenChars（检出清单与计数、全角仅提示）、hasInvisible
 */
import { describe, expect, it } from 'vitest'
import { normalizeInvisible, scanHiddenChars, hasInvisible } from './invisibleChars'

describe('normalizeInvisible', () => {
  it('NBSP/全角空格等异形空白 → 普通空格', () => {
    expect(normalizeInvisible('touch\u00a0note.txt')).toBe('touch note.txt')
    expect(normalizeInvisible('echo\u3000hi')).toBe('echo hi')
    expect(normalizeInvisible('a\u2003b')).toBe('a b') // em-space
    expect(normalizeInvisible('a\u202f b')).toBe('a  b')
  })
  it('零宽/格式字符直接删除', () => {
    expect(normalizeInvisible('ls\u200b -la')).toBe('ls -la')
    expect(normalizeInvisible('cat \ufeffa.txt')).toBe('cat a.txt')
    expect(normalizeInvisible('a\u200db')).toBe('ab')
    expect(normalizeInvisible('soft­hyphen')).toBe('softhyphen')
  })
  it('正常文本恒等（幂等，可安全挂在发送/入库路径）', () => {
    const ok = "git commit -m 'fix: 双引号 \"ok\"' && ls -la /tmp"
    expect(normalizeInvisible(ok)).toBe(ok)
    expect(normalizeInvisible(normalizeInvisible('a\u00a0b'))).toBe(normalizeInvisible('a\u00a0b'))
  })
  it('合法 CJK 文本不误伤（普通半角空格分隔不变）', () => {
    expect(normalizeInvisible('echo 正常 空格')).toBe('echo 正常 空格')
  })
})

describe('scanHiddenChars', () => {
  it('检出隐形字符并按名称计数', () => {
    const issues = scanHiddenChars('a\u00a0b\u200bc\u200bd')
    expect(issues).toContain('不间断空格(U+00A0)×1')
    expect(issues).toContain('零宽空格(U+200B)×2')
  })
  it('全角/形近标点默认不报，includeLookalikes 才报且标注不替换', () => {
    const text = 'echo "中文，测试"'
    expect(scanHiddenChars(text)).toEqual([])
    const withLk = scanHiddenChars(text, true)
    expect(withLk.length).toBe(1)
    expect(withLk[0]).toContain('全角/形近标点×1')
  })
  it('干净文本返回空数组', () => {
    expect(scanHiddenChars('ls -la /tmp')).toEqual([])
  })
})

describe('hasInvisible', () => {
  it('与 normalize 变化一致', () => {
    expect(hasInvisible('a\u00a0b')).toBe(true)
    expect(hasInvisible('a b')).toBe(false)
  })
})
