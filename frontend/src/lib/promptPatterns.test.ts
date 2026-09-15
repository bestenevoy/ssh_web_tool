/**
 * 提示符识别单元测试。
 *
 * 覆盖：9 个内置正则的典型命中、自定义正则编译（锚定行首/非法丢弃/上限）、
 * detectPrompt 的 extra 参数优先级。
 */
import { describe, expect, it } from 'vitest'
import { compilePromptPatterns, detectPrompt, MAX_PROMPT_PATTERN_COUNT, MAX_PROMPT_PATTERN_LENGTH } from './promptPatterns'

describe('内置提示符正则', () => {
  const hit = (text: string) => expect(detectPrompt(text)).not.toBeNull()
  const miss = (text: string) => expect(detectPrompt(text)).toBeNull()

  it('PowerShell / cmd 提示符', () => {
    hit('PS C:\\Users\\alice>')
    hit('PS /home/alice>')
    hit('C:\\Users\\alice>')
    hit('\\\\server\\share>')
  })

  it('Unix user@host 提示符（含 venv 前缀 / fish 风格）', () => {
    hit('root@server:~#')
    hit('alice@mac ~ $')
    hit('(venv) user@host:/app$')
  })

  it('方括号 / macOS / powerline / starship / 版本号 / POSIX 提示符', () => {
    hit('[root@web01 /var/log]#')
    hit('host:dir user$')
    hit(`path\uE0B0`)
    hit('❯')
    hit('➜ ~/dir')
    hit('bash-5.2$')
    hit('$')
    hit('#')
  })

  it('普通输出行不命中（tracker 层还有提示符后无内容的二次防线）', () => {
    miss('hello world')
    miss('total 42')
    // 注意：形如 "foo$" 的输出行会命中 bash-5.2$ 风格正则——这是已知设计取舍，
    // 由调用方约束（只检测提示符候选行）+ 提示符后无内容双条件兜底。
  })
})

describe('compilePromptPatterns', () => {
  it('编译并自动锚定行首', () => {
    const [p] = compilePromptPatterns(['mini>'])
    expect(p).toBeInstanceOf(RegExp)
    expect(p.exec('mini> ')).not.toBeNull()
    expect(p.exec('x mini> ')).toBeNull() // 不锚定行首会命中
  })

  it('保留合法条目、丢弃空/超长/非法正则', () => {
    const longBody = 'a'.repeat(MAX_PROMPT_PATTERN_LENGTH + 1)
    const out = compilePromptPatterns(['mini>', '   ', longBody, '['])
    expect(out).toHaveLength(1)
    expect(out[0].exec('mini> x')).not.toBeNull()
  })

  it('非字符串条目忽略、条数不设上限截断（截断在上层/后端）', () => {
    const out = compilePromptPatterns(['a>', 42 as unknown as string])
    expect(out).toHaveLength(1)
    const many = Array.from({ length: 50 }, (_, i) => `p${i}>`)
    expect(compilePromptPatterns(many)).toHaveLength(50)
    expect(MAX_PROMPT_PATTERN_COUNT).toBe(20)
  })
})

describe('detectPrompt extra 参数', () => {
  it('内置不匹配时自定义正则生效', () => {
    const extra = compilePromptPatterns(['db \\d+ =>'])
    expect(detectPrompt('db 1 => ')).toBeNull() // 内置正则不识别该形态
    expect(detectPrompt('db 1 => ', extra)).toEqual({ end: 7 })
    expect(detectPrompt('mini> ', compilePromptPatterns(['mini>']))).toEqual({ end: 5 })
  })

  it('自定义正则优先于内置（命中更长前缀）', () => {
    const extra = [new RegExp('^(?:❯ mini>)')]
    // 内置 starship 正则只命中 "❯"（end=1），自定义命中 "❯ mini>"（end=7）
    expect(detectPrompt('❯ mini> ', extra)).toEqual({ end: 7 })
  })

  it('extra 全部未命中时回退内置', () => {
    const extra = compilePromptPatterns(['db \\d+ =>'])
    expect(detectPrompt('root@server:~#', extra)).toEqual({ end: 14 })
  })
})
