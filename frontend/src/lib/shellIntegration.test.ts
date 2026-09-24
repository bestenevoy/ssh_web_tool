/**
 * S1 shell 集成解析单元测试：纯解析函数 + attach 分发（fake parser）。
 */
import { describe, expect, it, vi } from 'vitest'
import { attachShellIntegration, decodeB64Utf8, parseShellIntegrationOsc } from './shellIntegration'

// UTF-8 安全的 base64（与被测的 decodeB64Utf8 对称；不依赖 Node 类型声明）
const b64 = (s: string) => btoa(String.fromCharCode(...new TextEncoder().encode(s)))

describe('parseShellIntegrationOsc', () => {
  it('133;A → prompt（含带参变体 A;cmd）', () => {
    expect(parseShellIntegrationOsc(133, 'A')).toEqual({ kind: 'prompt' })
    expect(parseShellIntegrationOsc(133, 'A;ls')).toEqual({ kind: 'prompt' })
  })

  it('133;D;<rc> → exit（0/非 0/负数回显均按整数解析）', () => {
    expect(parseShellIntegrationOsc(133, 'D;0')).toEqual({ kind: 'exit', exitCode: 0 })
    expect(parseShellIntegrationOsc(133, 'D;127')).toEqual({ kind: 'exit', exitCode: 127 })
    expect(parseShellIntegrationOsc(133, 'D;abc')).toBeNull()
    expect(parseShellIntegrationOsc(133, 'D')).toBeNull()
  })

  it('633;E;<base64> → command（UTF-8 中文往返）', () => {
    expect(parseShellIntegrationOsc(633, `E;${b64('ls -la')}`)).toEqual({ kind: 'command', command: 'ls -la' })
    expect(parseShellIntegrationOsc(633, `E;${b64('cd 目录/文件')}`)).toEqual({
      kind: 'command',
      command: 'cd 目录/文件',
    })
    // 前导 TAB（fc 输出原样）保留在事件里，由记录方 trim
    expect(parseShellIntegrationOsc(633, `E;${b64('\techo hi')}`)).toEqual({ kind: 'command', command: '\techo hi' })
  })

  it('633;E 非法 base64 → null（宁丢一条不误记）', () => {
    expect(parseShellIntegrationOsc(633, 'E;!!!not base64!!!')).toBeNull()
  })

  it('633;P;Cwd → 路径（可含空格）；633;SI → ready', () => {
    expect(parseShellIntegrationOsc(633, 'P;Cwd=/var/log')).toEqual({ kind: 'cwd', cwd: '/var/log' })
    expect(parseShellIntegrationOsc(633, 'P;Cwd=/home/a b/c')).toEqual({ kind: 'cwd', cwd: '/home/a b/c' })
    expect(parseShellIntegrationOsc(633, 'SI;ready')).toEqual({ kind: 'ready' })
    expect(parseShellIntegrationOsc(633, 'SI')).toEqual({ kind: 'ready' })
  })

  it('未知子类型/其他 OSC 码 → null', () => {
    expect(parseShellIntegrationOsc(133, 'B')).toBeNull()
    expect(parseShellIntegrationOsc(633, 'A;whatever')).toBeNull()
    expect(parseShellIntegrationOsc(7, 'P;Cwd=/tmp')).toBeNull()
  })
})

describe('decodeB64Utf8', () => {
  it('合法输入往返、非法输入 null', () => {
    expect(decodeB64Utf8(b64('hello 世界 🌍'))).toBe('hello 世界 🌍')
    expect(decodeB64Utf8('@#$ invalid')).toBeNull()
  })
})

describe('attachShellIntegration 分发', () => {
  function makeFakeTerm() {
    const osc: Record<number, (data: string) => boolean> = {}
    const term = {
      parser: {
        registerOscHandler: (code: number, fn: (data: string) => boolean) => {
          osc[code] = fn
          return { dispose: () => { delete osc[code] } }
        },
      },
    }
    return { term: term as unknown as import('@xterm/xterm').Terminal, osc }
  }

  it('SI/A/P 事件都触发 onReady（无 SI 回执的主题也能激活）', () => {
    const f = makeFakeTerm()
    const onReady = vi.fn()
    const d = attachShellIntegration(f.term, { onReady })
    expect(Object.keys(f.osc).sort()).toEqual(['133', '633'])
    f.osc[633]('SI;ready')
    expect(onReady).toHaveBeenCalledTimes(1)
    f.osc[133]('A')
    f.osc[633]('P;Cwd=/tmp')
    expect(onReady).toHaveBeenCalledTimes(3)
    d.dispose()
    expect(Object.keys(f.osc)).toEqual([])
  })

  it('命令/退出码分发与未知序列放行（返回 false）', () => {
    const f = makeFakeTerm()
    const onCommand = vi.fn()
    const onExit = vi.fn()
    attachShellIntegration(f.term, { onCommand, onExit })
    expect(f.osc[633](`E;${b64('ls')}`)).toBe(true)
    expect(onCommand).toHaveBeenCalledWith('ls')
    expect(f.osc[133]('D;2')).toBe(true)
    expect(onExit).toHaveBeenCalledWith(2)
    // 本集成不认识的子类型：放行给其他消费者，不误触发回调
    expect(f.osc[133]('P;1;2;3')).toBe(false)
    expect(f.osc[633]('Vc;foo')).toBe(false)
    expect(onCommand).toHaveBeenCalledTimes(1)
    expect(onExit).toHaveBeenCalledTimes(1)
  })
})
