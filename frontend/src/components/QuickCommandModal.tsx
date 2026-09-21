import { useState, useEffect, useRef } from 'react'
import type { QuickCommand, QuickPreOp } from '../types'
import { api } from '../lib/api'

interface Props {
  onClose: () => void
  onAdd: (data: Partial<QuickCommand>) => void
  onUpdate?: (id: string, data: Partial<QuickCommand>) => void
  editing?: QuickCommand | null  // 传入则为编辑模式
}

const PRE_OP_LABELS: Record<QuickPreOp['type'], string> = {
  upload: '上传文件',
  chmod: '设置权限',
  exec: '执行命令',
}

function emptyPreOp(type: QuickPreOp['type'] = 'upload'): QuickPreOp {
  if (type === 'chmod') return { type, mode: '+x', path: '' }
  if (type === 'exec') return { type, cmd: '' }
  return { type, source_type: 'script', source: '', remote: '' }
}

export function QuickCommandModal({ onClose, onAdd, onUpdate, editing }: Props) {
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [description, setDescription] = useState('')
  const [cmdType, setCmdType] = useState<'direct' | 'param'>('direct')
  const [preOps, setPreOps] = useState<QuickPreOp[]>([])
  const [scripts, setScripts] = useState<string[]>([])
  const [qcKey, setQcKey] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)

  // 加载 scripts 目录文件列表（上传预操作下拉选择）
  useEffect(() => {
    api.listScripts()
      .then((res) => setScripts(res.scripts || []))
      .catch(() => setScripts([]))
  }, [])

  // 编辑模式：填充初始值
  useEffect(() => {
    if (editing) {
      setName(editing.name)
      setCommand(editing.command)
      setDescription(editing.description || '')
      setCmdType(editing.type || 'direct')
      setPreOps(editing.pre_ops?.map((o) => ({ ...o })) || [])
      setQcKey(editing.key || '')
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
      pre_ops: preOps.filter((o) => {
        if (o.type === 'upload') return o.source?.trim() && o.remote?.trim()
        if (o.type === 'chmod') return o.mode?.trim() && o.path?.trim()
        if (o.type === 'exec') return o.cmd?.trim()
        return false
      }),
      key: qcKey.trim(),
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

  // window 级 ESC：无论焦点是否在弹窗内都能关闭（防止点遮罩后焦点丢失导致 ESC 失效）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
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
          <label>调用 key（可选）</label>
          <input
            type="text"
            placeholder="例如：deploy，供 .zs 脚本 @deploy 调用"
            value={qcKey}
            onChange={(e) => setQcKey(e.target.value)}
            onKeyDown={handleKeyDown}
            style={{ fontFamily: 'Consolas, Monaco, monospace' }}
          />
          {qcKey.trim() && !/^[A-Za-z0-9_-]{1,50}$/.test(qcKey.trim()) && (
            <div style={{ fontSize: 'calc(11px * var(--ui-fs-scale))', color: 'var(--danger)', marginTop: 3 }}>
              key 仅限字母/数字/下划线/连字符，最长 50 字符
            </div>
          )}
          <div style={{ fontSize: 'calc(10px * var(--ui-fs-scale))', color: 'var(--text-muted)', marginTop: 3 }}>
            全局唯一（不区分大小写），.zs 脚本中用 @key 或 @名称 引用（仅支持直接执行型指令）
          </div>
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
            <div style={{ fontSize: 'calc(11px * var(--ui-fs-scale))', color: 'var(--text-muted)' }}>
              命令中使用 {`{args}`} 表示参数位置；未包含 {`{args}`} 时参数自动追加到命令末尾。
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
            <div style={{ fontSize: 'calc(10px * var(--ui-fs-scale))', color: 'var(--text-muted)', padding: '4px 0' }}>
              无预操作。可添加上传文件、设置执行权限、先执行命令等步骤
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
                <option value="exec">⌨️ 执行命令</option>
              </select>
              {op.type === 'upload' && (
                <>
                  <select
                    value={op.source_type || 'script'}
                    onChange={(e) => updatePreOp(idx, { source_type: e.target.value as 'path' | 'script', source: '' })}
                    className="qc-preop-type"
                    title="源文件来源"
                  >
                    <option value="script">📁 scripts 文件</option>
                    <option value="path">💻 本机路径</option>
                  </select>
                  {op.source_type === 'script' ? (
                    <input
                      type="text"
                      list={`qc-scripts-${idx}`}
                      placeholder="scripts 目录下文件名，如 deploy.sh"
                      value={op.source || ''}
                      onChange={(e) => updatePreOp(idx, { source: e.target.value })}
                      className="qc-preop-input"
                      title="填文件名，在 ~/.ai4one/sshtool/scripts/ 下查找；或从下拉选择"
                    />
                  ) : (
                    <input
                      type="text"
                      placeholder="本机绝对路径，如 D:/scripts/deploy.sh"
                      value={op.source || ''}
                      onChange={(e) => updatePreOp(idx, { source: e.target.value })}
                      className="qc-preop-input"
                      title="Server 同机直接读取该路径后上传（浏览器无需选文件）"
                    />
                  )}
                  <datalist id={`qc-scripts-${idx}`}>
                    {scripts.map((s) => <option key={s} value={s} />)}
                  </datalist>
                  <span className="qc-preop-note">→</span>
                  <input
                    type="text"
                    placeholder="远端目标路径，如 /opt/deploy.sh"
                    value={op.remote || ''}
                    onChange={(e) => updatePreOp(idx, { remote: e.target.value })}
                    className="qc-preop-input"
                    title="上传到远端哪个路径"
                  />
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
              {op.type === 'exec' && (
                <input
                  type="text"
                  placeholder="先执行的命令，如 export TAG=v1.2 或 cd /opt/app"
                  value={op.cmd || ''}
                  onChange={(e) => updatePreOp(idx, { cmd: e.target.value })}
                  className="qc-preop-input"
                  title="在执行当前命令之前先执行此命令（自由命令，可设置环境变量、切换目录等）"
                />
              )}
              <button
                className="action-btn danger"
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
          <div style={{ fontSize: 'calc(10px * var(--ui-fs-scale))', color: 'var(--text-muted)', marginTop: 4 }}>
            上传文件：源文件填「本机绝对路径」由 Server 直接读取，或选「scripts 文件」在 ~/.ai4one/sshtool/scripts/ 下查找（可下拉选择），通过 SFTP 上传后再执行命令
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>取消 (ESC)</button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={!name.trim() || !command.trim() || (!!qcKey.trim() && !/^[A-Za-z0-9_-]{1,50}$/.test(qcKey.trim()))}
          >
            {editing ? '保存' : '添加'}
          </button>
        </div>
        <div style={{ fontSize: 'calc(10px * var(--ui-fs-scale))', color: 'var(--text-muted)', marginTop: 8, textAlign: 'center' }}>
          Ctrl+Enter 快速保存 | ESC 关闭
        </div>
      </div>
    </div>
  )
}

export { PRE_OP_LABELS }
