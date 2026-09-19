import type { TerminalInstance } from './terminalInstance'
import type { Host } from '../types'

// 会话列表 / tab 栏共用的排序逻辑：
// - 组 key：本地终端 '__local__'；本地终端 ssh 拦截的临时会话 '__tmp__'；
//   已保存主机会话 'ip:port'（同 ip:port 不同登录用户允许共存，组内条目以 username@host 区分）
// - 组间顺序：自定义拖拽顺序（localStorage）优先，未收录的组按组 key 升序
// - 组内顺序：会话创建顺序（terminals 迭代序）
// - tab 栏与会话列表使用同一份排序结果，保证两边顺序一致

export const ORDER_KEY = 'ssh-web-tool-session-order'

export function loadOrder(): string[] {
  try {
    const raw = localStorage.getItem(ORDER_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function saveOrder(order: string[]): void {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(order))
  } catch {
    /* localStorage 不可用时忽略（排序仅在当前会话生效） */
  }
}

/** 会话所属组 key：本地 / ssh 链接（本地终端 ssh 命令建立的会话，独立）/ 已保存主机 ip:port */
export function groupKeyOf(t: TerminalInstance, hostMap: Map<string, Host>): string {
  if (t.type === 'local') return '__local__'
  // 本地终端 ssh 链接建立的会话：host_id 为空（不属于任何已保存主机会话），
  // 独立成组；兼容历史数据（早期此类会话可能未带 ssh_conn 记录）
  if (!t.host_id || t.ssh_conn) return '__tmp__'
  const h = hostMap.get(t.host_id)
  const ip = h?.host || t.host_name
  const port = h?.port ?? 22
  return `${ip}:${port}`
}

/** 组显示名：本地终端 / ssh 链接 / 主机名（无则 ip） */
export function groupLabelOf(key: string, hostMap: Map<string, Host>): string {
  if (key === '__local__') return '本地终端'
  if (key === '__tmp__') return 'SSH 链接'
  // key = ip:port，找到第一个匹配该组的主机名
  for (const h of hostMap.values()) {
    const ip = h.host
    const port = h.port ?? 22
    if (`${ip}:${port}` === key) return h.name || h.host
  }
  return key
}

/** 按组排序后的会话 id 列表（tab 栏与会话列表共用，保证顺序一致） */
export function sortSessionIds(
  terminals: Map<string, TerminalInstance>,
  hostMap: Map<string, Host>,
  customOrder: string[],
): string[] {
  const groups = new Map<string, string[]>()
  for (const t of terminals.values()) {
    const k = groupKeyOf(t, hostMap)
    const arr = groups.get(k)
    if (arr) arr.push(t.session_id)
    else groups.set(k, [t.session_id])
  }
  const keys = Array.from(groups.keys())
  const pos = new Map(customOrder.map((k, i) => [k, i]))
  keys.sort((a, b) => {
    const pa = pos.get(a)
    const pb = pos.get(b)
    if (pa !== undefined && pb !== undefined) return pa - pb
    if (pa !== undefined) return -1
    if (pb !== undefined) return 1
    return a.localeCompare(b)
  })
  return keys.flatMap((k) => groups.get(k)!)
}
