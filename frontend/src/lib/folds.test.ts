/**
 * FoldStore（真折叠）单元测试——精简移植验证（rssh 原版 975 行测试不搬运）。
 *
 * 假终端同时实现两套面：
 *   - 公开面（tracker 需要）：buffer.active / onData / onWriteParsed /
 *     registerMarker / parser.registerEscHandler
 *   - 私有面（folds 需要）：_core.buffer.{lines,ybase,ydisp,y,getBlankLine,
 *     addMarker} + _core._viewport.queueSync
 * 并模拟 xterm 的关键机制：marker 随 splice 自动迁移/范围内 dispose
 * （Buffer.addMarker 注册 lines.onDelete/onInsert 的等价行为）。
 *
 * 覆盖：fold 不变量（lines.length === ybase + rows）、unfold 完整恢复、
 * 非 LIFO 展开顺序、discardAll 不塞回、savedLines 预算、自动折叠 enforceAutoFold。
 */
import { describe, expect, it, vi } from 'vitest'
import { createCommandBlockTracker } from './commandBlocks'
import type { CommandBlockTracker } from './commandBlocks'
import { createFoldStore } from './folds'
import type { Terminal } from '@xterm/xterm'

const ROWS = 6

interface FakeLine {
  text: string
  isWrapped: boolean
  getTrimmedLength(): number
}

function makeLine(text: string): FakeLine {
  return { text, isWrapped: false, getTrimmedLength: () => text.replace(/\s+$/, '').length }
}

function makeFakeTerm() {
  const lines: FakeLine[] = []
  let ybase = 0
  let y = 0
  let ydisp = 0

  // ---- marker 迁移机制（等价于 xterm Buffer.addMarker 注册的 lines 事件）----
  interface FakeMarker {
    line: number
    isDisposed: boolean
    _onDispose: (() => void)[]
    onDispose(fn: () => void): { dispose(): void }
    dispose(): void
  }
  const markers: FakeMarker[] = []
  function makeMarker(line: number): FakeMarker {
    const m: FakeMarker = {
      line,
      isDisposed: false,
      _onDispose: [],
      onDispose(fn: () => void) {
        m._onDispose.push(fn)
        return { dispose: () => {} }
      },
      dispose() {
        if (m.isDisposed) return
        m.isDisposed = true
        m._onDispose.forEach((fn) => fn())
      },
    }
    markers.push(m)
    return m
  }
  const onDeleteSubs: ((start: number, amount: number) => void)[] = []
  const onInsertSubs: ((start: number, amount: number) => void)[] = []

  function handleDelete(start: number, amount: number) {
    for (const m of markers) {
      if (m.isDisposed) continue
      if (m.line >= start && m.line < start + amount) m.dispose()
      else if (m.line >= start + amount) m.line -= amount
    }
    onDeleteSubs.forEach((fn) => fn(start, amount))
  }
  function handleInsert(start: number, amount: number) {
    for (const m of markers) {
      if (!m.isDisposed && m.line >= start) m.line += amount
    }
    onInsertSubs.forEach((fn) => fn(start, amount))
  }

  // ---- 私有 lines（CircularList 最小面）----
  const privateLines = {
    get length() {
      return lines.length
    },
    get(i: number) {
      return lines[i]
    },
    splice(start: number, deleteCount: number, ...items: FakeLine[]) {
      const removed = lines.splice(start, deleteCount, ...items)
      if (deleteCount > 0) handleDelete(start, deleteCount)
      if (items.length > 0) handleInsert(start, items.length)
      return removed
    },
    push(item: FakeLine) {
      lines.push(item)
    },
  }

  // ---- 公开 buffer.active（tracker 需要）----
  const buf = {
    type: 'normal' as 'normal' | 'alternate',
    get baseY() {
      return ybase
    },
    get cursorY() {
      return y
    },
    active: null as unknown,
    getLine(y_: number) {
      const l = lines[y_]
      return l ? { isWrapped: l.isWrapped, translateToString: () => l.text } : undefined
    },
  }
  buf.active = buf

  const handlers = {
    data: [] as ((d: string) => void)[],
    writeParsed: [] as (() => void)[],
    lineFeed: [] as (() => void)[],
    cursorMove: [] as (() => void)[],
  }

  const term = {
    rows: ROWS,
    refresh: vi.fn(),
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
    onLineFeed: (fn: () => void) => {
      handlers.lineFeed.push(fn)
      return { dispose: () => {} }
    },
    onCursorMove: (fn: () => void) => {
      handlers.cursorMove.push(fn)
      return { dispose: () => {} }
    },
    onScroll: () => ({ dispose: () => {} }),
    onResize: () => ({ dispose: () => {} }),
    onDispose: () => ({ dispose: () => {} }),
    onBell: () => ({ dispose: () => {} }),
    parser: { registerEscHandler: () => ({ dispose: () => {} }) },
    registerMarker: (cursorOffset = 0) => makeMarker(ybase + y + cursorOffset),
    // ---- 私有面（folds.ts 唯一允许的入口）----
    _core: {
      buffer: {
        lines: privateLines,
        get ybase() {
          return ybase
        },
        set ybase(v: number) {
          ybase = v
        },
        get ydisp() {
          return ydisp
        },
        set ydisp(v: number) {
          ydisp = v
        },
        get y() {
          return y
        },
        set y(v: number) {
          y = v
        },
        getBlankLine(_attr: unknown) {
          return makeLine('')
        },
        addMarker(line: number) {
          return makeMarker(line)
        },
      },
      _viewport: { queueSync: vi.fn() },
    },
  }

  return {
    term: term as unknown as Terminal,
    /** 模拟输出：逐行推进（LF 事件逐行触发，模拟真实解析节奏） */
    feed(texts: string[]) {
      for (const text of texts) {
        lines.push(makeLine(text))
        if (lines.length > ROWS) {
          ybase = lines.length - ROWS
          y = ROWS - 1
        } else {
          y = lines.length - 1
        }
        handlers.lineFeed.forEach((fn) => fn())
      }
      handlers.cursorMove.forEach((fn) => fn())
      handlers.writeParsed.forEach((fn) => fn())
    },
    /** 模拟键盘输入 */
    type(data: string) {
      handlers.data.forEach((fn) => fn(data))
    },
    lines,
    markerCount: () => markers.filter((m) => !m.isDisposed).length,
  }
}

/** 搭建 3 个块的固定场景：
 *   b1 = PS> cmd1 + out1,out2；b2 = PS> cmd2 + outA,outB；b3 = PS>（打开中）
 * 行号：cmd1(0) out1(1) out2(2) cmd2(3) outA(4) outB(5) PS>(6) */
function setup3Blocks() {
  const f = makeFakeTerm()
  const tracker: CommandBlockTracker = createCommandBlockTracker(f.term, 'enter')
  f.feed(['PS> cmd1'])
  f.type('\r') // b1 @0
  f.feed(['out1', 'out2', 'PS> cmd2'])
  f.type('\r') // b1 end=2, b2 @3
  f.feed(['outA', 'outB', 'PS>'])
  f.type('\r') // b2 end=5, b3 @6
  return { f, tracker }
}

describe('FoldStore fold/unfold 基础', () => {
  it('fold 抽出 body 行并维持 lines.length === ybase + rows 不变量', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 100 })
    expect(store.fold(1)).toBe(true)
    // b1 body（out1,out2）被抽出，补偿空行补在末尾
    expect(f.lines).toHaveLength(6)
    expect(f.lines.map((l) => l.text)).toEqual(['PS> cmd1', 'PS> cmd2', 'outA', 'outB', 'PS>', ''])
    const bufPriv = (f.term as any)._core.buffer
    expect(bufPriv.lines.length).toBe(bufPriv.ybase + f.term.rows)
    expect(store.getFold(1)?.savedLines.map((l) => (l as FakeLine).text)).toEqual(['out1', 'out2'])
    store.dispose()
    tracker.dispose()
  })

  it('unfold 完整恢复内容与后续 marker 位置', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 100 })
    store.fold(1)
    expect(store.unfold(1)).toBe(true)
    expect(store.folds).toHaveLength(0)
    expect(f.lines.map((l) => l.text)).toEqual([
      'PS> cmd1', 'out1', 'out2', 'PS> cmd2', 'outA', 'outB', 'PS>',
    ])
    const bufPriv = (f.term as any)._core.buffer
    expect(bufPriv.lines.length).toBe(bufPriv.ybase + f.term.rows)
    // b2 的 marker 必须回到正确行（start=3 / end=5）
    const b2 = tracker.blocks.find((b) => b.id === 2)!
    expect(b2.start.line).toBe(3)
    expect(b2.end!.line).toBe(5)
    store.dispose()
    tracker.dispose()
  })

  it('运行中块（无 end）不能折叠', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 100 })
    expect(store.fold(3)).toBe(false)
    expect(store.folds).toHaveLength(0)
    store.dispose()
    tracker.dispose()
  })
})

describe('FoldStore 非 LIFO 展开', () => {
  it('先展开较早块再展开较晚块，内容与 marker 均不错乱', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 100 })
    expect(store.fold(1)).toBe(true)
    expect(store.fold(2)).toBe(true)
    // 两个 body（各 2 行）都抽出，只剩命令行/提示符 + 3 个补偿空行（b1 补 1 + b2 补 2）
    expect(f.lines.map((l) => l.text)).toEqual(['PS> cmd1', 'PS> cmd2', 'PS>', '', '', ''])
    // 非 LIFO：先展开 b1
    expect(store.unfold(1)).toBe(true)
    expect(f.lines.map((l) => l.text)).toEqual([
      'PS> cmd1', 'out1', 'out2', 'PS> cmd2', 'PS>', '', '',
    ])
    // 再展开 b2
    expect(store.unfold(2)).toBe(true)
    expect(f.lines.map((l) => l.text)).toEqual([
      'PS> cmd1', 'out1', 'out2', 'PS> cmd2', 'outA', 'outB', 'PS>',
    ])
    // b3 的 start marker 始终跟随 PS> 行
    const b3 = tracker.blocks.find((b) => b.id === 3)!
    expect(b3.start.line).toBe(6)
    // full fold 展开后 b2 的 end marker 被重建
    const b2 = tracker.blocks.find((b) => b.id === 2)!
    expect(b2.end && !b2.end.isDisposed).toBe(true)
    expect(b2.end!.line).toBe(5)
    store.dispose()
    tracker.dispose()
  })
})

describe('FoldStore discardAll（term.reset 路径）', () => {
  it('只清记录不塞回（reset 后 savedLines 全 stale）', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 100 })
    store.fold(1)
    store.discardAll()
    expect(store.folds).toHaveLength(0)
    // buffer 保持折叠后状态（不恢复）
    expect(f.lines.map((l) => l.text)).toEqual(['PS> cmd1', 'PS> cmd2', 'outA', 'outB', 'PS>', ''])
    // 之后 unfold 无记录可展开
    expect(store.unfold(1)).toBe(false)
    store.dispose()
    tracker.dispose()
  })
})

describe('FoldStore savedLines 预算', () => {
  it('超出 maxCachedLines 时自动展开最旧 fold', () => {
    const { f, tracker } = setup3Blocks()
    // 预算 3：fold b1（2 行）后再 fold b2（2 行）→ 总 4 > 3 → b1 被展开
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 100, maxCachedLines: 3 })
    expect(store.fold(1)).toBe(true)
    expect(store.isFolded(1)).toBe(true)
    expect(store.fold(2)).toBe(true)
    expect(store.isFolded(2)).toBe(true)
    expect(store.isFolded(1)).toBe(false) // 预算挤掉最旧
    // b1 内容已交还 xterm
    expect(f.lines[1].text).toBe('out1')
    store.dispose()
    tracker.dispose()
  })
})

describe('FoldStore 自动折叠（enforceAutoFold）', () => {
  it('body 超过 maxVisibleLines 时 prefix 折叠最早部分，保留最新行可见', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, { maxVisibleLines: 1 })
    store.enforceAutoFold()
    // b1 body 2 行 → 最早 1 行（out1）被抽走；b2 同理（outA 被抽走）
    expect(store.getFold(1)?.kind).toBe('prefix')
    expect(store.getFold(1)?.savedLines.map((l) => (l as FakeLine).text)).toEqual(['out1'])
    expect(store.getFold(2)?.savedLines.map((l) => (l as FakeLine).text)).toEqual(['outA'])
    expect(f.lines.map((l) => l.text)).toEqual(['PS> cmd1', 'out2', 'PS> cmd2', 'outB', 'PS>', ''])
    const bufPriv = (f.term as any)._core.buffer
    expect(bufPriv.lines.length).toBe(bufPriv.ybase + f.term.rows)
    store.dispose()
    tracker.dispose()
  })

  it('shouldAutoFold 返回 false 时不折叠（搜索打开/开关关闭）', () => {
    const { f, tracker } = setup3Blocks()
    const store = createFoldStore(f.term, tracker, {
      maxVisibleLines: 1,
      shouldAutoFold: () => false,
    })
    store.enforceAutoFold()
    expect(store.folds).toHaveLength(0)
    store.dispose()
    tracker.dispose()
  })
})
