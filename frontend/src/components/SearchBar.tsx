/**
 * 终端内容搜索条（Ctrl+F，rssh TerminalPane 同款交互）。
 *
 * 真折叠互操作：折叠内容不在 xterm buffer 里搜不到——挂载时先 unfoldAll
 * 并暂停自动折叠（setSearchActive），保证全量可搜；关闭时清装饰、恢复
 * 自动折叠、焦点回终端（不自动重折叠，用户自行决定）。
 *
 * 计数：decorations 启用后 SearchAddon.onDidChangeResults 提供 n/N。
 * 高亮：普通匹配淡蓝灰、当前匹配红色（SEARCH_DECORATIONS）。
 * 选项：区分大小写（Aa）与正则表达式（.*），切换后立即重跑当前搜索。
 */
import { useEffect, useRef, useState } from 'react'
import type { TerminalInstance } from '../lib/useTerminals'
import { SEARCH_DECORATIONS } from '../lib/terminalInstance'

interface Props {
  instance: TerminalInstance
  onClose: () => void
}

export function SearchBar({ instance, onClose }: Props) {
  const [query, setQuery] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [useRegex, setUseRegex] = useState(false)
  const [results, setResults] = useState<{ index: number; count: number } | null>(null)
  const [regexError, setRegexError] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const queryRef = useRef('')
  // 选项用 ref 保存：键盘/切换回调里拿到最新值，不依赖重渲染时序
  const caseSensitiveRef = useRef(false)
  const useRegexRef = useRef(false)

  // 挂载：展开全部折叠 + 暂停自动折叠 + 订阅结果计数 + 聚焦输入框
  // （焦点必须在条内：xterm 吞 Esc 的坑见 SettingsModal 同款处理）
  useEffect(() => {
    instance.blockBar?.setSearchActive(true)
    instance.blockBar?.unfoldAll()
    const sub = instance.search.onDidChangeResults((e) => {
      setResults({ index: e.resultIndex, count: e.resultCount })
    })
    inputRef.current?.focus()
    return () => {
      sub.dispose()
      instance.search.clearDecorations()
      instance.blockBar?.setSearchActive(false)
      instance.term.focus()
    }
  }, [instance])

  /** 执行搜索：正则非法时提示错误、清掉旧高亮；否则带选项 + 高亮装饰查找 */
  const runSearch = (direction: 'next' | 'prev') => {
    const q = queryRef.current
    if (!q) return
    if (useRegexRef.current) {
      try {
        new RegExp(q)
      } catch {
        setRegexError(true)
        setResults(null)
        instance.search.clearDecorations()
        return
      }
    }
    setRegexError(false)
    const opts = {
      caseSensitive: caseSensitiveRef.current,
      regex: useRegexRef.current,
      decorations: SEARCH_DECORATIONS,
    }
    if (direction === 'next') instance.search.findNext(q, opts)
    else instance.search.findPrevious(q, opts)
  }

  const onInput = (value: string) => {
    setQuery(value)
    queryRef.current = value
    if (!value) {
      instance.search.clearDecorations()
      setResults(null)
      setRegexError(false)
      return
    }
    // 输入即搜：每次从当前位置向后找（与浏览器 Ctrl+F 行为一致）
    runSearch('next')
  }

  /** 切换选项并重跑当前搜索（query 为空只切状态）；onMouseDown 阻止焦点离开输入框 */
  const toggleOption = (kind: 'case' | 'regex') => {
    if (kind === 'case') {
      caseSensitiveRef.current = !caseSensitiveRef.current
      setCaseSensitive(caseSensitiveRef.current)
    } else {
      useRegexRef.current = !useRegexRef.current
      setUseRegex(useRegexRef.current)
    }
    if (queryRef.current) {
      // addon-search 0.16 缺陷：findNext 先把 lastSearchOptions 覆盖成新值再做
      // didOptionsChange 对比（恒 false），同词换选项不会重建高亮，计数停留在
      // 旧选项结果上。先清装饰（连带清 cachedSearchTerm）强制下次 find 全量重建。
      instance.search.clearDecorations()
      runSearch('next')
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      runSearch(e.shiftKey ? 'prev' : 'next')
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
      return
    }
    // 输入条内按键不外溢（全局快捷键/终端不感知）
    e.stopPropagation()
  }

  const countText = regexError
    ? '正则无效'
    : results
      ? results.count === 0
        ? '无结果'
        : results.index >= 0
          ? `${results.index + 1}/${results.count}`
          : `${results.count}+`
      : ''

  return (
    <div className="terminal-search-bar">
      <input
        ref={inputRef}
        className="terminal-search-input"
        value={query}
        onChange={(e) => onInput(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="搜索终端内容…"
        spellCheck={false}
      />
      <span className={`terminal-search-count${(results && results.count === 0) || regexError ? ' no-result' : ''}`}>
        {countText}
      </span>
      <button
        className={`terminal-search-btn${caseSensitive ? ' active' : ''}`}
        title="区分大小写"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => toggleOption('case')}
      >Aa</button>
      <button
        className={`terminal-search-btn${useRegex ? ' active' : ''}`}
        title="正则表达式"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => toggleOption('regex')}
      >.*</button>
      <button className="terminal-search-btn" title="上一个 (Shift+Enter)" onClick={() => runSearch('prev')}>▲</button>
      <button className="terminal-search-btn" title="下一个 (Enter)" onClick={() => runSearch('next')}>▼</button>
      <button className="terminal-search-btn" title="关闭 (Esc)" onClick={onClose}>✕</button>
    </div>
  )
}
