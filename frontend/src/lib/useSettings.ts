import { useState, useEffect, useCallback } from 'react'

export type Theme = 'dark' | 'light'

export interface TerminalSettings {
  theme: Theme
  fontFamily: string
  fontSize: number
}

const STORAGE_KEY = 'ssh-web-tool-settings'

const DEFAULT_SETTINGS: TerminalSettings = {
  theme: 'dark',
  fontFamily: 'Consolas, Monaco, monospace',
  fontSize: 13,
}

// 可选字体列表
export const FONT_OPTIONS = [
  { label: 'Consolas', value: 'Consolas, Monaco, monospace' },
  { label: 'Monaco', value: 'Monaco, Consolas, monospace' },
  { label: 'Menlo', value: 'Menlo, Consolas, monospace' },
  { label: 'Courier New', value: '"Courier New", Courier, monospace' },
  { label: 'Source Code Pro', value: '"Source Code Pro", Consolas, monospace' },
  { label: 'Fira Code', value: '"Fira Code", Consolas, monospace' },
  { label: 'JetBrains Mono', value: '"JetBrains Mono", Consolas, monospace' },
  { label: 'Cascadia Code', value: '"Cascadia Code", Consolas, monospace' },
]

function loadSettings(): TerminalSettings {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      return { ...DEFAULT_SETTINGS, ...JSON.parse(saved) }
    }
  } catch (e) {
    console.error('加载设置失败', e)
  }
  return DEFAULT_SETTINGS
}

function saveSettings(settings: TerminalSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch (e) {
    console.error('保存设置失败', e)
  }
}

export function useSettings() {
  const [settings, setSettings] = useState<TerminalSettings>(loadSettings)

  // 应用主题到 document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', settings.theme)
  }, [settings.theme])

  const updateSettings = useCallback((partial: Partial<TerminalSettings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...partial }
      saveSettings(next)
      return next
    })
  }, [])

  const toggleTheme = useCallback(() => {
    updateSettings({ theme: settings.theme === 'dark' ? 'light' : 'dark' })
  }, [settings.theme, updateSettings])

  const setFontFamily = useCallback((fontFamily: string) => {
    updateSettings({ fontFamily })
  }, [updateSettings])

  const setFontSize = useCallback((fontSize: number) => {
    updateSettings({ fontSize: Math.max(8, Math.min(32, fontSize)) })
  }, [updateSettings])

  return {
    settings,
    toggleTheme,
    setFontFamily,
    setFontSize,
    updateSettings,
  }
}
