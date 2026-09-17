import { useState, useEffect, useRef } from 'react'
import type { TerminalSettings } from '../lib/useSettings'
import { FONT_OPTIONS } from '../lib/useSettings'
import { MAX_PROMPT_PATTERN_COUNT, MAX_PROMPT_PATTERN_LENGTH } from '../lib/promptPatterns'
import {
  DEFAULT_HIGHLIGHT_RULES,
  MAX_HIGHLIGHT_KEYWORD_LENGTH,
  MAX_HIGHLIGHT_RULES,
  validateHighlightRule,
  type HighlightRule,
  type HighlightValidationError,
} from '../lib/highlight'
import { api } from '../lib/api'
import { DirPickerModal } from './DirPickerModal'

interface Props {
  settings: TerminalSettings
  onUpdate: (partial: Partial<TerminalSettings>) => void
  fallbackShell: string
  shellChoices: string[]
  onSetFallbackShell: (shell: string) => void
  connectTimeout: number
  onSetConnectTimeout: (seconds: number) => Promise<void>
  configFile: string
  onReloadConfig: () => Promise<void>
  onClose: () => void
}

// 前端临时缓存键前缀（localStorage 只存变化快的临时内容：布局宽度等）
const CACHE_KEY_PREFIX = 'ssh-web-tool-'

const SHELL_LABELS: Record<string, string> = {
  cmd: 'cmd',
  powershell: 'PowerShell',
  pwsh: 'PowerShell 7 (pwsh)',
}

/** 设置页分区（左侧导航） */
type SectionId = 'appearance' | 'terminal' | 'blocks' | 'logging' | 'highlight' | 'cache'

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'appearance', label: '字体与主题' },
  { id: 'terminal', label: '本机终端' },
  { id: 'blocks', label: '命令块' },
  { id: 'logging', label: '日志记录' },
  { id: 'highlight', label: '关键字高亮' },
  { id: 'cache', label: '缓存' },
]

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
 * 设置页（整页覆盖层，非弹窗）：左侧分区导航 + 右侧内容区（rssh 式侧边栏布局）。
 * 分区：字体与主题 / 本机终端 / 命令块 / 关键字高亮 / 缓存清理。
 * 仅关闭按钮/完成/Esc 可关闭（遵循 AGENTS.md UI 规范）；
 * 所有持久化配置写入后端 config.json，localStorage 只保留临时内容。
 */
export function SettingsModal({
  settings,
  onUpdate,
  fallbackShell,
  shellChoices,
  onSetFallbackShell,
  connectTimeout,
  onSetConnectTimeout,
  configFile,
  onReloadConfig,
  onClose,
}: Props) {
  const [cacheMsg, setCacheMsg] = useState('')
  const [openDirMsg, setOpenDirMsg] = useState('')
  const [reloadMsg, setReloadMsg] = useState('')
  const doneBtnRef = useRef<HTMLButtonElement>(null)
  // 当前分区（左侧导航；切换不丢草稿 state）
  const [section, setSection] = useState<SectionId>('appearance')
  // 自定义提示符正则：本地草稿 + 失焦/关闭时提交（逐键提交会频繁 POST 并重建 tracker）
  const [patternDraft, setPatternDraft] = useState(settings.customPromptPatterns.join('\n'))
  const [patternError, setPatternError] = useState<string | null>(null)
  // SSH 连接超时：本地草稿 + 失焦提交（1-300 秒）
  const [timeoutDraft, setTimeoutDraft] = useState(String(connectTimeout))
  const [timeoutMsg, setTimeoutMsg] = useState('')
  // 关键字高亮：编辑态（index=-1 表示新增草稿；null 表示未在编辑）
  const [ruleEdit, setRuleEdit] = useState<{ index: number; draft: HighlightRule } | null>(null)
  const [ruleError, setRuleError] = useState<string | null>(null)
  // 日志记录：默认目录草稿（失焦提交）+ 目录选择弹窗开关
  const [logDirDraft, setLogDirDraft] = useState(settings.logRecordDir)
  const [logDirPickerOpen, setLogDirPickerOpen] = useState(false)

  // 外部设置变化（App 层 onUpdate 回写）时同步日志目录草稿
  useEffect(() => {
    setLogDirDraft(settings.logRecordDir)
  }, [settings.logRecordDir])

  // 外部超时变化（保存成功后 App 回传）时同步草稿
  useEffect(() => {
    setTimeoutDraft(String(connectTimeout))
  }, [connectTimeout])

  // 打开后聚焦页头「返回」按钮：Esc 从页内元素冒泡到 window 才能触发关闭
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

  /** 关闭前先提交正则草稿（Esc / 完成按钮统一走这里；遮罩点击不关闭，见 AGENTS.md UI 交互规范） */
  function handleClose() {
    commitPatterns()
    onClose()
  }

  const handleClearCache = () => {
    const n = clearFrontendCache()
    setCacheMsg(n > 0 ? `已清理 ${n} 项临时缓存（布局宽度等），重新打开页面后生效` : '没有可清理的临时缓存')
  }

  /** 在系统文件管理器中打开配置文件所在目录（后端 os.startfile / xdg-open） */
  const handleOpenConfigDir = () => {
    api.openConfigDir()
      .then((r) => setOpenDirMsg(`已打开 ${r.dir}`))
      .catch((e) => setOpenDirMsg('打开目录失败: ' + (e as Error).message))
  }

  /** 检查配置/脚本更新：扫描 config/data/scripts 后刷新主机与快捷指令（原顶栏按钮，移入设置页） */
  const handleReloadConfig = () => {
    setReloadMsg('正在扫描配置与脚本...')
    onReloadConfig()
      .then(() => setReloadMsg(''))
      .catch((e) => setReloadMsg('刷新失败: ' + (e as Error).message))
  }

  /** 提交连接超时草稿：1-300 秒，失焦生效 */
  function commitTimeout() {
    const n = Math.round(Number(timeoutDraft))
    if (!Number.isFinite(n)) return setTimeoutMsg('请输入数字')
    if (n < 1 || n > 300) return setTimeoutMsg('范围 1-300 秒')
    if (n === connectTimeout) return setTimeoutMsg('')
    onSetConnectTimeout(n)
      .then(() => setTimeoutMsg(''))
      .catch((e) => {
        setTimeoutDraft(String(connectTimeout)) // 保存失败回退显示
        setTimeoutMsg('保存失败: ' + (e as Error).message)
      })
  }

  /** 提交日志默认目录草稿：清空 = 恢复程序默认目录 */
  function commitLogDir() {
    const dir = logDirDraft.trim()
    if (dir === settings.logRecordDir) return
    onUpdate({ logRecordDir: dir })
  }

  /** 校验错误 → 中文提示 */
  function ruleErrorText(err: HighlightValidationError): string {
    switch (err.kind) {
      case 'name_required':
        return '规则名称不能为空'
      case 'name_too_long':
        return '规则名称最长 100 字符'
      case 'keyword_too_long':
        return `关键字最长 ${MAX_HIGHLIGHT_KEYWORD_LENGTH} 字符`
      case 'zero_width':
        return '该正则匹配不到任何可见字符（纯锚点/零宽断言）'
      case 'invalid':
        return `正则语法错误: ${err.message}`
    }
  }

  /** 就地更新某条规则（产生新数组，保证 settings 更新触发装饰层重画） */
  function updateRule(i: number, patch: Partial<HighlightRule>) {
    onUpdate({ highlightRules: settings.highlightRules.map((r, idx) => (idx === i ? { ...r, ...patch } : r)) })
  }

  function deleteRule(i: number) {
    if (ruleEdit?.index === i) setRuleEdit(null)
    onUpdate({ highlightRules: settings.highlightRules.filter((_, idx) => idx !== i) })
  }

  function startEditRule(i: number) {
    setRuleError(null)
    setRuleEdit({ index: i, draft: { ...settings.highlightRules[i] } })
  }

  function startAddRule() {
    setRuleError(null)
    setRuleEdit({ index: -1, draft: { keyword: '', name: '', color: '#FF6B6B', enabled: true, is_case_sensitive: false } })
  }

  /** 提交新增/编辑的规则：先校验，keyword 与其它规则重复则拒绝（keyword 是身份键） */
  function commitRuleEdit() {
    if (!ruleEdit) return
    if (!ruleEdit.draft.keyword.trim()) return setRuleError('关键字不能为空')
    const err = validateHighlightRule(ruleEdit.draft)
    if (err) return setRuleError(ruleErrorText(err))
    const dup = settings.highlightRules.findIndex((r, i) => i !== ruleEdit.index && r.keyword === ruleEdit.draft.keyword)
    if (dup >= 0) return setRuleError(`关键字与「${settings.highlightRules[dup].name}」重复（keyword 是规则身份键）`)
    const rules = settings.highlightRules.slice()
    if (ruleEdit.index < 0) rules.push(ruleEdit.draft)
    else rules[ruleEdit.index] = ruleEdit.draft
    onUpdate({ highlightRules: rules })
    setRuleEdit(null)
    setRuleError(null)
  }

  /** 恢复默认规则（rssh 同款 9 条） */
  function resetRules() {
    setRuleEdit(null)
    setRuleError(null)
    onUpdate({ highlightRules: DEFAULT_HIGHLIGHT_RULES.map((r) => ({ ...r })) })
  }

  return (
    <div className="settings-overlay">
      <div className="settings-page">
        {/* 页头：标题 + 右上角返回按钮（打开时聚焦此处，保证 Esc 能冒泡关闭） */}
        <div className="settings-page-header">
          <span className="settings-page-title">⚙️ 设置</span>
          <button
            ref={doneBtnRef}
            className="btn btn-secondary btn-sm"
            onClick={handleClose}
            title="返回主界面（Esc）"
          >
            ← 返回
          </button>
        </div>
        <div className="settings-page-layout">
          {/* 左侧分区导航 */}
          <nav className="settings-page-nav">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                className={`settings-nav-item${section === s.id ? ' active' : ''}`}
                onClick={() => setSection(s.id)}
              >
                {s.label}
              </button>
            ))}
          </nav>
          {/* 右侧内容区：只渲染当前分区；命令块分区让自定义提示符输入框占满剩余空间 */}
          <div className={`settings-page-content${section === 'blocks' ? ' settings-content-fill' : ''}`}>

        {section === 'appearance' && (
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
          <div className="settings-modal-row">
            <label title="整个应用界面文本的字号倍率（终端字号由上方终端字体/字号单独控制）">
              界面字号
            </label>
            <select
              value={settings.uiFontScale}
              onChange={(e) => onUpdate({ uiFontScale: Number(e.target.value) })}
            >
              <option value="0.9">小（90%）</option>
              <option value="1">标准（100%）</option>
              <option value="1.15">大（115%）</option>
              <option value="1.3">特大（130%）</option>
            </select>
          </div>
        </div>
        )}

        {section === 'terminal' && (
        <div className="settings-modal-section">
          <div className="settings-modal-title">本机终端</div>
          <div className="settings-modal-row">
            <label title="左侧「默认终端」条目打开的 shell，也是 SSH 断开后自动进入的 shell">默认 shell</label>
            <select value={fallbackShell} onChange={(e) => onSetFallbackShell(e.target.value)}>
              {shellChoices.map((s) => (
                <option key={s} value={s}>{SHELL_LABELS[s] || s}</option>
              ))}
            </select>
          </div>
          <div className="settings-modal-row">
            <label title="SSH 建连（TCP）超时上限；目标不可达时最多等待该时长即报错">连接超时（秒）</label>
            <input
              type="number" min={1} max={300} value={timeoutDraft}
              onChange={(e) => setTimeoutDraft(e.target.value)}
              onBlur={commitTimeout}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
              style={{ width: 72 }}
            />
          </div>
          {timeoutMsg && <div className="settings-modal-hint">{timeoutMsg}</div>}
          <div className="settings-modal-hint" title="编辑此文件后重启生效">
            配置文件：{configFile || '（加载中）'}
            <button className="btn btn-secondary btn-sm settings-open-dir-btn" onClick={handleOpenConfigDir} title="在文件管理器中打开配置文件所在目录">
              📂 打开目录
            </button>
            <button className="btn btn-secondary btn-sm settings-open-dir-btn" onClick={handleReloadConfig} title="扫描配置文件与 scripts 脚本目录并刷新主机/快捷指令列表">
              🔄 检查配置
            </button>
            {reloadMsg && <div className="settings-open-dir-msg">{reloadMsg}</div>}
            {openDirMsg && <div className="settings-open-dir-msg">{openDirMsg}</div>}
          </div>
        </div>
        )}

        {section === 'blocks' && (
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
            <label title="命令左侧显示色条标记：单击色条选中块（Shift 范围 / Ctrl 多选），双击复制块内容；折叠/展开走终端右键菜单">块标记</label>
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
        )}

        {section === 'logging' && (
        <div className="settings-modal-section">
          <div className="settings-modal-title">日志记录</div>
          <div className="settings-modal-hint">
            终端默认不记录日志；在终端右键菜单选择「开始记录日志」后才开始写入。
          </div>
          <div className="settings-modal-row-column">
            <label title="右键「开始记录日志」时若勾选了「不再询问」，将直接使用此目录（留空 = 程序默认目录 ~/.ai4one/sshtool/logs）">
              默认保存目录
            </label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                type="text"
                value={logDirDraft}
                placeholder="留空使用程序默认目录"
                spellCheck={false}
                onChange={(e) => setLogDirDraft(e.target.value)}
                onBlur={commitLogDir}
                onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                style={{ flex: 1 }}
              />
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setLogDirPickerOpen(true)}
                title="浏览并选择默认保存目录"
              >
                浏览…
              </button>
            </div>
          </div>
          <div className="settings-modal-row">
            <label title="勾选后右键「开始记录日志」不再弹目录选择，直接用上方默认目录">
              开启记录时不再询问目录
            </label>
            <input
              type="checkbox" checked={settings.logRecordNoAsk}
              onChange={(e) => onUpdate({ logRecordNoAsk: e.target.checked })}
            />
          </div>
          {logDirPickerOpen && (
            <DirPickerModal
              title="选择日志默认保存目录"
              initialDir={logDirDraft}
              onConfirm={(dir) => {
                setLogDirPickerOpen(false)
                setLogDirDraft(dir)
                onUpdate({ logRecordDir: dir })
              }}
              onCancel={() => setLogDirPickerOpen(false)}
            />
          )}
        </div>
        )}

        {section === 'highlight' && (
        <div className="settings-modal-section">
          <div className="settings-modal-title">关键字高亮</div>
          {settings.highlightRules.length === 0 && <div className="settings-modal-hint">暂无规则</div>}
          {settings.highlightRules.map((r, i) => (
            <div className="settings-hl-row" key={r.keyword}>
              <input
                type="color" className="settings-hl-color" value={r.color} disabled={!r.enabled}
                onChange={(e) => updateRule(i, { color: e.target.value })}
                title="匹配文本颜色"
              />
              <span className="settings-hl-name" title={r.keyword}>{r.name}</span>
              <input
                type="checkbox" checked={r.enabled}
                onChange={(e) => updateRule(i, { enabled: e.target.checked })}
                title="启用/停用该规则"
              />
              <button className="btn btn-secondary btn-sm" onClick={() => startEditRule(i)}>编辑</button>
              <button className="btn btn-secondary btn-sm" onClick={() => deleteRule(i)}>删除</button>
            </div>
          ))}
          {ruleEdit && (
            <div className="settings-hl-edit">
              <input
                value={ruleEdit.draft.name}
                placeholder="规则名称"
                onChange={(e) => setRuleEdit({ ...ruleEdit, draft: { ...ruleEdit.draft, name: e.target.value } })}
              />
              <input
                value={ruleEdit.draft.keyword}
                placeholder="正则，例如 \\bERROR\\b"
                spellCheck={false}
                onChange={(e) => setRuleEdit({ ...ruleEdit, draft: { ...ruleEdit.draft, keyword: e.target.value } })}
                onKeyDown={(e) => { if (e.key === 'Enter') commitRuleEdit() }}
              />
              <label className="settings-hl-case" title="关闭时按大小写不敏感匹配">
                <input
                  type="checkbox" checked={ruleEdit.draft.is_case_sensitive}
                  onChange={(e) => setRuleEdit({ ...ruleEdit, draft: { ...ruleEdit.draft, is_case_sensitive: e.target.checked } })}
                />
                区分大小写
              </label>
              <input
                type="color" className="settings-hl-color" value={ruleEdit.draft.color}
                onChange={(e) => setRuleEdit({ ...ruleEdit, draft: { ...ruleEdit.draft, color: e.target.value } })}
                title="匹配文本颜色"
              />
              <button className="btn btn-primary btn-sm" onClick={commitRuleEdit}>保存</button>
              <button className="btn btn-secondary btn-sm" onClick={() => { setRuleEdit(null); setRuleError(null) }}>取消</button>
            </div>
          )}
          {ruleError && <div className="settings-pattern-error">{ruleError}</div>}
          <div className="settings-hl-actions">
            <button
              className="btn btn-secondary btn-sm"
              disabled={settings.highlightRules.length >= MAX_HIGHLIGHT_RULES || !!ruleEdit}
              onClick={startAddRule}
            >
              ＋ 新增规则
            </button>
            <button className="btn btn-secondary btn-sm" onClick={resetRules}>恢复默认</button>
          </div>
          <div className="settings-modal-hint">
            keyword 为 JS 正则（即规则身份键）；重叠匹配时列表中先出现的规则优先。
            高亮只是显示层叠加，不会改写终端内容。
          </div>
        </div>
        )}

        {section === 'cache' && (
        <div className="settings-modal-section">
          <div className="settings-modal-title">缓存</div>
          <div className="settings-modal-row">
            <label title="布局宽度等临时内容；持久化设置保存在 config.json，不受影响">前端临时缓存</label>
            <button className="btn btn-secondary btn-sm" onClick={handleClearCache}>清理</button>
          </div>
          {cacheMsg && <div className="settings-modal-hint">{cacheMsg}</div>}
        </div>
        )}

          </div>
        </div>

        {/* 页脚：完成按钮（与页头 ✕ 均可关闭） */}
        <div className="settings-page-footer">
          <button className="btn btn-primary" onClick={handleClose}>完成</button>
        </div>
      </div>
    </div>
  )
}
