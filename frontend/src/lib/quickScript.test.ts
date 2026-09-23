/**
 * quickScript 运行时单测：编译校验 / send 链路 / expect 命中与超时 / run 退出码
 * 闭环与输出采集 / waitIdle / 连接状态 / reconnect·disconnect 透传 /
 * 协作取消与全局超时 / sessions 枚举（假 host，无 React/xterm 依赖）
 */
import { describe, expect, it } from 'vitest'
import { compileScript, runQuickScript, ScriptAbort, type QuickScriptHost, type ScriptSessionState } from './quickScript'

type SentRow = [string, boolean | undefined]

interface HostOpts {
  lines?: string[][] | ((poll: number, sent: SentRow[]) => string[])
  send?: (cmd: string, execute?: boolean) => boolean
  cancelled?: () => boolean
  deadlineMs?: number
  state?: ScriptSessionState
}

function makeHost(opts: HostOpts) {
  const logs: string[] = []
  const sent: SentRow[] = []
  const reconnected: string[] = []
  const disconnected: string[] = []
  let polls = 0
  const deadlineAt = Date.now() + (opts.deadlineMs ?? 10_000)
  const state = opts.state ?? 'open'
  const host: QuickScriptHost = {
    resolve: (idOrName) => {
      // 仅认 s1 / test-host / id 前缀（模拟 App 的真实解析：未知目标返回 null）
      if (idOrName && idOrName !== 's1' && idOrName !== 'test-host' && !idOrName.startsWith('s1')) return null
      return {
        id: idOrName ?? 's1',
        name: 'test-host',
        state: () => state,
        send: (c, e = true) => {
          sent.push([c, e])
          return opts.send ? opts.send(c, e) : true
        },
        lines: (n) => {
          let snap: string[]
          if (typeof opts.lines === 'function') snap = opts.lines(polls, sent)
          else {
            const seq = opts.lines ?? [[]]
            snap = seq[Math.min(polls, seq.length - 1)]
          }
          polls++
          return snap.slice(0, n ?? 50)
        },
      }
    },
    list: () => [{ id: 's1', name: 'test-host', state }],
    activeId: () => 's1',
    log: (m) => logs.push(m),
    reconnect: async (id) => {
      reconnected.push(id)
      return true
    },
    disconnect: async (id) => {
      disconnected.push(id)
      return true
    },
    cancelled: opts.cancelled ?? (() => false),
    deadline: () => deadlineAt,
  }
  return { host, logs, sent, reconnected, disconnected }
}

/** 假屏幕：命令回显 + 一行输出 +（出现足够轮询后）退出码标记行 */
function rcScreen(rc: number | null) {
  return (_poll: number, sent: SentRow[]): string[] => {
    const last = sent[sent.length - 1]?.[0] ?? ''
    const mk = last.match(/"(__Q\d+_RC__:)\$\?"/)?.[1]
    const base = [`root@host:~$ ${last}`, 'FILE  SIZE  USED']
    return mk && rc !== null ? [...base, `${mk}${rc}`] : base
  }
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

  it('sessions() 枚举注入列表（含连接状态）', async () => {
    const h = makeHost({})
    await runQuickScript('t.log(t.sessions().map(s => s.name + ":" + s.state).join(","))', h.host)
    expect(h.logs).toEqual(['test-host:open'])
  })
})

describe('s.run 执行闭环', () => {
  it('注入退出码探针，rc=0 → ok，命令回显行（含字面 $?）不误命中', async () => {
    const h = makeHost({ lines: rcScreen(0) })
    await runQuickScript(
      "const r = await t.session().run('df -h'); t.log(JSON.stringify({ok:r.ok, code:r.code, out:r.output, m:r.matched.includes('_RC__') }))",
      h.host,
    )
    // 实际发出的命令带探针
    expect(h.sent[0][0]).toMatch(/^df -h; echo "__Q\d+_RC__:\$\?"$/)
    expect(h.logs).toEqual(['{"ok":true,"code":0,"out":["FILE  SIZE  USED"],"m":true}'])
  })

  it('非零退出码 → ok=false 且 code 透传', async () => {
    const h = makeHost({ lines: rcScreen(2) })
    await runQuickScript("const r = await t.session().run('false'); t.log(r.ok + ',' + r.code)", h.host)
    expect(h.logs).toEqual(['false,2'])
  })

  it('探针超时 → ok=false code=null', async () => {
    const h = makeHost({ lines: () => ['root@host:~$ sleep 999; echo "__Q1_RC__:$?"'] })
    await runQuickScript(
      "const r = await t.session().run('sleep 999', {timeoutMs: 300}); t.log(r.ok + ',' + r.code)",
      h.host,
    )
    expect(h.logs).toEqual(['false,null'])
  })

  it('probe:false + expect 完成判定（交互/长任务）', async () => {
    const h = makeHost({ lines: [['building...'], ['building...', 'Build Finished']] })
    await runQuickScript(
      "const r = await t.session().run('make', {probe:false, expect:/Finished/, timeoutMs:2000}); t.log(r.ok + ',' + r.code + ',' + r.matched)",
      h.host,
    )
    expect(h.logs).toEqual(['true,null,Build Finished'])
    expect(h.sent[0][0]).toBe('make')
  })

  it('send 失败（会话不可用）立即返回空结果', async () => {
    const h = makeHost({ send: () => false })
    await runQuickScript("const r = await t.session().run('ls'); t.log(JSON.stringify(r))", h.host)
    expect(h.logs).toEqual(['{"ok":false,"code":null,"output":[],"matched":null}'])
  })
})

describe('状态检测与会话控制', () => {
  it('s.state()/s.connected() 反映宿主连接状态', async () => {
    const h = makeHost({ state: 'disconnected' })
    await runQuickScript('const s = t.session(); t.log(s.state() + "/" + s.connected())', h.host)
    expect(h.logs).toEqual(['disconnected/false'])
  })

  it('t.reconnect/t.disconnect 按目标解析透传宿主', async () => {
    const h = makeHost({})
    await runQuickScript("t.log(String(await t.reconnect('test-host')))", h.host)
    await runQuickScript("t.log(String(await t.disconnect('s1')))", h.host)
    await runQuickScript("t.log(String(await t.reconnect('不存在')))", h.host)
    expect(h.logs).toEqual(['true', 'true', 'false'])
    expect(h.reconnected).toEqual(['s1'])
    expect(h.disconnected).toEqual(['s1'])
  })

  it('waitIdle：屏幕连续稳定后返回 true', async () => {
    const h = makeHost({ lines: (p) => [`frame ${Math.min(p, 4)}`] })
    await runQuickScript('const ok = await t.session().waitIdle(300, 5000); t.log(String(ok))', h.host)
    expect(h.logs).toEqual(['true'])
  })
})
