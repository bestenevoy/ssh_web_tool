import { useState, useEffect } from 'react'
import type { Host, HostType } from '../types'

interface Props {
  open: boolean
  host: Host | null
  groups: string[]
  hostTypes: HostType[]
  onClose: () => void
  onSave: (data: Partial<Host>) => void
  onAddGroup?: (name: string) => void
}

export function HostModal({ open, host, groups, hostTypes, onClose, onSave, onAddGroup }: Props) {
  const [name, setName] = useState('')
  const [hostAddr, setHostAddr] = useState('')
  const [port, setPort] = useState(22)
  const [username, setUsername] = useState('root')
  const [password, setPassword] = useState('')
  const [type, setType] = useState('other')
  const [group, setGroup] = useState('')
  const [newGroupName, setNewGroupName] = useState('')
  const [showNewGroup, setShowNewGroup] = useState(false)
  // 设备类型：linux（普通主机）/ storage（存储阵列）
  const [deviceType, setDeviceType] = useState('linux')
  // 存储阵列管理页面配置
  const [mgmtPort, setMgmtPort] = useState(8088)
  const [mgmtUsername, setMgmtUsername] = useState('')
  const [mgmtPassword, setMgmtPassword] = useState('')
  // Playwright 自动登录选择器配置
  const [showSelectorConfig, setShowSelectorConfig] = useState(false)
  const [pwUsernameSelector, setPwUsernameSelector] = useState('')
  const [pwPasswordSelector, setPwPasswordSelector] = useState('')
  const [pwLoginBtnSelector, setPwLoginBtnSelector] = useState('')
  const [pwOldPasswordSelector, setPwOldPasswordSelector] = useState('')
  const [pwNewPasswordSelector, setPwNewPasswordSelector] = useState('')
  const [pwConfirmPasswordSelector, setPwConfirmPasswordSelector] = useState('')
  const [pwConfirmBtnSelector, setPwConfirmBtnSelector] = useState('')
  const [pwSuccessSelector, setPwSuccessSelector] = useState('')
  const [pwHeadless, setPwHeadless] = useState(false)

  useEffect(() => {
    if (host) {
      setName(host.name || '')
      setHostAddr(host.host)
      setPort(host.port)
      setUsername(host.username)
      setPassword(host.password || '')
      setType(host.type)
      setGroup(host.group || '')
      setDeviceType(host.device_type || 'linux')
      setMgmtPort(host.mgmt_port || 8088)
      setMgmtUsername(host.mgmt_username || '')
      setMgmtPassword(host.mgmt_password || '')
      setPwUsernameSelector(host.pw_username_selector || '')
      setPwPasswordSelector(host.pw_password_selector || '')
      setPwLoginBtnSelector(host.pw_login_btn_selector || '')
      setPwOldPasswordSelector(host.pw_old_password_selector || '')
      setPwNewPasswordSelector(host.pw_new_password_selector || '')
      setPwConfirmPasswordSelector(host.pw_confirm_password_selector || '')
      setPwConfirmBtnSelector(host.pw_confirm_btn_selector || '')
      setPwSuccessSelector(host.pw_success_selector || '')
      setPwHeadless(host.pw_headless || false)
    } else {
      setName('')
      setHostAddr('')
      setPort(22)
      setUsername('root')
      setPassword('')
      setType('other')
      setGroup('')
      setDeviceType('linux')
      setMgmtPort(8088)
      setMgmtUsername('')
      setMgmtPassword('')
      setPwUsernameSelector('')
      setPwPasswordSelector('')
      setPwLoginBtnSelector('')
      setPwOldPasswordSelector('')
      setPwNewPasswordSelector('')
      setPwConfirmPasswordSelector('')
      setPwConfirmBtnSelector('')
      setPwSuccessSelector('')
      setPwHeadless(false)
    }
  }, [host, open])

  if (!open) return null

  const handleSave = () => {
    if (!hostAddr) return
    onSave({
      name,
      host: hostAddr,
      port,
      username,
      password,
      type,
      group,
      device_type: deviceType,
      mgmt_port: mgmtPort,
      mgmt_username: mgmtUsername,
      mgmt_password: mgmtPassword,
      pw_username_selector: pwUsernameSelector,
      pw_password_selector: pwPasswordSelector,
      pw_login_btn_selector: pwLoginBtnSelector,
      pw_old_password_selector: pwOldPasswordSelector,
      pw_new_password_selector: pwNewPasswordSelector,
      pw_confirm_password_selector: pwConfirmPasswordSelector,
      pw_confirm_btn_selector: pwConfirmBtnSelector,
      pw_success_selector: pwSuccessSelector,
      pw_headless: pwHeadless,
    })
  }

  const isStorage = deviceType === 'storage'

  return (
    <div className="modal-overlay show" onClick={onClose}>
      <div className="modal modal-large" onClick={(e) => e.stopPropagation()}>
        <h3>{host ? '编辑主机' : '新建主机'}</h3>
        <div className="form-group">
          <label>别名（显示名称）</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：阿里云生产服务器" />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>设备类型</label>
            <select value={deviceType} onChange={(e) => setDeviceType(e.target.value)}>
              <option value="linux">普通主机（Linux）</option>
              <option value="storage">存储阵列（控制器）</option>
            </select>
          </div>
          <div className="form-group">
            <label>环境类型</label>
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {hostTypes.map((t) => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>主机 IP</label>
            <input type="text" value={hostAddr} onChange={(e) => setHostAddr(e.target.value)} placeholder="192.168.1.1" />
          </div>
          <div className="form-group">
            <label>SSH 端口</label>
            <input type="number" value={port} onChange={(e) => setPort(parseInt(e.target.value) || 22)} />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>SSH 用户名</label>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="form-group">
            <label>SSH 密码</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
        </div>
        <div className="form-group">
          <label>分组</label>
          <div className="group-select-row">
            <select value={group} onChange={(e) => setGroup(e.target.value)} className="group-select">
              <option value="">未分组</option>
              {groups.map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setShowNewGroup(!showNewGroup)}
            >
              {showNewGroup ? '取消' : '+ 新建'}
            </button>
          </div>
          {showNewGroup && (
            <div className="new-group-row">
              <input
                type="text"
                placeholder="输入新分组名称..."
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newGroupName.trim() && onAddGroup) {
                    onAddGroup(newGroupName.trim())
                    setGroup(newGroupName.trim())
                    setNewGroupName('')
                    setShowNewGroup(false)
                  }
                }}
                className="new-group-input"
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => {
                  if (newGroupName.trim() && onAddGroup) {
                    onAddGroup(newGroupName.trim())
                    setGroup(newGroupName.trim())
                    setNewGroupName('')
                    setShowNewGroup(false)
                  }
                }}
              >
                添加
              </button>
            </div>
          )}
        </div>

        {isStorage && (
          <>
            <div className="form-section-title">存储阵列管理页面配置</div>
            <div className="form-row">
              <div className="form-group">
                <label>管理页面端口</label>
                <input type="number" value={mgmtPort} onChange={(e) => setMgmtPort(parseInt(e.target.value) || 8088)} />
              </div>
              <div className="form-group">
                <label>管理用户名</label>
                <input type="text" value={mgmtUsername} onChange={(e) => setMgmtUsername(e.target.value)} placeholder="如：admin" />
              </div>
            </div>
            <div className="form-group">
              <label>管理密码</label>
              <input type="password" value={mgmtPassword} onChange={(e) => setMgmtPassword(e.target.value)} />
            </div>
            <div className="form-hint">
              保存后，主机列表会出现「打开管理页面」按钮。点击可选择自动登录（Playwright）或手动打开。
            </div>

            {/* Playwright 选择器配置（可折叠） */}
            <div className="selector-config-toggle" onClick={() => setShowSelectorConfig(!showSelectorConfig)}>
              <span style={{ display: 'inline-block', transform: showSelectorConfig ? 'none' : 'rotate(-90deg)', transition: 'transform .15s' }}>▼</span>
              Playwright 自动登录选择器配置（点击展开）
            </div>
            {showSelectorConfig && (
              <div className="selector-config">
                <div className="form-hint" style={{ marginBottom: '12px' }}>
                  填写各步骤的 CSS 选择器，用于自动登录。用浏览器开发者工具（F12）检查元素，右键复制 selector。
                  <br />必填：用户名输入框、密码输入框、登录按钮。可选：修改密码相关、登录成功标志。
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label>用户名输入框选择器 *</label>
                    <input type="text" value={pwUsernameSelector} onChange={(e) => setPwUsernameSelector(e.target.value)} placeholder="如：input[name='username']" />
                  </div>
                  <div className="form-group">
                    <label>密码输入框选择器 *</label>
                    <input type="text" value={pwPasswordSelector} onChange={(e) => setPwPasswordSelector(e.target.value)} placeholder="如：input[name='password']" />
                  </div>
                </div>
                <div className="form-group">
                  <label>登录按钮选择器 *</label>
                  <input type="text" value={pwLoginBtnSelector} onChange={(e) => setPwLoginBtnSelector(e.target.value)} placeholder="如：button[type='submit']" />
                </div>

                <div className="form-section-subtitle">首次登录修改密码（可选）</div>
                <div className="form-row">
                  <div className="form-group">
                    <label>旧密码输入框选择器</label>
                    <input type="text" value={pwOldPasswordSelector} onChange={(e) => setPwOldPasswordSelector(e.target.value)} placeholder="如：input[name='oldPassword']" />
                  </div>
                  <div className="form-group">
                    <label>新密码输入框选择器</label>
                    <input type="text" value={pwNewPasswordSelector} onChange={(e) => setPwNewPasswordSelector(e.target.value)} placeholder="如：input[name='newPassword']" />
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label>确认密码输入框选择器</label>
                    <input type="text" value={pwConfirmPasswordSelector} onChange={(e) => setPwConfirmPasswordSelector(e.target.value)} placeholder="如：input[name='confirmPassword']" />
                  </div>
                  <div className="form-group">
                    <label>确认按钮选择器</label>
                    <input type="text" value={pwConfirmBtnSelector} onChange={(e) => setPwConfirmBtnSelector(e.target.value)} placeholder="如：button#confirmBtn" />
                  </div>
                </div>

                <div className="form-section-subtitle">登录成功标志（可选）</div>
                <div className="form-group">
                  <label>登录成功后出现的元素选择器</label>
                  <input type="text" value={pwSuccessSelector} onChange={(e) => setPwSuccessSelector(e.target.value)} placeholder="如：.main-content, #dashboard" />
                </div>

                <div className="form-group">
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input type="checkbox" checked={pwHeadless} onChange={(e) => setPwHeadless(e.target.checked)} />
                    无头模式（不显示浏览器窗口，仅后台运行）
                  </label>
                </div>
              </div>
            )}
          </>
        )}

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={handleSave}>保存</button>
        </div>
      </div>
    </div>
  )
}
