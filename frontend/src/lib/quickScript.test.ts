/**
 * quickScript 运行时单测：编译校验 / send 链路 / expect 命中与超时 /
 * 协作取消与全局超时 / sessions 枚举（假 host，无 React/xterm 依赖）
 */
import { describe, expect, it } from 'vitest'
import { compileScript, runQuickScript, ScriptAbort, type QuickScriptHost } from './quickScript'

interface HostOpts {
  lines?: string[][] // 依次弹出的屏幕快照（取完重复最后一个）
  send?: (cmd: string, execute?: boolean) => boolean
  cancelled?: () => boolean
  deadlineMs?: number
}

function makeHost(opts: HostOpts) {
  const logs: string[] = []
  const sent: Array<[string, boolean | undefined]> = []
  let polls = 0
  const deadlineAt = Date.now() + (opts.deadlineMs ?? 10_000)
  const host: QuickScriptHost = {
    resolve: (idOrName) => {
      // 仅认 s1 / test-host / id 前缀（模拟 App 的真实解析：未知目标返回 null）
      if (idOrName && idOrName !== 's1' && idOrName !== 'test-host' && !idOrName.startsWith('s1')) return null
      return {
        id: idOrName ?? 's1',
        name: 'test-host',
        send: (c, e = true) => {
          sent.push([c, e])
          return opts.send ? opts.send(c, e) : true
        },
        lines: (n) => {
          const seq = opts.lines ?? [[]]
          const snap = seq[Math.min(polls, seq.length - 1)]
          polls++
          return snap.slice(0, n ?? 50)
        },
      }
    },
    list: () => [{ id: 's1', name: 'test-host' }],
    activeId: () => 's1',
    log: (m) => logs.push(m),
    cancelled: opts.cancelled ?? (() => false),
    deadline: () => deadlineAt,
  }
  return { host, logs, sent }
}

describe('compileScript', () => {
  it('语法错误编译期抛出（保存校验依赖此行为）', () => {
    expect(() => compileScript('const x =')).toThrow(SyntaxError)
    expect(() => compileScript("s.send('ok')")).not.toThrow()
  })
})

describe('runQuickScript', () => {
  it('send/log 直通宿主', async () => {
    const h = makeHost({})
    await runQuickScript("const s = t.session(); s.send('ls -la'); t.log('done')", h.host)
    expect(h.sent).toEqual([['ls -la', true]])
    expect(h.logs).toEqual(['done'])
  })

  it('expect 命中变化行并返回整行文本', async () => {
    const h = makeHost({ lines: [['root@host:~#'], ['backup DONE rc=0']] })
    await runQuickScript(
      "const s=t.session(); const hit = await s.expect('DONE', 2000); t.log(String(hit))",
      h.host,
    )
    expect(h.logs).toEqual(['backup DONE rc=0'])
  })

  it('expect 支持正则，静默旧屏不误命中、超时返回 null', async () => {
    const h = makeHost({ lines: [['PING 10.0.0.1 56(84) bytes of data.']] })
    await runQuickScript(
      "const s=t.session(); const hit = await s.expect(/time=\\d+/, 300); t.log(String(hit))",
      h.host,
    )
    expect(h.logs).toEqual(['null'])
  })

  it('脚本 return 提前结束且不报错', async () => {
    const h = makeHost({})
    await runQuickScript("if (!t.session('不存在')) { t.log('no session'); return } t.log('bad')", h.host)
    expect(h.logs).toEqual(['no session'])
  })

  it('协作取消：sleep 节拍检查 cancelled 抛 ScriptAbort', async () => {
    let stop = false
    const h = makeHost({ cancelled: () => stop })
    setTimeout(() => { stop = true }, 80)
    await expect(runQuickScript('await t.sleep(5000)', h.host)).rejects.toBeInstanceOf(ScriptAbort)
  })

  it('全局超时：deadline 过后等待节拍抛 ScriptAbort', async () => {
    const h = makeHost({ deadlineMs: 120 })
    await expect(runQuickScript('await t.sleep(5000)', h.host)).rejects.toBeInstanceOf(ScriptAbort)
  })

  it('运行时异常原样抛给宿主', async () => {
    const h = makeHost({})
    await expect(runQuickScript("throw new Error('boom')", h.host)).rejects.toThrow('boom')
  })

  it('sessions() 枚举注入列表', async () => {
    const h = makeHost({})
    await runQuickScript('t.log(t.sessions().map(s => s.name).join(","))', h.host)
    expect(h.logs).toEqual(['test-host'])
  })
})
