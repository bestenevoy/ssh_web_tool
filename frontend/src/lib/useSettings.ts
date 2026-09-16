import { useState, useEffect, useCallback } from 'react'
import { api, type UiSettingsPayload } from './api'
import { DEFAULT_HIGHLIGHT_RULES, MAX_HIGHLIGHT_RULES, type HighlightRule } from './highlight'

export type Theme = 'dark' | 'light'

export interface TerminalSettings {
  theme: Theme
  fontFamily: string
  fontSize: number
  // 命令块（借鉴 rssh）：左侧色条标记 / 自动折叠 / 折叠保留行数
  blockBar: boolean
  blockAutoFold: boolean
  blockMaxLines: number
  // 命令块切块方式：按提示符出现（默认）/ 按 Enter 键
  blockSplitMode: 'enter' | 'prompt'
  // 自定义提示符正则（一行一条存储；prompt 模式下优先于内置正则）
  customPromptPatterns: string[]
  // 关键字高亮规则（借鉴 rssh；keyword 即正则源，也是规则身份键）
  highlightRules: HighlightRule[]
}

export const DEFAULT_SETTINGS: TerminalSettings = {
  theme: 'light',
  fontFamily: 'Consolas, "Microsoft YaHei", monospace',
  fontSize: 13,
  blockBar: true,
  blockAutoFold: false, // 折叠默认关闭（可在设置弹窗开启）
  blockMaxLines: 30,
  blockSplitMode: 'prompt',
  customPromptPatterns: [],
  // 深拷贝默认规则：SettingsModal 的编辑都产生新数组，此处防御共享引用被就地改
  highlightRules: DEFAULT_HIGHLIGHT_RULES.map((r) => ({ ...r })),
}

// 可选字体列表
export const FONT_OPTIONS = [
  { label: 'Consolas', value: 'Consolas, Monaco, "Microsoft YaHei", monospace' },
  { label: 'Monaco', value: 'Monaco, Consolas, "Microsoft YaHei", monospace' },
  { label: 'Menlo', value: 'Menlo, Consolas, "Microsoft YaHei", monospace' },
  { label: 'Courier New', value: '"Courier New", Courier, "Microsoft YaHei", monospace' },
  { label: 'Source Code Pro', value: '"Source Code Pro", Consolas, "Microsoft YaHei", monospace' },
  { label: 'Fira Code', value: '"Fira Code", Consolas, "Microsoft YaHei", monospace' },
  { label: 'JetBrains Mono', value: '"JetBrains Mono", Consolas, "Microsoft YaHei", monospace' },
  { label: 'Cascadia Code', value: '"Cascadia Code", Consolas, "Microsoft YaHei", monospace' },
]

function clampFontSize(size: number): number {
  return Math.max(8, Math.min(32, Math.round(size)))
}

/** 前端 camelCase 设置 → 后端 snake_case ui_settings 负载（只映射传入的键，用于部分更新） */
export function toUiSettingsPayload(partial: Partial<TerminalSettings>): Partial<UiSettingsPayload> {
  const out: Partial<UiSettingsPayload> = {}
  if (partial.theme !== undefined) out.theme = partial.theme
  if (partial.fontFamily !== undefined) out.font_family = partial.fontFamily
  if (partial.fontSize !== undefined) out.font_size = partial.fontSize
  if (partial.blockBar !== undefined) out.block_bar = partial.blockBar
  if (partial.blockAutoFold !== undefined) out.block_auto_fold = partial.blockAutoFold
  if (partial.blockMaxLines !== undefined) out.block_max_lines = partial.blockMaxLines
  if (partial.blockSplitMode !== undefined) out.block_split_mode = partial.blockSplitMode
  if (partial.customPromptPatterns !== undefined) out.custom_prompt_patterns = partial.customPromptPatterns
  if (partial.highlightRules !== undefined) out.highlight_rules = partial.highlightRules
  return out
}

/** 高亮规则的形状校验（逐项过滤的判据；与后端 _validate_highlight_rule 对齐） */
function isHighlightRuleShape(r: unknown): r is HighlightRule {
  if (!r || typeof r !== 'object') return false
  const v = r as Partial<HighlightRule>
  return (
    typeof v.keyword === 'string' &&
    v.keyword.trim() !== '' &&
    typeof v.name === 'string' &&
    v.name.trim() !== '' &&
    typeof v.color === 'string' &&
    typeof v.enabled === 'boolean' &&
    typeof v.is_case_sensitive === 'boolean'
  )
}

/** 后端 ui_settings 负载 → 前端设置（逐键校验，非法/缺失键保留 base 默认值） */
export function applyUiSettings(
  payload: Partial<UiSettingsPayload> | null | undefined,
  base: TerminalSettings = DEFAULT_SETTINGS
): TerminalSettings {
  const next: TerminalSettings = { ...base }
  if (!payload) return next
  if (payload.theme === 'dark' || payload.theme === 'light') next.theme = payload.theme
  if (typeof payload.font_family === 'string' && payload.font_family.trim()) next.fontFamily = payload.font_family
  if (typeof payload.font_size === 'number' && Number.isFinite(payload.font_size)) {
    next.fontSize = clampFontSize(payload.font_size)
  }
  if (typeof payload.block_bar === 'boolean') next.blockBar = payload.block_bar
  if (typeof payload.block_auto_fold === 'boolean') next.blockAutoFold = payload.block_auto_fold
  if (typeof payload.block_max_lines === 'number' && Number.isFinite(payload.block_max_lines)) {
    next.blockMaxLines = payload.block_max_lines
  }
  if (payload.block_split_mode === 'enter' || payload.block_split_mode === 'prompt') {
    next.blockSplitMode = payload.block_split_mode
  }
  if (Array.isArray(payload.custom_prompt_patterns)) {
    // 逐项过滤：只保留非空字符串（编译层还会做长度/语法兜底）
    next.customPromptPatterns = payload.custom_prompt_patterns.filter(
      (p): p is string => typeof p === 'string' && p.trim().length > 0
    )
  }
  if (Array.isArray(payload.highlight_rules)) {
    // 逐项过滤（形状校验）+ keyword 去重（keyword 是规则身份键）+ 条数上限，
    // 与后端 config.py 的校验规则对齐；非法条目剔除而非整键回退
    const seenKeywords = new Set<string>()
    const cleaned: HighlightRule[] = []
    for (const r of payload.highlight_rules) {
      if (!isHighlightRuleShape(r)) continue
      if (seenKeywords.has(r.keyword)) continue
      seenKeywords.add(r.keyword)
      cleaned.push(r)
      if (cleaned.length >= MAX_HIGHLIGHT_RULES) break
    }
    next.highlightRules = cleaned
  }
  return next
}

export function useSettings() {
  const [settings, setSettings] = useState<TerminalSettings>(DEFAULT_SETTINGS)

  // 挂载时从后端 config.json 读取持久化设置（设置不再存 localStorage，只存临时内容）
  useEffect(() => {
    let cancelled = false
    api
      .getConfig()
      .then((cfg) => {
        if (!cancelled) setSettings((prev) => applyUiSettings(cfg.ui_settings, prev))
      })
      .catch((e) => console.error('读取 UI 设置失败，使用默认值', e))
    return () => {
      cancelled = true
    }
  }, [])

  // 应用主题到 document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', settings.theme)
  }, [settings.theme])

  const updateSettings = useCallback((partial: Partial<TerminalSettings>) => {
    setSettings((prev) => ({ ...prev, ...partial }))
    // 持久化到后端 config.json（异步不阻塞 UI，失败仅提示）
    api.updateUiSettings(toUiSettingsPayload(partial)).catch((e) => console.error('保存 UI 设置失败', e))
  }, [])

  const toggleTheme = useCallback(() => {
    updateSettings({ theme: settings.theme === 'dark' ? 'light' : 'dark' })
  }, [settings.theme, updateSettings])

  const setFontFamily = useCallback((fontFamily: string) => {
    updateSettings({ fontFamily })
  }, [updateSettings])

  const setFontSize = useCallback((fontSize: number) => {
    updateSettings({ fontSize: clampFontSize(fontSize) })
  }, [updateSettings])

  return {
    settings,
    toggleTheme,
    setFontFamily,
    setFontSize,
    updateSettings,
  }
}
