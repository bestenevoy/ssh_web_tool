/**
 * 通用右键菜单（rssh TabContextMenu / BlockContextMenu 同款交互，React 版）。
 *
 * 主机列表与终端窗口各自构造 sections 复用本组件，菜单之间天然区分。
 * 交互要点（均与 rssh 一致）：
 *   - 先按 (x, y) 渲染再量尺寸做视口边缘 clamp，测量前隐藏避免闪帧；
 *   - backdrop 遮罩：mousedown / 右键点击遮罩即关闭（右键菜单非表单弹窗，
 *     不适用 AGENTS.md「弹窗禁止点遮罩关闭」规范——该规范针对表单弹窗防误触）；
 *   - Esc 关闭：preventDefault + stopPropagation 吃干净事件（终端在底下
 *     接 keydown，Esc 会顺路被 vim/less/shell 吃掉）；
 *   - danger 项红色（删除类操作），disabled 置灰但仍显示（让用户看到
 *     "操作存在，只是当前不可用"）。
 */
import { useEffect, useRef, useState } from 'react'

export interface CtxMenuItem {
  label: string
  shortcut?: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
}

export interface ContextMenuPosition {
  x: number
  y: number
}

interface Props {
  x: number
  y: number
  /** 分组渲染，组间画分隔线 */
  sections: CtxMenuItem[][]
  onClose: () => void
}

export function ContextMenu({ x, y, sections, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null)
  // 视口边缘 clamp 偏移：先以 (x, y) 渲染，量出尺寸再算修正量
  const [offset, setOffset] = useState({ dx: 0, dy: 0 })
  const [ready, setReady] = useState(false)
  // onClose 用 ref 转发：父组件每次渲染传新箭头，effect 只挂一次监听（deps []）
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const el = menuRef.current
    if (el) {
      const r = el.getBoundingClientRect()
      const dx = r.right > window.innerWidth ? window.innerWidth - r.right - 4 : 0
      const dy = r.bottom > window.innerHeight ? window.innerHeight - r.bottom - 4 : 0
      setOffset((prev) => (prev.dx === dx && prev.dy === dy ? prev : { dx, dy }))
      setReady(true)
    }
    const onWindowMouseDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onCloseRef.current()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onCloseRef.current()
      }
    }
    // 下一帧再挂监听，避免捕获触发本菜单的那一次 mousedown / contextmenu
    const timer = setTimeout(() => {
      window.addEventListener('mousedown', onWindowMouseDown)
      window.addEventListener('keydown', onKeyDown)
    }, 0)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('mousedown', onWindowMouseDown)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  return (
    <>
      {/* backdrop = 纯遮罩：点空白处关闭右键菜单（菜单无表单输入，无误触丢失问题） */}
      <div
        className="ctx-backdrop"
        onMouseDown={onClose}
        onContextMenu={(e) => { e.preventDefault(); onClose() }}
      />
      <div
        ref={menuRef}
        className={`ctx-menu${ready ? ' ready' : ''}`}
        role="menu"
        style={{ left: x + offset.dx, top: y + offset.dy }}
      >
        {sections.map((section, si) => (
          <div key={si} className="ctx-section">
            {si > 0 && <div className="ctx-sep" />}
            {section.map((item, ii) => (
              <button
                key={ii}
                className={`ctx-item${item.danger ? ' danger' : ''}`}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  if (item.disabled) return
                  item.onClick()
                  onClose()
                }}
              >
                <span className="ctx-label">{item.label}</span>
                {item.shortcut && <span className="ctx-shortcut">{item.shortcut}</span>}
              </button>
            ))}
          </div>
        ))}
      </div>
    </>
  )
}
