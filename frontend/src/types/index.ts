// API 类型定义

export interface Host {
  id: string
  name: string
  host: string
  port: number
  username: string
  password: string
  private_key: string
  passphrase: string
  type: string
  group: string
  // 设备类型：linux（普通主机）/ storage（存储阵列）
  device_type?: string
  // 存储阵列管理页面配置
  mgmt_port?: number
  mgmt_username?: string
  mgmt_password?: string
  // Playwright 自动登录选择器配置（用户自行填写）
  pw_username_selector?: string
  pw_password_selector?: string
  pw_login_btn_selector?: string
  pw_old_password_selector?: string
  pw_new_password_selector?: string
  pw_confirm_password_selector?: string
  pw_confirm_btn_selector?: string
  pw_success_selector?: string
  pw_headless?: boolean
  terminal_count?: number
  is_connected?: boolean
}

export interface HostType {
  key: string
  label: string
  color: string
}

export interface Session {
  session_id: string
  host: string
  port: number
  username: string
  host_id: string
  terminal_name: string
  connected: boolean
  has_shell: boolean
  created_at: number
  last_active: number
}

export interface ActiveTerminal {
  session_id: string
  host_id: string
  host: string
  port: number
  username: string
  terminal_name: string
  host_name: string
  host_type: string
  created_at: number
  last_active: number
}

export interface QuickCommand {
  id: string
  name: string
  command: string
  description?: string
}

export interface SftpItem {
  name: string
  type: 'dir' | 'file' | 'unknown'
  size: number
  mtime: number
  mode: string
}

export interface CommandResult {
  returncode: number | null
  stdout: string
  stderr: string
  mode?: string
}

export interface TerminalState {
  session_id?: string
  connected?: boolean
  foreground_process: string
  in_python: boolean
  in_mysql: boolean
  in_pager: boolean
  in_shell: boolean
  has_prompt: boolean
  last_line: string
}

export interface SavedTerminal {
  session_id: string
  host_id: string
  terminal_name: string
  created_at: number
  last_used: number
  is_active: boolean
}

export interface EventLogItem {
  type: 'event'
  event_type: string
  source: string
  detail: string
  timestamp: number
  time_str: string
  [key: string]: any
}
