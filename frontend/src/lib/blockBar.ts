/**
 * 命令块渲染层：左侧色条 + 遮罩式折叠（借鉴 rssh TerminalPane）。
 *
 * 与 rssh 真 splice 私有 buffer 的折叠不同，这里用"遮罩式视觉折叠"：
 *  - 折叠 = 主题化遮罩盖住块的 body 行区域 + 「⋯ 已折叠 N 行」角标，点击展开；
 *  - 自动折叠 = 块结束时 body 超过 blockMaxLines 行，遮住最早部分，保留最新输出；
 *  - 只用 xterm 公开 API（buffer/marker/viewportY），不碰 _core 私有结构，
 *    xterm 升级零风险（rssh 的 folds.ts 锁死 6.0.0 就是前车之鉴）。
 *
 * DOM 结构（挂在 .terminal-instance 内，xterm 本体之上）：
 *   .block-bar-overlay（pointer-events: none）
 *     ├─ svg.block-bar-svg   每块一条左侧色条 + 透明加宽点击热区
 *     └─ .block-mask-layer   折叠遮罩（点击展开）
 *
 * 几何换算：行号 × 行高 → 像素。行高取 .xterm-screen 高度 / rows
 * （DOM/WebGL 渲染器通用，同 rssh row-height.ts），视口偏移取
 * buffer.viewportY，滚动/输出/换行都触发重绘（rAF 合帧）。
 */
import type { Terminal } from '@xterm/xterm'
import type { TerminalSettings } from './useSettings'
import { createCommandBlockTracker } from './commandBlocks'
import type { CommandBlock, CommandBlockTracker } from './commandBlocks'

export interface BlockBarController {
  /** 运行时设置变更（顶栏开关/行数）实时生效。 */
  applySettings(s: TerminalSettings): void
  dispose(): void
}

/** 折叠记录：[from, to] 为被遮住的绝对行号闭区间。 */
interface FoldEntry {
  from: number
  to: number
  auto: boolean
}

// 左侧色条区宽度：与 index.css 中 .terminal-instance 的 padding-left 保持一致
const BAR_ZONE_PX = 14
// 色条视觉宽度；点击热区宽度单独加宽
const BAR_WIDTH = 3
const HIT_WIDTH = 12

export function attachBlockBar(
  term: Terminal,
  container: HTMLElement,
  settings: TerminalSettings,
): BlockBarController {
  let enabled = settings.blockBar
  let autoFoldOn = settings.blockAutoFold
  let maxLines = Math.max(1, settings.blockMaxLines)

  const tracker: CommandBlockTracker = createCommandBlockTracker(term, 'enter')

  // 折叠状态（按 blockId）：
  //   manualUnfolded：用户手动展开过的自动折叠块——不再自动折叠（尊重用户）
  //   manualFolded：用户手动折叠的块——自动折叠策略不覆盖
  const folds = new Map<number, FoldEntry>()
  const manualUnfolded = new Set<number>()
  const manualFolded = new Set<number>()

  // ---- DOM ----
  const overlay = document.createElement('div')
  overlay.className = 'block-bar-overlay'
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.classList.add('block-bar-svg')
  const maskLayer = document.createElement('div')
  maskLayer.className = 'block-mask-layer'
  overlay.appendChild(svg)
  overlay.appendChild(maskLayer)
  container.appendChild(overlay)

  let rafId = 0
  let disposed = false
  const scheduleRedraw = () => {
    if (!disposed && !rafId) {
      rafId = requestAnimationFrame(() => {
        rafId = 0
        redraw()
      })
    }
  }

  // ---- 折叠逻辑 ----

  /** 自动折叠评估：对新关闭的块，body 超过 maxLines 行时遮住最早部分（保留最新 maxLines 行）。 */
  function evaluateAutoFold(blocks: readonly CommandBlock[]) {
    for (const b of blocks) {
      if (!b.end || b.end.isDisposed || b.start.isDisposed) continue
      if (manualUnfolded.has(b.id) || manualFolded.has(b.id) || folds.has(b.id)) continue
      const endLine = b.end.line
      // body = 命令行之后的行数；遮罩必须非空（from <= to）
      if (endLine - b.start.line > maxLines) {
        folds.set(b.id, { from: b.start.line + 1, to: endLine - maxLines, auto: true })
      }
    }
  }

  /** 手动切换折叠（仅已关闭的块——运行中块终点会漂移，遮不住）。 */
  function toggleFold(b: CommandBlock) {
    if (!b.end || b.end.isDisposed || b.start.isDisposed) return
    if (folds.has(b.id)) {
      folds.delete(b.id)
      manualUnfolded.add(b.id)
    } else {
      folds.set(b.id, { from: b.start.line + 1, to: b.end.line, auto: false })
      manualFolded.add(b.id)
    }
    scheduleRedraw()
  }

  /** 展开全部（resize 时行号会因 reflow 漂移，与 rssh 同策略先全展开）。 */
  function unfoldAll() {
    if (folds.size === 0) return
    folds.clear()
    manualFolded.clear()
    scheduleRedraw()
  }

  // ---- 几何与渲染 ----

  let lastSig = ''
  function redraw() {
    if (disposed) return
    const active = term.buffer.active
    // 备用缓冲区（vim/top/less）：行号坐标系完全不同，直接隐藏
    if (!enabled || active.type !== 'normal') {
      overlay.style.display = 'none'
      lastSig = ''
      return
    }
    // 行高测量：rssh 用 .xterm-screen 高度 / rows，但那是 xterm 6.0 的结构；
    // 5.3 的 DOM 渲染器里 .xterm-rows 绝对定位导致 .xterm-screen 盒高为 0，
    // 因此优先实测 .xterm-rows（渲染器会内联设置其高度），.xterm-screen 作退路
    const rowsEl = term.element?.querySelector('.xterm-rows') as HTMLElement | null
    const screen = term.element?.querySelector('.xterm-screen') as HTMLElement | null
    if (!rowsEl && !screen) return
    const rowsRect = rowsEl?.getBoundingClientRect()
    const screenRect = screen?.getBoundingClientRect()
    const originRect = rowsRect && rowsRect.height > 0 ? rowsRect : (screenRect && screenRect.height > 0 ? screenRect : null)
    let rowHeight = 0
    if (rowsRect && rowsRect.height > 0) rowHeight = rowsRect.height / term.rows
    else if (screenRect && screenRect.height > 0) rowHeight = screenRect.height / term.rows
    if (!originRect || rowHeight <= 0 || term.rows <= 0) {
      overlay.style.display = 'none'
      return
    }
    overlay.style.display = 'block'

    if (autoFoldOn) evaluateAutoFold(tracker.blocks)

    const viewportY = active.viewportY
    const cursorAbs = active.baseY + active.cursorY
    const visBot = viewportY + term.rows - 1
    const containerRect = container.getBoundingClientRect()
    const originTop = originRect.top - containerRect.top
    const originLeft = originRect.left - containerRect.left
    // 遮罩宽度：行区宽度（.xterm-rows 内联宽度不可靠时退到 screen 宽）
    const textWidth = (rowsRect && rowsRect.width > 0 ? rowsRect.width : screenRect?.width) ?? 0

    const bars: string[] = []
    const masks: string[] = []
    let sig = `${viewportY}|${term.rows}|`
    for (const b of tracker.blocks) {
      if (b.start.isDisposed) continue
      const endLine = b.end && !b.end.isDisposed ? b.end.line : cursorAbs
      if (endLine < b.start.line) continue
      // 色条覆盖整块可见范围（折叠态画虚线感——透明度减半）
      const t = Math.max(b.start.line, viewportY)
      const btm = Math.min(endLine, visBot)
      const folded = folds.has(b.id)
      if (btm >= t) {
        const top = originTop + (t - viewportY) * rowHeight
        const h = (btm - t + 1) * rowHeight
        bars.push(
          `<rect class="block-hit" data-block="${b.id}" x="0" y="${top.toFixed(1)}" width="${HIT_WIDTH}" height="${h.toFixed(1)}" fill="transparent"></rect>` +
          `<rect class="block-bar${folded ? ' folded' : ''}" x="${((BAR_ZONE_PX - BAR_WIDTH) / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${BAR_WIDTH}" height="${h.toFixed(1)}" rx="1.5" fill="${b.color}"></rect>`,
        )
        sig += `${b.id}:${t}-${btm}${folded ? 'F' : ''};`
      }
      // 折叠遮罩：只画可见部分
      const f = folds.get(b.id)
      if (f) {
        const mt = Math.max(f.from, viewportY)
        const mb = Math.min(f.to, visBot)
        if (mb >= mt) {
          const top = originTop + (mt - viewportY) * rowHeight
          const h = (mb - mt + 1) * rowHeight
          const count = f.to - f.from + 1
          masks.push(
            `<div class="block-fold-mask" data-block="${b.id}" style="left:${originLeft.toFixed(1)}px;top:${top.toFixed(1)}px;width:${textWidth.toFixed(1)}px;height:${h.toFixed(1)}px;--block-color:${b.color}">` +
            `<span class="block-fold-label">⋯ 已折叠 ${count} 行，点击展开</span></div>`,
          )
          sig += `M${b.id}:${mt}-${mb};`
        }
      }
    }
    if (sig === lastSig) return
    lastSig = sig
    svg.innerHTML = bars.join('')
    maskLayer.innerHTML = masks.join('')
  }

  // ---- 交互 ----

  function copyBlockText(b: CommandBlock) {
    if (b.start.isDisposed) return
    const buf = term.buffer.active
    const cursorAbs = buf.baseY + buf.cursorY
    const endLine = b.end && !b.end.isDisposed ? b.end.line : cursorAbs
    // 逻辑行提取：软换行合并（isWrapped 追加到上一行），行尾 trim（同 rssh block-content）
    const lines: string[] = []
    for (let y = b.start.line; y <= endLine; y++) {
      const line = buf.getLine(y)
      if (!line) continue
      const text = line.translateToString(true)
      if (line.isWrapped && lines.length > 0) lines[lines.length - 1] += text
      else lines.push(text)
    }
    const text = lines.join('\n')
    if (!text) return
    navigator.clipboard.writeText(text).catch(() => {})
  }

  const onClick = (ev: MouseEvent) => {
    const el = (ev.target as Element).closest('[data-block]') as HTMLElement | null
    if (!el) return
    const id = Number(el.getAttribute('data-block'))
    if (el.classList.contains('block-fold-mask')) {
      // 点遮罩 → 展开
      folds.delete(id)
      manualUnfolded.add(id)
      scheduleRedraw()
      return
    }
    const b = tracker.blocks.find((x) => x.id === id)
    if (b) toggleFold(b)
  }
  // 双击色条复制块内容（双击前会触发两次 click，折叠状态翻转两次复原，无副作用）
  const onDblClick = (ev: MouseEvent) => {
    const el = (ev.target as Element).closest('rect.block-hit') as SVGRectElement | null
    if (!el) return
    const id = Number(el.getAttribute('data-block'))
    const b = tracker.blocks.find((x) => x.id === id)
    if (b) copyBlockText(b)
  }
  overlay.addEventListener('click', onClick)
  overlay.addEventListener('dblclick', onDblClick)

  // ---- 事件订阅 ----
  const disposables: { dispose(): void }[] = [
    tracker.onChange(scheduleRedraw),
    term.onScroll(scheduleRedraw),
    term.onWriteParsed(scheduleRedraw),
    // 尺寸变化（字体/拖拽/fit）：reflow 会漂移行号，先全部展开再重绘
    term.onResize(() => {
      unfoldAll()
      scheduleRedraw()
    }),
    term.buffer.onBufferChange(scheduleRedraw),
  ]
  scheduleRedraw()

  return {
    applySettings(s: TerminalSettings) {
      enabled = s.blockBar
      autoFoldOn = s.blockAutoFold
      maxLines = Math.max(1, s.blockMaxLines)
      // 阈值/开关变化：清掉旧自动折叠——重绘时按新阈值重评（manualUnfolded 保留，
      // 用户手动展开过的块仍不自动折叠）；关闭自动折叠后旧折叠保持解除
      for (const [id, f] of Array.from(folds)) {
        if (f.auto) folds.delete(id)
      }
      scheduleRedraw()
    },
    dispose() {
      disposed = true
      if (rafId) cancelAnimationFrame(rafId)
      rafId = 0
      overlay.removeEventListener('click', onClick)
      overlay.removeEventListener('dblclick', onDblClick)
      disposables.forEach((d) => d.dispose())
      tracker.dispose()
      overlay.remove()
    },
  }
}
