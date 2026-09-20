// WebSocket 消息协议类型（/ws/ssh/{session_id} 通道）
// 与后端 ssh_web_tool/ws_protocol.py 一一对应；改动必须同步两侧，
// 一致性与必填字段由 tests/protocol/test_ws_protocol.py 校验

// ---------- 服务端 → 客户端（下行） ----------

export interface WsOutput {
  type: 'output' // 终端输出
  data: string
}

export interface WsInfo {
  type: 'info' // 提示（黄色）
  data: string
}

export interface WsError {
  type: 'error' // 错误（红色）
  data: string
}

export interface WsClosed {
  type: 'closed' // 会话关闭
  data: string
}

export type WsShell = 'cmd' | 'powershell' | 'pwsh'

export interface WsSshConnected {
  type: 'ssh_connected' // 本地终端拦截 SSH 成功，切换到远端（附重连凭据）
  host: string
  port: number
  username: string
  password: string
  terminal_name: string
  host_id?: string // 拦截连接匹配到已保存主机时下发（列表计数/单击轮换跟随挂载）
}

export interface WsSwitchedToLocal {
  type: 'switched_to_local' // SSH 退出/断开 → 已自动切到本机 shell
  shell: WsShell
  terminal_name: string
}

export interface WsSshDisconnected {
  type: 'ssh_disconnected' // SSH 已断开（仅页面重开恢复路径）：服务端发送后关闭 WebSocket，前端 onclose 走断开态
  data: string
}

export interface WsPong {
  type: 'pong'
}

export type WsServerMessage =
  | WsOutput
  | WsInfo
  | WsError
  | WsClosed
  | WsSshConnected
  | WsSwitchedToLocal
  | WsSshDisconnected
  | WsPong

// ---------- 客户端 → 服务端（上行） ----------

export interface WsInput {
  type: 'input'
  data: string
}

export interface WsResize {
  type: 'resize'
  cols: number
  rows: number
}

export interface WsPing {
  type: 'ping'
}

export type WsClientMessage = WsInput | WsResize | WsPing

// ---------- 事件通道 /ws/events（服务端 → 客户端；event_bus.publish 广播） ----------

// 事件消息：event_type 取值见后端 event_bus.py（session_create / command_run / sftp_* 等）
export interface WsEventMessage {
  type: 'event'
  event_type: string
  source: string
  detail: string
  timestamp: number
  time_str: string
  [key: string]: unknown // 附加数据（session_id / host / port 等 kwargs）
}

export type WsEventsServerMessage = WsEventMessage

// type 字面量集合（一致性测试从本文件解析：union 成员接口的 type 字段）
// 终端通道服务端类型：output / info / error / closed / ssh_connected / switched_to_local / ssh_disconnected / pong
// 终端通道客户端类型：input / resize / ping
// 事件通道服务端类型：event