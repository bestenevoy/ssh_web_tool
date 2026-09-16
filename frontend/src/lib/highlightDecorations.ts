// 关键字高亮装饰层（移植自 rssh src/lib/terminal/highlight-decorations.ts）
// 匹配/布局的纯函数在 highlight.ts；本文件负责 xterm 装饰的注册与增量刷新。
//
// 为什么用装饰层而不是改写字节流（rssh issue #114 的教训）：旧做法在 write() 前
// 往 PTY 输出里注入 ANSI 颜色码，会复位程序自身的 SGR 状态、撕裂被拆分到多个
// chunk 的 OSC 序列。xterm 已把字节流解析成带样式的 cell 网格，高亮应该叠加在
// 网格之上，而不是回到原始字节里。
//
// 装饰是持久（PERSISTENT）的且用 marker 锚定：一条被装饰过的线滚动时高亮跟随
// （marker 追踪），我们只重画内容真正变化的线。滚动back被裁剪时绝对行号会平移，
// marker（而非行号）才是稳定身份：每轮读取 marker.line，并剪除已被裁剪 disposal
// 的条目。
//
// 范围：只高亮 normal buffer。TUI 程序（vim/htop 等）跑在 alternate buffer 且
// 频繁整屏重绘——装饰它们既昂贵又正是旧方案破坏输出的重灾区。
//
// 触发器用 onWriteParsed/onScroll/onResize/onBufferChange（不能用 onRender）：
// 注册装饰本身会触发 render，用 onRender 会自我反馈形成死循环。这四个钩子只在
// 内容/位置真实变化时触发。resize 与 buffer 切换会重排几何，需要强制全量重建。

import type { IBufferLine, IDecoration, IDisposable, IMarker, Terminal } from '@xterm/xterm'
import { planLine, type CompiledHighlightRule, type LineCells, type LineDecoration } from './highlight'

/**
 * 读取一条 buffer 行并归约为 LineCells：可见文本 + 字符偏移 → cell 列映射。
 *
 * 两种情况导致字符偏移 ≠ cell 列，都在这里处理：
 *   - 宽字符（CJK）占 2 列；xterm 在其后存一个宽度 0 的 spacer cell，跳过它——
 *     下一字形的列号已经算入了 spacer
 *   - 从未写入过的行尾 cell（内容为 ''）是填充而非输出，修剪掉；
 *     否则 `.*`/`\s` 类规则会把空白死区也涂上颜色
 */
export function readLineCells(line: Pick<IBufferLine, 'getCell' | 'length'>): LineCells {
  let text = ''
  const cellAt: number[] = []
  let writtenLen = 0 // 截至最后一个已写入字形的文本长度
  let writtenEndCol = 0 // 该字形右侧的 cell 列
  for (let x = 0; x < line.length; x++) {
    const cell = line.getCell(x)
    if (!cell) break
    const w = cell.getWidth()
    if (w === 0) continue // 宽字形后面的 spacer 列
    const chars = cell.getChars()
    // 一个字形可占多个 UTF-16 单元（emoji/星面字符/组合记号），而 findMatches
    // 以 UTF-16 偏移为索引——每个单元都要压入一个指向该字形列的 cellAt，
    // 否则 text 与 cellAt 会错位
    const glyph = chars === '' ? ' ' : chars // 行内未写入的中间 cell → 空格
    for (let k = 0; k < glyph.length; k++) cellAt.push(x)
    text += glyph
    if (chars !== '') {
      writtenLen = text.length
      writtenEndCol = x + w
    }
  }
  text = text.slice(0, writtenLen)
  cellAt.length = writtenLen
  cellAt.push(writtenEndCol)
  return { text, cellAt }
}

/** 一条可见行归约出的高亮方案及其签名（用于增量 diff） */
export interface VisibleLine {
  line: number
  sig: string
  plan: LineDecoration[]
}

export interface ReconcilePlan {
  disposeLines: number[]
  createLines: VisibleLine[]
}

/**
 * 把可见行的期望高亮与已装饰状态（行号 → 当前签名）做差，得到
 * 保留/重建/销毁/新建决策。纯函数，脱离 xterm 可单测。
 *
 *   - 签名相同        → 保留（无 DOM 抖动——增量刷新的意义所在）
 *   - 签名不同        → 销毁旧装饰，若仍有匹配则新建
 *   - 匹配消失（空方案）→ 销毁旧装饰
 *   - 新出现匹配      → 新建
 */
export function reconcile(visible: VisibleLine[], existing: Map<number, string>): ReconcilePlan {
  const disposeLines: number[] = []
  const createLines: VisibleLine[] = []
  for (const v of visible) {
    const cur = existing.get(v.line)
    if (cur !== undefined && cur === v.sig) continue
    if (cur !== undefined) disposeLines.push(v.line)
    if (v.plan.length) createLines.push(v)
  }
  return { disposeLines, createLines }
}

interface LineEntry {
  marker: IMarker
  sig: string
  items: IDecoration[]
}

function sigOf(plan: LineDecoration[]): string {
  return plan.map((d) => `${d.x}:${d.width}:${d.color}`).join(',')
}

/**
 * 关键字实时高亮装饰层。
 *
 * 装饰为持久装饰并以 marker 锚定；滚动back裁剪后 marker.line 变 -1 或
 * isDisposed=true，据此剪除条目。只规划可视视口 + 签名 diff，滚动/输出时
 * 只触碰变化的行。resize / buffer 切换 / 规则变更强制全量重建。
 */
export class HighlightDecorator {
  private compiled: CompiledHighlightRule[] = []
  private entries: LineEntry[] = [] // 每条当前被装饰的行一个条目
  private disposables: IDisposable[] = []
  private scheduled = false
  private fullRebuild = true
  private disposed = false
  private readonly term: Terminal

  constructor(term: Terminal) {
    this.term = term
    const onContent = () => this.schedule(false)
    const onReflow = () => this.schedule(true)
    this.disposables.push(
      term.onWriteParsed(onContent),
      term.onScroll(onContent),
      term.onResize(onReflow),
      term.buffer.onBufferChange(onReflow),
    )
  }

  /** 替换生效的规则集并全量重画 */
  setRules(compiled: CompiledHighlightRule[]): void {
    this.compiled = compiled
    this.schedule(true)
  }

  /** 把触发器的突发合并成每动画帧一次重画 */
  private schedule(full: boolean): void {
    if (full) this.fullRebuild = true
    if (this.scheduled || this.disposed) return
    this.scheduled = true
    requestAnimationFrame(() => {
      this.scheduled = false
      if (!this.disposed) this.repaint() // 标签页在帧触发前被关闭的情况
    })
  }

  private clearAll(): void {
    for (const e of this.entries) {
      for (const d of e.items) d.dispose()
      e.marker.dispose()
    }
    this.entries.length = 0
  }

  private repaint(): void {
    // 重排（resize / buffer 切换 / 规则变更）会使 cell 列失效：全部丢弃重建
    if (this.fullRebuild) {
      this.clearAll()
      this.fullRebuild = false
    }
    const buf = this.term.buffer.active
    if (buf.type === 'alternate' || !this.compiled.length) {
      this.clearAll()
      return
    }

    // 以"当前行号"为键索引存活条目；剪除 marker 已被 disposal（行被滚动back
    // 裁剪掉）的条目
    const byLine = new Map<number, LineEntry>()
    const live: LineEntry[] = []
    for (const e of this.entries) {
      if (e.marker.isDisposed || e.marker.line === -1) {
        for (const d of e.items) d.dispose()
        continue
      }
      byLine.set(e.marker.line, e)
      live.push(e)
    }

    // 规划可视视口，再与已有装饰做差
    const visStart = buf.viewportY
    const visEnd = buf.viewportY + this.term.rows
    const visible: VisibleLine[] = []
    for (let absLine = visStart; absLine < visEnd; absLine++) {
      const line = buf.getLine(absLine)
      if (!line) continue
      const plan = planLine(readLineCells(line), this.compiled)
      visible.push({ line: absLine, sig: sigOf(plan), plan })
    }
    const { disposeLines, createLines } = reconcile(visible, new Map(Array.from(byLine, ([l, e]) => [l, e.sig])))

    const removed = new Set<LineEntry>()
    for (const l of disposeLines) {
      const e = byLine.get(l)
      if (!e) continue
      for (const d of e.items) d.dispose()
      e.marker.dispose()
      removed.add(e)
    }

    const cursorAbs = buf.baseY + buf.cursorY
    const created: LineEntry[] = []
    for (const v of createLines) {
      const entry = this.createEntry(v.line - cursorAbs, v.plan, v.sig)
      if (entry) created.push(entry)
    }

    this.entries = live.filter((e) => !removed.has(e)).concat(created)
  }

  /** 为一条线注册 marker，并按方案注册每条匹配的装饰 */
  private createEntry(offset: number, plan: LineDecoration[], sig: string): LineEntry | null {
    const marker = this.term.registerMarker(offset)
    if (!marker || marker.line === -1) return null
    const items: IDecoration[] = []
    for (const d of plan) {
      const dec = this.term.registerDecoration({
        marker,
        x: d.x,
        width: d.width,
        foregroundColor: d.color,
        layer: 'top',
      })
      if (dec) items.push(dec)
    }
    if (!items.length) {
      marker.dispose()
      return null
    }
    return { marker, sig, items }
  }

  dispose(): void {
    this.disposed = true
    this.clearAll()
    for (const d of this.disposables) d.dispose()
    this.disposables.length = 0
  }
}
