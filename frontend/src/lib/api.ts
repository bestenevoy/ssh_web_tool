// API 调用封装
import type { Host, HostType, Session, ActiveTerminal, QuickCommand, SftpItem, CommandResult, TerminalState, SavedTerminal } from '../types'
import type { HighlightRule } from './highlight'

// 后端 ui_settings（config.json 持久化的前端 UI 设置；snake_case）
export interface UiSettingsPayload {
  theme: 'dark' | 'light'
  font_family: string
  font_size: number
  ui_font_scale: number
  block_bar: boolean
  block_auto_fold: boolean
  block_max_lines: number
  block_split_mode: 'enter' | 'prompt'
  custom_prompt_patterns: string[]
  highlight_rules: HighlightRule[]
  editor_recent_paths: string[]
  log_record_dir: string
  log_record_no_ask: boolean
}

// SFTP 传输任务（后端 transfer_registry；前端 1s 轮询渲染传输列表）
export interface SftpTransfer {
  id: string
  session_id: string
  direction: 'upload' | 'download'
  filename: string
  src: string
  dst: string
  // 远端主机显示名 user@host（上传=目标主机，下载=来源主机）
  host: string
  total: number // -1 表示未知
  done: number
  status: 'running' | 'done' | 'failed' | 'canceled'
  error: string
  started_at: number
  finished_at: number
}

// ---- 编辑器文件接口 ----
export interface FileReadResult {
  path: string
  size: number
  readonly: boolean
  encoding: string
  content: string
  start_line: number
  lines_returned: number
  has_more: boolean
}

export interface FileListEntry {
  name: string
  is_dir: boolean
  size: number
  mtime: number
}

export interface FileListResult {
  dir: string
  parent: string | null
  entries: FileListEntry[]
}

export interface EditorDefaults {
  scripts_dir: string
  logs_dir: string
}

/** 编辑器广播目标会话条目（App 从终端实例列表派生） */
export interface EditorSessionInfo {
  session_id: string
  /** 显示名（终端名，如「终端1」/「本机 PowerShell」） */
  label: string
  /** 主机名（user@host 或 localhost） */
  host: string
  disconnected: boolean
}

const BASE = ''

async function request<T>(path: string, options?: RequestInit, timeoutMs?: number): Promise<T> {
  // 连接类请求（SSH 建连/重连）超时兜底：后端最迟 30s 返回，前端 35s 中止，
  // 避免"正在连接…"无限挂起（主机不可达/认证卡住时）
  let resp: Response
  if (timeoutMs && timeoutMs > 0) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      resp = await fetch(BASE + path, {
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        ...options,
      })
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError') {
        throw new Error(`连接超时（${Math.round(timeoutMs / 1000)}s 未响应），请确认主机可达后重试`)
      }
      throw new Error('网络错误：无法连接后端服务')
    } finally {
      clearTimeout(timer)
    }
  } else {
    resp = await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

// 主机管理
export const api = {
  // 全局配置（含 ui_settings：前端 UI 设置，持久化在 config.json）
  getConfig: () =>
    request<{
      fallback_local_shell: string
      local_shell_choices: string[]
      connect_timeout: number
      ui_settings: UiSettingsPayload
      config_file: string
    }>('/api/config'),
  // 设置默认本机终端 shell（默认终端条目 + SSH 断开后自动进入共用）
  setFallbackShell: (shell: string) =>
    request<{ status: string; fallback_local_shell: string }>('/api/config/fallback-shell', {
      method: 'POST',
      body: JSON.stringify({ shell }),
    }),
  // 设置 SSH 连接超时（秒，1-300）
  setConnectTimeout: (seconds: number) =>
    request<{ status: string; connect_timeout: number }>('/api/config/connect-timeout', {
      method: 'POST',
      body: JSON.stringify({ seconds }),
    }),
  // 部分更新前端 UI 设置（只传需要修改的键，非法值后端返回 400）
  updateUiSettings: (partial: Partial<UiSettingsPayload>) =>
    request<{ status: string; ui_settings: UiSettingsPayload }>('/api/config/ui-settings', {
      method: 'POST',
      body: JSON.stringify(partial),
    }),
  // 在系统文件管理器中打开配置文件所在目录（设置弹窗「打开目录」按钮）
  openConfigDir: () => request<{ status: string; dir: string }>('/api/config/open-dir', { method: 'POST' }),

  // 主机
  listHosts: () => request<{ hosts: Host[]; groups: string[]; host_types: HostType[] }>('/api/hosts'),
  addHost: (data: Partial<Host>) => request<Host>('/api/hosts', { method: 'POST', body: JSON.stringify(data) }),
  updateHost: (id: string, data: Partial<Host>) => request<Host>(`/api/hosts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteHost: (id: string) => request(`/api/hosts/${id}`, { method: 'DELETE' }),
  duplicateHost: (id: string) => request<Host>(`/api/hosts/${id}/duplicate`, { method: 'POST' }),

  // 分组
  addGroup: (name: string) => request('/api/groups', { method: 'POST', body: JSON.stringify({ name }) }),
  renameGroup: (oldName: string, newName: string) =>
    request(`/api/groups/${oldName}`, { method: 'PUT', body: JSON.stringify({ new_name: newName }) }),
  deleteGroup: (name: string) => request(`/api/groups/${name}`, { method: 'DELETE' }),
  duplicateGroup: (name: string) =>
    request<{ status: string; name: string; copied_hosts: Host[] }>(`/api/groups/${encodeURIComponent(name)}/duplicate`, { method: 'POST' }),

  // 排序（拖拽后保存顺序）
  reorderHosts: (ids: string[]) => request('/api/hosts/reorder', { method: 'POST', body: JSON.stringify({ ids }) }),
  reorderGroups: (names: string[]) => request('/api/groups/reorder', { method: 'POST', body: JSON.stringify({ names }) }),

  // 会话
  listSessions: () => request<{ sessions: Session[] }>('/api/sessions'),
  listActiveTerminals: () => request<{ terminals: ActiveTerminal[] }>('/api/sessions/active'),
  // password：可选密码覆盖（重连弹窗输入，优先于已保存密码；不传则后端用主机配置）
  connectHost: (host_id: string, terminal_name?: string, password?: string) =>
    request<{ session_id: string; status: string; terminal_name: string; host: Host }>('/api/sessions/from-host', {
      method: 'POST',
      body: JSON.stringify({ host_id, terminal_name, ...(password ? { password } : {}) }),
    }, 35000),
  // 原始连接信息创建会话（本地终端拦截 SSH 后的"重连"入口，无已保存主机）
  connectRaw: (conn: { host: string; port: number; username: string; password: string }, terminal_name?: string) =>
    request<{ session_id: string; status: string; terminal_name: string }>('/api/sessions/raw', {
      method: 'POST',
      body: JSON.stringify({ ...conn, terminal_name }),
    }, 35000),
  // 本机终端（cmd / powershell，winpty ConPTY，不经过 SSH）
  createLocalSession: (shell: string) =>
    request<{ session_id: string; status: string; terminal_name: string; host: string; shell: string }>('/api/local/session', {
      method: 'POST',
      body: JSON.stringify({ shell }),
    }),
  closeSession: (session_id: string) => request(`/api/sessions/${session_id}`, { method: 'DELETE' }),
  // 手动断开 SSH 并切回本机终端（顶部「断开」按钮）
  // 手动重连：复用当前终端会话从本机 shell 切回 SSH（历史/日志不丢）
  reconnectSession: (session_id: string, payload: { host: string; port: number; username: string; password?: string | null; private_key?: string | null; passphrase?: string | null }) =>
    request<{ status: string }>(`/api/sessions/${session_id}/reconnect`, { method: 'POST', body: JSON.stringify(payload) }),
  disconnectToLocal: (session_id: string) =>
    request<{ status: string }>(`/api/sessions/${session_id}/disconnect`, { method: 'POST' }),
  runCommand: (session_id: string, command: string, timeout = 30) =>
    request<CommandResult>(`/api/sessions/${session_id}/run`, {
      method: 'POST',
      body: JSON.stringify({ command, timeout }),
    }),
  getSessionState: (session_id: string) => request<TerminalState>(`/api/sessions/${session_id}/state`),
  // 获取 SSH 会话当前目录（cd 跟踪；SFTP 打开时定位初始目录）
  getSessionCwd: (session_id: string) => request<{ cwd: string | null }>(`/api/sessions/${session_id}/cwd`),
  // 批量获取终端状态（减少 HTTP 请求次数）
  getBatchSessionStates: (session_ids: string[]) =>
    request<{ states: Record<string, TerminalState & { state?: string }> }>('/api/sessions/states', {
      method: 'POST',
      body: JSON.stringify(session_ids),
    }),
  getHistoryLogs: (session_id: string, offset = 0, limit = 2000) =>
    request<{ session_id: string; offset: number; limit: number; content: string }>(
      `/api/sessions/${session_id}/logs?offset=${offset}&limit=${limit}`
    ),
  // 按会话ID从日志文件读历史（会话已删除仍可读，用于重连保留旧内容）
  getLogsByFile: (session_id: string, offset = 0, limit = 200000) =>
    request<{ session_id: string; content: string }>(
      `/api/logs/${session_id}?offset=${offset}&limit=${limit}`
    ),
  // 会话日志记录开关（默认不记录；开启记录时可指定保存目录，省略用后端默认目录）
  getSessionRecord: (session_id: string) =>
    request<{ session_id: string; recording: boolean }>(`/api/sessions/${session_id}/record`),
  setSessionRecord: (session_id: string, enabled: boolean, log_dir?: string) =>
    request<{ session_id: string; recording: boolean }>(`/api/sessions/${session_id}/record`, {
      method: 'POST',
      body: JSON.stringify({ enabled, log_dir: log_dir || null }),
    }),

  // ---- 编辑器：本地文件读写/目录浏览 ----
  readFile: (path: string, start_line = 0, max_lines = 2000000) =>
    request<FileReadResult>(
      `/api/files/read?path=${encodeURIComponent(path)}&start_line=${start_line}&max_lines=${max_lines}`
    ),
  writeFile: (path: string, content: string, encoding: string) =>
    request<{ path: string; size: number; encoding: string }>('/api/files/write', {
      method: 'POST',
      body: JSON.stringify({ path, content, encoding }),
    }),
  listDir: (dir: string) => request<FileListResult>(`/api/files/list?dir=${encodeURIComponent(dir)}`),
  editorDefaults: () => request<EditorDefaults>('/api/files/defaults'),
  // 系统原生文件对话框（本机后端弹窗，同机场景；非 Windows 返回 400）
  pickFile: (mode: 'open' | 'save', initialDir = '', title = '') =>
    request<{ status: 'picked' | 'canceled'; path: string }>('/api/files/pick', {
      method: 'POST',
      body: JSON.stringify({ mode, initial_dir: initialDir, title }),
    }),

  // 持久化终端（会话ID复用）
  listSavedTerminals: () => request<{ terminals: SavedTerminal[] }>('/api/terminals/saved'),
  restoreTerminal: (session_id: string) =>
    request<{ session_id: string; status: string; terminal_name: string }>(`/api/terminals/${session_id}/restore`, {
      method: 'POST',
    }),

  // 快速指令
  listQuickCommands: () => request<{ commands: QuickCommand[] }>('/api/quick-commands'),
  addQuickCommand: (data: Partial<QuickCommand>) =>
    request<QuickCommand>('/api/quick-commands', { method: 'POST', body: JSON.stringify(data) }),
  updateQuickCommand: (id: string, data: Partial<QuickCommand>) =>
    request<QuickCommand>(`/api/quick-commands/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteQuickCommand: (id: string) => request(`/api/quick-commands/${id}`, { method: 'DELETE' }),
  reorderQuickCommands: (ids: string[]) =>
    request('/api/quick-commands/reorder', { method: 'PUT', body: JSON.stringify({ ids }) }),

  // SFTP
  sftpList: (session_id: string, path: string) =>
    request<{ path: string; items: SftpItem[] }>(`/api/sftp/${session_id}/list`, {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  sftpRead: (session_id: string, path: string) =>
    request<{ path: string; content: string }>(`/api/sftp/${session_id}/read?path=${encodeURIComponent(path)}`),
  sftpWrite: (session_id: string, path: string, content: string) =>
    request<{ status: string; path: string }>(`/api/sftp/${session_id}/write`, {
      method: 'POST',
      body: JSON.stringify({ path, content }),
    }),
  sftpDelete: (session_id: string, path: string) =>
    request(`/api/sftp/${session_id}/delete`, { method: 'POST', body: JSON.stringify({ path }) }),
  // 远程文件下载到本机目录（服务器端流式下载，SFTP 双窗工作台用）
  sftpDownloadTo: (session_id: string, remote_path: string, local_dir: string) =>
    request<{ status: string; path: string; local: string; size: number }>(`/api/sftp/${session_id}/download-to`, {
      method: 'POST',
      body: JSON.stringify({ remote_path, local_dir }),
    }),
  sftpDownloadUrl: (session_id: string, path: string) =>
    `/api/sftp/${session_id}/download?path=${encodeURIComponent(path)}`,
  // 传输任务列表（活动在前）与取消
  listTransfers: (sessionId?: string | null) =>
    request<{ transfers: SftpTransfer[] }>(
      `/api/sftp/transfers${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`
    ),
  cancelTransfer: (id: string) =>
    request<{ status: string }>(`/api/sftp/transfers/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  // 下载任务完成后打开本机目录并选中文件（仅本机路径的任务可用）
  revealTransfer: (id: string) =>
    request<{ status: string; path: string }>(`/api/sftp/transfers/${encodeURIComponent(id)}/reveal`, { method: 'POST' }),
  // 上传本地文件到远端（multipart，XHR 以支持上传进度回调）
  sftpUpload: (session_id: string, remote_path: string, file: File, onProgress?: (pct: number) => void) =>
    new Promise<{ status: string; path: string; size: number }>((resolve, reject) => {
      const fd = new FormData()
      fd.append('file', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${BASE}/api/sftp/${session_id}/upload?remote_path=${encodeURIComponent(remote_path)}`)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch {
            resolve({ status: 'uploaded', path: remote_path, size: 0 })
          }
        } else {
          let detail = `HTTP ${xhr.status}`
          try {
            detail = JSON.parse(xhr.responseText).detail || detail
          } catch { /* 非 JSON 错误体 */ }
          reject(new Error(detail))
        }
      }
      xhr.onerror = () => reject(new Error('网络错误'))
      xhr.send(fd)
    }),

  // 预操作上传：后端直读本机源文件（绝对路径或 scripts 目录文件）后 SFTP 上传
  preopUpload: (session_id: string, source: string, source_type: string, remote: string) =>
    request<{ status: string; source: string; path: string; size: number }>('/api/preop/upload', {
      method: 'POST',
      body: JSON.stringify({ session_id, source, source_type, remote }),
    }),

  // scripts 脚本目录文件列表（预操作上传下拉选择）
  listScripts: () => request<{ scripts: string[]; dir: string }>('/api/scripts'),

  // 检查/刷新配置与脚本（扫描 config/data/scripts）
  reloadConfig: () => request<Record<string, unknown>>('/api/config/reload', { method: 'POST' }),

  // 全局命令历史（跨终端，按使用频次排序）
  recordCommand: (command: string) =>
    request('/api/history/record', { method: 'POST', body: JSON.stringify({ command }) }),
  searchCommands: (keyword: string = '', limit: number = 50) =>
    request<{ keyword: string; commands: Array<{ command: string; count: number; last_used: number }> }>(
      `/api/history/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`
    ),
  // 统一搜索：同时搜索快捷命令和历史命令
  unifiedSearch: (keyword: string = '', limit: number = 50) =>
    request<{
      keyword: string
      results: Array<
        | { type: 'quick'; id: string; name: string; command: string }
        | { type: 'history'; command: string; count: number; last_used: number }
      >
    }>(`/api/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),

  // 最近使用的命令（按最后使用时间降序，已忽略不返回）：历史搜索空输入第 1 页数据源
  historyRecent: (limit: number = 20) =>
    request<{ commands: Array<{ command: string; count: number; last_used: number }> }>(
      `/api/history/recent?limit=${limit}`
    ),

  // 历史命令忽略/恢复
  ignoreHistoryCommand: (command: string) =>
    request<{ status: string }>('/api/history/ignore', { method: 'POST', body: JSON.stringify({ command }) }),
  unignoreHistoryCommand: (command: string) =>
    request<{ status: string }>('/api/history/unignore', { method: 'POST', body: JSON.stringify({ command }) }),
  listIgnoredCommands: (limit: number = 200) =>
    request<{ commands: Array<{ command: string; count: number; last_used: number }> }>(
      `/api/history/ignored?limit=${limit}`
    ),
  // 清空全部命令历史（返回删除条数）
  clearHistory: () =>
    request<{ status: string; cleared: number }>('/api/history/clear', { method: 'POST' }),
}
