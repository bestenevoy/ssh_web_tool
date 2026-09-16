/**
 * 终端复制/粘贴独立模块（与 React 状态解耦，便于单元测试）。
 *
 * 双粘贴根因：keydown 里 preventDefault + term.paste 的路径，在个别浏览器/输入法
 * 环境下 paste 事件仍会触发，xterm 原生 handlePasteEvent 再粘贴一次 → 共两次。
 * 修复：keydown 只 return false（阻止 xterm 发送 ^V），不 preventDefault（让浏览器
 * 产生 paste 事件）；粘贴统一由 textarea capture 阶段拦截处理，单一路径。
 *
 * 复制：支持「选中即复制」（copy-on-select，见 setupCopyOnSelect）与「Ctrl+C 有选区
 * 时复制」两种入口，统一走 writeClipboardText 三级 fallback：
 *   1) pywebview 原生剪贴板桥（WebView2 下 navigator.clipboard.writeText 常被
 *      安全策略/焦点要求拒绝而静默失败 → 优先原生，稳定写入系统剪贴板）
 *   2) navigator.clipboard.writeText（secure context：http(s)://127.0.0.1/localhost）
 *   3) 隐藏 textarea + document.execCommand('copy') 兜底（旧 WebView / 无 API）
 */
import type { Terminal } from '@xterm/xterm'

declare global {
  interface Window {
    pywebview?: {
      api?: {
        copy_text?: (text: string) => Promise<unknown>
      }
    }
  }
}

/** 写系统剪贴板，返回是否成功（同步尝试三级方案，均失败返回 false） */
export function writeClipboardText(text: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    // 1) pywebview 原生剪贴板桥（桌面端最可靠）。typeof 守卫：vitest/node 环境无 window
    const win = typeof window !== 'undefined' ? window : undefined
    if (win?.pywebview?.api?.copy_text) {
      win.pywebview.api.copy_text(text).then(
        () => resolve(true),
        () => resolve(false),
      )
      return
    }
    // 2) navigator.clipboard（现代安全上下文）
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(
        () => resolve(true),
        () => resolve(false),
      )
      return
    }
    // 3) execCommand 兜底
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      resolve(!!ok)
    } catch {
      resolve(false)
    }
  })
}

// 终端复制：选中内容复制到系统剪贴板（成功失败均不抛，供调用方静默处理）
function copySelection(term: Terminal): Promise<boolean> {
  const sel = term.getSelection()
  if (!sel) return Promise.resolve(false)
  return writeClipboardText(sel).then((ok) => {
    if (!ok) console.warn('[terminal] 复制失败：剪贴板不可用')
    return ok
  })
}

/** 复制功能的容器绑定（mouseup 有选区即复制）+ 快捷键（Ctrl+C 复制 / Ctrl+V 粘贴 / Alt+R 历史搜索 / Ctrl+F 内容搜索） */
export function setupTerminalCopy(term: Terminal, onAltR?: () => void, onCtrlF?: () => void) {
  // 1) Ctrl+C：有选区时复制并阻止发送（避免打断远端正在运行的命令）
  //    Ctrl+V：不 preventDefault（让浏览器产生 paste 事件），return false 阻止 xterm
  //    把 Ctrl+V 当作按键（^V）发送到终端；粘贴统一由下方 textarea capture 阶段处理
  term.attachCustomKeyEventHandler((e) => {
    if (e.ctrlKey && (e.key === 'c' || e.key === 'C')) {
      if (term.hasSelection()) {
        copySelection(term)
        // 清除选区：下次 Ctrl+C 能正常发送 SIGINT（避免选区残留导致 Ctrl+C 始终只复制不中断）
        term.clearSelection()
        return false  // 阻止 xterm 把 Ctrl+C 发送到终端
      }
    }
    if (e.altKey && (e.key === 'r' || e.key === 'R')) {
      // Alt+R：打开全局命令搜索；return false 阻止 xterm 把 Alt+R 发送到终端
      if (onAltR) onAltR()
      return false
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')) {
      // Ctrl+F：打开终端内容搜索；preventDefault 阻止浏览器默认查找，
      // return false 阻止 xterm 把 Ctrl+F 发送到终端
      e.preventDefault()
      if (onCtrlF) onCtrlF()
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
      if (text) term.paste(preparePasteText(term, text))
    }, { capture: true })
  }
}

/**
 * 准备粘贴文本（参考 rssh 的 pasteText）：
 * - 把所有换行折叠为单个 \r：PTY 的 ICRNL 会把每个 \r 转成一个 \n，
 *   原样发送 CRLF 会变成双换行（\r\n → \n\n）。
 * - shell 已启用 bracketed paste（DECSET 2004，bash/zsh 默认开）时包裹
 *   \x1b[200~...\x1b[201~，多行粘贴时 shell 把内容当一整个缓冲区粘贴，
 *   而不是把每一行当作一条命令直接执行（粘贴注入防护）。
 * 走 term.paste()（等价于发送文本到终端 onData），不在本地回显处理。
 */
function preparePasteText(term: Terminal, text: string): string {
  // xterm 的 modes.bracketedPasteMode 反映远端 shell 是否开启 DECSET 2004
  const bracketed = !!(term as any).modes?.bracketedPasteMode
  const normalized = text.replace(/\r?\n/g, '\r')
  return bracketed ? `\x1b[200~${normalized}\x1b[201~` : normalized
}

/**
 * copy-on-select：左键拖选文本，松开鼠标即复制到系统剪贴板（终端用户最常见操作）。
 * 绑定在终端容器（registerContainer 传入的 .terminal-instance div）的 mouseup 上；
 * 仅响应左键（button===0），避免破坏右键/中键行为。
 */
export function setupCopyOnSelect(term: Terminal, container: HTMLElement) {
  if ((container as any).__wstoolCopyOnSelectBound) return
  ;(container as any).__wstoolCopyOnSelectBound = true
  container.addEventListener('mouseup', (e: MouseEvent) => {
    if (e.button !== 0) return
    // 保持选区（不清除）：复制后用户仍可看到;再次 Ctrl+C 仍会中断命令
    if (term.hasSelection()) {
      copySelection(term)
    }
  })
}