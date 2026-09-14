/**
 * outputFeeder 单元测试：验证背压控制、内存上限、丢弃逻辑。
 * 不依赖 DOM / xterm，纯逻辑测试。write mock 一律手动释放（cb 不自动调用），
 * 以便精确构造"前一帧仍在写"的背压场景。
 */
import { describe, expect, it, vi } from 'vitest'
import { createOutputFeeder } from './outputFeeder'

/** 手动释放版 feeder：write 只记录调用与回调，由测试控制 cb 触发时机 */
function makeManual(maxPendingBytes = 1024) {
  const releases: Array<() => void> = []
  const written: string[] = []
  const write = vi.fn((data: string, cb: () => void) => {
    written.push(data)
    releases.push(cb)
  })
  const feeder = createOutputFeeder({ write, maxPendingBytes })
  return { feeder, write, written, releases }
}

describe('outputFeeder', () => {
  it('空闲路径直通：无排队立即写', () => {
    const { feeder, write, releases } = makeManual()
    feeder.push('hello')
    expect(write).toHaveBeenCalledTimes(1) // 直通，无排队
    expect(feeder.pendingBytes()).toBeGreaterThan(0) // 在写的那块算 pending
    releases[0]()
    expect(feeder.pendingBytes()).toBe(0)
  })

  it('背压排队：前一帧未完成时入队，回调后按序喂出', () => {
    const { feeder, write, releases, written } = makeManual()
    feeder.push('a')
    feeder.push('b')
    expect(write).toHaveBeenCalledTimes(1) // b 排队，未写
    releases[0]() // a 解析完成 → 喂 b
    expect(written).toEqual(['a', 'b'])
    releases[1]()
    expect(feeder.pendingBytes()).toBe(0)
  })

  it('超过内存上限丢弃最旧整块', () => {
    const { feeder, written, releases } = makeManual(250) // 每块 100B，最多 2 块
    feeder.push('x'.repeat(100)) // 写 x（inflight 100）
    feeder.push('y'.repeat(100)) // 队列 [y]
    feeder.push('z'.repeat(100)) // 100(inf)+100(queued)+100 > 250 → 弹 y，队列 [z]
    expect(written).toEqual(['x'.repeat(100)]) // y 从未被写（最旧整块被丢弃）
    releases[0]() // x 完成 → 喂 z
    expect(written).toEqual(['x'.repeat(100), 'z'.repeat(100)])
    expect(feeder.pendingBytes()).toBeLessThanOrEqual(250)
  })

  it('dropPending 丢弃排队中的块', () => {
    const { feeder, written, releases } = makeManual()
    feeder.push('a')
    feeder.push('b')
    feeder.push('c')
    feeder.dropPending()
    releases[0]()
    expect(written).toEqual(['a']) // b、c 被丢弃
    expect(feeder.pendingBytes()).toBe(0)
  })

  it('armQuiescentDrop 在静默窗口内丢弃新数据', () => {
    vi.useFakeTimers()
    try {
      const { feeder, written } = makeManual()
      feeder.armQuiescentDrop(100)
      feeder.push('stale1') // 静默期内 → 丢弃
      feeder.push('stale2')
      expect(written).toEqual([])
      vi.advanceTimersByTime(150) // 静默期结束
      feeder.push('fresh')
      expect(written).toEqual(['fresh'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('dispose 后停止接收', () => {
    const { feeder, written } = makeManual()
    feeder.dispose()
    feeder.push('x')
    expect(written).toEqual([])
    expect(feeder.pendingBytes()).toBe(0)
  })
})