import { useState, useEffect, useRef } from 'react'

interface Props {
  title: string  // 弹窗标题（如「连接 root@1.2.3.4」）
  onSubmit: (password: string) => void
  onCancel: () => void
}

/**
 * 重连密码弹窗：会话/主机未保存密码且无私钥时，统一重连流程先弹窗取密码再建连。
 * 取消（Esc/按钮）视为放弃重连，走断开流程收尾。
 */
export function PasswordModal({ title, onSubmit, onCancel }: Props) {
  const [password, setPassword] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开后聚焦输入框，直接键入
  useEffect(() => {
    inputRef.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  const submit = () => onSubmit(password)

  return (
    <div className="modal-overlay show">
      <div className="modal" style={{ width: 340 }}>
        <h3>🔐 {title}</h3>
        <div className="form-group">
          <label>该主机未保存密码，请输入 SSH 密码</label>
          <input
            ref={inputRef}
            type="password"
            value={password}
            placeholder="SSH 密码（回车确认）"
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
          />
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button className="btn btn-secondary" onClick={onCancel}>取消</button>
          <button className="btn btn-primary" onClick={submit} disabled={!password}>连接</button>
        </div>
      </div>
    </div>
  )
}
