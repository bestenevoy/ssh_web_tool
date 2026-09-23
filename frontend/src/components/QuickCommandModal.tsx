import { useState, useEffect, useRef } from 'react'
import type { QuickCommand, QuickPreOp } from '../types'
import { api } from '../lib/api'
import { compileScript } from '../lib/quickScript'
import { normalizeInvisible, scanHiddenChars } from '../lib/invisibleChars'

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
  const [cmdType, setCmdType] = useState<'direct' | 'param' | 'script'>('direct')
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
    // 隐形字符防御：网页/文档/聊天工具复制进来的命令常混入 NBSP/零宽字符，
    // 显示无异但执行时进 shell（建出脏文件名）——保存入库前统一清理
    const nz = (s: string) => normalizeInvisible(s).trim()
    const cleanPreOp = (o: QuickPreOp): QuickPreOp => {
      if (o.type === 'upload') return { ...o, source: nz(o.source ?? ''), remote: nz(o.remote ?? '') }
      if (o.type === 'chmod') return { ...o, mode: nz(o.mode ?? ''), path: nz(o.path ?? '') }
      return { ...o, cmd: nz(o.cmd ?? '') }
    }
    const cleanedPreOps = preOps.map(cleanPreOp).filter((o) => {
        if (o.type === 'upload') return !!o.source?.trim() && !!o.remote?.trim()
        if (o.type === 'chmod') return !!o.mode?.trim() && !!o.path?.trim()
        if (o.type === 'exec') return !!o.cmd?.trim()
        return false
      })
    const data: Partial<QuickCommand> = {
      name: nz(name),
      command: nz(command),
      description: description.trim(),  // 描述允许原样（含合法全角），不做替换
      type: cmdType,
      pre_ops: cleanedPreOps,
      key: nz(qcKey),
    }
    if (editing && onUpdate) {
      onUpdate(editing.id, data)
    } else {
      onAdd(data)
    }
    onClose()
  }

  const updatePreOp = (idx: number, patch: Partial<QuickPreOp>) => {    setPreOps((prev) => prev.map((o, i) => (i === idx ? { ...o, ...patch } : o)))
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

  // 脚本类型实时编译校验：语法错误禁止保存（保存的是隐形字符归一后的代码）
  const scriptErr =
    cmdType === 'script' && command.trim()
      ? (() => {
          try {
            compileScript(normalizeInvisible(command))
            return ''
          } catch (e) {
            return (e as Error).message
          }
        })()
      : ''

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
            <label className="qc-type-option">
              <input
                type="radio"
                name="qc-type"
                checked={cmdType === 'script'}
                onChange={() => setCmdType('script')}
              />
              <span>📜 JS 脚本</span>
              <span className="qc-type-desc">JavaScript 流程控制（send/run/expect/重连），Xshell 动态对象式</span>
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
          <label>{cmdType === 'script' ? '脚本代码 *' : '命令 *'}</label>
          {cmdType === 'script' ? (
            <>
              <textarea
                placeholder={'例如：\nconst s = t.session()\nif (!s) { t.log(\'没有活动会话\'); return }\nfor (const ip of [\'10.0.0.1\', \'10.0.0.2\']) {\n  s.send(`ping -c1 ${ip}`)\n  const ok = await s.expect(`${ip}.*time=`, 5000)\n  t.log(ok ? `通: ${ip}` : `不通: ${ip}`)\n}'}
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={10}
                spellCheck={false}
                style={{ resize: 'vertical', fontFamily: 'Consolas, Monaco, monospace', fontSize: 'calc(12px * var(--ui-fs-scale))' }}
              />
              <div style={{ fontSize: 'calc(10px * var(--ui-fs-scale))', color: 'var(--text-muted)', marginTop: 3 }}>
                API（async 函数体，可直接 await）：<code>t.session(id或名称)</code> 取会话（省略=当前活动）、
                <code>t.sessions()</code> 列表（含 state）、<code>s.send(cmd, execute=true)</code>、
                <code>await s.run(cmd)</code> → 发命令并等执行完 <code>{'{ok, code, output[], matched}'}</code>（注入退出码探针，bash/zsh）、
                <code>await s.expect('文本'或/正则/, 超时ms)</code> → 命中行/null、
                <code>await s.waitIdle()</code> 等屏幕停止刷新、<code>s.state()/s.connected()</code> 连接状态、
                <code>await t.reconnect(id或名称)/t.disconnect()</code> 重连/断开会话、
                <code>await t.sleep(ms)</code>、<code>t.log(msg)</code>。全局超时 120s；运行中再次点击本指令 = 停止。
              </div>
              {scriptErr && (
                <div style={{ fontSize: 'calc(11px * var(--ui-fs-scale))', color: 'var(--danger)', marginTop: 3 }}>
                  语法错误：{scriptErr}
                </div>
              )}
            </>
          ) : (
            <>
              <textarea
                placeholder="例如：uname -a && free -h && df -h"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={3}
                style={{ resize: 'vertical', fontFamily: 'Consolas, Monaco, monospace' }}
              />
              {(() => {
                // 实时检出：隐形字符（保存时自动清理）与全角/形近标点（仅提醒，不替换）
                const issues = scanHiddenChars(command, true)
                if (issues.length === 0) return null
                return (
                  <div style={{ fontSize: 'calc(11px * var(--ui-fs-scale))', color: 'var(--danger)', marginTop: 3 }}>
                    检出：{issues.join('，')}——隐形字符将在保存时自动清理
                  </div>
                )
              })()}
            </>
          )}
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
            disabled={!name.trim() || !command.trim() || (!!qcKey.trim() && !/^[A-Za-z0-9_-]{1,50}$/.test(qcKey.trim())) || !!scriptErr}
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
