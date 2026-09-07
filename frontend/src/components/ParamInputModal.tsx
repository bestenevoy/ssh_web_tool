import { useState, useEffect, useRef } from 'react'

interface Props {
  open: boolean
  commandName: string
  paramHint?: string
  onConfirm: (param: string) => void
  onClose: () => void
}

// 带参数快捷指令的参数输入弹窗（替代 window.prompt，内置浏览器禁用 prompt 时也能用）
export function ParamInputModal({ open, commandName, paramHint, onConfirm, onClose }: Props) {
  const [param, setParam] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开时清空并聚焦
  useEffect(() => {
    if (open) {
      setParam('')
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  // window 级 ESC 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const handleSubmit = () => {
    onConfirm(param)
    onClose()
  }

  return (
    <div className="modal-overlay show" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal modal-small" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title-row">
          <div className="modal-title">
            <span className="qc-type-badge param" title="带参数">⌨️</span>
            执行：{commandName}
          </div>
          <button className="modal-close" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="form-group" style={{ marginTop: 12 }}>
          <label>参数{paramHint ? `（${paramHint}）` : ''}</label>
          <input
            ref={inputRef}
            type="text"
            value={param}
            onChange={(e) => setParam(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSubmit()
            }}
            placeholder={paramHint || '请输入参数（替换命令中的 {args}）'}
          />
        </div>
        <div className="form-hint">
          参数将替换命令中的 <code>{'{args}'}</code> 占位符；命令中无占位符时追加到末尾
        </div>
        <div className="modal-footer" style={{ marginTop: 16 }}>
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={handleSubmit} disabled={!param.trim()}>确定并执行</button>
        </div>
      </div>
    </div>
  )
}
