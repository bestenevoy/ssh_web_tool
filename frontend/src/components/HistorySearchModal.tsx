import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../lib/api'

interface HistorySearchModalProps {
  onClose: () => void
  onExecute: (command: string) => void      // 直接执行
  onInputToTerminal: (command: string) => void  // 只输入到终端，可编辑
  onRefreshQuickCommands: () => void  // 保存快捷命令后刷新列表
}

type SearchResult =
  | { type: 'quick'; id: string; name: string; command: string }
  | { type: 'history'; command: string; count: number; last_used: number }

export default function HistorySearchModal({ onClose, onExecute, onInputToTerminal, onRefreshQuickCommands }: HistorySearchModalProps) {
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [loading, setLoading] = useState(false)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [saveName, setSaveName] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 统一搜索（快捷命令 + 历史命令）
  const doSearch = useCallback(async (kw: string) => {
    setLoading(true)
    try {
      const res = await api.unifiedSearch(kw, 50)
      setResults(res.results || [])
      setSelectedIndex(0)
    } catch (e) {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [])

  // 初始加载
  useEffect(() => {
    doSearch('')
    inputRef.current?.focus()
  }, [doSearch])

  // 防抖搜索
  useEffect(() => {
    const timer = setTimeout(() => {
      doSearch(keyword)
    }, 200)
    return () => clearTimeout(timer)
  }, [keyword, doSearch])

  // 保存为快捷命令
  const handleSaveAsQuick = async (command: string, id: string) => {
    setSavingId(id)
    setSaveName(command.slice(0, 20))  // 默认名称为命令前20个字符
  }

  const confirmSave = async (command: string) => {
    if (!saveName.trim()) return
    try {
      await api.addQuickCommand(saveName.trim(), command)
      onRefreshQuickCommands()
      setSavingId(null)
      setSaveName('')
      // 重新搜索以显示新的快捷命令
      doSearch(keyword)
    } catch (e) {
      alert('保存失败: ' + (e as Error).message)
    }
  }

  // 键盘事件
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (savingId) {
      // 保存名称输入框的键盘处理
      if (e.key === 'Enter') {
        e.preventDefault()
        const item = results[selectedIndex]
        if (item && item.type === 'history') {
          confirmSave(item.command)
        }
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setSavingId(null)
        setSaveName('')
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (results.length > 0) {
        setSelectedIndex((prev) => Math.min(prev + 1, results.length - 1))
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (results.length > 0) {
        setSelectedIndex((prev) => Math.max(prev - 1, 0))
      }
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const item = results[selectedIndex]
      if (item) {
        if (item.type === 'quick') {
          // 快捷命令：直接执行
          onExecute(item.command)
          onClose()
        } else {
          // 历史命令：输入到终端，可编辑
          onInputToTerminal(item.command)
          onClose()
        }
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  // 滚动到选中项
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-index="${selectedIndex}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  return (
    <div
      className="history-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="history-modal" onClick={(e) => e.stopPropagation()}>
        <div className="history-modal-header">
          <span className="history-modal-title">🔍 命令搜索 (Alt+R)</span>
          <span className="history-modal-hint">↑↓ 选择 | Enter 执行/输入 | Ctrl+S 保存 | ESC 关闭</span>
        </div>
        <input
          ref={inputRef}
          type="text"
          className="history-search-input"
          placeholder="搜索快捷命令和历史命令（按名称或命令内容）..."
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="history-result-list" ref={listRef}>
          {loading && <div className="history-loading">搜索中...</div>}
          {!loading && results.length === 0 && (
            <div className="history-empty">暂无匹配的命令</div>
          )}
          {results.map((item, idx) => (
            <div key={idx} data-index={idx}>
              {savingId === `save-${idx}` && item.type === 'history' ? (
                // 保存为快捷命令的输入框
                <div className="history-save-row">
                  <span className="history-save-label">名称:</span>
                  <input
                    type="text"
                    className="history-save-input"
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    onKeyDown={handleKeyDown}
                    autoFocus
                    placeholder="输入快捷命令名称"
                  />
                  <button
                    className="history-save-btn"
                    onClick={() => confirmSave(item.command)}
                  >
                    保存
                  </button>
                  <button
                    className="history-cancel-btn"
                    onClick={() => { setSavingId(null); setSaveName('') }}
                  >
                    取消
                  </button>
                </div>
              ) : (
                <div
                  className={`history-result-item ${idx === selectedIndex ? 'selected' : ''} ${item.type}`}
                  onClick={() => {
                    setSelectedIndex(idx)
                    if (item.type === 'quick') {
                      onExecute(item.command)
                      onClose()
                    } else {
                      onInputToTerminal(item.command)
                      onClose()
                    }
                  }}
                >
                  {item.type === 'quick' ? (
                    <>
                      <span className="history-type-badge quick">⚡快捷</span>
                      <span className="history-quick-name">{item.name}</span>
                      <span className="history-cmd-text">{item.command}</span>
                    </>
                  ) : (
                    <>
                      <span className="history-type-badge history">🕐历史</span>
                      <span className="history-cmd-text">{item.command}</span>
                      <span className="history-cmd-count" title={`使用 ${item.count} 次`}>
                        ×{item.count}
                      </span>
                      <button
                        className="history-save-quick-btn"
                        title="保存为快捷命令"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleSaveAsQuick(item.command, `save-${idx}`)
                        }}
                      >
                        ⭐
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
