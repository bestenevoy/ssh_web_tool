import { useState, useEffect, useRef } from 'react'
import type { QuickCommand } from '../types'

interface Props {
  onClose: () => void
  onAdd: (name: string, command: string, description: string) => void
  onUpdate?: (id: string, name: string, command: string, description: string) => void
  editing?: QuickCommand | null  // 传入则为编辑模式
}

export function QuickCommandModal({ onClose, onAdd, onUpdate, editing }: Props) {
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [description, setDescription] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)

  // 编辑模式：填充初始值
  useEffect(() => {
    if (editing) {
      setName(editing.name)
      setCommand(editing.command)
      setDescription(editing.description || '')
    }
  }, [editing])

  useEffect(() => {
    nameInputRef.current?.focus()
  }, [])

  const handleSubmit = () => {
    if (!name.trim() || !command.trim()) return
    if (editing && onUpdate) {
      onUpdate(editing.id, name.trim(), command.trim(), description.trim())
    } else {
      onAdd(name.trim(), command.trim(), description.trim())
    }
    onClose()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div
      className="modal-overlay show"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      onKeyDown={handleKeyDown}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{editing ? '编辑快捷指令' : '添加快捷指令'}</h3>
        <div className="form-group">
          <label>名称 *</label>
          <input
            ref={nameInputRef}
            type="text"
            placeholder="例如：查看系统信息"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className="form-group">
          <label>描述</label>
          <input
            type="text"
            placeholder="例如：查看 CPU、内存、磁盘使用情况"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className="form-group">
          <label>命令 *</label>
          <textarea
            placeholder="例如：uname -a && free -h && df -h"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            style={{ resize: 'vertical', fontFamily: 'Consolas, Monaco, monospace' }}
          />
        </div>
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={!name.trim() || !command.trim()}
          >
            {editing ? '保存' : '添加'}
          </button>
        </div>
        <div style={{ fontSize: 10, color: '#5a6a8a', marginTop: 8, textAlign: 'center' }}>
          Ctrl+Enter 快速保存 | ESC 关闭
        </div>
      </div>
    </div>
  )
}
