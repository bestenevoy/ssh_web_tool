import { useState, useEffect, useRef } from 'react'
import type { TerminalSettings } from '../lib/useSettings'
import { FONT_OPTIONS } from '../lib/useSettings'
import { MAX_PROMPT_PATTERN_COUNT, MAX_PROMPT_PATTERN_LENGTH } from '../lib/promptPatterns'

interface Props {
  settings: TerminalSettings
  onUpdate: (partial: Partial<TerminalSettings>) => void
  fallbackShell: string
  shellChoices: string[]
  onSetFallbackShell: (shell: 'cmd' | 'powershell' | 'pwsh') => void
  configFile: string
  onClose: () => void
}

// 前端临时缓存键前缀（localStorage 只存变化快的临时内容：布局宽度等）
const CACHE_KEY_PREFIX = 'ssh-web-tool-'

const SHELL_LABELS: Record<string, string> = {
  cmd: 'cmd',
  powershell: 'PowerShell',
  pwsh: 'PowerShell 7 (pwsh)',
}

/** 清理前端临时缓存（ssh-web-tool- 前缀的 localStorage 键），返回清理数量 */
export function clearFrontendCache(): number {
  const keys: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith(CACHE_KEY_PREFIX)) keys.push(k)
  }
  keys.forEach((k) => localStorage.removeItem(k))
  return keys.length
}

/**
 * 设置弹窗：本机终端 / 命令块 / 字体主题 / 缓存清理。
 * 除字体与主题外，其余配置项统一收拢到本弹窗（不再占顶栏）；
 * 所有持久化配置写入后端 config.json，localStorage 只保留临时内容。
 */
export function SettingsModal({ settings, onUpdate, fallbackShell, shellChoices, onSetFallbackShell, configFile, onClose }: Props) {
  const [cacheMsg, setCacheMsg] = useState('')
  const doneBtnRef = useRef<HTMLButtonElement>(null)
  // 自定义提示符正则：本地草稿 + 失焦/关闭时提交（逐键提交会频繁 POST 并重建 tracker）
  const [patternDraft, setPatternDraft] = useState(settings.customPromptPatterns.join('\n'))
  const [patternError, setPatternError] = useState<string | null>(null)

  // 打开后聚焦「完成」按钮：Esc 从弹窗内元素冒泡到 window 才能触发关闭
  // （否则焦点留在 xterm textarea，Esc 被终端截获）
  useEffect(() => {
    doneBtnRef.current?.focus()
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // 依赖草稿/设置：保证 handler 闭包里的 handleClose 拿到最新正则草稿
  }, [onClose, patternDraft, settings.customPromptPatterns])

  /** 提交自定义正则草稿：校验合法才落盘，非法红字提示保持草稿 */
  function commitPatterns() {
    const items = patternDraft.split('\n').map((s) => s.trim()).filter(Boolean)
    if (items.length > MAX_PROMPT_PATTERN_COUNT) return setPatternError(`最多 ${MAX_PROMPT_PATTERN_COUNT} 条`)
    if (items.some((s) => s.length > MAX_PROMPT_PATTERN_LENGTH)) {
      return setPatternError(`单条最长 ${MAX_PROMPT_PATTERN_LENGTH} 字符`)
    }
    try {
      items.forEach((s) => new RegExp(`^(?:${s})`))
    } catch {
      return setPatternError('存在无法编译的正则表达式，请检查语法')
    }
    setPatternError(null)
    if (items.join('\n') !== settings.customPromptPatterns.join('\n')) {
      onUpdate({ customPromptPatterns: items })
    }
  }

  /** 关闭前先提交正则草稿（Esc / 点遮罩 / 完成按钮统一走这里） */
  function handleClose() {
    commitPatterns()
    onClose()
  }

  const handleClearCache = () => {
    const n = clearFrontendCache()
    setCacheMsg(n > 0 ? `已清理 ${n} 项临时缓存（布局宽度等），重新打开页面后生效` : '没有可清理的临时缓存')
  }

  return (
    <div className="modal-overlay show" onClick={(e) => { if (e.target === e.currentTarget) handleClose() }}>
      <div className="modal settings-modal">
        <h3>⚙️ 设置</h3>

        {/* 字体与主题：保留在顶栏快捷操作，这里提供完整设置 */}
        <div className="settings-modal-section">
          <div className="settings-modal-title">字体与主题</div>
          <div className="settings-modal-row">
            <label>终端字体</label>
            <select value={settings.fontFamily} onChange={(e) => onUpdate({ fontFamily: e.target.value })}>
              {FONT_OPTIONS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
          </div>
          <div className="settings-modal-row">
            <label>字号（{settings.fontSize}px）</label>
            <input
              type="range" min={8} max={32} value={settings.fontSize}
              onChange={(e) => onUpdate({ fontSize: Number(e.target.value) })}
            />
          </div>
          <div className="settings-modal-row">
            <label>主题</label>
            <select
              value={settings.theme}
              onChange={(e) => onUpdate({ theme: e.target.value as TerminalSettings['theme'] })}
            >
              <option value="light">亮色</option>
              <option value="dark">暗色</option>
            </select>
          </div>
        </div>

        {/* 本机终端：默认 shell 与 SSH 断开后共用同一配置 */}
        <div className="settings-modal-section">
          <div className="settings-modal-title">本机终端</div>
          <div className="settings-modal-row">
            <label title="左侧「默认终端」条目打开的 shell，也是 SSH 断开后自动进入的 shell">默认 shell</label>
            <select value={fallbackShell} onChange={(e) => onSetFallbackShell(e.target.value as 'cmd' | 'powershell' | 'pwsh')}>
              {shellChoices.map((s) => (
                <option key={s} value={s}>{SHELL_LABELS[s] || s}</option>
              ))}
            </select>
          </div>
          <div className="settings-modal-hint" title="编辑此文件后重启生效">配置文件：{configFile || '（加载中）'}</div>
        </div>

        {/* 命令块：从顶栏收拢到设置弹窗 */}
        <div className="settings-modal-section">
          <div className="settings-modal-title">命令块</div>
          <div className="settings-modal-row">
            <label title="按提示符：识别到新 shell 提示符即开新块（更贴近真实命令边界）；按回车：每次回车开新块（确定性最高）">
              切块方式
            </label>
            <select
              value={settings.blockSplitMode}
              onChange={(e) => onUpdate({ blockSplitMode: e.target.value as TerminalSettings['blockSplitMode'] })}
            >
              <option value="prompt">按提示符</option>
              <option value="enter">按回车</option>
            </select>
          </div>
          <div className="settings-modal-row">
            <label title="命令左侧显示色条标记：单击色条折叠/展开输出，双击复制块内容">块标记</label>
            <input
              type="checkbox" checked={settings.blockBar}
              onChange={(e) => onUpdate({ blockBar: e.target.checked })}
            />
          </div>
          <div className="settings-modal-row">
            <label title="命令输出超过保留行数时自动折叠旧输出（下一个回车关闭块时生效）">自动折叠</label>
            <input
              type="checkbox" checked={settings.blockAutoFold}
              onChange={(e) => onUpdate({ blockAutoFold: e.target.checked })}
            />
          </div>
          <div className="settings-modal-row">
            <label>折叠保留行数</label>
            <select
              value={settings.blockMaxLines}
              onChange={(e) => onUpdate({ blockMaxLines: Number(e.target.value) })}
            >
              {[15, 30, 50, 100, 200].map((n) => (
                <option key={n} value={n}>{n} 行</option>
              ))}
            </select>
          </div>
          {/* 自定义提示符正则：prompt 模式下优先于内置正则；草稿失焦/关闭时提交 */}
          <div className="settings-modal-row-column">
            <label title="每行一条 JS 正则，无需写 ^（自动锚定行首）。内置正则不识别你的提示符形态时在此添加">
              自定义提示符正则（每行一条）
            </label>
            <textarea
              className="settings-pattern-input"
              value={patternDraft}
              placeholder={'每行一条，例如：\nmini> \ndb \\d+ =>\n留空则只用内置正则'}
              onChange={(e) => setPatternDraft(e.target.value)}
              onBlur={commitPatterns}
              spellCheck={false}
            />
            {patternError ? (
              <div className="settings-pattern-error">{patternError}（未保存）</div>
            ) : (
              <div className="settings-modal-hint">修改后从下一个提示符开始生效，已有块会被重置</div>
            )}
          </div>
        </div>

        {/* 缓存清理：localStorage 只存临时内容（布局宽度等），可一键清空 */}
        <div className="settings-modal-section">
          <div className="settings-modal-title">缓存</div>
          <div className="settings-modal-row">
            <label title="布局宽度等临时内容；持久化设置保存在 config.json，不受影响">前端临时缓存</label>
            <button className="btn btn-secondary btn-sm" onClick={handleClearCache}>清理</button>
          </div>
          {cacheMsg && <div className="settings-modal-hint">{cacheMsg}</div>}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
          <button ref={doneBtnRef} className="btn btn-primary" onClick={handleClose}>完成</button>
        </div>
      </div>
    </div>
  )
}
