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
  closeSession: (session_id: string) => request(`/api/sessions/${session_id}`, { method: 'DELETE' }),
  runCommand: (session_id: string, command: string, timeout = 30) =>
    request<CommandResult>(`/api/sessions/${session_id}/run`, {
      method: 'POST',
      body: JSON.stringify({ command, timeout }),
    }),
  getSessionState: (session_id: string) => request<TerminalState>(`/api/sessions/${session_id}/state`),
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
}
