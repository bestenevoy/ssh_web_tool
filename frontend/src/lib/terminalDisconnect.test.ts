import { describe, expect, it } from 'vitest'
import { markDisconnected, sessionConnState } from './terminalDisconnect'

interface FakeTerm {
  disconnected: boolean
  ws: unknown | null
  name?: string
}

describe('markDisconnected', () => {
  it('设置 disconnected=true 且 ws=null，保留其他字段', () => {
    let map = new Map<string, FakeTerm>([
      ['s1', { disconnected: false, ws: {}, name: '主机A' }],
    ])
    markDisconnected<FakeTerm>('s1', (fn) => {
      map = fn(map)
    })
    const cur = map.get('s1')!
    expect(cur.disconnected).toBe(true)
    expect(cur.ws).toBeNull()
    expect(cur.name).toBe('主机A') // 其他字段保留（内容不清）
  })

  it('不向终端写入任何红字/提示内容（函数无 term 参数、无 write 调用）', () => {
    // 本函数签名不再接收 term，结构上不可能写红字——验证调用后状态集仅含断开标记
    let map = new Map<string, FakeTerm>([['s1', { disconnected: false, ws: {} }]])
    let updaterCalls = 0
    markDisconnected<FakeTerm>('s1', (fn) => {
      updaterCalls++
      map = fn(map)
    })
    expect(updaterCalls).toBe(1)
    expect(map.get('s1')!.disconnected).toBe(true)
  })

  it('已断开状态不重复更新', () => {
    let map = new Map<string, FakeTerm>([['s1', { disconnected: true, ws: null }]])
    let updaterCalls = 0
    markDisconnected<FakeTerm>('s1', (fn) => {
      updaterCalls++
      map = fn(map)
    })
    expect(updaterCalls).toBe(1)
    expect(map.get('s1')!.disconnected).toBe(true)
  })

  it('不存在的会话不报错', () => {
    let map = new Map<string, FakeTerm>()
    expect(() =>
      markDisconnected<FakeTerm>('none', (fn) => {
        map = fn(map)
      }),
    ).not.toThrow()
    expect(map.size).toBe(0)
  })
})

describe('sessionConnState', () => {
  const base = { disconnected: false, ssh_exited: false, pending: false, reconnecting: false }

  it('全 false = 已连接（本机终端/正常 SSH）', () => {
    expect(sessionConnState(base)).toBe('connected')
  })

  it('pending 或 reconnecting = 连接中（优先于其他标记）', () => {
    expect(sessionConnState({ ...base, pending: true })).toBe('connecting')
    expect(sessionConnState({ ...base, reconnecting: true })).toBe('connecting')
  })

  it('disconnected = 已断开', () => {
    expect(sessionConnState({ ...base, disconnected: true })).toBe('disconnected')
  })

  it('ssh_exited（主动断开自动切回本机 shell）也算已断开——只看 disconnected 会误标绿', () => {
    expect(sessionConnState({ ...base, ssh_exited: true })).toBe('disconnected')
  })
})
