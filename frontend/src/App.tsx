import { useState, useEffect, useCallback } from 'react'
import 'xterm/css/xterm.css'
import './App.css'
import type { Host, HostType, QuickCommand } from './types'
import { api } from './lib/api'
import { useTerminals } from './lib/useTerminals'
import { useEvents } from './lib/useEvents'
import { useSettings, FONT_OPTIONS } from './lib/useSettings'
import { HostList } from './components/HostList'
import { GroupManager } from './components/GroupManager'
import { TerminalTabs } from './components/TerminalTabs'
import { TerminalView } from './components/TerminalView'
import { QuickCommands } from './components/QuickCommands'
import { SftpPanel } from './components/SftpPanel'
import { EventLog } from './components/EventLog'
import { ApiDocs } from './components/ApiDocs'
import { HostModal } from './components/HostModal'
import { QuickCommandModal } from './components/QuickCommandModal'
import HistorySearchModal from './components/HistorySearchModal'

type PanelTab = 'quick' | 'sftp' | 'events' | 'api'

function App() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [groups, setGroups] = useState<string[]>([])
  const [hostTypes, setHostTypes] = useState<HostType[]>([])
  const [quickCommands, setQuickCommands] = useState<QuickCommand[]>([])
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [panelCollapsed, setPanelCollapsed] = useState(false)
  const [panelTab, setPanelTab] = useState<PanelTab>('quick')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingHost, setEditingHost] = useState<Host | null>(null)
  const [status, setStatus] = useState('就绪')
  const [historySearchOpen, setHistorySearchOpen] = useState(false)
  const [quickCommandModalOpen, setQuickCommandModalOpen] = useState(false)
  const [editingQuickCommand, setEditingQuickCommand] = useState<QuickCommand | null>(null)
  const [showPasswordPlaintext, setShowPasswordPlaintext] = useState(false)

  // 获取全局配置（如密码是否明文展示）
  useEffect(() => {
    api.getConfig()
      .then((cfg) => setShowPasswordPlaintext(!!cfg.show_password_plaintext))
      .catch(() => setShowPasswordPlaintext(false))
  }, [])

  const { settings, toggleTheme, setFontFamily, setFontSize } = useSettings()
  const terminals = useTerminals(settings)
  const { focusActiveTerminal } = terminals
  const events = useEvents()

  // 设置快捷键处理函数（Alt+R 打开历史搜索）
  useEffect(() => {
    terminals.setShortcutHandler(() => {
      if (terminals.activeId) {
        setHistorySearchOpen(true)
      }
    })
    return () => terminals.setShortcutHandler(null)
  }, [terminals, terminals.activeId])

  // 全局快捷键监听（备用，终端未获得焦点时也能触发）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'r' || e.key === 'R')) {
        if (terminals.activeId) {
          e.preventDefault()
          setHistorySearchOpen(true)
        }
      }
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [terminals.activeId])

  const loadHosts = useCallback(async () => {
    try {
      const data = await api.listHosts()
      setHosts(data.hosts)
      setGroups(data.groups)
      setHostTypes(data.host_types)
    } catch (e) {
      console.error('加载主机失败', e)
    }
  }, [])

  const loadQuickCommands = useCallback(async () => {
    try {
      const data = await api.listQuickCommands()
      setQuickCommands(data.commands)
    } catch (e) {
      console.error('加载快速指令失败', e)
    }
  }, [])

  useEffect(() => {
    loadHosts()
    loadQuickCommands()
    terminals.restoreTerminals()
    // 定期刷新主机列表，确保终端计数及时更新（CLI/Python 包创建的终端也能显示）
    const timer = setInterval(() => {
      loadHosts()
    }, 5000)
    return () => clearInterval(timer)
  }, [])

  const activeHostId: string | null = terminals.activeId ? terminals.terminals.get(terminals.activeId)?.host_id || null : null

  const handleHostClick = useCallback((host: Host) => {
    // 连接防抖：已有连接正在建立时忽略新点击，避免并发连接
    if (terminals.connecting) {
      setStatus('正在连接其他主机，请稍候...')
      return
    }
    const existing = Array.from(terminals.terminals.values()).find((t) => t.host_id === host.id && !t.disconnected)
    if (existing) {
      terminals.switchTerminal(existing.session_id)
      setStatus(`已连接 ${host.host} [${existing.session_id}]`)
    } else {
      setStatus(`正在连接 ${host.host}...`)
      terminals.createTerminal(host)
        .then(() => {
          setStatus(`已连接 ${host.host}`)
          // 立即刷新（连接已建立，terminal_count 已更新）
          loadHosts()
          // 延迟再次刷新（确保 WebSocket 和 shell 启动完成，状态完全同步）
          setTimeout(() => loadHosts(), 800)
        })
        .catch((e) => {
          if ((e as any)?.isConnecting) {
            setStatus('正在连接其他主机，请稍候...')
            return
          }
          setStatus('连接失败')
          alert('SSH连接失败: ' + (e as Error).message)
        })
    }
  }, [terminals, loadHosts])

  const handleNewTerminal = useCallback(() => {
    if (terminals.activeId) {
      const inst = terminals.terminals.get(terminals.activeId)
      if (inst) {
        const host = hosts.find((h) => h.id === inst.host_id)
        if (host) {
          terminals.createTerminal(host)
            .then(() => {
              loadHosts()
              setTimeout(() => loadHosts(), 800)
            })
            .catch((e) => alert('创建终端失败: ' + (e as Error).message))
        }
      }
    } else {
      alert('请先选择一个主机')
    }
  }, [terminals, hosts, loadHosts])

  const handleCloseTerminal = useCallback((session_id: string) => {
    const closeBackend = confirm('关闭终端标签？\n\n选择「确定」：同时关闭后端SSH连接\n选择「取消」：仅关闭标签，后端连接保持（可在页面重开时恢复）')
    terminals.closeTerminal(session_id, closeBackend)
      .then(() => loadHosts())
  }, [terminals, loadHosts])

  // 手动断开当前终端连接（保留标签，可重新连接）
  const handleDisconnectTerminal = useCallback((session_id: string) => {
    terminals.disconnectTerminal(session_id).then(() => loadHosts())
  }, [terminals, loadHosts])

  // 重新连接：对同一主机建立新会话，成功后移除旧标签
  const handleReconnectTerminal = useCallback(async (session_id: string) => {
    if (terminals.connecting) {
      setStatus('正在连接其他主机，请稍候...')
      return
    }
    const inst = terminals.terminals.get(session_id)
    if (!inst) return
    const host = hosts.find((h) => h.id === inst.host_id)
    if (!host) { alert('找不到该主机信息'); return }
    setStatus(`正在重新连接 ${host.host}...`)
    try {
      // 先建新会话（保留原标签名），再关闭旧标签；keepActive=true 避免 activeId 跳回第一个 Tab
      await terminals.createTerminal(host, inst.terminal_name)
      await terminals.closeTerminal(session_id, false, true)
      loadHosts()
      setTimeout(() => loadHosts(), 800)
    } catch (e) {
      if ((e as any)?.isConnecting) { setStatus('正在连接其他主机，请稍候...'); return }
      setStatus('重新连接失败')
      alert('重新连接失败: ' + (e as Error).message)
    }
  }, [terminals, hosts, loadHosts])

  const handleSaveHost = useCallback(async (data: Partial<Host>) => {
    try {
      if (editingHost) {
        await api.updateHost(editingHost.id, data)
      } else {
        await api.addHost(data)
      }
      setModalOpen(false)
      setEditingHost(null)
      focusActiveTerminal()
      loadHosts()
    } catch (e) {
      alert('保存失败: ' + (e as Error).message)
    }
  }, [editingHost, loadHosts])

  const handleDeleteHost = useCallback(async (host: Host) => {
    if (!confirm(`确定删除主机「${host.name || host.host}」？`)) return
    try {
      await api.deleteHost(host.id)
      loadHosts()
    } catch (e) {
      alert('删除失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  const handleCopyConnection = useCallback((host: Host) => {
    const cmd = `ssh ${host.username}@${host.host} -p ${host.port}`
    // 密码已脱敏，不下发到前端，复制时只带连接命令
    const text = `${cmd}\n（密码已脱敏，不在前端展示）`
    navigator.clipboard.writeText(text)
      .then(() => alert('已复制连接信息（密码已脱敏，不在前端展示）'))
      .catch(() => {
        const ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
        alert('已复制连接信息')
      })
  }, [])

  // 复制主机
  const handleDuplicateHost = useCallback(async (host: Host) => {
    try {
      await api.duplicateHost(host.id)
      loadHosts()
    } catch (e) {
      alert('复制失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  // 分组管理
  const [groupManagerOpen, setGroupManagerOpen] = useState(false)

  const handleAddGroup = useCallback(async (name: string) => {
    try {
      await api.addGroup(name)
      loadHosts()
    } catch (e) {
      alert('添加分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  const handleRenameGroup = useCallback(async (oldName: string, newName: string) => {
    try {
      await api.renameGroup(oldName, newName)
      loadHosts()
    } catch (e) {
      alert('重命名分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  const handleDeleteGroup = useCallback(async (name: string) => {
    try {
      await api.deleteGroup(name)
      loadHosts()
    } catch (e) {
      alert('删除分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  // 复制分组（含组内主机）
  const handleDuplicateGroup = useCallback(async (name: string) => {
    try {
      const r = await api.duplicateGroup(name)
      loadHosts()
      alert(`已复制分组"${name}" -> "${r.name}"（含 ${r.copied_hosts.length} 台主机）`)
    } catch (e) {
      alert('复制分组失败: ' + (e as Error).message)
    }
  }, [loadHosts])

  // 分组/主机拖拽排序后保存
  const handleReorderGroups = useCallback(async (names: string[]) => {
    try {
      await api.reorderGroups(names)
      loadHosts()
    } catch (e) {
      console.error('保存分组排序失败', e)
    }
  }, [loadHosts])

  const handleReorderHosts = useCallback(async (ids: string[]) => {
    try {
      await api.reorderHosts(ids)
    } catch (e) {
      console.error('保存主机排序失败', e)
    }
  }, [])

  const handleSendQuickCommand = useCallback((qc: QuickCommand) => {
    executeQuickCommand(qc, null)
  }, [terminals])


  // 执行快捷指令（含预操作流水线：上传文件 → chmod → env → 命令本体）
  const executeQuickCommand = useCallback(async (qc: QuickCommand, param: string | null) => {
    if (!terminals.activeId) return
    const inst = terminals.terminals.get(terminals.activeId)
    if (!inst || !inst.ws || inst.ws.readyState !== WebSocket.OPEN) return
    const session_id = inst.session_id

    // 参数替换：{args} 占位符替换，无占位符则追加到末尾
    let cmd = qc.command
    if (param !== null) {
      cmd = cmd.includes('{args}')
        ? cmd.split('{args}').join(param)
        : (cmd + ' ' + param).trim()
    }

    // 依次执行预操作（命令通过终端发送，SFTP 上传通过后端 API 完成后才继续）
    for (const op of qc.pre_ops || []) {
      if (op.type === 'upload') {
        const remote = (op.remote || '').trim()
        if (!remote) continue
        const file = await pickLocalFile()
        if (!file) {
          setStatus('已取消：未选择上传文件，命令未执行')
          return
        }
        setStatus(`上传中 ${file.name} -> ${remote}...`)
        try {
          await api.sftpUpload(session_id, remote, file)
          setStatus(`已上传 ${file.name} -> ${remote}`)
        } catch (e) {
          setStatus('上传失败，命令未执行')
          alert('预操作失败（上传文件）: ' + (e as Error).message)
          return
        }
      } else if (op.type === 'chmod') {
        if (!op.mode || !op.path) continue
        terminals.sendCommand(`chmod ${op.mode} ${op.path}`, true)
      } else if (op.type === 'env') {
        if (!op.key) continue
        terminals.sendCommand(`export ${op.key}=${op.value ?? ''}`, true)
      }
    }

    terminals.sendCommand(cmd, true)
    setStatus(`已执行: ${cmd.slice(0, 60)}`)
  }, [terminals, setStatus])

  // 选择本地文件（隐藏 input[type=file]，返回 File 或 null）
  const pickLocalFile = useCallback((): Promise<File | null> => {
    return new Promise((resolve) => {
      const input = document.createElement('input')
      input.type = 'file'
      input.style.display = 'none'
      document.body.appendChild(input)
      input.onchange = () => {
        const file = input.files?.[0] || null
        document.body.removeChild(input)
        resolve(file)
      }
      input.oncancel = () => {
        document.body.removeChild(input)
        resolve(null)
      }
      input.click()
    })
  }, [])

  // 编辑后执行：只输入到终端，不执行，用户可编辑后手动执行
  const handleEditExecuteQuickCommand = useCallback((qc: QuickCommand) => {
    terminals.sendCommand(qc.command, false)
  }, [terminals])

  // 打开编辑快捷指令弹窗
  const handleEditQuickCommand = useCallback((qc: QuickCommand) => {
    setEditingQuickCommand(qc)
    setQuickCommandModalOpen(true)
  }, [])

  // 更新快捷指令
  const handleUpdateQuickCommand = useCallback(async (id: string, data: Partial<QuickCommand>) => {
    try {
      await api.updateQuickCommand(id, data)
      loadQuickCommands()
    } catch (e) {
      alert('更新失败: ' + (e as Error).message)
    }
  }, [loadQuickCommands])

  const handleAddQuickCommand = useCallback(async (data: Partial<QuickCommand>) => {
    try {
      await api.addQuickCommand(data)
      loadQuickCommands()
    } catch (e) {
      alert('添加失败: ' + (e as Error).message)
    }
  }, [loadQuickCommands])

  const handleDeleteQuickCommand = useCallback(async (id: string) => {
    if (!confirm('确定删除这个快捷指令吗？')) return
    try {
      await api.deleteQuickCommand(id)
      loadQuickCommands()
    } catch (e) {
      alert('删除失败: ' + (e as Error).message)
    }
  }, [loadQuickCommands])

  // 拖拽排序：本地先更新（拖拽立即生效），再保存到后端
  const handleReorderQuickCommands = useCallback(async (ids: string[]) => {
    const orderMap = new Map(quickCommands.map((q) => [q.id, q]))
    const next = ids.map((id) => orderMap.get(id)!).filter(Boolean)
    setQuickCommands(next)
    try {
      await api.reorderQuickCommands(ids)
    } catch (e) {
      alert('排序保存失败: ' + (e as Error).message)
      loadQuickCommands()
    }
  }, [quickCommands, loadQuickCommands])

  const renderPanelContent = () => {
    switch (panelTab) {
      case 'quick':
        return (
          <QuickCommands
            commands={quickCommands}
            onExecute={handleSendQuickCommand}
            onEditExecute={handleEditExecuteQuickCommand}
            onEdit={handleEditQuickCommand}
            onDelete={handleDeleteQuickCommand}
            onOpenAddModal={() => { setEditingQuickCommand(null); setQuickCommandModalOpen(true) }}
            onReorder={handleReorderQuickCommands}
            disabled={!terminals.activeId}
          />
        )
      case 'sftp':
        return <SftpPanel sessionId={terminals.activeId} />
      case 'events':
        return <EventLog events={events.events} onClear={events.clearEvents} />
      case 'api':
        return <ApiDocs />
    }
  }

  return (
    <div className="app">
      {/* 顶部栏 */}
      <div className="topbar">
        <button className="btn btn-secondary btn-sm" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} title="折叠主机列表">☰</button>
        <span className="logo">SSH Web Tool</span>
        <span className="status">{status}</span>
        {terminals.activeId && (() => {
          const activeInst = terminals.terminals.get(terminals.activeId!)
          return activeInst?.disconnected ? (
            <button
              className="btn btn-primary btn-sm conn-action-btn"
              onClick={() => handleReconnectTerminal(terminals.activeId!)}
              title="重新连接当前主机"
            >🔗 重连</button>
          ) : (
            <button
              className="btn btn-secondary btn-sm conn-action-btn"
              onClick={() => handleDisconnectTerminal(terminals.activeId!)}
              title="断开当前终端连接（保留标签，可重新连接）"
            >⛓️‍💥 断开</button>
          )
        })()}
        <div style={{ flex: 1 }} />
        {/* 终端设置 */}
        <div className="topbar-settings">
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={() => setFontSize(settings.fontSize - 1)}
            title="减小字体"
            disabled={settings.fontSize <= 8}
          >
            A-
          </button>
          <span className="settings-font-size" title="当前字体大小">{settings.fontSize}px</span>
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={() => setFontSize(settings.fontSize + 1)}
            title="增大字体"
            disabled={settings.fontSize >= 32}
          >
            A+
          </button>
          <select
            className="settings-font-select"
            value={settings.fontFamily}
            onChange={(e) => setFontFamily(e.target.value)}
            title="选择终端字体"
          >
            {FONT_OPTIONS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          <button
            className="btn btn-secondary btn-sm settings-btn"
            onClick={toggleTheme}
            title={settings.theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
          >
            {settings.theme === 'dark' ? '☀️' : '🌙'}
          </button>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => setPanelCollapsed(!panelCollapsed)}>
          📋 面板
        </button>
        <button className="btn btn-primary btn-sm" onClick={() => { setEditingHost(null); setModalOpen(true) }}>+ 新建主机</button>
      </div>

      {/* 主体 */}
      <div className="main">
        {/* 左侧主机列表 */}
        <div className={`sidebar${sidebarCollapsed ? ' collapsed' : ''}`}>
          <div className="sidebar-header">
            <span className="title">主机列表</span>
            <button className="btn btn-secondary btn-sm" onClick={loadHosts}>🔄</button>
          </div>
          <HostList
            hosts={hosts}
            hostTypes={hostTypes}
            groups={groups}
            activeHostId={activeHostId}
            onHostClick={handleHostClick}
            onEdit={(h) => { setEditingHost(h); setModalOpen(true) }}
            onDelete={handleDeleteHost}
            onCopy={handleCopyConnection}
            onDuplicate={handleDuplicateHost}
            onManageGroups={() => setGroupManagerOpen(true)}
            onReorderHosts={handleReorderHosts}
          />
        </div>

        {/* 中间终端区域 */}
        <div className="terminal-area">
          <TerminalTabs
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            hostTypes={hostTypes}
            hosts={hosts}
            groups={groups}
            onSwitch={terminals.switchTerminal}
            onClose={handleCloseTerminal}
            onNew={handleNewTerminal}
          />
          <TerminalView
            terminals={terminals.terminals}
            activeId={terminals.activeId}
            registerContainer={terminals.registerContainer}
            onReconnect={handleReconnectTerminal}
            onCloseTab={handleCloseTerminal}
          />
        </div>

        {/* 右侧面板 */}
        {!panelCollapsed && (
          <div className="panel">
            <div className="panel-header">
              <span className="title">工具面板</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setPanelCollapsed(true)}>✕</button>
            </div>
            <div className="panel-tabs">
              <div className={`panel-tab${panelTab === 'quick' ? ' active' : ''}`} onClick={() => setPanelTab('quick')}>⚡ 快速指令</div>
              <div className={`panel-tab${panelTab === 'sftp' ? ' active' : ''}`} onClick={() => setPanelTab('sftp')}>📁 SFTP</div>
              <div className={`panel-tab${panelTab === 'events' ? ' active' : ''}`} onClick={() => setPanelTab('events')}>📊 活动日志</div>
              <div className={`panel-tab${panelTab === 'api' ? ' active' : ''}`} onClick={() => setPanelTab('api')}>🔌 API</div>
            </div>
            <div className="panel-body">{renderPanelContent()}</div>
          </div>
        )}
      </div>

      {/* 主机弹窗 */}
      <HostModal
        open={modalOpen}
        host={editingHost}
        groups={groups}
        hostTypes={hostTypes}
        showPasswordPlaintext={showPasswordPlaintext}
        onClose={() => { setModalOpen(false); setEditingHost(null); focusActiveTerminal() }}
        onSave={handleSaveHost}
        onAddGroup={handleAddGroup}
      />

      {/* 分组管理弹窗 */}
      <GroupManager
        open={groupManagerOpen}
        groups={groups}
        onClose={() => { setGroupManagerOpen(false); focusActiveTerminal() }}
        onAdd={handleAddGroup}
        onRename={handleRenameGroup}
        onDelete={handleDeleteGroup}
        onDuplicate={handleDuplicateGroup}
        onReorder={handleReorderGroups}
      />

      {/* 历史搜索弹窗 */}
      {historySearchOpen && (
        <HistorySearchModal
          onClose={() => { setHistorySearchOpen(false); focusActiveTerminal() }}
          onExecute={(cmd) => terminals.sendCommand(cmd, true)}
          onInputToTerminal={(cmd) => terminals.sendCommand(cmd, false)}
          onRefreshQuickCommands={loadQuickCommands}
        />
      )}

      {/* 添加/编辑快捷指令弹窗 */}
      {quickCommandModalOpen && (
        <QuickCommandModal
          onClose={() => { setQuickCommandModalOpen(false); setEditingQuickCommand(null); focusActiveTerminal() }}
          onAdd={handleAddQuickCommand}
          onUpdate={handleUpdateQuickCommand}
          editing={editingQuickCommand}
        />
      )}


      {/* 拖拽提示 */}
      <div id="dropHint">释放文件以上传到当前 SFTP 目录</div>
    </div>
  )
}

export default App
