/**
 * 终端内容搜索条（Ctrl+F，rssh TerminalPane 同款交互）。
 *
 * 真折叠互操作：折叠内容不在 xterm buffer 里搜不到——挂载时先 unfoldAll
 * 并暂停自动折叠（setSearchActive），保证全量可搜；关闭时清装饰、恢复
 * 自动折叠、焦点回终端（不自动重折叠，用户自行决定）。
 *
 * 计数：decorations 启用后 SearchAddon.onDidChangeResults 提供 n/N。
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
  const [results, setResults] = useState<{ index: number; count: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const queryRef = useRef('')

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

  const doFind = (direction: 'next' | 'prev') => {
    const q = queryRef.current
    if (!q) return
    if (direction === 'next') instance.search.findNext(q, { decorations: SEARCH_DECORATIONS })
    else instance.search.findPrevious(q, { decorations: SEARCH_DECORATIONS })
  }

  const onInput = (value: string) => {
    setQuery(value)
    queryRef.current = value
    if (!value) {
      instance.search.clearDecorations()
      setResults(null)
      return
    }
    // 输入即搜：每次从当前位置向后找（与浏览器 Ctrl+F 行为一致）
    instance.search.findNext(value, { decorations: SEARCH_DECORATIONS })
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      doFind(e.shiftKey ? 'prev' : 'next')
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
      return
    }
    // 输入条内按键不外溢（全局快捷键/终端不感知）
    e.stopPropagation()
  }

  const countText = results
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
      <span className={`terminal-search-count${results && results.count === 0 ? ' no-result' : ''}`}>
        {countText}
      </span>
      <button className="terminal-search-btn" title="上一个 (Shift+Enter)" onClick={() => doFind('prev')}>▲</button>
      <button className="terminal-search-btn" title="下一个 (Enter)" onClick={() => doFind('next')}>▼</button>
      <button className="terminal-search-btn" title="关闭 (Esc)" onClick={onClose}>✕</button>
    </div>
  )
}
