import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../lib/api'

interface HistorySearchModalProps {
  onClose: () => void
  onExecute: (command: string) => void      // 直接执行
  onInputToTerminal: (command: string) => void  // 只输入到终端，可编辑
  onRefreshQuickCommands: () => void  // 保存快捷命令后刷新列表
}

type SearchResult =
  | { type: 'quick'; id: string; name: string; command: string; cmd_type?: string }
  | { type: 'history'; command: string; count: number; last_used: number }

export default function HistorySearchModal({ onClose, onExecute, onInputToTerminal, onRefreshQuickCommands }: HistorySearchModalProps) {
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [loading, setLoading] = useState(false)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [saveName, setSaveName] = useState('')
  const [confirmingCmd, setConfirmingCmd] = useState<string | null>(null)  // 待确认忽略的命令
  const [showIgnored, setShowIgnored] = useState(false)  // 已忽略列表视图
  const [ignoredList, setIgnoredList] = useState<Extract<SearchResult, { type: 'history' }>[]>([])
  const [ignoredLoading, setIgnoredLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 统一搜索（快捷命令 + 历史命令，已忽略的不显示），最多显示20条
  const doSearch = useCallback(async (kw: string) => {
    setLoading(true)
    try {
      const res = await api.unifiedSearch(kw, 20)
      setResults(res.results || [])
      setSelectedIndex(0)
    } catch (e) {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [])

  // 加载已忽略命令列表
  const loadIgnored = useCallback(async () => {
    setIgnoredLoading(true)
    try {
      const res = await api.listIgnoredCommands(200)
      setIgnoredList((res.commands || []).map(c => ({ type: 'history' as const, ...c })))
    } catch (e) {
      setIgnoredList([])
    } finally {
      setIgnoredLoading(false)
    }
  }, [])

  // 初始加载
  useEffect(() => {
    doSearch('')
    inputRef.current?.focus()
  }, [doSearch])

  // window 级 ESC：无论焦点在哪都能关闭（点遮罩后焦点丢失也能 ESC 关闭）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (confirmingCmd) { setConfirmingCmd(null); return }
        if (showIgnored) { setShowIgnored(false); return }
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, confirmingCmd, showIgnored])

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
      await api.addQuickCommand({ name: saveName.trim(), command })
      onRefreshQuickCommands()
      setSavingId(null)
      setSaveName('')
      // 重新搜索以显示新的快捷命令
      doSearch(keyword)
    } catch (e) {
      alert('保存失败: ' + (e as Error).message)
    }
  }

  // 忽略（确认后调用）
  const confirmIgnore = async (command: string) => {
    try {
      await api.ignoreHistoryCommand(command)
      setConfirmingCmd(null)
      doSearch(keyword)  // 刷新：被忽略的命令从结果中消失
    } catch (e) {
      alert('忽略失败: ' + (e as Error).message)
    }
  }

  // 恢复已忽略命令
  const restoreIgnore = async (command: string) => {
    try {
      await api.unignoreHistoryCommand(command)
      await loadIgnored()
      doSearch(keyword)
    } catch (e) {
      alert('恢复失败: ' + (e as Error).message)
    }
  }

  // 切换已忽略视图
  const toggleIgnored = () => {
    if (showIgnored) {
      setShowIgnored(false)
    } else {
      setShowIgnored(true)
      setConfirmingCmd(null)
      loadIgnored()
    }
  }

  // 键盘事件
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (confirmingCmd) {
      // 忽略确认行的键盘处理
      if (e.key === 'Enter') {
        e.preventDefault()
        confirmIgnore(confirmingCmd)
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setConfirmingCmd(null)
      }
      return
    }
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
      const list = showIgnored ? ignoredList : results
      if (list.length > 0) {
        setSelectedIndex((prev) => Math.min(prev + 1, list.length - 1))
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const list = showIgnored ? ignoredList : results
      if (list.length > 0) {
        setSelectedIndex((prev) => Math.max(prev - 1, 0))
      }
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (showIgnored) return  // 已忽略视图不执行
      const item = results[selectedIndex]
      if (item) {
        if (item.type === 'quick' && item.cmd_type !== 'param') {
          // 快捷命令（direct）：直接执行
          onExecute(item.command)
          onClose()
        } else {
          // 带参数快捷命令 / 历史命令：输入到终端，可编辑
          onInputToTerminal(item.command)
          onClose()
        }
      } else if (keyword.trim()) {
        // 没有匹配结果：把搜索框内容输入到终端（不执行），用户可编辑
        onInputToTerminal(keyword.trim())
        onClose()
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      if (showIgnored) { setShowIgnored(false); return }
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
      // 点击遮罩不关闭：防止鼠标误触导致输入内容丢失，请用右上角 ✕ 或 ESC 关闭
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="history-modal" onClick={(e) => e.stopPropagation()}>
        <div className="history-modal-header">
          <span className="history-modal-title">🔍 命令搜索 (Alt+R)</span>
          <div className="history-modal-header-right">
            <span className="history-modal-hint">↑↓ 选择 | Enter 执行/输入 | Ctrl+S 保存 | ESC 关闭</span>
            <button
              className={`history-ignored-toggle${showIgnored ? ' active' : ''}`}
              onClick={toggleIgnored}
              title={showIgnored ? '返回搜索' : '查看已忽略的命令'}
            >
              {showIgnored ? '← 返回搜索' : '已忽略'}
            </button>
            <button className="history-modal-close" onClick={onClose} title="关闭 (ESC)">✕</button>
          </div>
        </div>
        {showIgnored ? (
          <div className="history-result-list" ref={listRef}>
            {ignoredLoading && <div className="history-loading">加载中...</div>}
            {!ignoredLoading && ignoredList.length === 0 && (
              <div className="history-empty">暂无已忽略的命令</div>
            )}
            {ignoredList.map((item, idx) => (
              <div key={idx} data-index={idx}>
                <div className="history-result-item ignored-item" onClick={() => setSelectedIndex(idx)}>
                  <span className="history-type-badge ignored">🚫已忽略</span>
                  <span className="history-cmd-text">{item.command}</span>
                  <span className="history-cmd-count" title={`使用 ${item.count} 次`}>
                    ×{item.count}
                  </span>
                  <button
                    className="history-restore-btn"
                    title="恢复该命令"
                    onClick={(e) => {
                      e.stopPropagation()
                      restoreIgnore(item.command)
                    }}
                  >
                    恢复
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <>
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
                  ) : confirmingCmd === item.command && item.type === 'history' ? (
                    // 忽略确认行（防止误操作）
                    <div className="history-confirm-row">
                      <span className="history-confirm-label">确定忽略该命令？搜索中将不再显示</span>
                      <button
                        className="history-confirm-btn"
                        onClick={() => confirmIgnore(item.command)}
                      >
                        忽略
                      </button>
                      <button
                        className="history-cancel-btn"
                        onClick={() => setConfirmingCmd(null)}
                      >
                        取消
                      </button>
                    </div>
                  ) : (
                    <div
                      className={`history-result-item ${idx === selectedIndex ? 'selected' : ''} ${item.type}`}
                      onClick={() => {
                        setSelectedIndex(idx)
                        if (item.type === 'quick' && item.cmd_type !== 'param') {
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
                          <span className={`history-type-badge quick${item.cmd_type === 'param' ? ' param' : ''}`} title={item.cmd_type === 'param' ? '带参数指令：输入到终端后编辑 {args} 再执行' : '直接执行'}>
                            {item.cmd_type === 'param' ? '⌨️快捷' : '⚡快捷'}
                          </span>
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
                          <button
                            className="history-ignore-btn"
                            title="忽略此命令（不再出现在搜索中）"
                            onClick={(e) => {
                              e.stopPropagation()
                              setConfirmingCmd(item.command)
                            }}
                          >
                            🚫
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
