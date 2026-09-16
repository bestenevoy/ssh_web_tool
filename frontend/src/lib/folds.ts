/**
 * FoldStore — 命令块真折叠/展开（移植自 rssh folds.ts，适配本项目）。
 *
 * 与 blockBar 旧"遮罩式视觉折叠"不同：真折叠直接 splice xterm 私有 buffer
 * 抽出 body 行，折叠后 buffer 实际行数减少（滚动条变短）；savedLines 暂存
 * 被抽出的 BufferLine，展开时按对象引用塞回原位。
 *
 * 设计基础（rssh spike 已验证）：
 *   1. xterm Buffer.addMarker 注册了 lines.onDelete/onInsert/onTrim：
 *      splice 时 marker 行号自动迁移、范围内的 marker 自动 dispose
 *   2. 隐藏不变量 lines.length === ybase + rows：splice 后必须用
 *      Buffer.getBlankLine 在末尾补齐（补偿空行）
 *   3. 不变量 cursor 内容跟随：splice 在 cursor 上方时 cursor 绝对行
 *      要相应减少（fold）或增加（unfold）
 *
 * fold 流程：splice 抽出 → push 空行补齐（记下引用）→ drain ybase 再 y → 重排 ydisp。
 * 手动折叠（full）抽完整 body，block.end marker 被 dispose，展开时 addMarker 重建；
 * 自动折叠（prefix）只抽最早的前缀，保留最新 maxVisibleLines 行输出可见。
 *
 * unfold 流程：splice 塞回 → 删除本 fold 当初补在 buffer 里的空行。
 *   补偿空行可能已被后续 fold 推到 buffer 中间；因此不能只看末尾，
 *   更不能用全局集合去 pop 其他 fold 的空行。按对象引用找到
 *   本 fold 自己的空行，确认仍为空后删除，才能让非 LIFO 展开恢复不变量。
 *
 * 自动展开触发：
 *   - 终端 resize 前由调用方（useTerminals.fitTerminal）执行 unfoldAll
 *     （savedLines 是旧列宽快照，reflow 后塞回会损坏）
 *   - block.start 死亡（scrollback 修剪到该 block 之前）— 通过监听
 *     tracker.onChange 检测 block 从 tracker 消失来代理
 *   - term.reset() / 硬重置：savedLines 全部 stale，discardAll() 只清记录
 *     禁止塞回（reset 后 buffer 已清空，塞回会写进别人的行）
 *
 * 缓存不变量：所有 fold.savedLines 总数受 maxCachedLines 限制。预算满时先
 * 展开旧 fold 把历史交还 xterm；单个超长活动块只保留最新的可恢复前缀。
 * 调用方保证 visible + cached < xterm scrollback（5000 − blockMaxLines − 1）。
 *
 * ⚠️ 私有 API 警告：依赖 _core.buffer（或 buffers.normal）的 lines/ybase/
 *    ydisp/y/getBlankLine/addMarker，以及 _core._viewport.queueSync（滚动条
 *    重同步）。本项目 package.json 精确 pin "@xterm/xterm": "6.0.0"。
 *    升级 xterm 必须人工复核这些私有钩子（本文件的单元测试用假终端，
 *    只验证本文件逻辑，不触真实内部结构）。—— 全项目唯一允许碰这些
 *    私有 API 的文件，其余模块只经 FoldStore 公开接口使用。
 */
import type { Terminal, IDisposable, IMarker } from '@xterm/xterm'
import type { CommandBlock, CommandBlockTracker } from './commandBlocks'

export interface Fold {
  /** 自增 id（仅用于调试）；外界以 blockId 索引 */
  id: number
  blockId: number
  /** full = 手动折叠完整已关闭块；prefix = 自动抽取最早前缀行 */
  kind: 'full' | 'prefix'
  /** 当前暂存行数（= savedLines.length） */
  count: number
  /** splice 抽出的 BufferLine 实例（对我们透明，展开时原样塞回） */
  savedLines: unknown[]
  /** 这次 fold push 进 buffer 末尾的补偿空行 refs。
   *  refs 只由对应 Fold 持有；删除 fold 记录即可释放。 */
  pushedBlankRefs: unknown[]
}

interface FoldState extends Fold {
  /** 输出光标已触及的前导补偿空行数。单调递增：
   *  光标上移不得让已消费的空行重新变得可删除。 */
  consumedBlankCount: number
}

export interface FoldStore extends IDisposable {
  readonly folds: ReadonlyArray<Fold>
  /** 手动折叠已关闭块的完整 body（full）。 */
  fold(blockId: number): boolean
  /** 展开（塞回 savedLines、删除自己的补偿空行）。 */
  unfold(blockId: number): boolean
  isFolded(blockId: number): boolean
  /** O(1) 取 fold 记录（高频渲染路径必走这条，避免线性 find）。 */
  getFold(blockId: number): Fold | undefined
  /** xterm 几何（行/列）变化前展开全部（savedLines 是旧列宽快照）。 */
  unfoldAll(): void
  /** xterm reflow 后按 maxVisibleLines 重新应用自动折叠。 */
  enforceAutoFold(): void
  /** term.reset() 后调用：savedLines 全部 stale，只清记录禁止塞回。 */
  discardAll(): void
  /** 折叠状态变化时通知（fold/unfold/scrollback 失效）。 */
  onChange(fn: () => void): IDisposable
}

export interface FoldStoreOptions {
  /** 增长中的命令块保留最新的这么多行 body 可见。 */
  maxVisibleLines?: number
  /** 全部 fold 的 savedLines 总预算（跨整个终端）。 */
  maxCachedLines?: number
  /** 自动折叠总开关（返回 false 时 enforceAutoFold 与增量折叠都不动作）。 */
  shouldAutoFold?: () => boolean
}

/** xterm 默认 attr（fg=0,bg=0），与 DEFAULT_ATTR_DATA 等价。getBlankLine 必填。 */
const BLANK_ATTR = { fg: 0, bg: 0, extended: { ext: 0, urlId: 0, underlineStyle: 0 } }
// 单次解析批次内限制增量折叠工作量（不在每个 LF 上都 splice）。
// 设置上限至少留 99 行 scrollback，32 远低于 xterm 可能裁掉当前块 start marker 的距离。
const AUTO_FOLD_BATCH_LINES = 32

/** xterm 私有 buffer 的最小使用面（见文件头警告）。 */
interface PrivateBuffer {
  lines: {
    length: number
    get(i: number): unknown
    splice(start: number, deleteCount: number, ...items: unknown[]): void
    push(item: unknown): void
  }
  ybase: number
  ydisp: number
  y: number
  getBlankLine(attr: unknown): unknown
  addMarker(line: number): IMarker
}

interface PrivateViewport {
  queueSync(yDisp?: number): void
}

function getBuf(term: Terminal): PrivateBuffer {
  const core = (term as unknown as {
    _core: { buffer: PrivateBuffer; buffers?: { normal: PrivateBuffer } }
  })._core
  // 折叠属于普通缓冲区的命令历史；resize 请求到来时活动缓冲区可能正处在备用区
  return core.buffers?.normal ?? core.buffer
}

/** 视口滚动高度由 buffer.lines.length 推导，只在核心 scroll/resize 事件时
 *  重同步。我们直接 splice buffer.lines 绕过了这些事件，滚动条会失灵
 *  （"展开后滚不上去"）。queueSync() 在下一渲染帧重算；folds 随后总是调
 *  term.refresh() 驱动该帧。（xterm 6.0 把 _core.viewport.syncScrollArea
 *  改名为 _core._viewport.queueSync。） */
function syncViewport(term: Terminal): void {
  const vp = (term as unknown as { _core: { _viewport?: PrivateViewport } })._core._viewport
  vp?.queueSync()
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n))
}

function indexLineRefs(lines: PrivateBuffer['lines'], refs: Iterable<unknown>): Map<unknown, number> {
  const targets = new Set(refs)
  const indices = new Map<unknown, number>()
  if (targets.size === 0) return indices
  for (let i = 0; i < lines.length; i++) {
    const line = lines.get(i)
    if (targets.has(line)) {
      indices.set(line, i)
      if (indices.size === targets.size) break
    }
  }
  return indices
}

/** 补偿空行是否仍为"空"（未被新输出覆写）。 */
function isStillBlankLine(line: unknown): boolean {
  const candidate = line as { getTrimmedLength?: () => number; isWrapped?: boolean } | null
  if (candidate && typeof candidate.getTrimmedLength === 'function') {
    return candidate.getTrimmedLength() === 0 && candidate.isWrapped !== true
  }
  return true
}

export function createFoldStore(
  term: Terminal,
  tracker: CommandBlockTracker,
  options: FoldStoreOptions = {},
): FoldStore {
  // 以 blockId 为键便于 O(1) 判断"该 block 是否折叠"。Fold 的 id 仅用于调试。
  const folds = new Map<number, FoldState>()
  // 补偿空行身份 -> 属主 fold/index。光标事件可 O(1) 更新消费进度，
  // 而不必每个 LF 都全量扫描 scrollback。
  const blankOwners = new Map<unknown, { blockId: number; index: number }>()
  const listeners = new Set<() => void>()
  const disposables: IDisposable[] = []
  let nextId = 1

  const emit = () => listeners.forEach((fn) => fn())

  const maxVisibleLines = Number.isFinite(options.maxVisibleLines)
    ? Math.max(1, Math.trunc(options.maxVisibleLines!))
    : null
  const maxCachedLines = Number.isFinite(options.maxCachedLines)
    ? Math.max(1, Math.trunc(options.maxCachedLines!))
    : Number.POSITIVE_INFINITY

  /** 从 blankOwners 摘除该 fold 的空行登记（不碰 buffer）。 */
  function discardFold(f: FoldState): void {
    for (const line of f.pushedBlankRefs) {
      const owner = blankOwners.get(line)
      if (owner?.blockId === f.blockId) blankOwners.delete(line)
    }
  }

  /** 消费进度回收：已确认被输出覆写的补偿空行移出 refs 并重排索引。 */
  function pruneConsumedBlankRefs(f: FoldState): void {
    const consumed = Math.min(f.consumedBlankCount, f.pushedBlankRefs.length)
    if (consumed === 0) return
    for (const line of f.pushedBlankRefs.slice(0, consumed)) {
      const owner = blankOwners.get(line)
      if (owner?.blockId === f.blockId) blankOwners.delete(line)
    }
    f.pushedBlankRefs.splice(0, consumed)
    f.consumedBlankCount = 0
    f.pushedBlankRefs.forEach((line, index) => {
      blankOwners.set(line, { blockId: f.blockId, index })
    })
  }

  /** savedLines 预算超限时先丢最旧的（与 xterm scrollback 同策略：
   *  静默删掉完整 fold 会让只剩命令行、无徽标可展开）。 */
  function enforceSavedLineBudget(currentBlockId: number): void {
    if (!Number.isFinite(maxCachedLines)) return
    let overflow = Array.from(folds.values())
      .reduce((total, item) => total + item.savedLines.length, 0) - maxCachedLines
    if (overflow <= 0) return

    // 先把旧 fold 展开回 xterm 再回收它们的缓存预算
    for (const blockId of Array.from(folds.keys())) {
      if (blockId === currentBlockId) continue
      unfold(blockId)
      overflow = Array.from(folds.values())
        .reduce((total, item) => total + item.savedLines.length, 0) - maxCachedLines
      if (overflow <= 0) return
    }

    // 单个超长活动块仍可能超预算：对齐 xterm 的头部裁剪策略，只保留最新的可恢复前缀
    const current = folds.get(currentBlockId)
    if (!current || overflow <= 0) return
    current.savedLines.splice(0, Math.min(overflow, current.savedLines.length))
    current.count = current.savedLines.length
  }

  /** 核心 splice：抽出 [startLine, startLine+count) 并维护全部不变量。 */
  function removeLines(
    blockId: number,
    kind: Fold['kind'],
    startLine: number,
    count: number,
  ): boolean {
    if (count <= 0) return false
    const existing = folds.get(blockId)
    // 同一块只允许"prefix 续折"（前缀再往前扩）；full/prefix 混合、full 重复都拒绝
    if (existing && (existing.kind !== 'prefix' || kind !== 'prefix')) return false
    if (existing) {
      recordCursorConsumption()
      for (let i = existing.consumedBlankCount; i < existing.pushedBlankRefs.length; i++) {
        if (!isStillBlankLine(existing.pushedBlankRefs[i])) {
          existing.consumedBlankCount = i + 1
        }
      }
      pruneConsumedBlankRefs(existing)
    }

    const buf = getBuf(term)
    const cursorAbs = buf.ybase + buf.y
    const endLine = startLine + count - 1
    if (endLine >= cursorAbs) return false // 不能抽走光标所在行
    const wasLive = buf.ydisp === buf.ybase

    const saved: unknown[] = []
    for (let i = 0; i < count; i++) saved.push(buf.lines.get(startLine + i))

    // 先 drain scrollback；只有从可见屏幕区删除才需要补偿空行
    // 维持 lines.length === ybase + rows 不变量
    const ybaseDrain = Math.min(buf.ybase, count)
    const pushCount = count - ybaseDrain
    buf.lines.splice(startLine, count)

    const pushedRefs: unknown[] = []
    for (let i = 0; i < pushCount; i++) {
      const blank = buf.getBlankLine(BLANK_ATTR)
      buf.lines.push(blank)
      pushedRefs.push(blank)
    }

    buf.ybase -= ybaseDrain
    buf.y = Math.max(0, buf.y - pushCount)
    if (buf.ydisp >= startLine + count) buf.ydisp -= count
    else if (buf.ydisp >= startLine) buf.ydisp = startLine
    if (buf.ydisp > buf.ybase) buf.ydisp = buf.ybase
    if (wasLive) buf.ydisp = buf.ybase

    const foldState = existing ?? {
      id: nextId++,
      blockId,
      kind,
      count: 0,
      savedLines: [],
      pushedBlankRefs: [],
      consumedBlankCount: 0,
    }
    foldState.savedLines.push(...saved)
    foldState.count = foldState.savedLines.length
    const blankOffset = foldState.pushedBlankRefs.length
    foldState.pushedBlankRefs.push(...pushedRefs)
    folds.set(blockId, foldState)
    pushedRefs.forEach((line, index) => {
      blankOwners.set(line, { blockId, index: blankOffset + index })
    })
    enforceSavedLineBudget(blockId)
    syncViewport(term)
    term.refresh(0, term.rows - 1)
    emit()
    return true
  }

  function fold(blockId: number): boolean {
    if (folds.has(blockId)) return false
    const block = tracker.blocks.find((b) => b.id === blockId)
    if (!block || !block.end) return false
    if (block.start.isDisposed || block.end.isDisposed) return false
    const startLine = block.start.line + 1
    const endLine = block.end.line
    if (startLine > endLine) return false // 空 body

    const count = endLine - startLine + 1
    return removeLines(blockId, 'full', startLine, count)
  }

  function canAutoFold(): boolean {
    return maxVisibleLines !== null
      && options.shouldAutoFold?.() !== false
      && term.buffer.active.type === 'normal'
  }

  /** 新到达输出的增量折叠。 */
  function foldBlockOverflow(block: CommandBlock, minimumExcess: number): void {
    if (maxVisibleLines === null || block.start.isDisposed) return
    if (folds.get(block.id)?.kind === 'full') return // 手动 full 折叠不被自动策略覆盖
    const buf = getBuf(term)
    const endLine = block.end === null
      ? buf.ybase + buf.y
      : block.end.isDisposed ? null : block.end.line
    if (endLine === null) return
    const visibleBodyLines = endLine - block.start.line
    const excess = visibleBodyLines - maxVisibleLines
    if (excess >= minimumExcess) {
      removeLines(block.id, 'prefix', block.start.line + 1, excess)
    }
  }

  function foldActiveOverflow(minimumExcess: number): void {
    if (!canAutoFold()) return
    const block = tracker.blocks[tracker.blocks.length - 1]
    if (block?.end === null) foldBlockOverflow(block, minimumExcess)
  }

  function foldRecentOverflow(): void {
    if (!canAutoFold()) return
    const blocks = tracker.blocks
    // prompt 检测（tracker）在本监听之前运行，可能在同一解析批次里
    // 关闭上一块并打开新提示符块——检查最后两块。
    for (let i = Math.max(0, blocks.length - 2); i < blocks.length; i++) {
      foldBlockOverflow(blocks[i], 1)
    }
  }

  /** reflow/阈值变化后重新应用自动折叠（对全部块）。 */
  function enforceAutoFold(): void {
    if (!canAutoFold()) return
    // marker 随旧行移除自动迁移，稳定块快照足够（removeLines 会在底层改 buffer）
    for (const block of Array.from(tracker.blocks)) foldBlockOverflow(block, 1)
  }

  function recordCursorConsumption(): void {
    if (folds.size === 0) return
    // 光标/换行事件属于活动缓冲区；备用区应用不得因休眠普通区光标
    // 恰好指向某补偿空行就把它标记为已消费。
    if (term.buffer.active.type !== 'normal') return
    const buf = getBuf(term)
    const cursorAbs = buf.ybase + buf.y
    const owner = blankOwners.get(buf.lines.get(cursorAbs))
    if (!owner) return
    const fold = folds.get(owner.blockId)
    if (fold && owner.index >= fold.consumedBlankCount) {
      fold.consumedBlankCount = owner.index + 1
    }
  }

  function unfold(blockId: number): boolean {
    const f = folds.get(blockId)
    if (!f) return false
    const block = tracker.blocks.find((b) => b.id === blockId)
    if (!block || block.start.isDisposed) {
      // block 已被 scrollback 吞噬 — 丢弃 saved（用户也看不见原内容了）
      discardFold(f)
      folds.delete(blockId)
      emit()
      return false
    }
    const buf = getBuf(term)
    const insertAt = block.start.line + 1
    const cursorAbsBefore = buf.ybase + buf.y
    let nextCursorAbs = insertAt <= cursorAbsBefore ? cursorAbsBefore + f.count : cursorAbsBefore
    let nextYdisp = buf.ydisp
    const wasLive = buf.ydisp === buf.ybase

    // 光标位置不是历史：输出可能把空行往下推再上移。保留事件高水位；
    // 同时把"最后被修改的补偿行"之前的每个空行都视为已消费——覆盖
    // 同一解析批次内"下移、写入、再回起点"的情况。
    recordCursorConsumption()
    for (let i = f.consumedBlankCount; i < f.pushedBlankRefs.length; i++) {
      if (!isStillBlankLine(f.pushedBlankRefs[i])) f.consumedBlankCount = i + 1
    }
    const blankRefIndicesBeforeInsert = indexLineRefs(buf.lines, f.pushedBlankRefs)
    const untouchedBlankRefs = new Set(
      f.pushedBlankRefs.slice(f.consumedBlankCount).filter((line) => {
        const index = blankRefIndicesBeforeInsert.get(line)
        return index !== undefined && index > cursorAbsBefore && isStillBlankLine(line)
      }),
    )

    // splice 塞回 → marker 反向迁移。
    // 分块插：Array spread 在 V8 上有 ~65k 参数硬上限（大构建日志/find 输出
    // 轻易超过），一次性 splice(...savedLines) 会抛 RangeError。
    const SPLICE_CHUNK = 32768
    let inserted = 0
    let trimmedDuringInsert = 0
    for (let i = 0; i < f.savedLines.length; i += SPLICE_CHUNK) {
      const chunk = f.savedLines.slice(i, i + SPLICE_CHUNK)
      // CircularList 在每次 splice 后立即强制 maxLength（不是整个逻辑插入后）。
      // 每次头部裁剪都让下一个插入点左移；用 insertAt+i 会让后续块乱序。
      const chunkInsertAt = clamp(
        insertAt + inserted - trimmedDuringInsert,
        0,
        buf.lines.length,
      )
      const lengthBeforeChunk = buf.lines.length
      buf.lines.splice(chunkInsertAt, 0, ...chunk)
      trimmedDuringInsert += Math.max(
        0,
        lengthBeforeChunk + chunk.length - buf.lines.length,
      )
      inserted += chunk.length
    }
    if (insertAt <= nextYdisp) nextYdisp += f.count

    // CircularList.splice 在 maxLength 满时从头 trim；我们直接碰私有
    // lines，必须自己把 cursor/viewport 的绝对行同步扣回来。
    if (trimmedDuringInsert > 0) {
      nextCursorAbs = Math.max(0, nextCursorAbs - trimmedDuringInsert)
      nextYdisp = Math.max(0, nextYdisp - trimmedDuringInsert)
    }

    // 走任一返回路径前，先删除本 fold 未被消费的补偿空行。头部裁剪可能
    // dispose 掉 block.start，但那些人造行不该变成真实终端历史。
    const removableIndices = indexLineRefs(buf.lines, untouchedBlankRefs)
    const removable = Array.from(untouchedBlankRefs)
      .map((line) => ({ line, index: removableIndices.get(line) }))
      .filter((item): item is { line: unknown; index: number } => item.index !== undefined && isStillBlankLine(item.line))
      .sort((a, b) => b.index - a.index)

    for (const { index } of removable) {
      buf.lines.splice(index, 1)
      if (index < nextCursorAbs) nextCursorAbs--
      if (index < nextYdisp) nextYdisp--
    }

    // 头部裁剪可能在删除补偿行之前就消费掉部分恢复的历史。此时删除后
    // 行数可能少于可见视口行数；只补足缺失的屏幕填充（尾部追加不改变
    // cursor/viewport 坐标）。
    while (buf.lines.length < term.rows) {
      buf.lines.push(buf.getBlankLine(BLANK_ATTR))
    }

    if (block.start.isDisposed || block.start.line < 0) {
      // 插入已改动 CircularList。虽然块无法重建，ybase/ydisp/y 也必须
      // 在丢弃 fold 记录前描述新 buffer。
      buf.ybase = Math.max(0, buf.lines.length - term.rows)
      buf.y = clamp(nextCursorAbs - buf.ybase, 0, term.rows - 1)
      buf.ydisp = wasLive ? buf.ybase : clamp(nextYdisp, 0, buf.ybase)
      discardFold(f)
      folds.delete(blockId)
      syncViewport(term)
      term.refresh(0, term.rows - 1)
      emit()
      return false
    }

    buf.ybase = Math.max(0, buf.lines.length - term.rows)
    buf.y = clamp(nextCursorAbs - buf.ybase, 0, term.rows - 1)
    buf.ydisp = wasLive ? buf.ybase : clamp(nextYdisp, 0, buf.ybase)

    // full 折叠消费了 block.end，这里重建；prefix 折叠最新行仍在 buffer，
    // 后续 end marker 会随插入自动迁移。
    if (f.kind === 'full') {
      try {
        const newEnd = buf.addMarker(block.start.line + f.count)
        ;(block as { end: IMarker | null }).end = newEnd
      } catch {
        // 保留已 dispose 的 marker；渲染层已回退到光标行
      }
    }

    discardFold(f)
    folds.delete(blockId)
    syncViewport(term)
    term.refresh(0, term.rows - 1)
    emit()
    return true
  }

  function unfoldAll(): void {
    for (const blockId of Array.from(folds.keys())) unfold(blockId)
  }

  function discardAll(): void {
    // 仅清记录：term.reset() 后 buffer 已清空，savedLines 引用的旧行对象
    // 全部 stale，塞回会写进别人的行。补偿空行登记一并丢弃。
    for (const f of Array.from(folds.values())) discardFold(f)
    folds.clear()
    blankOwners.clear()
    emit()
  }

  // onCursorMove 只上报解析批次的最终位置；onLineFeed 对中间下移也会触发，
  // 两者配合在输出回移时仍保留消费高水位。
  disposables.push(
    term.onCursorMove(recordCursorConsumption),
    term.onLineFeed(() => {
      recordCursorConsumption()
      foldActiveOverflow(AUTO_FOLD_BATCH_LINES)
    }),
  )
  if (maxVisibleLines !== null) {
    disposables.push(term.onWriteParsed(foldRecentOverflow))
  }

  // scrollback 修剪：tracker 监听 block.start.onDispose 后从 blocks 数组移除。
  // 这里经 onChange 比对 tracker 现存 block — 折叠记录里 block 不在了就丢弃。
  disposables.push(tracker.onChange(() => {
    const trackedIds = new Set(tracker.blocks.map((b) => b.id))
    let dropped = false
    for (const blockId of Array.from(folds.keys())) {
      if (!trackedIds.has(blockId)) {
        const fold = folds.get(blockId)
        if (fold) discardFold(fold)
        folds.delete(blockId)
        dropped = true
      }
    }
    if (dropped) emit()
  }))

  return {
    get folds() {
      return Array.from(folds.values())
    },
    fold,
    unfold,
    isFolded(blockId) {
      return folds.has(blockId)
    },
    getFold(blockId) {
      return folds.get(blockId)
    },
    unfoldAll,
    enforceAutoFold,
    discardAll,
    onChange(fn) {
      listeners.add(fn)
      return { dispose: () => listeners.delete(fn) }
    },
    dispose() {
      disposables.forEach((d) => d.dispose())
      folds.clear()
      blankOwners.clear()
      listeners.clear()
    },
  }
}
