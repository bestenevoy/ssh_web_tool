"""WebSocket 消息协议（单一事实来源：交互式终端 /ws/ssh/{session_id} 与事件订阅 /ws/events 通道）

- 后端发送用下方常量构造消息（禁止手写字符串，防拼写漂移）
- 前端类型定义见 frontend/src/types/ws.ts（服务端/客户端消息 discriminated union）
- tests/protocol/test_ws_protocol.py 校验两侧类型集合与必填字段一致，改动必须同步两侧
"""

# ---------- 客户端 → 服务端（上行） ----------
CLIENT_INPUT = "input"  # {data: str}
CLIENT_RESIZE = "resize"  # {cols: int, rows: int}
CLIENT_PING = "ping"

# ---------- 服务端 → 客户端（下行） ----------
SERVER_OUTPUT = "output"  # {data: str} 终端输出
SERVER_INFO = "info"  # {data: str} 提示（黄色）
SERVER_ERROR = "error"  # {data: str} 错误（红色）
SERVER_CLOSED = "closed"  # {data: str} 会话关闭
SERVER_SSH_CONNECTED = "ssh_connected"  # {host, port, username, password, terminal_name}
SERVER_SWITCHED_TO_LOCAL = "switched_to_local"  # {shell, terminal_name} SSH 退出/断开切本机 shell
SERVER_SSH_DISCONNECTED = (
    "ssh_disconnected"  # {data} SSH 已断开（仅页面重开恢复路径）：通知后关闭 WebSocket，前端走断开态
)
SERVER_PONG = "pong"

# ---------- 事件通道 /ws/events（服务端 → 客户端；event_bus.publish 广播） ----------
SERVER_EVENT = "event"  # {event_type, source, detail, timestamp, time_str, **kwargs}

# ---------- 分组（一致性测试以此为基准） ----------
CLIENT_MSG_TYPES: tuple[str, ...] = (CLIENT_INPUT, CLIENT_RESIZE, CLIENT_PING)
SERVER_MSG_TYPES: tuple[str, ...] = (
    SERVER_OUTPUT,
    SERVER_INFO,
    SERVER_ERROR,
    SERVER_CLOSED,
    SERVER_SSH_CONNECTED,
    SERVER_SWITCHED_TO_LOCAL,
    SERVER_SSH_DISCONNECTED,
    SERVER_PONG,
)
EVENT_MSG_TYPES: tuple[str, ...] = (SERVER_EVENT,)

# ---------- 外部观测通道 /ws/stream（ssh-monkeypatch 测试进程 → 服务端） ----------
# 事件由独立 pip 包在测试进程侧构造（无法反向依赖本项目，契约见 docs/external-observation.md），
# 服务端 external_sessions.py 按以下常量匹配事件类型
EXT_CONNECT = "connect"
EXT_COMMAND = "command"
EXT_OUTPUT = "output"
EXT_CLOSE = "close"
EXT_EVENT_TYPES: tuple[str, ...] = (EXT_CONNECT, EXT_COMMAND, EXT_OUTPUT, EXT_CLOSE)

# 各消息类型必填字段：字段变更必须同步前端 ws.ts 对应接口与一致性测试
MSG_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    CLIENT_INPUT: ("data",),
    CLIENT_RESIZE: ("cols", "rows"),
    CLIENT_PING: (),
    SERVER_OUTPUT: ("data",),
    SERVER_INFO: ("data",),
    SERVER_ERROR: ("data",),
    SERVER_CLOSED: ("data",),
    SERVER_SSH_CONNECTED: ("host", "port", "username", "password", "terminal_name"),
    SERVER_SWITCHED_TO_LOCAL: ("shell", "terminal_name"),
    SERVER_SSH_DISCONNECTED: ("data",),
    SERVER_PONG: (),
    SERVER_EVENT: ("event_type", "source", "detail", "timestamp", "time_str"),
}
