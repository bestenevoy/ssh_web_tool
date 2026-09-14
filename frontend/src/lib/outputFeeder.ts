/**
 * 需求驱动的终端输出供给器（参考 rssh 的 output-feeder）。
 *
 * 洪水输出（大文件 cat / grep -r）到达速度远快于 xterm 解析+绘制，
 * xterm 自己的 write 缓冲无上限（超 50MB 会抛错）。本 feeder 持有积压，
 * 一次只喂一块：前一块的 write 回调触发（=已解析）才喂下一块。
 * 空闲路径直通（零额外延迟），交互式输出（vim/less/按键回显）不等待。
 *
 * 内存上限：按字节记账（UTF-8，避免 CJK 的 String.length 少报 ~3 倍），
 * 超限丢最旧的整块（洪水输出本来就是用户即将 Ctrl+C 的内容，丢一行可接受）。
 */

interface QueuedChunk {
  data: string
  size: number
}

const utf8Encoder = new TextEncoder()

function chunkSize(data: string): number {
  return utf8Encoder.encode(data).byteLength
}

export interface OutputFeeder {
  push(data: string): void
  /** 尚未被终端解析的字节数：排队中 + 正在写入的。 */
  pendingBytes(): number
  /** 丢弃所有排队数据（正在写入的那一块仍会解析完）。 */
  dropPending(): void
  /**
   * 在 windowMs 静默期内丢弃后续 push。Ctrl+C 打断洪水后，WebView 里已排队
   * 的事件仍会持续送达陈旧的洪水字节；只有一段安静的空隙才证明管道已排空。
   */
  armQuiescentDrop(windowMs: number): void
  dispose(): void
}

export function createOutputFeeder(opts: {
  write: (data: string, cb: () => void) => void
  maxPendingBytes: number
}): OutputFeeder {
  const queue: QueuedChunk[] = []
  let queuedBytes = 0
  let inflightBytes = 0 // 正在 write() 内的那一块
  let quiesceMs = 0
  let quiesceTimer: ReturnType<typeof setTimeout> | null = null
  let disposed = false

  function writeChunk(chunk: QueuedChunk) {
    inflightBytes = chunk.size
    opts.write(chunk.data, () => {
      inflightBytes = 0
      feedNext()
    })
  }

  function feedNext() {
    if (disposed) return
    const next = queue.shift()
    if (next === undefined) return
    queuedBytes -= next.size
    writeChunk(next)
  }

  return {
    push(data) {
      if (disposed) return
      if (quiesceTimer !== null) {
        // 洪水残液仍在排空：延长静默窗口并丢弃
        clearTimeout(quiesceTimer)
        quiesceTimer = setTimeout(() => { quiesceTimer = null }, quiesceMs)
        return
      }
      const chunk: QueuedChunk = { data, size: chunkSize(data) }
      // 内存上限：丢最旧的整块
      while (
        queue.length > 0 &&
        queuedBytes + inflightBytes + chunk.size > opts.maxPendingBytes
      ) {
        const oldest = queue.shift()!
        queuedBytes -= oldest.size
      }
      if (queue.length === 0 && inflightBytes === 0) {
        writeChunk(chunk) // 空闲：直通，无计时器
        return
      }
      queue.push(chunk)
      queuedBytes += chunk.size
    },
    pendingBytes() {
      return queuedBytes + inflightBytes
    },
    dropPending() {
      queue.length = 0
      queuedBytes = 0
    },
    armQuiescentDrop(windowMs) {
      if (disposed) return
      quiesceMs = windowMs
      if (quiesceTimer !== null) clearTimeout(quiesceTimer)
      quiesceTimer = setTimeout(() => { quiesceTimer = null }, windowMs)
    },
    dispose() {
      disposed = true
      queue.length = 0
      queuedBytes = 0
      if (quiesceTimer !== null) {
        clearTimeout(quiesceTimer)
        quiesceTimer = null
      }
    },
  }
}
