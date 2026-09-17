/**
 * 命令块 tracker 单元测试（最小 fake Terminal，不依赖真实 xterm）。
 *
 * 覆盖：prompt 模式（banner 不误开块 → 首提示符开块 → Enter 后下个提示符切块、
 * 提示符行尾有命令文本不切块、自定义正则命中）、enter 模式回归。
 */
import { describe, expect, it, vi } from 'vitest'
import { createCommandBlockTracker } from './commandBlocks'
import { compilePromptPatterns } from './promptPatterns'

// ---- 最小 fake xterm：只实现 tracker 用到的面 ----

interface FakeLine {
  text: string
  wrapped?: boolean
}

function makeFakeTerm() {
  const lines: FakeLine[] = []
  let cursorY = 0
  const baseY = 0

  const buf = {
    type: 'normal' as 'normal' | 'alternate',
    baseY,
    get cursorY() {
      return cursorY
    },
    active: null as unknown,
    getLine(y: number) {
      const l = lines[y]
      return l ? { isWrapped: !!l.wrapped, translateToString: () => l.text } : undefined
    },
  }
  buf.active = buf

  const handlers = {
    data: [] as ((d: string) => void)[],
    writeParsed: [] as (() => void)[],
    esc: {} as Record<string, (data: string) => boolean>,
  }

  const term = {
    buffer: {
      active: buf,
      onBufferChange: () => ({ dispose: () => {} }),
    },
    onData: (fn: (d: string) => void) => {
      handlers.data.push(fn)
      return { dispose: () => {} }
    },
    onWriteParsed: (fn: () => void) => {
      handlers.writeParsed.push(fn)
      return { dispose: () => {} }
    },
    onBufferChange: () => ({ dispose: () => {} }),
    parser: {
      registerEscHandler: (ident: { final: string }, fn: (data: string) => boolean) => {
        handlers.esc[ident.final] = fn
        return { dispose: () => { delete handlers.esc[ident.final] } }
      },
    },
    registerMarker: (cursorOffset = 0) => makeMarker(baseY + cursorY + cursorOffset),
  }

  return {
    term: term as unknown as import('@xterm/xterm').Terminal,
    /** 模拟输出流结束：推进缓冲区并触发 onWriteParsed */
    feed(newLines: FakeLine[]) {
      lines.push(...newLines)
      cursorY = lines.length - 1
      handlers.writeParsed.forEach((fn) => fn())
    },
    /** 模拟键盘输入 */
    type(data: string) {
      handlers.data.forEach((fn) => fn(data))
    },
    /** 模拟 shell 输出 ESC c（硬重置） */
    fireEsc() {
      handlers.esc['c']?.('')
    },
  }
}

function makeMarker(line: number) {
  const m = {
    line,
    isDisposed: false,
    handlers: [] as (() => void)[],
    onDispose(fn: () => void) {
      m.handlers.push(fn)
      return { dispose: () => {} }
    },
    dispose() {
      if (m.isDisposed) return
      m.isDisposed = true
      m.handlers.forEach((fn) => fn())
    },
  }
  return m
}

// ---- 用例 ----

describe('prompt 模式切块', () => {
  it('banner 不开块，首个提示符行开块', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'prompt', {
      extraPromptPatterns: compilePromptPatterns(['mini>']),
    })
    f.feed([{ text: 'Welcome to Server v1.0' }, { text: 'mini> ' }])
    expect(tracker.blocks).toHaveLength(1)
    expect(tracker.blocks[0].start.line).toBe(1) // 块起点 = 提示符行，banner 未切块
    tracker.dispose()
  })

  it('Enter 后下个提示符出现时关闭上一块并开新块', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'prompt', {
      extraPromptPatterns: compilePromptPatterns(['mini>']),
    })
    f.feed([{ text: 'mini> ' }])
    expect(tracker.blocks).toHaveLength(1)
    f.type('\r') // 提交命令：仅标记等待，不切块
    expect(tracker.blocks).toHaveLength(1)
    expect(tracker.blocks[0].end).toBeNull()
    f.feed([{ text: 'output line 1' }, { text: 'output line 2' }, { text: 'mini> ' }])
    expect(tracker.blocks).toHaveLength(2)
    // 上一块终点 = 新提示符前一行（输出最后一行）
    expect(tracker.blocks[0].end?.line).toBe(2)
    expect(tracker.blocks[1].start.line).toBe(3)
    tracker.dispose()
  })

  it('提示符行尾有命令文本时不切块', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'prompt', {
      extraPromptPatterns: compilePromptPatterns(['mini>']),
    })
    f.feed([{ text: 'mini> ls -la' }])
    expect(tracker.blocks).toHaveLength(0)
    tracker.dispose()
  })

  it('Enter 后未出新提示符前重复触发 writeParsed 不重复切块', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'prompt', {
      extraPromptPatterns: compilePromptPatterns(['mini>']),
    })
    f.feed([{ text: 'mini> ' }])
    f.type('\r')
    f.feed([{ text: 'partial out' }]) // 光标行不是提示符 → 不切块
    expect(tracker.blocks).toHaveLength(1)
    expect(tracker.blocks[0].end).toBeNull()
    tracker.dispose()
  })

  it('自定义正则 + 内置正则同时可用', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'prompt', {
      extraPromptPatterns: compilePromptPatterns(['mini>']),
    })
    f.feed([{ text: 'mini> ' }])
    expect(tracker.blocks).toHaveLength(1) // 第 1 块由自定义正则开
    f.type('\r')
    f.feed([{ text: 'out' }, { text: 'root@db:~#' }])
    expect(tracker.blocks).toHaveLength(2) // 第 2 块由内置 user@host 正则开
    expect(tracker.blocks[1].start.line).toBe(2)
    tracker.dispose()
  })
})

describe('enter 模式回归', () => {
  it('每个回车切块（含多行粘贴），提示符正则不参与', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'enter', {
      extraPromptPatterns: compilePromptPatterns(['mini>']),
    })
    f.type('ls\r')
    expect(tracker.blocks).toHaveLength(1)
    f.type('dir\r\r') // 粘贴多行：每个 \r 都切
    expect(tracker.blocks).toHaveLength(3)
    f.feed([{ text: 'mini> ' }]) // writeParsed 在 enter 模式不切块
    expect(tracker.blocks).toHaveLength(3)
    tracker.dispose()
  })

  it('notifySubmit 与键盘 Enter 同语义（快捷指令程序注入路径）', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'enter')
    // 程序化提交（不发 onData）：enter 模式直接开新块
    tracker.notifySubmit()
    expect(tracker.blocks).toHaveLength(1)
    tracker.notifySubmit()
    expect(tracker.blocks).toHaveLength(2)
    tracker.dispose()
  })

  it('notifySubmit 在 prompt 模式武装等待提示符（不立即开块）', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'prompt')
    tracker.notifySubmit()
    expect(tracker.blocks).toHaveLength(0) // 等待返回的提示符
    f.feed([{ text: 'user@host:~$ ' }])
    expect(tracker.blocks).toHaveLength(1) // 提示符出现才开块
    tracker.dispose()
  })
})

describe('resetAll 导出与 onReset 通知', () => {
  it('resetAll 清空全部块并 dispose marker', () => {
    const f = makeFakeTerm()
    const tracker = createCommandBlockTracker(f.term, 'enter')
    f.type('a\r\r')
    expect(tracker.blocks).toHaveLength(2)
    tracker.resetAll()
    expect(tracker.blocks).toHaveLength(0)
  })

  it('ESC c 硬重置触发 onReset 回调（FoldStore discardAll 的通知链）', () => {
    const f = makeFakeTerm()
    const onReset = vi.fn()
    const tracker = createCommandBlockTracker(f.term, 'enter', { onReset })
    f.type('a\r')
    f.fireEsc() // 模拟 shell 输出 ESC c（reset）
    expect(onReset).toHaveBeenCalledTimes(1)
    expect(tracker.blocks).toHaveLength(0)
    tracker.dispose()
  })

  it('手动 resetAll 也触发 onReset（term.reset 路径）', () => {
    const f = makeFakeTerm()
    const onReset = vi.fn()
    const tracker = createCommandBlockTracker(f.term, 'enter', { onReset })
    tracker.resetAll()
    expect(onReset).toHaveBeenCalledTimes(1)
    tracker.dispose()
  })
})
