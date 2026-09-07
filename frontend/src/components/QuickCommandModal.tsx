import { useState, useEffect, useRef } from 'react'
import type { QuickCommand, QuickPreOp } from '../types'

interface Props {
  onClose: () => void
  onAdd: (data: Partial<QuickCommand>) => void
  onUpdate?: (id: string, data: Partial<QuickCommand>) => void
  editing?: QuickCommand | null  // 传入则为编辑模式
}

const PRE_OP_LABELS: Record<QuickPreOp['type'], string> = {
  upload: '上传文件',
  chmod: '设置权限',
  env: '设置环境变量',
}

function emptyPreOp(type: QuickPreOp['type'] = 'upload'): QuickPreOp {
  if (type === 'chmod') return { type, mode: '+x', path: '' }
  if (type === 'env') return { type, key: '', value: '' }
  return { type, remote: '' }
}

export function QuickCommandModal({ onClose, onAdd, onUpdate, editing }: Props) {
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [description, setDescription] = useState('')
  const [cmdType, setCmdType] = useState<'direct' | 'param'>('direct')
  const [paramHint, setParamHint] = useState('')
  const [preOps, setPreOps] = useState<QuickPreOp[]>([])
  const nameInputRef = useRef<HTMLInputElement>(null)

  // 编辑模式：填充初始值
  useEffect(() => {
    if (editing) {
      setName(editing.name)
      setCommand(editing.command)
      setDescription(editing.description || '')
      setCmdType(editing.type || 'direct')
      setParamHint(editing.param_hint || '')
      setPreOps(editing.pre_ops?.map((o) => ({ ...o })) || [])
    }
  }, [editing])

  useEffect(() => {
    nameInputRef.current?.focus()
  }, [])

  const handleSubmit = () => {
    if (!name.trim() || !command.trim()) return
    const data: Partial<QuickCommand> = {
      name: name.trim(),
      command: command.trim(),
      description: description.trim(),
      type: cmdType,
      param_hint: cmdType === 'param' ? paramHint.trim() : '',
      pre_ops: preOps.filter((o) => {
        if (o.type === 'upload') return o.remote?.trim()
        if (o.type === 'chmod') return o.mode?.trim() && o.path?.trim()
        if (o.type === 'env') return o.key?.trim()
        return false
      }),
    }
    if (editing && onUpdate) {
      onUpdate(editing.id, data)
    } else {
      onAdd(data)
    }
    onClose()
  }

  const updatePreOp = (idx: number, patch: Partial<QuickPreOp>) => {
    setPreOps((prev) => prev.map((o, i) => (i === idx ? { ...o, ...patch } : o)))
  }

  const changePreOpType = (idx: number, type: QuickPreOp['type']) => {
    setPreOps((prev) => prev.map((o, i) => (i === idx ? emptyPreOp(type) : o)))
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
      onMouseDown={(e) => e.stopPropagation()}
      onKeyDown={handleKeyDown}
    >
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title-row">
          <h3>{editing ? '编辑快捷指令' : '添加快捷指令'}</h3>
          <button className="modal-close-btn" onClick={onClose} title="关闭 (ESC)">✕</button>
        </div>

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
          <label>类型</label>
          <div className="qc-type-row">
            <label className="qc-type-option">
              <input
                type="radio"
                name="qc-type"
                checked={cmdType === 'direct'}
                onChange={() => setCmdType('direct')}
              />
              <span>⚡ 直接执行</span>
              <span className="qc-type-desc">点击即执行命令</span>
            </label>
            <label className="qc-type-option">
              <input
                type="radio"
                name="qc-type"
                checked={cmdType === 'param'}
                onChange={() => setCmdType('param')}
              />
              <span>⌨️ 带参数</span>
              <span className="qc-type-desc">执行前弹输入框，替换命令中的 {`{args}`} 占位符</span>
            </label>
          </div>
        </div>

        {cmdType === 'param' && (
          <div className="form-group">
            <label>参数说明（提示语）</label>
            <input
              type="text"
              placeholder="例如：输入目标环境（prod/test）"
              value={paramHint}
              onChange={(e) => setParamHint(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <div style={{ fontSize: 10, color: '#5a6a8a', marginTop: 4 }}>
              命令中使用 {`{args}`} 表示参数位置；未包含 {`{args}`} 时参数自动追加到命令末尾
            </div>
          </div>
        )}

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

        {/* 预操作：执行命令前依次执行（文件传输 / 设置权限 / 设置环境变量） */}
        <div className="form-group">
          <label>预操作（执行命令前依次执行）</label>
          {preOps.length === 0 && (
            <div style={{ fontSize: 10, color: '#5a6a8a', padding: '4px 0' }}>
              无预操作。可添加上传文件、设置执行权限、设置环境变量等步骤
            </div>
          )}
          {preOps.map((op, idx) => (
            <div key={idx} className="qc-preop-row">
              <select
                value={op.type}
                onChange={(e) => changePreOpType(idx, e.target.value as QuickPreOp['type'])}
                className="qc-preop-type"
                title="预操作类型"
              >
                <option value="upload">📤 上传文件</option>
                <option value="chmod">🔧 设置权限</option>
                <option value="env">🌱 环境变量</option>
              </select>
              {op.type === 'upload' && (
                <>
                  <input
                    type="text"
                    placeholder="远端目标路径，如 /opt/app.jar"
                    value={op.remote || ''}
                    onChange={(e) => updatePreOp(idx, { remote: e.target.value })}
                    className="qc-preop-input"
                    title="上传到远端哪个路径（执行时选择本地文件）"
                  />
                  <span className="qc-preop-note">上传到</span>
                </>
              )}
              {op.type === 'chmod' && (
                <>
                  <input
                    type="text"
                    placeholder="+x / 755"
                    value={op.mode || ''}
                    onChange={(e) => updatePreOp(idx, { mode: e.target.value })}
                    className="qc-preop-input-sm"
                    title="权限模式，如 +x 或 755"
                  />
                  <input
                    type="text"
                    placeholder="目标文件路径"
                    value={op.path || ''}
                    onChange={(e) => updatePreOp(idx, { path: e.target.value })}
                    className="qc-preop-input"
                  />
                </>
              )}
              {op.type === 'env' && (
                <>
                  <input
                    type="text"
                    placeholder="变量名，如 ENV"
                    value={op.key || ''}
                    onChange={(e) => updatePreOp(idx, { key: e.target.value })}
                    className="qc-preop-input-sm"
                  />
                  <span className="qc-preop-note">=</span>
                  <input
                    type="text"
                    placeholder="变量值"
                    value={op.value || ''}
                    onChange={(e) => updatePreOp(idx, { value: e.target.value })}
                    className="qc-preop-input"
                  />
                </>
              )}
              <button
                className="action-btn"
                onClick={() => setPreOps((prev) => prev.filter((_, i) => i !== idx))}
                title="删除此预操作"
              >🗑</button>
            </div>
          ))}
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => setPreOps((prev) => [...prev, emptyPreOp('upload')])}
            style={{ marginTop: 6 }}
          >
            + 添加预操作
          </button>
          <div style={{ fontSize: 10, color: '#5a6a8a', marginTop: 4 }}>
            上传文件：执行时浏览器会弹出文件选择，文件通过 SFTP 上传后再执行命令
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>取消 (ESC)</button>
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

export { PRE_OP_LABELS }
