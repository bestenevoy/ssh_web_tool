/**
 * 终端复制/粘贴逻辑回归测试（重复粘贴 Bug）。
 *
 * 双粘贴根因：keydown 里 preventDefault + term.paste 的路径，在个别浏览器/输入法
 * 环境下 paste 事件仍会触发，xterm 原生 handlePasteEvent 再粘贴一次。
 * 修复后：keydown 只 return false，粘贴统一由 textarea capture 阶段拦截处理，
 * 单一路径，从机制上保证只粘贴一次。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { setupTerminalCopy, writeClipboardText } from './terminalCopy'

interface FakeTerm {
  textarea: EventTarget & { __wstoolPasteBound?: boolean }
  hasSelection: () => boolean
  getSelection: () => string
  clearSelection: ReturnType<typeof vi.fn>
  paste: ReturnType<typeof vi.fn>
  attachCustomKeyEventHandler: (h: (e: KeyboardEvent) => boolean) => void
  _keyHandler?: (e: KeyboardEvent) => boolean
}

function makeFakeTerm(): FakeTerm {
  const term: FakeTerm = {
    textarea: new EventTarget() as EventTarget & { __wstoolPasteBound?: boolean },
    hasSelection: () => true,
    getSelection: () => 'selected text',
    clearSelection: vi.fn(),
    paste: vi.fn(),
    attachCustomKeyEventHandler(h) {
      term._keyHandler = h
    },
  }
  return term
}

function dispatchPaste(ta: EventTarget, text: string) {
  const ev = new Event('paste') as unknown as ClipboardEvent
  Object.defineProperty(ev, 'clipboardData', {
    value: { getData: (fmt: string) => (fmt === 'text/plain' ? text : '') },
  })
  // 手动记录 preventDefault/stopImmediatePropagation 是否被调用
  let prevented = false
  let stopped = false
  ev.preventDefault = () => { prevented = true }
  ev.stopImmediatePropagation = () => { stopped = true }
  ;(ev as any).__prevented = () => prevented
  ;(ev as any).__stopped = () => stopped
  ta.dispatchEvent(ev)
  return ev as unknown as { __prevented(): boolean; __stopped(): boolean }
}

describe('setupTerminalCopy 粘贴逻辑', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('Ctrl+V keydown 返回 false（不发送 ^V 到终端）且不 preventDefault（让 paste 事件产生）', () => {
    const term = makeFakeTerm()
    setupTerminalCopy(term as any, () => {})
    const h = term._keyHandler!
    const e = { ctrlKey: true, key: 'v', preventDefault: vi.fn() } as any
    expect(h(e)).toBe(false)
    expect(e.preventDefault).not.toHaveBeenCalled()
  })

  it('paste 事件只粘贴一次（capture 拦截 + 纯文本）', () => {
    const term = makeFakeTerm()
    setupTerminalCopy(term as any, () => {})
    const ev = dispatchPaste(term.textarea, 'hello world')
    expect(term.paste).toHaveBeenCalledTimes(1)
    expect(term.paste).toHaveBeenCalledWith('hello world')
    expect(ev.__prevented()).toBe(true)
    expect(ev.__stopped()).toBe(true)
  })

  it('空剪贴板不触发粘贴', () => {
    const term = makeFakeTerm()
    setupTerminalCopy(term as any, () => {})
    dispatchPaste(term.textarea, '')
    expect(term.paste).not.toHaveBeenCalled()
  })

  it('重复调用 setupTerminalCopy 不会重复绑定（防重标志）', () => {
    const term = makeFakeTerm()
    setupTerminalCopy(term as any, () => {})
    setupTerminalCopy(term as any, () => {})
    dispatchPaste(term.textarea, 'once')
    expect(term.paste).toHaveBeenCalledTimes(1)
  })

  it('粘贴多行文本时折叠换行为 \\r（避免 CRLF 双换行）', () => {
    const term = makeFakeTerm()
    setupTerminalCopy(term as any, () => {})
    dispatchPaste(term.textarea, 'line1\r\nline2\nline3')
    expect(term.paste).toHaveBeenCalledTimes(1)
    expect(term.paste).toHaveBeenCalledWith('line1\rline2\rline3')
  })

  it('shell 开启 bracketed paste 时包裹 \\x1b[200~/\\x1b[201~', () => {
    const term = makeFakeTerm() as any
    // 模拟远端 shell 启用了 DECSET 2004（bash/zsh 默认）
    term.modes = { bracketedPasteMode: true }
    setupTerminalCopy(term, () => {})
    dispatchPaste(term.textarea, 'echo hi\nls -la')
    expect(term.paste).toHaveBeenCalledTimes(1)
    expect(term.paste).toHaveBeenCalledWith('\x1b[200~echo hi\rls -la\x1b[201~')
  })

  it('Ctrl+C 有选区时复制并返回 false（不发送 SIGINT）', () => {
    const term = makeFakeTerm()
    // mock copySelection 依赖的 navigator.clipboard
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) }
    vi.stubGlobal('navigator', { clipboard })
    setupTerminalCopy(term as any, () => {})
    const h = term._keyHandler!
    const e = { ctrlKey: true, key: 'c' } as any
    expect(h(e)).toBe(false)
    expect(clipboard.writeText).toHaveBeenCalledTimes(1)
  })

  it('Alt+R 触发搜索回调并返回 false', () => {
    const term = makeFakeTerm()
    const onAltR = vi.fn()
    setupTerminalCopy(term as any, onAltR)
    const h = term._keyHandler!
    const e = { altKey: true, key: 'r' } as any
    expect(h(e)).toBe(false)
    expect(onAltR).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+F 触发内容搜索回调、preventDefault 并返回 false（不发送 ^F 也不开浏览器查找）', () => {
    const term = makeFakeTerm()
    const onCtrlF = vi.fn()
    setupTerminalCopy(term as any, undefined, onCtrlF)
    const h = term._keyHandler!
    const e = { ctrlKey: true, key: 'f', preventDefault: vi.fn() } as any
    expect(h(e)).toBe(false)
    expect(e.preventDefault).toHaveBeenCalled()
    expect(onCtrlF).toHaveBeenCalledTimes(1)
  })

  it('Cmd+F（macOS metaKey）同样触发内容搜索回调', () => {
    const term = makeFakeTerm()
    const onCtrlF = vi.fn()
    setupTerminalCopy(term as any, undefined, onCtrlF)
    const h = term._keyHandler!
    const e = { metaKey: true, key: 'F', preventDefault: vi.fn() } as any
    expect(h(e)).toBe(false)
    expect(onCtrlF).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+B 触发折叠主机列表回调、preventDefault 并返回 false（不发送 ^B）', () => {
    const term = makeFakeTerm()
    const onCtrlB = vi.fn()
    setupTerminalCopy(term as any, undefined, undefined, onCtrlB)
    const h = term._keyHandler!
    const e = { ctrlKey: true, key: 'b', preventDefault: vi.fn() } as any
    expect(h(e)).toBe(false)
    expect(e.preventDefault).toHaveBeenCalled()
    expect(onCtrlB).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+B 大写 B（Shift 或大写锁定）同样触发', () => {
    const term = makeFakeTerm()
    const onCtrlB = vi.fn()
    setupTerminalCopy(term as any, undefined, undefined, onCtrlB)
    const h = term._keyHandler!
    const e = { ctrlKey: true, key: 'B', preventDefault: vi.fn() } as any
    expect(h(e)).toBe(false)
    expect(onCtrlB).toHaveBeenCalledTimes(1)
  })

  it('普通按键返回 true（xterm 正常处理）', () => {
    const term = makeFakeTerm()
    setupTerminalCopy(term as any, () => {})
    const h = term._keyHandler!
    expect(h({ key: 'a' } as any)).toBe(true)
  })
})
describe('writeClipboardText 三级兜底', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('优先走 pywebview 原生剪贴板桥', async () => {
    const copyText = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('window', { pywebview: { api: { copy_text: copyText } } })
    await expect(writeClipboardText('secret')).resolves.toBe(true)
    expect(copyText).toHaveBeenCalledWith('secret')
  })

  it('无 pywebview 时回退 navigator.clipboard', async () => {
    vi.stubGlobal('window', undefined)
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    await expect(writeClipboardText('text')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('text')
  })

  it('navigator.clipboard 拒绝时返回 false（不抛异常）', async () => {
    vi.stubGlobal('window', undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    await expect(writeClipboardText('text')).resolves.toBe(false)
  })
})