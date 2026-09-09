/**
 * 终端复制/粘贴独立模块（与 React 状态解耦，便于单元测试）。
 *
 * 双粘贴根因：keydown 里 preventDefault + term.paste 的路径，在个别浏览器/输入法
 * 环境下 paste 事件仍会触发，xterm 原生 handlePasteEvent 再粘贴一次 → 共两次。
 * 修复：keydown 只 return false（阻止 xterm 发送 ^V），不 preventDefault（让浏览器
 * 产生 paste 事件）；粘贴统一由 textarea capture 阶段拦截处理，单一路径。
 */
import type { Terminal } from 'xterm'

// 终端复制：选中内容立即复制；Ctrl+C 有选区时复制（不发送 SIGINT 到远端）
function copySelection(term: Terminal) {
  const sel = term.getSelection()
  if (!sel) return
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(sel).catch(() => {
      // clipboard API 被拒绝时兜底：用文本域 execCommand
      try {
        const ta = document.createElement('textarea')
        ta.value = sel
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      } catch { /* ignore */ }
    })
  } else {
    try {
      const ta = document.createElement('textarea')
      ta.value = sel
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    } catch { /* ignore */ }
  }
}

export function setupTerminalCopy(term: Terminal, onAltR?: () => void) {
  // 1) Ctrl+C：有选区时复制并阻止发送（避免打断远端正在运行的命令）
  //    Ctrl+V：不 preventDefault（让浏览器产生 paste 事件），return false 阻止 xterm
  //    把 Ctrl+V 当作按键（^V）发送到终端；粘贴统一由下方 textarea capture 阶段处理
  term.attachCustomKeyEventHandler((e) => {
    if (e.ctrlKey && (e.key === 'c' || e.key === 'C')) {
      if (term.hasSelection()) {
        copySelection(term)
        return false  // 阻止 xterm 把 Ctrl+C 发送到终端
      }
    }
    if (e.altKey && (e.key === 'r' || e.key === 'R')) {
      // Alt+R：打开全局命令搜索；return false 阻止 xterm 把 Alt+R 发送到终端
      if (onAltR) onAltR()
      return false
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'v' || e.key === 'V')) {
      // 返回 false：xterm 不把 Ctrl+V 发送到终端；不 preventDefault，让 paste 事件产生，
      // 由下方 textarea capture 监听统一完成粘贴（单一路径，杜绝双粘贴）
      return false
    }
    return true
  })
  // 2) 统一粘贴入口：textarea 的 paste 事件在 capture 阶段拦截（先于 xterm 内部监听），
  //    preventDefault + stopImmediatePropagation 保证 xterm 的 handlePasteEvent 不会二次粘贴；
  //    读取纯文本（自动清除格式）。Ctrl+V / Ctrl+Shift+V / 右键粘贴都走这一条路径
  const ta = term.textarea
  if (ta && !(ta as any).__wstoolPasteBound) {
    ;(ta as any).__wstoolPasteBound = true
    ta.addEventListener('paste', (e: ClipboardEvent) => {
      e.preventDefault()
      e.stopImmediatePropagation()
      const text = e.clipboardData?.getData('text/plain')
      if (text) term.paste(text)
    }, { capture: true })
  }
}
