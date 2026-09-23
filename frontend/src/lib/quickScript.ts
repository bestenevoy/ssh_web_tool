/**
 * 快捷指令 JS 脚本运行时（Xshell「动态对象」式流程控制，初版）
 *
 * 脚本用 JavaScript 编写（async 函数体，可直接 await），通过注入的 t API
 * 操作 shell 终端：发送命令、等待输出特征、读屏幕缓冲、条件分支/循环。
 * 本模块刻意与 React/xterm 解耦——宿主（App）注入 QuickScriptHost，这里只做
 * 调度与等待语义，可脱离界面单测。
 *
 * 语义约定：
 * - expect：匹配"调用之后屏幕上出现/变化的行"（逐行索引比对快照），命中返回
 *   该行文本，超时返回 null；不消费输出流，纯轮询缓冲（低频、实现简单）
 * - 协作式取消：sleep/expect 的轮询间隔检查 cancelled()（同一脚本再次点击执行
 *   = 停止）；deadline() 全局超时（防跑飞）
 * - 脚本内 return/异常 → resolve/reject 回宿主，宿主转成状态提示
 */

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
}

/** 脚本 API（注入给脚本代码的第一个参数 t） */
export interface ScriptApi {
  /** 取会话：省略 = 当前活动会话；参数 = session_id / 名称 / 主机 精确匹配 */
  session(idOrName?: string): ScriptSession | null
  /** 全部会话摘要（供脚本按名字挑目标） */
  sessions(): Array<{ id: string; name: string }>
  sleep(ms: number): Promise<void>
  log(msg: string): void
}

/** 宿主注入面（App 实现；测试用假实现） */
export interface QuickScriptHost {
  /** 解析会话（undefined = 活动会话）；找不到返回 null */
  resolve(idOrName?: string): Omit<ScriptSession, 'expect'> | null
  list(): Array<{ id: string; name: string }>
  activeId(): string | null
  log(msg: string): void
  /** 协作取消：sleep/expect 轮询点检查 */
  cancelled(): boolean
  /** 全局截止时刻（epoch ms）：到点各等待节拍抛 ScriptAbort */
  deadline(): number
}

/** 脚本超时/停止（协作式中断，宿主按"停止"提示而非"错误"展示） */
export class ScriptAbort extends Error {}

export const SCRIPT_DEFAULT_EXPECT_MS = 15_000
export const SCRIPT_MAX_MS = 120_000
const POLL_MS = 200

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

/** 给会话绑定 expect 的等待语义（基于宿主 lines + 协作节拍） */
function bindSession(s: Omit<ScriptSession, 'expect'>, host: QuickScriptHost): ScriptSession {
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
  }
}

/** 运行脚本；异常（含 ScriptAbort）抛给宿主处理 */
export async function runQuickScript(code: string, host: QuickScriptHost): Promise<void> {
  const fn = compileScript(code)
  const t: ScriptApi = {
    session: (idOrName) => {
      const sid = idOrName ?? host.activeId() ?? undefined
      const raw = host.resolve(sid ?? undefined)
      return raw ? bindSession(raw, host) : null
    },
    sessions: host.list,
    sleep: (ms) => waitPump(Math.max(0, Math.min(ms, SCRIPT_MAX_MS)), host),
    log: host.log,
  }
  await fn(t)
}
