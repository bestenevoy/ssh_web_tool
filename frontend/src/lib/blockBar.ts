/**
 * 命令块渲染层：左侧色条 + 真折叠（rssh FoldStore 移植版）。
 *
 * 折叠本体在 folds.ts（FoldStore）：直接 splice xterm buffer 抽出 body 行，
 * 折叠后 buffer 实际行数减少（滚动条变短）；本文件只负责渲染与交互——
 *   - 色条：每块一条左侧色条，折叠态虚线描边（rssh 同款）、选中态加描边光环；
 *   - 徽标：折叠块在命令行右端叠加行末角标「⋯ 已折叠 N 行」（rssh 同款）；
 *   - 交互：单击色条选中块（rssh Finder 风格：Shift 范围 / Ctrl 切换）、
 *     点徽标展开、双击色条切换折叠/展开（复制走单击选中 + Ctrl+C 或右键菜单）、
 *     折叠/展开也可走终端右键菜单（hitTest + toggleFold 供 App 层菜单使用）。
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
import { writeClipboardText } from './terminalCopy'
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
  /** clear/cls 后暂停自动折叠直到用户下次输入：历史展开进 scrollback 后可滚动回看。 */
  suppressAutoFoldUntilInput(): void
  /** 命中测试：屏幕坐标 → 左侧色条区命中的块 id（未命中/色条关闭返回 null）。 */
  hitTest(clientX: number, clientY: number): number | null
  /** 选中块 id 快照（rssh Finder 风格多选）。 */
  getSelection(): number[]
  /** 排他选中单块并顺带选中文本（单击语义，让 Ctrl+C 直接复制单块输出）。 */
  selectBlock(id: number): void
  clearSelection(): void
  isFolded(id: number): boolean
  /** 折叠/展开单块（右键菜单用）：已折叠展开，未折叠且已关闭块则折叠。 */
  toggleFold(id: number): void
  /** 复制一个或多个块内容（多块按块序拼接，块间空行分隔）。 */
  copyBlocks(ids: number[]): void
  /** 程序化提交通知（快捷指令/.zs 播放注入命令时调用）：与用户敲 Enter 同语义。
   *  多行注入传行数 lines（见 commandBlocks.ts notifySubmit）。 */
  notifySubmit(lines?: number): void
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
  // clear/cls 后暂停自动折叠：历史展开进 scrollback 可滚动回看，直到用户下次输入
  let suppressUntilInput = false

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

  // ---- 色块选中（rssh TerminalPane 同款 Finder 风格多选）----
  //   单击 → 排他选中 + 顺带选中文本；Shift+click → 锚点..目标范围（锚点不动）；
  //   Ctrl/Cmd+click → toggle（锚点移到此块）；点 bar 外 → 清空；Esc → 清空。
  const selectedIds = new Set<number>()
  let selectionAnchorId: number | null = null

  /** 块当前可见终点行（full 折叠抽走 body 后只剩提示符行；运行中块随光标增长）。 */
  function endLineOf(b: CommandBlock): number {
    const f = foldStore?.getFold(b.id)
    if (f && f.kind === 'full') return b.start.line
    const cursorAbs = term.buffer.active.baseY + term.buffer.active.cursorY
    return b.end && !b.end.isDisposed ? b.end.line : cursorAbs
  }

  function clearSelection() {
    if (selectedIds.size > 0) scheduleRedraw()
    selectedIds.clear()
    selectionAnchorId = null
  }

  function singleSelect(id: number) {
    selectedIds.clear()
    selectedIds.add(id)
    selectionAnchorId = id
    scheduleRedraw()
    // 顺带选中文本——单块复制是最高频场景，Ctrl+C 直接走通
    const b = tracker.blocks.find((x) => x.id === id)
    if (b && !b.start.isDisposed) {
      try { term.selectLines(b.start.line, endLineOf(b)) } catch { /* 备用缓冲区等场景忽略 */ }
    }
  }

  function toggleSelect(id: number) {
    if (selectedIds.has(id)) selectedIds.delete(id)
    else selectedIds.add(id)
    selectionAnchorId = id
    scheduleRedraw()
  }

  function rangeSelectTo(id: number) {
    // 没 anchor 时退化为单击——Finder 同款行为
    if (selectionAnchorId === null) {
      singleSelect(id)
      return
    }
    const lo = Math.min(selectionAnchorId, id)
    const hi = Math.max(selectionAnchorId, id)
    selectedIds.clear()
    for (const b of tracker.blocks) {
      if (b.id >= lo && b.id <= hi) selectedIds.add(b.id)
    }
    // anchor 不动：shift 是"扩展"，不重置锚点
    scheduleRedraw()
  }

  /** 块被 scrollback GC 后从选中集合剪枝（新块复用旧 id 会"鬼选中"） */
  function pruneSelection() {
    if (selectedIds.size === 0) return
    const alive = new Set(tracker.blocks.map((b) => b.id))
    for (const id of selectedIds) {
      if (!alive.has(id)) selectedIds.delete(id)
    }
    if (selectionAnchorId !== null && !alive.has(selectionAnchorId)) selectionAnchorId = null
  }

  /** tracker 变化统一回调：GC 剪枝选中集合 + 重绘（重建 tracker 后同样接线） */
  function onTrackerChanged() {
    pruneSelection()
    scheduleRedraw()
  }

  // 点 bar 外（且不在右键菜单内）清空选中；Esc 清空但不拦截（Esc 仍要送到 shell）
  const onWindowMouseDown = (ev: MouseEvent) => {
    if (selectedIds.size === 0) return
    const t = ev.target as Element | null
    if (!t) return
    if (t.closest('.block-bar-overlay') || t.closest('.ctx-menu')) return
    clearSelection()
  }
  const onWindowKeyDown = (ev: KeyboardEvent) => {
    if (ev.key === 'Escape' && selectedIds.size > 0) clearSelection()
  }
  window.addEventListener('mousedown', onWindowMouseDown)
  window.addEventListener('keydown', onWindowKeyDown)

  /** 按 maxLines 等当前设置创建 FoldStore（tracker 之后——订阅顺序：tracker 先、fold 后）。 */
  function createFoldStoreFor() {
    foldStore = createFoldStore(term, tracker, {
      maxVisibleLines: maxLines,
      // 缓存预算保证 visible + cached < scrollback（5000 − blockMaxLines − 1）
      maxCachedLines: TERMINAL_SCROLLBACK_LINES - maxLines - 1,
      // 色条关闭/自动折叠关闭/搜索打开/clear 后暂停折叠时都不折叠
      shouldAutoFold: () => enabled && autoFoldOn && !searchActive && !suppressUntilInput,
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
    trackerChangeSub = tracker.onChange(onTrackerChanged)
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
  trackerChangeSub = tracker.onChange(onTrackerChanged) // 初始订阅（重建时由 rebuildTrackerIfNeeded 更换）
  createFoldStoreFor()

  // ---- 几何与渲染 ----

  interface BarGeometry {
    rowHeight: number
    originTop: number
    viewportY: number
    visBot: number
  }

  /** 几何测量（redraw 与 hitTest 共用）：行高/容器内原点/视口范围；不可测返回 null */
  function measureGeometry(): BarGeometry | null {
    const active = term.buffer.active
    // 备用缓冲区（vim/top/less）：行号坐标系完全不同，无法换算
    if (active.type !== 'normal') return null
    // 行高/原点测量：rssh 同款用 .xterm-screen（rows × cellHeight，canvas/DOM 渲染器都准）；
    // .xterm-rows 只在 DOM 渲染器下可靠（xterm 6 canvas 渲染器下测量有偏差导致色条错位），仅作退路
    const screen = term.element?.querySelector('.xterm-screen') as HTMLElement | null
    const rowsEl = term.element?.querySelector('.xterm-rows') as HTMLElement | null
    if (!screen && !rowsEl) return null
    const screenRect = screen?.getBoundingClientRect()
    const rowsRect = rowsEl?.getBoundingClientRect()
    const originRect = screenRect && screenRect.height > 0 ? screenRect : (rowsRect && rowsRect.height > 0 ? rowsRect : null)
    let rowHeight = 0
    if (screenRect && screenRect.height > 0) rowHeight = screenRect.height / term.rows
    else if (rowsRect && rowsRect.height > 0) rowHeight = rowsRect.height / term.rows
    if (!originRect || rowHeight <= 0 || term.rows <= 0) return null
    const containerRect = container.getBoundingClientRect()
    const viewportY = active.viewportY
    return {
      rowHeight,
      originTop: originRect.top - containerRect.top,
      viewportY,
      visBot: viewportY + term.rows - 1,
    }
  }

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
    const g = measureGeometry()
    if (!g) {
      overlay.style.display = 'none'
      return
    }
    overlay.style.display = 'block'
    const { rowHeight, originTop, viewportY, visBot } = g
    const cursorAbs = active.baseY + active.cursorY

    const bars: string[] = []
    const badges: string[] = []
    let sig = `${viewportY}|${term.rows}|${selectedIds.size}|`
    for (const b of tracker.blocks) {
      if (b.start.isDisposed) continue
      const f = foldStore?.getFold(b.id)
      // 折叠态终点：full 折叠抽走了 body（end marker 已 dispose），块只剩提示符行；
      // prefix 折叠的 end marker 随 splice 自动迁移，仍指向最新保留行
      let endLine = b.end && !b.end.isDisposed ? b.end.line : cursorAbs
      if (f && f.kind === 'full') endLine = b.start.line
      if (endLine < b.start.line) continue
      // 色条覆盖可见范围（折叠态画虚线感——透明度减半；选中态加描边光环）
      const t = Math.max(b.start.line, viewportY)
      const btm = Math.min(endLine, visBot)
      const folded = !!f
      const selected = selectedIds.has(b.id)
      if (btm >= t) {
        const top = originTop + (t - viewportY) * rowHeight
        const h = (btm - t + 1) * rowHeight
        // 折叠态：虚线描边（rssh 同款，fill 透明透出背景）；正常态实心填充
        const barAttrs = folded
          ? `fill="none" stroke="${b.color}" stroke-width="1" stroke-dasharray="2,2"`
          : `fill="${b.color}"`
        bars.push(
          `<rect class="block-hit" data-block="${b.id}" x="0" y="${top.toFixed(1)}" width="${HIT_WIDTH}" height="${h.toFixed(1)}" fill="transparent"></rect>` +
          `<rect class="block-bar${folded ? ' folded' : ''}${selected ? ' selected' : ''}" x="${((BAR_ZONE_PX - BAR_WIDTH) / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${BAR_WIDTH}" height="${h.toFixed(1)}" rx="1.5" ${barAttrs}></rect>`,
        )
        sig += `${b.id}:${t}-${btm}${folded ? 'F' : ''}${selected ? 'S' : ''};`
      }
      // 折叠徽标：rssh 同款行末角标——与命令行同一行、贴容器右缘（不遮挡任何文本），
      // 仅命令行在视口内时绘制
      if (f && b.start.line >= viewportY && b.start.line <= visBot) {
        const top = originTop + (b.start.line - viewportY) * rowHeight
        badges.push(
          `<div class="block-fold-badge" data-block="${b.id}" title="点击展开" style="top:${top.toFixed(1)}px">⋯ 已折叠 ${f.count} 行</div>`,
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

  /** 命中测试：屏幕坐标 → 左侧色条区命中的块 id（终端右键菜单据此决定块操作项） */
  function hitTest(clientX: number, clientY: number): number | null {
    if (disposed || !enabled) return null
    const g = measureGeometry()
    if (!g) return null
    const containerRect = container.getBoundingClientRect()
    const x = clientX - containerRect.left
    // 命中区放宽 4px：色条 14px 窄区 + 少量余量，右键更容易命中
    if (x < -4 || x > BAR_ZONE_PX + 4) return null
    const line = g.viewportY + Math.floor((clientY - containerRect.top - g.originTop) / g.rowHeight)
    for (const b of tracker.blocks) {
      if (b.start.isDisposed) continue
      if (line >= b.start.line && line <= endLineOf(b)) return b.id
    }
    return null
  }

  // ---- 交互 ----

  /** 块内容 → 文本（折叠块走 savedLines，rssh block-content 同款；软换行合并） */
  function blockText(b: CommandBlock): string {
    if (b.start.isDisposed) return ''
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
    return lines.join('\n')
  }

  function writeBlockClipboard(text: string) {
    if (!text) return
    // 统一走三级兜底（pywebview 桥 → navigator.clipboard → execCommand）
    writeClipboardText(text)
  }

  /** 双击色条 / 右键菜单共用：切换折叠态（已折叠展开；未折叠且已关闭块则折叠） */
  function toggleFoldById(id: number) {
    if (!foldStore) return
    const b = tracker.blocks.find((x) => x.id === id)
    if (!b || b.start.isDisposed) return
    if (foldStore.isFolded(id)) foldStore.unfold(id)
    // 仅已关闭块可折叠——运行中块终点会漂移，抽不住
    else if (b.end && !b.end.isDisposed) foldStore.fold(id)
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
    // 点色条 → 选中块（rssh Finder 风格：Shift 范围 / Ctrl 切换 / 单击排他）；
    // 折叠/展开改走右键菜单（点徽标仍可直接展开），双击复制保留
    if (ev.shiftKey) rangeSelectTo(id)
    else if (ev.ctrlKey || ev.metaKey) toggleSelect(id)
    else singleSelect(id)
  }
  // 双击色条：切换折叠/展开（双击前会触发两次 click，单击选中同一块两次，无副作用）；
  // 复制块内容改走单击选中 + Ctrl+C（singleSelect 已顺带选中块文本）或右键菜单
  const onDblClick = (ev: MouseEvent) => {
    const el = (ev.target as Element).closest('rect.block-hit') as SVGRectElement | null
    if (!el) return
    toggleFoldById(Number(el.getAttribute('data-block')))
  }
  overlay.addEventListener('click', onClick)
  overlay.addEventListener('dblclick', onDblClick)

  // ---- 事件订阅 ----
  const disposables: { dispose(): void }[] = [
    // tracker.onChange / foldStore.onChange 不在这里订阅：由各自专属 sub 独立管理（支持重建时更换）
    term.onScroll(scheduleRedraw),
    term.onWriteParsed(scheduleRedraw),
    // 用户输入后恢复自动折叠（clear 暂停期间，下一个字符即解除）
    term.onData(() => {
      if (suppressUntilInput) {
        suppressUntilInput = false
        scheduleRedraw()
      }
    }),
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
    suppressAutoFoldUntilInput() {
      suppressUntilInput = true
    },
    hitTest,
    getSelection(): number[] {
      return [...selectedIds]
    },
    selectBlock(id: number) {
      singleSelect(id)
    },
    clearSelection,
    isFolded(id: number): boolean {
      return !!foldStore?.isFolded(id)
    },
    toggleFold(id: number) {
      toggleFoldById(id)
    },
    copyBlocks(ids: number[]) {
      const blocks = ids
        .map((id) => tracker.blocks.find((x) => x.id === id))
        .filter((b): b is CommandBlock => !!b && !b.start.isDisposed)
        .sort((a, b) => a.id - b.id)
      if (blocks.length === 0) return
      // 多块按块序拼接，块间空行分隔
      writeBlockClipboard(blocks.map(blockText).filter(Boolean).join('\n\n'))
    },
    notifySubmit() {
      // tracker 热重建后此处闭包引用的是最新实例（let tracker）
      tracker.notifySubmit()
    },
    dispose() {
      disposed = true
      if (rafId) cancelAnimationFrame(rafId)
      rafId = 0
      window.removeEventListener('mousedown', onWindowMouseDown)
      window.removeEventListener('keydown', onWindowKeyDown)
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
