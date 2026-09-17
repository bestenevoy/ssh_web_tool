// zsScript 纯逻辑层测试：解析 / @ 引用解析（param 阻止）/ 校验 / 播放引擎
import { describe, it, expect } from 'vitest'
import { parseZs, resolveZsStep, validateZs, playZsBlock, ZS_LINE_INTERVAL_MS } from './zsScript'
import type { QuickCommand } from '../types'

const QC = (over: Partial<QuickCommand> & { id: string; name: string; command: string }): QuickCommand => ({
  type: 'direct',
  ...over,
})

const qcs: QuickCommand[] = [
  QC({ id: '1', name: '查看磁盘', command: 'df -h', key: 'df' }),
  QC({ id: '2', name: '重启服务', command: 'systemctl restart app' }),
  QC({ id: '3', name: 'Ping 主机', command: 'ping {args}', type: 'param', key: 'ping' }),
]

describe('parseZs', () => {
  it('解析 %% 分块与块名', () => {
    const f = parseZs('%% 部署\ndf -h\n%% 验证\necho ok')
    expect(f.blocks).toHaveLength(2)
    expect(f.blocks[0].name).toBe('部署')
    expect(f.blocks[0].startLine).toBe(0)
    expect(f.blocks[0].lines).toEqual([{ text: 'df -h', line: 1 }])
    expect(f.blocks[1].name).toBe('验证')
    expect(f.blocks[1].lines).toEqual([{ text: 'echo ok', line: 3 }])
  })

  it('隐式首块（无 %%）从第一个可执行行开始', () => {
    const f = parseZs('# 注释\n\necho hi')
    expect(f.blocks).toHaveLength(1)
    expect(f.blocks[0].startLine).toBe(2)
    expect(f.blocks[0].lines).toEqual([{ text: 'echo hi', line: 2 }])
  })

  it('空行与注释行不进入可执行序列', () => {
    const f = parseZs('a\n# c\n\nb')
    expect(f.blocks[0].lines.map((l) => l.text)).toEqual(['a', 'b'])
  })

  it('空文件无块', () => {
    expect(parseZs('').blocks).toHaveLength(0)
    expect(parseZs('# 只有注释').blocks).toHaveLength(0)
  })
})

describe('resolveZsStep', () => {
  it('普通行原样发送', () => {
    expect(resolveZsStep('ls -la', 0, qcs)).toEqual({ kind: 'send', cmd: 'ls -la', line: 0, source: null })
  })

  it('@key 与 @名称 都可引用（key 优先）', () => {
    expect(resolveZsStep('@df', 0, qcs)).toEqual({ kind: 'send', cmd: 'df -h', line: 0, source: '查看磁盘' })
    expect(resolveZsStep('@重启服务', 0, qcs)).toEqual({ kind: 'send', cmd: 'systemctl restart app', line: 0, source: '重启服务' })
  })

  it('引用大小写不敏感', () => {
    expect(resolveZsStep('@DF', 0, qcs)).toMatchObject({ kind: 'send', source: '查看磁盘' })
  })

  it('硬性规则：param 型指令报错（.zs 不允许带参数指令）', () => {
    const r = resolveZsStep('@ping', 3, qcs)
    expect(r.kind).toBe('error')
    if (r.kind === 'error') expect(r.message).toContain('不允许引用带参数的指令')
  })

  it('未知引用报错', () => {
    expect(resolveZsStep('@nope', 0, qcs).kind).toBe('error')
  })

  it('@sleep 解析秒数，支持小数', () => {
    expect(resolveZsStep('@sleep 2', 0, qcs)).toEqual({ kind: 'sleep', seconds: 2, line: 0 })
    expect(resolveZsStep('@SLEEP 0.5', 0, qcs)).toEqual({ kind: 'sleep', seconds: 0.5, line: 0 })
  })

  it('@sleep 缺秒数/非法秒数报错', () => {
    expect(resolveZsStep('@sleep', 0, qcs).kind).toBe('error')
    expect(resolveZsStep('@sleep abc', 0, qcs).kind).toBe('error')
  })

  it('空 @ 引用报错', () => {
    expect(resolveZsStep('@', 0, qcs).kind).toBe('error')
  })
})

describe('validateZs', () => {
  it('返回全部错误行，跳过注释/空行/%% 行', () => {
    const content = '%% a\n@ping\ndf -h\n# @ping\n%% b\n@nope'
    const errs = validateZs(content, qcs)
    expect(errs.map((e) => e.line).sort()).toEqual([1, 5])
  })

  it('全部合法时返回空数组', () => {
    expect(validateZs('df -h\n@df\n@sleep 1', qcs)).toEqual([])
  })
})

describe('playZsBlock', () => {
  const mkOpts = () => {
    const sent: { sid: string; cmd: string }[] = []
    const statuses: string[] = []
    return {
      sent,
      statuses,
      opts: {
        send: (sid: string, cmd: string) => {
          sent.push({ sid, cmd })
          return true
        },
        onStatus: (m: string) => statuses.push(m),
        cancel: { cancelled: false },
      },
    }
  }

  it('播放前整块检查：块内有 param 引用则一行都不发送', async () => {
    const { sent, statuses, opts } = mkOpts()
    const block = parseZs('df -h\n@ping').blocks[0]
    const ok = await playZsBlock(block, qcs, ['s1'], opts)
    expect(ok).toBe(false)
    expect(sent).toHaveLength(0)
    expect(statuses[0]).toContain('不允许引用带参数的指令')
  })

  it('播放前检查：未知引用/空目标会话均拒绝', async () => {
    const a = mkOpts()
    const block = parseZs('@nope').blocks[0]
    await expect(playZsBlock(block, qcs, ['s1'], a.opts)).resolves.toBe(false)
    expect(a.sent).toHaveLength(0)

    const b = mkOpts()
    const block2 = parseZs('df -h').blocks[0]
    await expect(playZsBlock(block2, qcs, [], b.opts)).resolves.toBe(false)
    expect(b.sent).toHaveLength(0)
  })

  it('正常播放：逐行广播到全部会话，注释/空行跳过', async () => {
    const { sent, opts } = mkOpts()
    const content = '%% 部署\n# 说明\ndf -h\n@df'
    const ok = await playZsBlock(parseZs(content).blocks[0], qcs, ['s1', 's2'], opts)
    expect(ok).toBe(true)
    expect(sent).toEqual([
      { sid: 's1', cmd: 'df -h' },
      { sid: 's2', cmd: 'df -h' },
      { sid: 's1', cmd: 'df -h' },
      { sid: 's2', cmd: 'df -h' },
    ])
  })

  it('发送失败（会话断开）中止播放', async () => {
    const statuses: string[] = []
    const sent: string[] = []
    const block = parseZs('df -h\necho ok').blocks[0]
    const ok = await playZsBlock(block, qcs, ['dead'], {
      send: () => {
        sent.push('x')
        return false
      },
      onStatus: (m) => statuses.push(m),
      cancel: { cancelled: false },
    })
    expect(ok).toBe(false)
    expect(sent).toHaveLength(1) // 只尝试了第一行的单会话发送
    expect(statuses[0]).toContain('发送失败')
  })

  it('取消句柄置位后停止播放', async () => {
    const sent: string[] = []
    const cancel = { cancelled: false }
    const block = parseZs('a\nb\nc').blocks[0]
    const p = playZsBlock(block, qcs, ['s1'], {
      send: (_sid, cmd) => {
        sent.push(cmd)
        return true
      },
      onStatus: () => {},
      cancel,
    })
    cancel.cancelled = true
    await expect(p).resolves.toBe(false)
    expect(sent.length).toBeLessThan(3)
  })

  it('行间隔常量合理（>0）', () => {
    expect(ZS_LINE_INTERVAL_MS).toBeGreaterThan(0)
  })
})
