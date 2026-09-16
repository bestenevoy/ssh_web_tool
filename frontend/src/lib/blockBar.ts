/**
 * 命令块渲染层：左侧色条 + 真折叠（rssh FoldStore 移植版）。
 *
 * 折叠本体在 folds.ts（FoldStore）：直接 splice xterm buffer 抽出 body 行，
 * 折叠后 buffer 实际行数减少（滚动条变短）；本文件只负责渲染与交互——
 *   - 色条：每块一条左侧色条，折叠态降透明度；
 *   - 徽标：折叠块在提示符行上叠加「⋯ 已折叠 N 行，点击展开」；
 *   - 交互：点色条折叠/展开、点徽标展开、双击色条复制块内容
 *     （折叠块复制走 fold.savedLines，见 copyBlockText）。
 *
 * 不变量与私有 API 全部收口在 folds.ts（本文件零私有 API，只用
 * buffer/marker/viewportY 公开接口），xterm 升级只需复核 folds.ts。
 *
 * DOM 结构（挂在 .terminal-instance 内，xterm 本体之上）：
 *   .block-bar-overlay（pointer-events: none，徽标单独放开）
 *     └─ svg.block-bar-svg   每块一条左侧色条 + 透明加宽点击热区
 *
 * 几何换算：行号 × 行高 → 像素。行高取 .xterm-rows 实测高 / rows，
 * 视口偏移取 buffer.viewportY，滚动/输出/换行都触发重绘（rAF 合帧）。
 */
import type { Terminal } from '@xterm/xterm'
import type { TerminalSettings } from './useSettings'
import { createCommandBlockTracker } from './commandBlocks'
import type { CommandBlock, CommandBlockTracker } from './commandBlocks'
import { createFoldStore } from './folds'
import type { FoldStore } from './folds'
import { compilePromptPatterns } from './promptPatterns'
import { TERMINAL_SCROLLBACK_LINES } from './terminalInstance'

export interface BlockBarController {
  /** 运行时设置变更（顶栏开关/行数）实时生效。 */
  applySettings(s: TerminalSettings): void
  /** xterm 几何变化（fit）前展开全部——savedLines 是旧列宽快照，必须先还原。 */
  unfoldAll(): void
  /** fit 完成后按当前阈值重新应用自动折叠并重绘（与 unfoldAll 配对）。 */
  afterFit(): void
  /** term.reset() 后调用：经 tracker.onReset 链丢弃全部折叠记录（savedLines 已 stale，禁止塞回）。 */
  handleTerminalReset(): void
  /** 搜索打开期间暂停自动折叠（保证 unfoldAll 后内容不被再次折起）。 */
  setSearchActive(active: boolean): void
  dispose(): void
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
  let searchActive = false

  // tracker：切块方式/自定义正则变化时热重建（旧块随之作废重置）。
  // onReset 经闭包引用 foldStore（let 变量），重建后自动指向新实例。
  const trackerSigOf = (s: TerminalSettings) => `${s.blockSplitMode}|${s.customPromptPatterns.join('\n')}`
  let foldStore: FoldStore | null = null
  let tracker: CommandBlockTracker = createCommandBlockTracker(term, settings.blockSplitMode, {
    extraPromptPatterns: compilePromptPatterns(settings.customPromptPatterns),
    onReset: () => foldStore?.discardAll(),
  })
  let trackerSig = trackerSigOf(settings)

  // tracker 的 onChange 订阅独立管理（重建时旧的单独 dispose，不进共享 disposables）
  let trackerChangeSub: { dispose(): void } | null = null
  // foldStore 的 onChange 订阅同理（重建时更换）
  let foldStoreChangeSub: { dispose(): void } | null = null

  /** 按 maxLines 等当前设置创建 FoldStore（tracker 之后——订阅顺序：tracker 先、fold 后）。 */
  function createFoldStoreFor() {
    foldStore = createFoldStore(term, tracker, {
      maxVisibleLines: maxLines,
      // 缓存预算保证 visible + cached < scrollback（5000 − blockMaxLines − 1）
      maxCachedLines: TERMINAL_SCROLLBACK_LINES - maxLines - 1,
      // 色条关闭/自动折叠关闭/搜索打开时都不折叠
      shouldAutoFold: () => enabled && autoFoldOn && !searchActive,
    })
    foldStoreChangeSub = foldStore.onChange(scheduleRedraw)
  }

  /** 仅重建 FoldStore（blockMaxLines 变化）：tracker 与块保留，id/颜色不丢。 */
  function rebuildFoldStore() {
    foldStore?.unfoldAll()
    foldStore?.dispose()
    foldStoreChangeSub?.dispose()
    createFoldStoreFor()
  }

  /** 切块方式/自定义正则变化时双重建：unfoldAll → foldStore.dispose → tracker.dispose → 再建。 */
  function rebuildTrackerIfNeeded(s: TerminalSettings) {
    const sig = trackerSigOf(s)
    if (sig === trackerSig) return
    trackerSig = sig
    foldStore?.unfoldAll()
    foldStore?.dispose()
    foldStoreChangeSub?.dispose()
    tracker.dispose()
    trackerChangeSub?.dispose()
    tracker = createCommandBlockTracker(term, s.blockSplitMode, {
      extraPromptPatterns: compilePromptPatterns(s.customPromptPatterns),
      onReset: () => foldStore?.discardAll(),
    })
    trackerChangeSub = tracker.onChange(scheduleRedraw)
    createFoldStoreFor() // 新 tracker 无块，fold 从零开始
    lastSig = '' // 新 tracker 无块，sig 可能与旧值相同，强制重绘
    scheduleRedraw()
  }

  /** 自动折叠关闭时解除全部自动（prefix）折叠；手动（full）折叠保留。 */
  function unfoldPrefixFolds() {
    if (!foldStore) return
    for (const f of foldStore.folds) {
      if (f.kind === 'prefix') foldStore.unfold(f.blockId)
    }
  }

  // ---- DOM ----
  const overlay = document.createElement('div')
  overlay.className = 'block-bar-overlay'
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.classList.add('block-bar-svg')
  overlay.appendChild(svg)
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
  trackerChangeSub = tracker.onChange(scheduleRedraw) // 初始订阅（重建时由 rebuildTrackerIfNeeded 更换）
  createFoldStoreFor()

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
    // 行高测量：优先实测 .xterm-rows（渲染器会内联设置其高度），
    // .xterm-screen 作退路（部分渲染器盒高为 0）
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

    const viewportY = active.viewportY
    const cursorAbs = active.baseY + active.cursorY
    const visBot = viewportY + term.rows - 1
    const containerRect = container.getBoundingClientRect()
    const originTop = originRect.top - containerRect.top
    const originLeft = originRect.left - containerRect.left

    const bars: string[] = []
    const badges: string[] = []
    let sig = `${viewportY}|${term.rows}|`
    for (const b of tracker.blocks) {
      if (b.start.isDisposed) continue
      const f = foldStore?.getFold(b.id)
      // 折叠态终点：full 折叠抽走了 body（end marker 已 dispose），块只剩提示符行；
      // prefix 折叠的 end marker 随 splice 自动迁移，仍指向最新保留行
      let endLine = b.end && !b.end.isDisposed ? b.end.line : cursorAbs
      if (f && f.kind === 'full') endLine = b.start.line
      if (endLine < b.start.line) continue
      // 色条覆盖可见范围（折叠态画虚线感——透明度减半）
      const t = Math.max(b.start.line, viewportY)
      const btm = Math.min(endLine, visBot)
      const folded = !!f
      if (btm >= t) {
        const top = originTop + (t - viewportY) * rowHeight
        const h = (btm - t + 1) * rowHeight
        bars.push(
          `<rect class="block-hit" data-block="${b.id}" x="0" y="${top.toFixed(1)}" width="${HIT_WIDTH}" height="${h.toFixed(1)}" fill="transparent"></rect>` +
          `<rect class="block-bar${folded ? ' folded' : ''}" x="${((BAR_ZONE_PX - BAR_WIDTH) / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${BAR_WIDTH}" height="${h.toFixed(1)}" rx="1.5" fill="${b.color}"></rect>`,
        )
        sig += `${b.id}:${t}-${btm}${folded ? 'F' : ''};`
      }
      // 折叠徽标：定位在提示符行，仅可见时绘制
      if (f && b.start.line >= viewportY && b.start.line <= visBot) {
        const top = originTop + (b.start.line - viewportY) * rowHeight
        badges.push(
          `<div class="block-fold-badge" data-block="${b.id}" style="left:${originLeft.toFixed(1)}px;top:${top.toFixed(1)}px;--block-color:${b.color}">⋯ 已折叠 ${f.count} 行，点击展开</div>`,
        )
        sig += `B${b.id}:${b.start.line};`
      }
    }
    if (sig === lastSig) return
    lastSig = sig
    svg.innerHTML = bars.join('')
    // 徽标画在 svg 之上：挂到 overlay 直接子级（每次重建）
    overlay.querySelectorAll('.block-fold-badge').forEach((el) => el.remove())
    overlay.insertAdjacentHTML('beforeend', badges.join(''))
  }

  // ---- 交互 ----

  function copyBlockText(b: CommandBlock) {
    if (b.start.isDisposed) return
    const f = foldStore?.getFold(b.id)
    const lines: string[] = []
    // BufferLine → 文本（行尾 trim；isWrapped 软换行合并到上一行）
    const pushLine = (line: unknown) => {
      const bl = line as { translateToString?: (trimRight: boolean) => string; isWrapped?: boolean } | null
      if (!bl || typeof bl.translateToString !== 'function') return
      const text = bl.translateToString(true)
      if (bl.isWrapped && lines.length > 0) lines[lines.length - 1] += text
      else lines.push(text)
    }
    if (f) {
      // 折叠块：prompt 行仍在 buffer，body 来自 savedLines（rssh block-content 同款）。
      // prefix 折叠只复制已缓存前缀 + prompt（与 rssh 行为一致）。
      pushLine(term.buffer.active.getLine(b.start.line))
      f.savedLines.forEach(pushLine)
    } else {
      const buf = term.buffer.active
      const cursorAbs = buf.baseY + buf.cursorY
      const endLine = b.end && !b.end.isDisposed ? b.end.line : cursorAbs
      for (let y = b.start.line; y <= endLine; y++) pushLine(buf.getLine(y))
    }
    const text = lines.join('\n')
    if (!text) return
    navigator.clipboard.writeText(text).catch(() => {})
  }

  const onClick = (ev: MouseEvent) => {
    const el = (ev.target as Element).closest('[data-block]') as HTMLElement | null
    if (!el) return
    const id = Number(el.getAttribute('data-block'))
    if (el.classList.contains('block-fold-badge')) {
      // 点徽标 → 展开
      foldStore?.unfold(id)
      return
    }
    const b = tracker.blocks.find((x) => x.id === id)
    if (!b) return
    // 点色条 → 切换折叠（仅已关闭块可折叠——运行中块终点会漂移，抽不住）
    if (foldStore?.isFolded(b.id)) {
      foldStore.unfold(b.id)
    } else if (b.end && !b.end.isDisposed && !b.start.isDisposed) {
      foldStore?.fold(b.id)
    }
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
    // tracker.onChange / foldStore.onChange 不在这里订阅：由各自专属 sub 独立管理（支持重建时更换）
    term.onScroll(scheduleRedraw),
    term.onWriteParsed(scheduleRedraw),
    // 尺寸变化（字体/拖拽/fit）：reflow 会漂移行号，重绘即可——
    // unfold 必须发生在 fit 之前（savedLines 是旧列宽快照），由 useTerminals.fitTerminal 负责
    term.onResize(scheduleRedraw),
    term.buffer.onBufferChange(() => {
      // 输出增长后按阈值重新应用自动折叠（内部自检 normal 缓冲区）
      foldStore?.enforceAutoFold()
      scheduleRedraw()
    }),
  ]
  scheduleRedraw()

  return {
    applySettings(s: TerminalSettings) {
      const prevMaxLines = maxLines
      const prevAutoFoldOn = autoFoldOn
      enabled = s.blockBar
      autoFoldOn = s.blockAutoFold
      maxLines = Math.max(1, s.blockMaxLines)
      // 切块方式/自定义正则变化：双重建（内部已 unfoldAll）
      rebuildTrackerIfNeeded(s)
      // 阈值变化：unfoldAll → 仅重建 foldStore（tracker 与块保留）
      if (maxLines !== prevMaxLines) rebuildFoldStore()
      // 自动折叠关闭：解除全部自动（prefix）折叠；手动（full）保留
      if (prevAutoFoldOn && !autoFoldOn) unfoldPrefixFolds()
      scheduleRedraw()
    },
    unfoldAll() {
      foldStore?.unfoldAll()
      scheduleRedraw()
    },
    afterFit() {
      foldStore?.enforceAutoFold()
      scheduleRedraw()
    },
    handleTerminalReset() {
      // tracker.resetAll → onReset 链 → foldStore.discardAll（单一通知源，避免重复 emit）
      tracker.resetAll()
      scheduleRedraw()
    },
    setSearchActive(active: boolean) {
      searchActive = active
    },
    dispose() {
      disposed = true
      if (rafId) cancelAnimationFrame(rafId)
      rafId = 0
      overlay.removeEventListener('click', onClick)
      overlay.removeEventListener('dblclick', onDblClick)
      disposables.forEach((d) => d.dispose())
      foldStoreChangeSub?.dispose()
      foldStore?.dispose()
      trackerChangeSub?.dispose()
      tracker.dispose()
      overlay.remove()
    },
  }
}
