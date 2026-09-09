// API 调用封装
import type { Host, HostType, Session, ActiveTerminal, QuickCommand, SftpItem, CommandResult, TerminalState, SavedTerminal } from '../types'

const BASE = ''

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

// 主机管理
export const api = {
  // 全局配置
  getConfig: () => request<Record<string, never>>('/api/config'),

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
  connectHost: (host_id: string, terminal_name?: string) =>
    request<{ session_id: string; status: string; terminal_name: string; host: Host }>('/api/sessions/from-host', {
      method: 'POST',
      body: JSON.stringify({ host_id, terminal_name }),
    }),
  // 本机终端（cmd / powershell，winpty ConPTY，不经过 SSH）
  createLocalSession: (shell: string) =>
    request<{ session_id: string; status: string; terminal_name: string; host: string; shell: string }>('/api/local/session', {
      method: 'POST',
      body: JSON.stringify({ shell }),
    }),
  closeSession: (session_id: string) => request(`/api/sessions/${session_id}`, { method: 'DELETE' }),
  runCommand: (session_id: string, command: string, timeout = 30) =>
    request<CommandResult>(`/api/sessions/${session_id}/run`, {
      method: 'POST',
      body: JSON.stringify({ command, timeout }),
    }),
  getSessionState: (session_id: string) => request<TerminalState>(`/api/sessions/${session_id}/state`),
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
  sftpDownloadUrl: (session_id: string, path: string) =>
    `/api/sftp/${session_id}/download?path=${encodeURIComponent(path)}`,
  // 上传本地文件到远端（multipart）
  sftpUpload: async (session_id: string, remote_path: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const resp = await fetch(`${BASE}/api/sftp/${session_id}/upload?remote_path=${encodeURIComponent(remote_path)}`, {
      method: 'POST',
      body: fd,
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    return resp.json()
  },

  // 预操作上传：后端直读本机源文件（绝对路径或 scripts 目录文件）后 SFTP 上传
  preopUpload: (session_id: string, source: string, source_type: string, remote: string) =>
    request('/api/preop/upload', {
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

  // 历史命令忽略/恢复
  ignoreHistoryCommand: (command: string) =>
    request<{ status: string }>('/api/history/ignore', { method: 'POST', body: JSON.stringify({ command }) }),
  unignoreHistoryCommand: (command: string) =>
    request<{ status: string }>('/api/history/unignore', { method: 'POST', body: JSON.stringify({ command }) }),
  listIgnoredCommands: (limit: number = 200) =>
    request<{ commands: Array<{ command: string; count: number; last_used: number }> }>(
      `/api/history/ignored?limit=${limit}`
    ),
}
