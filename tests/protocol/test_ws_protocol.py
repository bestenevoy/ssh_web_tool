"""前后端 WebSocket 消息协议一致性测试

- 事实来源：ssh_web_tool/ws_protocol.py（常量 + 必填字段）
- 前端对应：frontend/src/types/ws.ts（WsServerMessage / WsClientMessage 联合类型）
- 本测试解析 ws.ts 校验：类型集合与必填字段完全一致。
  单侧增删消息类型 / 字段必须同步另一侧，否则 CI 即红。

解析约定（保持 ws.ts 结构简单来保证测试成立）：
- 联合别名 `export type WsXxx = ...` 的成员均为接口名
- 接口均为扁平字段（无嵌套对象），以首个 `}` 结束
"""

import re
from pathlib import Path

import pytest

from ssh_web_tool import ws_protocol

FRONTEND_WS_TS = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "types" / "ws.ts"

# 所有构造 WS 下行消息的后端文件（结构测试与字面量测试共用）
BACKEND_WS_FILES = [
    Path(__file__).resolve().parent.parent.parent / "ssh_web_tool" / "api" / "routers" / "ws.py",
    Path(__file__).resolve().parent.parent.parent / "ssh_web_tool" / "api" / "routers" / "external.py",
    Path(__file__).resolve().parent.parent.parent / "ssh_web_tool" / "api" / "ws_common.py",
    Path(__file__).resolve().parent.parent.parent / "ssh_web_tool" / "event_bus.py",
    Path(__file__).resolve().parent.parent.parent / "ssh_web_tool" / "sessions.py",
]

_UNION_RE = re.compile(
    r"export type (WsServerMessage|WsClientMessage|WsEventsServerMessage) =([\s\S]*?)(?=\nexport type |\Z)"
)
_INTERFACE_RE = re.compile(r"export interface (\w+) \{([\s\S]*?)\n\}")
_TYPE_LITERAL_RE = re.compile(r"type:\s*'([^']+)'")


def _parse_ts() -> dict[str, str]:
    """解析 ws.ts，返回 {消息类型: 所属联合别名}"""
    src = FRONTEND_WS_TS.read_text(encoding="utf-8")
    interfaces = dict(_INTERFACE_RE.findall(src))
    result: dict[str, str] = {}
    for alias, body in _UNION_RE.findall(src):
        members = {m for m in re.findall(r"\b[A-Z][A-Za-z0-9]+\b", body) if m in interfaces}
        for member in members:
            literals = _TYPE_LITERAL_RE.findall(interfaces[member])
            assert len(literals) == 1, f"{alias} 成员 {member} 必须恰好一个 type 字面量，实际: {literals}"
            result[literals[0]] = alias
    return result


def test_ts_file_exists():
    assert FRONTEND_WS_TS.is_file(), f"前端 ws 类型文件不存在: {FRONTEND_WS_TS}"


def test_union_type_sets_match_backend():
    """类型集合完全一致（双向）"""
    ts = _parse_ts()
    server_expected = set(ws_protocol.SERVER_MSG_TYPES)
    client_expected = set(ws_protocol.CLIENT_MSG_TYPES)

    ts_server = {t for t, alias in ts.items() if alias == "WsServerMessage"}
    ts_client = {t for t, alias in ts.items() if alias == "WsClientMessage"}

    assert ts_server == server_expected, (
        f"服务端→客户端消息类型不一致\n"
        f"  ws.ts 独有: {sorted(ts_server - server_expected)}\n"
        f"  ws_protocol 独有: {sorted(server_expected - ts_server)}"
    )
    assert ts_client == client_expected, (
        f"客户端→服务端消息类型不一致\n"
        f"  ws.ts 独有: {sorted(ts_client - client_expected)}\n"
        f"  ws_protocol 独有: {sorted(client_expected - ts_client)}"
    )

    ts_event = {t for t, alias in ts.items() if alias == "WsEventsServerMessage"}
    event_expected = set(ws_protocol.EVENT_MSG_TYPES)
    assert ts_event == event_expected, (
        f"事件通道消息类型不一致\n"
        f"  ws.ts 独有: {sorted(ts_event - event_expected)}\n"
        f"  ws_protocol 独有: {sorted(event_expected - ts_event)}"
    )


def test_required_fields_present_in_frontend():
    """后端声明为必填的字段，前端对应接口必须已声明"""
    src = FRONTEND_WS_TS.read_text(encoding="utf-8")
    interfaces = dict(_INTERFACE_RE.findall(src))
    missing: list[str] = []
    for msg_type, fields in ws_protocol.MSG_REQUIRED_FIELDS.items():
        body = next(
            (body for body in interfaces.values() if (m := _TYPE_LITERAL_RE.search(body)) and m.group(1) == msg_type),
            None,
        )
        assert body is not None, f"ws.ts 中找不到 type={msg_type} 的接口"
        for field in fields:
            if not re.search(rf"^\s*{field}(?:\?)?\s*:", body, re.MULTILINE):
                missing.append(f"{msg_type}.{field}")
    assert not missing, f"前端类型缺失必填字段: {missing}"


def test_backend_uses_constants_not_literal_types():
    """后端路由构造消息必须引用 ws_protocol 常量（禁止手写 type 字符串漂移）"""
    targets = BACKEND_WS_FILES
    allowed_literals = (
        set(ws_protocol.SERVER_MSG_TYPES) | set(ws_protocol.CLIENT_MSG_TYPES) | set(ws_protocol.EVENT_MSG_TYPES)
    )
    for path in targets:
        src = path.read_text(encoding="utf-8")
        # 仅扫描 send_json 出现的行：SFTP 等 HTTP 响应的 "type" 字面量（dir/file 等）不属本协议
        for lineno, line in enumerate(src.splitlines(), start=1):
            if "send_json" not in line:
                continue
            for match in re.finditer(r'"type"\s*:\s*"([^"]+)"', line):
                assert match.group(1) in allowed_literals, (
                    f'{path.name}:{lineno} 手写 type 字面量 "type": "{match.group(1)}"，应改用 ws_protocol 常量'
                )


def test_backend_constants_cover_required_fields():
    """MSG_REQUIRED_FIELDS 声明的类型必须都属于 CLIENT/SERVER 分组"""
    declared = set(ws_protocol.MSG_REQUIRED_FIELDS)
    grouped = set(ws_protocol.CLIENT_MSG_TYPES) | set(ws_protocol.SERVER_MSG_TYPES) | set(ws_protocol.EVENT_MSG_TYPES)
    assert declared == grouped, (
        f"MSG_REQUIRED_FIELDS 与分组不一致\n"
        f"  仅在必填声明中: {sorted(declared - grouped)}\n"
        f"  仅分组中有: {sorted(grouped - declared)}"
    )


def _send_json_args(src: str) -> list[tuple[int, str]]:
    """括号配平提取所有 send_json(...) 调用的参数文本，返回 (起始行号, 参数文本)"""
    calls: list[tuple[int, str]] = []
    for m in re.finditer(r"send_json\(", src):
        depth = 1
        i = m.end()
        while i < len(src) and depth > 0:
            ch = src[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        lineno = src.count("\n", 0, m.start()) + 1
        calls.append((lineno, src[m.end() : i - 1]))
    return calls


def test_send_json_messages_have_type_key():
    """所有 WS 下行消息必须带 "type" 键。

    防回归：常量化改造曾把常量误当 dict key（send_json({SERVER_ERROR: "..."})），
    消息丢失 "type"/"data" 包装后前端 msg.type 匹配失败、终端完全无输出，
    且静态字面量扫描对此无感（参数里没有 "type": "字面量" 模式）。
    """
    for path in BACKEND_WS_FILES:
        src = path.read_text(encoding="utf-8")
        for lineno, arg in _send_json_args(src):
            arg = arg.strip()
            if not arg.startswith("{"):
                continue  # 变量传参（如 send_json(ev)），由构造方保证
            assert '"type"' in arg, f'{path.name}:{lineno} send_json 消息缺少 "type" 键: {arg[:60]!r}'


def test_ws_events_ping_pong_shape(client):
    """运行时验证事件通道消息形态：ping → {"type": "pong"}

    事件总线是进程级单例，订阅时可能回放此前测试累积的历史事件，故循环消费直到 pong。
    """
    with client.websocket_connect("/ws/events") as ws:
        ws.send_json({"type": "ping"})
        for _ in range(200):
            msg = ws.receive_json()
            if msg.get("type") == "pong":
                break
        else:
            pytest.fail("事件通道 200 条消息内未收到 pong")


def test_ws_ssh_unknown_session_error_shape(client):
    """运行时验证终端通道错误消息形态：{"type": "error", "data": ...}"""
    with client.websocket_connect("/ws/ssh/nonexistent-session") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "会话不存在" in msg["data"]


def test_ws_external_stream_connect_and_replay(client):
    """运行时验证外部观测通道：connect 入库 → 列表可见 → 订阅回放历史"""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "session_id": "ext-1",
                "type": "connect",
                "host": "10.0.0.9",
                "port": 2222,
                "username": "root",
                "kind": "paramiko",
                "ts": 1000.0,
            }
        )
        # 同连接 ping/pong 往返保证 FIFO：connect 一定已被服务端处理
        ws.send_json({"type": "ping"})
        for _ in range(50):
            if ws.receive_json().get("type") == "pong":
                break
        else:
            pytest.fail("stream 通道未收到 pong")

    sessions = client.get("/api/external-sessions").json()["sessions"]
    assert sessions[0]["session_id"] == "ext-1"
    assert sessions[0]["host"] == "10.0.0.9"
    assert sessions[0]["port"] == 2222
    assert sessions[0]["command_count"] == 0

    with client.websocket_connect("/ws/external/ext-1") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "connect"
        assert ev["host"] == "10.0.0.9"
