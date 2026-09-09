/**
 * 终端断开状态标记（纯逻辑，便于单测）。
 *
 * 需求约束：断开时【不向终端写入红字提示】——header「🔗 重连」按钮 + Tab 划线样式
 * 已足够提示断开状态；终端内容保持原样（用户要求：不清屏、消除红字、其他内容保留）。
 */
export function markDisconnected<T extends { disconnected: boolean }>(
  session_id: string,
  setTerminals: (fn: (prev: Map<string, T>) => Map<string, T>) => void,
): void {
  setTerminals((prev) => {
    const next = new Map(prev)
    const cur = next.get(session_id)
    if (cur && !cur.disconnected) {
      next.set(session_id, { ...cur, disconnected: true, ws: null } as T)
    }
    return next
  })
}
