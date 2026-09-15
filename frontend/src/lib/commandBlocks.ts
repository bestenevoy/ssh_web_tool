/**
 * 命令块跟踪器（移植自 rssh command-blocks.ts，适配 xterm 5.3 公开 API）。
 *
 * 一个命令块 = 提示符 + 输入命令 + 全部输出。块由一对 xterm marker
 * （start + end）锚定，随滚动/裁剪自动迁移。
 *
 * 规则：
 *   1. enter 模式（默认）：普通缓冲区里每次 Enter（含多行粘贴）关闭上一个块
 *      并打开新块——确定性最高。
 *   2. prompt 模式：输出解析完后仅检查光标当前逻辑行；必须是"只包含已识别
 *      提示符"的新行才切块，之后保持关闭直到下一次 Enter。
 *   3. 切到备用缓冲区（vim/top/less/tmux）→ 关闭当前块并忽略备用区写入。
 *   4. start marker 因 scrollback 裁剪被丢弃时，块一并移除（GC）。
 *   5. 硬重置（RIS / ESC c）一次性清空所有块——xterm 清缓冲区不会 dispose
 *      我们的 marker，必须自己清。
 *
 * 本模块不持有 DOM。渲染层（blockBar.ts）通过 onChange 订阅重绘。
 */
import type { Terminal, IMarker, IDisposable } from '@xterm/xterm'
import { detectPrompt } from './promptPatterns'

export type CommandBlockSplitMode = 'enter' | 'prompt'

export interface CommandBlock {
  id: number
  color: string
  start: IMarker
  end: IMarker | null
}

export interface CommandBlockTracker extends IDisposable {
  readonly blocks: ReadonlyArray<CommandBlock>
  /** blocks 数组变化（新增/关闭/GC）时触发。 */
  onChange(fn: () => void): IDisposable
}

/** 金色角 HSL 循环取色——无限调色板，相邻块不撞色。 */
function colorForIndex(i: number): string {
  const hue = (i * 137.508) % 360
  return `hsl(${hue.toFixed(1)}, 65%, 58%)`
}

/** 光标所在逻辑行（合并 isWrapped 软换行）。 */
function logicalLineAtCursor(term: Terminal): { text: string; startLine: number; startOffset: number } | null {
  const buf = term.buffer.active
  const cursorAbs = buf.baseY + buf.cursorY
  let startAbs = cursorAbs
  while (startAbs > 0 && buf.getLine(startAbs)?.isWrapped) startAbs--

  let text = ''
  for (let y = startAbs; y <= cursorAbs; y++) {
    const line = buf.getLine(y)
    if (!line) return null
    text += line.translateToString(true)
  }
  return { text, startLine: startAbs, startOffset: startAbs - cursorAbs }
}

export interface CommandBlockTrackerOptions {
  /** 用户自定义提示符正则（已编译，prompt 模式下优先于内置正则匹配）。 */
  extraPromptPatterns?: ReadonlyArray<RegExp>
}

export function createCommandBlockTracker(
  term: Terminal,
  splitMode: CommandBlockSplitMode = 'enter',
  opts?: CommandBlockTrackerOptions,
): CommandBlockTracker {
  const blocks: CommandBlock[] = []
  const listeners = new Set<() => void>()
  let nextId = 1
  const disposables: IDisposable[] = []

  const emit = () => listeners.forEach((fn) => fn())

  let waitingForPrompt = splitMode === 'prompt'
  let submittedLine: IMarker | null = null

  const clearSubmittedLine = () => {
    submittedLine?.dispose()
    submittedLine = null
  }

  const waitForReturnedPrompt = () => {
    const line = logicalLineAtCursor(term)
    clearSubmittedLine()
    submittedLine = line ? term.registerMarker(line.startOffset) : null
    waitingForPrompt = true
  }

  const closeCurrent = (endOffset = -1) => {
    const cur = blocks[blocks.length - 1]
    if (!cur || cur.end !== null) return
    // 规则 2 的"切换即关闭"：此时活动缓冲区已经是备用区，registerMarker 会落在
    // 备用区上，退出时被销毁导致块永远无终点（染色/折叠会一直长到光标）。
    // 启动备用程序的命令（less/vim/top）在提示符行后没有普通区输出，
    // 直接以它自己的 start marker 收尾——该 marker 在备用区往返后仍然有效。
    if (term.buffer.active.type === 'alternate') {
      cur.end = cur.start
      return
    }
    // 普通区：终点标记在下一块逻辑起点的前一行；缓冲区顶部失败则回退到光标。
    cur.end = term.registerMarker(endOffset) ?? term.registerMarker(0)
  }

  const openNew = (startOffset = 0) => {
    const start = term.registerMarker(startOffset)
    if (!start) return // 无法标记——静默放弃
    const id = nextId++
    const block: CommandBlock = {
      id,
      color: colorForIndex(id),
      start,
      end: null,
    }
    // start marker 被 scrollback 裁剪掉时，块一并移除。
    start.onDispose(() => {
      const i = blocks.indexOf(block)
      if (i >= 0) {
        blocks.splice(i, 1)
        emit()
      }
    })
    blocks.push(block)
    emit()
  }

  const splitAt = (startOffset = 0) => {
    closeCurrent(startOffset - 1)
    openNew(startOffset)
  }

  // 一次性清空所有块。用于硬重置：缓冲区已没了，任何块都不再有意义。
  // 先快照再清空：start.dispose() 触发的 onDispose 会 indexOf/splice blocks，
  // 直接 forEach(blocks) 会跳 index 导致 marker 泄漏。
  const resetAll = () => {
    clearSubmittedLine()
    waitingForPrompt = splitMode === 'prompt'
    if (blocks.length === 0) return
    const snapshot = blocks.slice()
    blocks.length = 0
    for (const b of snapshot) {
      b.start.dispose()
      b.end?.dispose()
    }
    emit()
  }

  // enter 模式按每个 `\r` 切块（含多行粘贴）；prompt 模式仅重置输出侧探测。
  disposables.push(
    term.onData((data: string) => {
      if (term.buffer.active.type === 'alternate') return
      for (const ch of data) {
        if (ch === '\r') {
          if (splitMode === 'enter') splitAt()
          else waitForReturnedPrompt()
        }
      }
    }),
  )

  if (splitMode === 'prompt') {
    disposables.push(
      term.onWriteParsed(() => {
        if (!waitingForPrompt || term.buffer.active.type === 'alternate') return
        const line = logicalLineAtCursor(term)
        if (!line) return
        if (submittedLine && !submittedLine.isDisposed && submittedLine.line === line.startLine) return
        const prompt = detectPrompt(line.text, opts?.extraPromptPatterns)
        if (!prompt || line.text.slice(prompt.end).trim().length > 0) return
        splitAt(line.startOffset)
        waitingForPrompt = false
        clearSubmittedLine()
      }),
    )
  }

  // 规则 2：缓冲区切换。进备用区时关闭当前块；返回普通区不动作。
  disposables.push(
    term.buffer.onBufferChange((buf) => {
      if (buf.type === 'alternate') closeCurrent()
    }),
  )

  // 规则 4：硬重置。RIS（ESC c，来自 `reset` / `tput reset`）清空缓冲区但
  // xterm 不会 dispose 我们的 marker（实测 marker.isDisposed 仍为 false），
  // 所有块会幽灵般悬在已清空的屏幕上。全部丢弃。返回 false 让 xterm 继续
  // 执行重置。普通 `clear`（ED3+ED2）会经 ED2 路径 dispose marker，无需处理。
  disposables.push(
    term.parser.registerEscHandler({ final: 'c' }, () => {
      resetAll()
      return false
    }),
  )

  return {
    get blocks() {
      return blocks
    },
    onChange(fn) {
      listeners.add(fn)
      return { dispose: () => listeners.delete(fn) }
    },
    dispose() {
      disposables.forEach((d) => d.dispose())
      clearSubmittedLine()
      // 用快照迭代：start.dispose() 的 onDispose 会 splice blocks，
      // 直接 forEach(blocks) 会跳过后续 index，导致 marker 泄漏。
      const snapshot = blocks.slice()
      blocks.length = 0
      for (const b of snapshot) {
        b.start.dispose()
        b.end?.dispose()
      }
      listeners.clear()
    },
  }
}
