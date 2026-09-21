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
  // 挂载到该主机的会话总数（含断开后退回本机终端、仍保留归属的）；徽标显示"在线/总数"
  terminal_total?: number
  is_connected?: boolean
  // 敏感字段已设置标记（密码等明文下发，便于前端展示/编辑确认）
  has_password?: boolean
  has_mgmt_password?: boolean
  has_private_key?: boolean
  // 连接信息（后端附加）
  connected_since?: number | null
  connected_duration?: number | null
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
  // 该会话当前是否有活跃 WebSocket（其他页面正在使用）：restore 时跳过，避免抢占连接
  ws_active?: boolean
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
  // 本地终端拦截 SSH 命令建立的会话（无已保存主机）：原始连接信息，重连凭据
  ssh_conn?: { host: string; port: number; username: string; password: string } | null
  created_at: number
  last_active: number
  // 该会话当前是否有活跃 WebSocket（其他页面正在使用）：restore 时跳过，避免抢占连接
  ws_active?: boolean
}

// 快捷指令预操作（执行命令前依次执行）
export interface QuickPreOp {
  type: 'upload' | 'chmod' | 'exec'
  // upload: 源文件类型 path=本机绝对路径（Server 同机直读） / script=scripts 目录下的文件
  source_type?: 'path' | 'script'
  source?: string  // upload: 源文件（本机绝对路径 或 scripts 下的文件名）
  remote?: string  // upload: 远端目标路径
  mode?: string    // chmod: 权限模式，如 +x / 755
  path?: string    // chmod: 目标文件路径
  cmd?: string     // exec: 先执行的命令（自由命令，export/cd/任意 shell 语句）
}

export interface QuickCommand {
  id: string
  name: string
  command: string
  description?: string
  // 指令类型：direct 直接执行 / param 带参数（点击后输入到终端，替换命令中的 {args} 占位符）
  type?: 'direct' | 'param'
  pre_ops?: QuickPreOp[]
  // 可选短标识（唯一，大小写不敏感），用于 .zs 脚本 @ 调用
  key?: string
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
