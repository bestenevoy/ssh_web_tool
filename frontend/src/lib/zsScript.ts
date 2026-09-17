// .zs 脚本解析与播放引擎（纯逻辑层，可单测）。
//
// 语法：
//   - `%%` 或 `%% 块名`：块分隔（块名可选）；文件开头到第一个 %% 之间是隐式首块
//   - `#` 开头的行：注释，播放时跳过
//   - `@key` / `@名称`：引用快捷指令（key 优先，其次名称；大小写不敏感）
//   - `@sleep N`：等待 N 秒（支持小数）
//   - 其余行：原样作为命令发送
//
// 硬性规则：**.zs 不允许引用带参数（param 型）的快捷指令**，解析（标红）与
// 播放（执行前检查）双重校验，播放引擎在发送任何一行前先整块检查。

import type { QuickCommand } from '../types'

/** 块内一行：text 原始文本、line 为 0 起始的文档行号 */
export interface ZsLine {
  text: string
  line: number
}

export interface ZsBlock {
  name: string | null
  /** 块首行（%% 分隔行本身，或隐式首块的第一个可执行行），0 起始行号 */
  startLine: number
  /** 可执行行（已剔除空行/注释/%% 行） */
  lines: ZsLine[]
}

export interface ZsFile {
  blocks: ZsBlock[]
}

/** 解析 .zs 内容为块列表。空文件返回空 blocks。 */
export function parseZs(content: string): ZsFile {
  const blocks: ZsBlock[] = []
  let cur: ZsBlock | null = null
  content.split('\n').forEach((text, i) => {
    const t = text.trim()
    if (t.startsWith('%%')) {
      const name = t.slice(2).trim()
      cur = { name: name || null, startLine: i, lines: [] }
      blocks.push(cur)
      return
    }
    if (!t || t.startsWith('#')) return // 空行/注释不进可执行序列
    if (!cur) {
      cur = { name: null, startLine: i, lines: [] }
      blocks.push(cur)
    }
    cur.lines.push({ text, line: i })
  })
  return { blocks }
}

export type ZsStep =
  | { kind: 'send'; cmd: string; line: number; source: string | null }
  | { kind: 'sleep'; seconds: number; line: number }

export type ZsStepResult = ZsStep | { kind: 'error'; message: string; line: number }

/**
 * 解析单行为播放步骤。@ 引用按 key（大小写不敏感）优先、名称（精确→大小写
 * 不敏感）次之匹配快捷指令；param 型指令直接报错（.zs 不允许带参数指令）。
 */
export function resolveZsStep(text: string, line: number, quickCommands: QuickCommand[]): ZsStepResult {
  const t = text.trim()
  if (!t.startsWith('@')) {
    return { kind: 'send', cmd: text, line, source: null }
  }
  const body = t.slice(1).trim()
  if (!body) {
    return { kind: 'error', message: '空的 @ 引用', line }
  }
  const m = /^sleep(?:\s+(\S+))?\s*$/i.exec(body)
  if (m) {
    const numStr = (m[1] ?? '').trim()
    if (!numStr) return { kind: 'error', message: '@sleep 缺少秒数（如 @sleep 2）', line }
    const n = Number(numStr)
    if (!Number.isFinite(n) || n < 0) return { kind: 'error', message: `@sleep 秒数无效: ${numStr}`, line }
    return { kind: 'sleep', seconds: n, line }
  }
  // @key / @名称：key 优先（唯一、大小写不敏感），名称其次
  const lower = body.toLowerCase()
  const byKey = quickCommands.find((q) => (q.key ?? '').toLowerCase() === lower)
  const byName =
    quickCommands.find((q) => q.name === body) ?? quickCommands.find((q) => q.name.toLowerCase() === lower)
  const qc = byKey ?? byName
  if (!qc) {
    return { kind: 'error', message: `未找到快捷指令: @${body}`, line }
  }
  if (qc.type === 'param') {
    // 硬性规则：param 型指令需要人工输入参数，禁止在 .zs 中自动播放
    return { kind: 'error', message: `不允许引用带参数的指令: ${qc.name}`, line }
  }
  return { kind: 'send', cmd: qc.command, line, source: qc.name }
}

export interface ZsError {
  line: number // 0 起始行号
  message: string
}

/** 全文校验：返回错误行列表（用于编辑器标红）。仅校验可执行行。 */
export function validateZs(content: string, quickCommands: QuickCommand[]): ZsError[] {
  const errors: ZsError[] = []
  content.split('\n').forEach((text, i) => {
    const t = text.trim()
    if (!t || t.startsWith('#') || t.startsWith('%%')) return
    const r = resolveZsStep(text, i, quickCommands)
    if (r.kind === 'error') errors.push({ line: i, message: r.message })
  })
  return errors
}

/** 行间发送间隔（毫秒） */
export const ZS_LINE_INTERVAL_MS = 300

/** 播放取消句柄：外部置 cancelled = true 即在下一个步骤边界停止 */
export interface ZsPlayHandle {
  cancelled: boolean
}

const sleepMs = (ms: number, handle: ZsPlayHandle): Promise<void> =>
  new Promise((resolve) => {
    let left = ms
    const tick = () => {
      if (handle.cancelled || left <= 0) {
        resolve()
        return
      }
      const chunk = Math.min(100, left)
      left -= chunk
      setTimeout(tick, chunk)
    }
    tick()
  })

export interface ZsPlayOptions {
  /** 向指定会话发送命令（execute=true 追加换行立即执行）；返回 false 表示会话不可用 */
  send: (session_id: string, cmd: string, execute: boolean) => boolean
  onStatus: (msg: string) => void
  cancel: ZsPlayHandle
}

/**
 * 播放一个块：广播到全部选中会话。
 *
 * 执行前检查：先整块解析全部行，任何错误（param 型引用 / 未知指令 / @sleep
 * 格式错误）都直接拒绝播放，不发送任何内容；播放中每步再校验取消标记。
 * 播放语义：同一行先广播到所有会话，再统一等待（间隔或 @sleep），保证各会话
 * 节奏一致。
 *
 * 返回是否完整播放成功。
 */
export async function playZsBlock(
  block: ZsBlock,
  quickCommands: QuickCommand[],
  sessionIds: string[],
  opts: ZsPlayOptions
): Promise<boolean> {
  const blockLabel = block.name ? `「${block.name}」` : ''
  if (sessionIds.length === 0) {
    opts.onStatus('播放中止：请先选择广播目标会话（＋ 添加）')
    return false
  }
  // ---- 执行前检查：整块解析，有任何错误拒绝播放 ----
  const steps: ZsStep[] = []
  for (const l of block.lines) {
    const r = resolveZsStep(l.text, l.line, quickCommands)
    if (r.kind === 'error') {
      opts.onStatus(`播放中止${blockLabel}：第 ${l.line + 1} 行 ${r.message}`)
      return false
    }
    steps.push(r)
  }
  if (steps.length === 0) {
    opts.onStatus(`块${blockLabel}没有可执行的命令行`)
    return false
  }
  // ---- 播放：逐行广播 ----
  const total = steps.length
  for (let i = 0; i < steps.length; i++) {
    if (opts.cancel.cancelled) {
      opts.onStatus(`播放已取消（${i}/${total}）`)
      return false
    }
    const step = steps[i]
    if (step.kind === 'send') {
      let anySent = false
      for (const sid of sessionIds) {
        if (opts.send(sid, step.cmd, true)) anySent = true
      }
      if (!anySent) {
        opts.onStatus(`播放中止：第 ${step.line + 1} 行发送失败（目标会话连接已断开）`)
        return false
      }
      const from = step.source ? `${step.source}: ` : ''
      opts.onStatus(`播放中 (${i + 1}/${total}) ${from}${step.cmd.slice(0, 50)}`)
    } else {
      opts.onStatus(`播放中 (${i + 1}/${total}) @sleep ${step.seconds}s`)
      await sleepMs(step.seconds * 1000, opts.cancel)
    }
    if (i < steps.length - 1) {
      await sleepMs(ZS_LINE_INTERVAL_MS, opts.cancel)
    }
  }
  opts.onStatus(`块${blockLabel}播放完成（${total} 步 × ${sessionIds.length} 会话）`)
  return true
}
