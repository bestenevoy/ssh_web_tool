/**
 * 快捷指令 JS 脚本运行时（Xshell「动态对象」式流程控制）
 *
 * 脚本用 JavaScript 编写（async 函数体，可直接 await），通过注入的 t API
 * 操作 shell 终端：发送命令、等待输出特征、读屏幕缓冲、条件分支/循环。
 * v2 扩展：会话连接状态检测（state/connected）、会话重连/断开（t.reconnect/
 * t.disconnect）、命令执行闭环（s.run：注入退出码探针 echo "__Q…_RC__:$?"，
 * 等待标记行→返回 {ok, code, output}）、静默判定（s.waitIdle）。
 * 本模块刻意与 React/xterm 解耦——宿主（App）注入 QuickScriptHost，这里只做
 * 调度与等待语义，可脱离界面单测。
 *
 * 语义约定：
 * - expect：匹配"调用之后屏幕上出现/变化的行"（逐行索引比对快照），命中返回
 *   该行文本，超时返回 null；不消费输出流，纯轮询缓冲（低频、实现简单）
 * - run：POSIX shell（bash/zsh 等）退出码探针闭环，命令须单行；probe:false 时
 *   退化为 send + 可选 expect 完成判定（交互/长任务），不产生 code
 * - 协作式取消：sleep/expect/run/waitIdle 的轮询间隔检查 cancelled()（同一脚本
 *   再次点击执行 = 停止）；deadline() 全局超时（防跑飞）
 * - 脚本内 return/异常 → resolve/reject 回宿主，宿主转成状态提示
 */

/** 会话连接状态（宿主从 ws 就绪态 + 前端断线/重连标记计算） */
export type ScriptSessionState = 'open' | 'connecting' | 'disconnected' | 'closed' | 'unknown'

/** 会话摘要（t.sessions() 返回项） */
export interface ScriptSessionInfo {
  id: string
  name: string
  state: ScriptSessionState
}

/** s.run 选项 */
export interface ScriptRunOptions {
  /** 等待命令完成的上限（默认 30s，仍受全局 120s deadline 截断） */
  timeoutMs?: number
  /** 是否注入退出码探针（默认 true；cmd 含换行时自动禁用）。false = 只等 expect */
  probe?: boolean
  /** 非探针模式的完成判定：等待匹配行出现（探针模式下忽略） */
  expect?: string | RegExp
  /** 输出采集窗口（屏幕末尾 n 行，默认 500，上限 2000） */
  lines?: number
}

/** s.run 返回值 */
export interface ScriptRunResult {
  /** 探针模式：退出码为 0；expect 模式：命中标记行；超时/发送失败 = false */
  ok: boolean
  /** 退出码（非探针模式或超时为 null） */
  code: number | null
  /** 命令回显与退出标记之间的输出行（尽力采集，长输出滚屏后只保尾部窗口） */
  output: string[]
  /** 完成行原文（退出标记行 / expect 命中行；超时 null） */
  matched: string | null
}

/** 单个会话的脚本视图（由宿主绑定到具体终端实例） */
export interface ScriptSession {
  id: string
  name: string
  /** 发送命令（execute=true 追加回车立即执行）；返回 false 表示会话不可用 */
  send(cmd: string, execute?: boolean): boolean
  /** 读屏幕缓冲末尾 n 行（含光标行；已去尾部空行由宿主决定） */
  lines(n?: number): string[]
  /** 等待字符串（子串）或正则出现在新输出中；命中返回整行文本，超时 null */
  expect(pattern: string | RegExp, timeoutMs?: number): Promise<string | null>
  /** 当前连接状态 */
  state(): ScriptSessionState
  /** 是否处于可发送状态（state()==='open'） */
  connected(): boolean
  /** 发送并等待执行完成（退出码探针闭环）；返回 {ok, code, output, matched} */
  run(cmd: string, opts?: ScriptRunOptions): Promise<ScriptRunResult>
  /** 等待屏幕停止变化（连续 idleMs 无新输出=命令跑完的启发式）；超时 false */
  waitIdle(idleMs?: number, timeoutMs?: number): Promise<boolean>
}

/** 脚本 API（注入给脚本代码的第一个参数 t） */
export interface ScriptApi {
  /** 取会话：省略 = 当前活动会话；参数 = session_id / 名称 / 主机 精确匹配 */
  session(idOrName?: string): ScriptSession | null
  /** 全部会话摘要（供脚本按名字挑目标） */
  sessions(): ScriptSessionInfo[]
  /** 重连目标会话（复用统一重连流程；无凭据时会弹密码框，取消=失败）；成功 true */
  reconnect(idOrName?: string): Promise<boolean>
  /** 主动断开目标会话（切回本机 shell，会话保留可重连）；执行 true */
  disconnect(idOrName?: string): Promise<boolean>
  sleep(ms: number): Promise<void>
  log(msg: string): void
}

/** 宿主注入面（App 实现；测试用假实现） */
export interface QuickScriptHost {
  /** 解析会话（undefined = 活动会话）；找不到返回 null */
  resolve(idOrName?: string): Omit<ScriptSession, 'expect' | 'run' | 'waitIdle' | 'connected'> | null
  list(): ScriptSessionInfo[]
  activeId(): string | null
  log(msg: string): void
  /** 重连指定会话，成功返回 true（宿主可阻塞到连接结果落定） */
  reconnect(id: string): Promise<boolean>
  /** 断开指定会话（保留会话可重连） */
  disconnect(id: string): Promise<boolean>
  /** 协作取消：sleep/expect 轮询点检查 */
  cancelled(): boolean
  /** 全局截止时刻（epoch ms）：到点各等待节拍抛 ScriptAbort */
  deadline(): number
}

/** 脚本超时/停止（协作式中断，宿主按"停止"提示而非"错误"展示） */
export class ScriptAbort extends Error {}

export const SCRIPT_DEFAULT_EXPECT_MS = 15_000
export const SCRIPT_DEFAULT_RUN_MS = 30_000
export const SCRIPT_MAX_MS = 120_000
const POLL_MS = 200
/** 退出码探针标记前缀（后接每次运行独立的随机 id，防旧屏残留误命中） */
const RC_MARK_PREFIX = '__Q'

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function checkAbort(host: QuickScriptHost): void {
  if (host.cancelled()) throw new ScriptAbort('脚本已停止')
  if (Date.now() >= host.deadline()) throw new ScriptAbort('脚本超过 120s 全局超时')
}

/** 分片等待（每 POLL_MS 检查一次取消/超时），返回 false 表示等待被外部条件终止 */
async function waitPump(ms: number, host: QuickScriptHost): Promise<void> {
  const until = Date.now() + ms
  for (;;) {
    checkAbort(host)
    const left = until - Date.now()
    if (left <= 0) return
    await new Promise((r) => setTimeout(r, Math.min(POLL_MS, left)))
  }
}

/** 编译脚本（new Function 包 async 体）；语法错误抛 SyntaxError 供保存时校验 */
export function compileScript(code: string): (t: ScriptApi) => Promise<unknown> {
  // eslint-disable-next-line no-new-func
  const fn = new Function('t', `"use strict";return (async () => {\n${code}\n})()`)
  return fn as (t: ScriptApi) => Promise<unknown>
}

/** 从输出采集窗口里切出"命令回显之后、退出标记之前"的行 */
function extractOutput(now: string[], markerIdx: number, cmd: string): string[] {
  const sig = cmd.trim().split('\n')[0].slice(0, 40)
  let start = 0
  if (sig) {
    for (let i = markerIdx - 1; i >= 0; i--) {
      if (now[i].includes(sig)) {
        start = i + 1
        break
      }
    }
  }
  const out = now.slice(start, markerIdx)
  while (out.length && !out[out.length - 1].trim()) out.pop()
  return out
}

/** 给会话绑定 expect/run/waitIdle 的等待语义（基于宿主 lines + 协作节拍） */
function bindSession(
  s: Omit<ScriptSession, 'expect' | 'run' | 'waitIdle' | 'connected'>,
  host: QuickScriptHost,
): ScriptSession {
  const tail = (n: number) => s.lines(n)
  return {
    ...s,
    expect: async (pattern: string | RegExp, timeoutMs = SCRIPT_DEFAULT_EXPECT_MS) => {
      const re = typeof pattern === 'string' ? new RegExp(escapeRegExp(pattern)) : pattern
      const until = Math.min(Date.now() + timeoutMs, host.deadline())
      const before = s.lines(200)
      for (;;) {
        checkAbort(host)
        const now = s.lines(200)
        for (let i = 0; i < now.length; i++) {
          const line = now[i]
          if (!line || line === before[i]) continue
          // 只认"与快照相比变化/新出现"的行：滚屏后索引整体平移，旧行也算变化，
          // 但对等待新输出的常见用法（等完成串/提示符）语义足够且不会命中静默旧屏
          if (re.test(line)) return line
        }
        if (Date.now() >= until) return null
        await waitPump(POLL_MS, host)
      }
    },
    connected: () => s.state() === 'open',
    run: async (cmd: string, opts: ScriptRunOptions = {}) => {
      const empty: ScriptRunResult = { ok: false, code: null, output: [], matched: null }
      const width = Math.max(50, Math.min(opts.lines ?? 500, 2000))
      // 探针：单行命令 + probe 未禁用；发 `${cmd}; echo "标记:$?"`，标记行出现即完成
      const useProbe = opts.probe !== false && !cmd.includes('\n')
      const marker = useProbe ? `${RC_MARK_PREFIX}${Math.floor(Math.random() * 1e6)}_RC__:` : ''
      const markerRe = useProbe ? new RegExp(escapeRegExp(marker) + '(-?\\d+)') : null
      const expectRe =
        !useProbe && opts.expect
          ? opts.expect instanceof RegExp
            ? opts.expect
            : new RegExp(escapeRegExp(opts.expect))
          : null
      const fullCmd = useProbe ? `${cmd}; echo "${marker}$?"` : cmd
      if (!s.send(fullCmd, true)) return empty
      const until = Math.min(Date.now() + (opts.timeoutMs ?? SCRIPT_DEFAULT_RUN_MS), host.deadline())
      for (;;) {
        checkAbort(host)
        const now = tail(width)
        for (let i = 0; i < now.length; i++) {
          const line = now[i]
          if (!line) continue
          if (markerRe) {
            // 命令回显行含字面 `标记:$?`（无数字）不会误命中；命中的只会是 echo 输出
            const m = markerRe.exec(line)
            if (m) {
              return { ok: m[1] === '0', code: Number(m[1]), output: extractOutput(now, i, cmd), matched: line }
            }
            continue
          }
          if (expectRe && expectRe.test(line)) {
            return { ok: true, code: null, output: extractOutput(now, i, cmd), matched: line }
          }
        }
        if (Date.now() >= until) return empty
        await waitPump(POLL_MS, host)
      }
    },
    waitIdle: async (idleMs = 1000, timeoutMs = 30_000) => {
      const until = Math.min(Date.now() + timeoutMs, host.deadline())
      let last = tail(80).join('\n')
      let quietSince = Date.now()
      for (;;) {
        checkAbort(host)
        await waitPump(POLL_MS, host)
        const cur = tail(80).join('\n')
        if (cur !== last) {
          last = cur
          quietSince = Date.now()
        } else if (Date.now() - quietSince >= idleMs) {
          return true
        }
        if (Date.now() >= until) return false
      }
    },
  }
}

/** 运行脚本；异常（含 ScriptAbort）抛给宿主处理 */
export async function runQuickScript(code: string, host: QuickScriptHost): Promise<void> {
  const fn = compileScript(code)
  const resolveBound = (idOrName?: string): ScriptSession | null => {
    const sid = idOrName ?? host.activeId() ?? undefined
    const raw = host.resolve(sid ?? undefined)
    return raw ? bindSession(raw, host) : null
  }
  const targetId = (idOrName?: string): string | null => {
    const info = idOrName ? host.list().find((x) => x.id === idOrName || x.name === idOrName || x.id.startsWith(idOrName)) : null
    if (idOrName && !info) return null
    return (info?.id ?? (idOrName ?? host.activeId())) ?? null
  }
  const t: ScriptApi = {
    session: resolveBound,
    sessions: host.list,
    reconnect: async (idOrName) => {
      const id = targetId(idOrName)
      return id ? host.reconnect(id) : false
    },
    disconnect: async (idOrName) => {
      const id = targetId(idOrName)
      return id ? host.disconnect(id) : false
    },
    sleep: (ms) => waitPump(Math.max(0, Math.min(ms, SCRIPT_MAX_MS)), host),
    log: host.log,
  }
  await fn(t)
}
