/**
 * useSettings 持久化映射函数单元测试。
 *
 * 回归背景：设置持久化从 localStorage 迁移到后端 config.json 的 ui_settings 段
 * （snake_case），前端 camelCase 与后端 snake_case 之间的映射必须逐键对应，
 * 且非法/缺失键不得污染默认值。
 */
import { describe, expect, it } from 'vitest'
import { DEFAULT_SETTINGS, applyUiSettings, toUiSettingsPayload } from './useSettings'

describe('toUiSettingsPayload', () => {
  it('camelCase 键映射为 snake_case，只包含传入的键', () => {
    const out = toUiSettingsPayload({ theme: 'dark', fontSize: 16 })
    expect(out).toEqual({ theme: 'dark', font_size: 16 })
  })

  it('空对象映射为空负载', () => {
    expect(toUiSettingsPayload({})).toEqual({})
  })

  it('全部键一一对应', () => {
    const out = toUiSettingsPayload({
      theme: 'light',
      fontFamily: 'Fira Code',
      fontSize: 14,
      blockBar: false,
      blockAutoFold: true,
      blockMaxLines: 50,
      blockSplitMode: 'prompt',
      customPromptPatterns: ['mini>', 'db \\d+ =>'],
    })
    expect(out).toEqual({
      theme: 'light',
      font_family: 'Fira Code',
      font_size: 14,
      block_bar: false,
      block_auto_fold: true,
      block_max_lines: 50,
      block_split_mode: 'prompt',
      custom_prompt_patterns: ['mini>', 'db \\d+ =>'],
    })
  })

  it('命令块两新键（切块方式/自定义正则）部分更新', () => {
    expect(toUiSettingsPayload({ blockSplitMode: 'enter' })).toEqual({ block_split_mode: 'enter' })
    expect(toUiSettingsPayload({ customPromptPatterns: ['x>'] })).toEqual({ custom_prompt_patterns: ['x>'] })
  })
})

describe('applyUiSettings', () => {
  it('完整负载覆盖 base，snake_case 映射回 camelCase', () => {
    const out = applyUiSettings(
      { theme: 'dark', font_family: 'Menlo', font_size: 18, block_bar: false, block_auto_fold: true, block_max_lines: 100 },
      DEFAULT_SETTINGS
    )
    expect(out).toEqual({
      theme: 'dark',
      fontFamily: 'Menlo',
      fontSize: 18,
      blockBar: false,
      blockAutoFold: true,
      blockMaxLines: 100,
      blockSplitMode: 'prompt',
      customPromptPatterns: [],
    })
  })

  it('部分负载只覆盖传入键，其余保留 base', () => {
    const out = applyUiSettings({ theme: 'dark' }, { ...DEFAULT_SETTINGS, blockAutoFold: true })
    expect(out.theme).toBe('dark')
    expect(out.blockAutoFold).toBe(true)
    expect(out.fontSize).toBe(DEFAULT_SETTINGS.fontSize)
  })

  it('null/undefined 负载原样返回 base', () => {
    const base = { ...DEFAULT_SETTINGS, theme: 'dark' as const }
    expect(applyUiSettings(null, base)).toEqual(base)
    expect(applyUiSettings(undefined, base)).toEqual(base)
  })

  it('非法值不污染 base（后端校验前的兜底防线）', () => {
    const out = applyUiSettings(
      {
        theme: 'solarized' as unknown as 'dark' | 'light',
        font_family: '   ',
        font_size: Number.NaN,
        block_bar: 'yes' as unknown as boolean,
      },
      DEFAULT_SETTINGS
    )
    expect(out).toEqual(DEFAULT_SETTINGS)
  })

  it('font_size 超界钳制到 8-32', () => {
    expect(applyUiSettings({ font_size: 99 }, DEFAULT_SETTINGS).fontSize).toBe(32)
    expect(applyUiSettings({ font_size: 2 }, DEFAULT_SETTINGS).fontSize).toBe(8)
  })

  it('block_split_mode 合法值生效、非法值保留 base', () => {
    const base = { ...DEFAULT_SETTINGS, blockSplitMode: 'enter' as const }
    expect(applyUiSettings({ block_split_mode: 'prompt' }, base).blockSplitMode).toBe('prompt')
    expect(applyUiSettings({ block_split_mode: 'auto' as unknown as 'enter' | 'prompt' }, base).blockSplitMode).toBe('enter')
  })

  it('custom_prompt_patterns 数组逐项过滤，非数组保留 base', () => {
    const out = applyUiSettings(
      { custom_prompt_patterns: ['mini>', '   ', 42 as unknown as string, 'db \\d+ =>'] },
      DEFAULT_SETTINGS
    )
    expect(out.customPromptPatterns).toEqual(['mini>', 'db \\d+ =>'])
    const base = { ...DEFAULT_SETTINGS, customPromptPatterns: ['keep>'] }
    expect(applyUiSettings({ custom_prompt_patterns: 'mini>' as unknown as string[] }, base).customPromptPatterns).toEqual([
      'keep>',
    ])
  })

  it('旧配置无新键时保留 DEFAULT（blockSplitMode=prompt / patterns=[]）', () => {
    const out = applyUiSettings({ theme: 'dark' }, DEFAULT_SETTINGS)
    expect(out.blockSplitMode).toBe('prompt')
    expect(out.customPromptPatterns).toEqual([])
  })
})
