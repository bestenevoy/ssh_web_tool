# -*- coding: utf-8 -*-
"""
ssh-monkeypatch 核心：劫持当前进程内 paramiko / asyncssh 的 SSH 连接入口，
自动登记连接状态，并采集 SSH 事件（连接 / 命令 / stdout/stderr 输出 / 关闭）。

采集到的事件可：
1. 保留在包内事件缓冲（get_events() / list_events(session_id)）；
2. 通过 start_streamer(ws_url) 用 WebSocket 推送到 ssh_web_tool 可视化服务
   （测试进程与可视化服务完全分离，跨进程实时展示与历史回放）。

不修改原有测试业务逻辑：只包装流对象的读方法，测试代码读到哪儿采集到哪儿。
"""
import threading
import time
import uuid
from typing import Dict, List, Optional

__all__ = [
    "Connection",
    "registry",
    "list_connections",
    "get_connection",
    "clear_registry",
    "bridge_to_web",
    "patch_paramiko",
    "patch_asyncssh",
    "patch_all",
    "unpatch",
    "set_capture_unread",
    "get_events",
    "list_events",
    "clear_events",
    "start_streamer",
    "stop_streamer",
]

# ============ 连接登记 ============

_registry: Dict[str, "Connection"] = {}
_registry_lock = threading.Lock()
registry = _registry

# 被替换的原始函数（unpatch 时恢复）
_original_paramiko_connect = None
_original_asyncssh_connect = None
_patched_paramiko = False
_patched_asyncssh = False

# Web UI 桥接（可选）：同步注册 ssh_web_tool 镜像会话
_web_bridge = False

# 事件采集
_events: List[dict] = []
_events_lock = threading.Lock()
_MAX_EVENTS = 100000

# 测试代码不读取输出时，是否后台线程自动采集（默认 False：读哪儿采哪儿，不抢数据）
_capture_unread = False

# 推送目标（WebSocket 可视化服务）
_streamer = None  # type: Optional["_WsStreamer"]


class Connection:
    """一条被劫持登记的 SSH 连接"""

    def __init__(self, host: str, port: int, username: str, kind: str,
                 client=None, conn=None):
        self.id = str(uuid.uuid4())[:8]
        self.host = host
        self.port = port
        self.username = username
        self.kind = kind            # 'paramiko' | 'asyncssh'
        self.client = client        # paramiko.SSHClient / asyncssh 连接对象
        self.conn = conn
        self.connected_at = time.time()
        self._closed = False

    @property
    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            if self.kind == 'paramiko' and self.client is not None:
                t = getattr(self.client, 'get_transport', None)
                return bool(t and t() and t().is_active())
            if self.kind == 'asyncssh' and self.conn is not None:
                return not self.conn.is_closed()
        except Exception:
            pass
        return not self._closed

    def mark_closed(self):
        self._closed = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "kind": self.kind,
            "connected_at": self.connected_at,
            "is_alive": self.is_alive,
        }

    def __repr__(self):
        return f"<Connection {self.kind} {self.username}@{self.host}:{self.port} {'alive' if self.is_alive else 'closed'}>"


# ============ 事件采集 ============

def emit_event(event_type: str, session_id: str, **fields):
    """记录一条 SSH 事件（内部缓冲 + 推送流）"""
    ev = {
        "type": event_type,
        "session_id": session_id,
        "ts": time.time(),
    }
    ev.update(fields)
    with _events_lock:
        _events.append(ev)
        if len(_events) > _MAX_EVENTS:
            del _events[: len(_events) - _MAX_EVENTS]
    s = _streamer
    if s is not None:
        s.send(ev)


def get_events(session_id: Optional[str] = None, limit: int = 500) -> List[dict]:
    """获取采集的事件（可只查某个 session_id，按时间正序）"""
    with _events_lock:
        if session_id is None:
            return list(_events[-limit:])
        return [e for e in _events if e.get("session_id") == session_id][-limit:]


def list_events(session_id: str, limit: int = 500) -> List[dict]:
    """按会话列出事件（alias）"""
    return get_events(session_id, limit)


def clear_events():
    with _events_lock:
        _events.clear()


def set_capture_unread(enabled: bool = True):
    """
    设置是否后台自动采集"测试代码未读取"的输出。

    - False（默认）：只采集测试代码实际读到的字节（读哪儿采哪儿），
      不会与业务逻辑抢数据；
    - True：每个 exec_command 启动后台线程持续读取 stdout/stderr 采集
      （适合测试代码执行命令后不读取输出的场景；注意会消费输出流）。
    """
    global _capture_unread
    _capture_unread = bool(enabled)


# ============ 流包装（读哪儿采哪儿） ============

class _ForwardStream:
    """包装 paramiko ChannelFile：读到的字节同时转发为 output 事件"""

    def __init__(self, base, session_id, stream_name, channel):
        self._base = base
        self._sid = session_id
        self._name = stream_name
        self._channel = channel
        self._eof_sent = False
        self._lock = threading.Lock()

    def _forward(self, data: bytes):
        if not data:
            return
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = repr(data)
        emit_event("output", self._sid, stream=self._name, data=text)

    def _check_eof(self):
        if not self._eof_sent:
            self._eof_sent = True
            emit_event("stream_eof", self._sid, stream=self._name)

    # ---- 读方法 ----
    def read(self, n=-1):
        data = self._base.read(n)
        if data:
            self._forward(data)
        if not data:
            self._check_eof()
        return data

    def readline(self, size=-1):
        line = self._base.readline(size)
        if line:
            self._forward(line)
        if not line:
            self._check_eof()
        return line

    def readlines(self, hint=-1):
        lines = self._base.readlines(hint)
        for ln in lines:
            self._forward(ln)
        if not lines:
            self._check_eof()
        return lines

    # ---- 迭代（for line in stdout） ----
    def __iter__(self):
        return self

    def __next__(self):
        line = self._base.readline()
        if line:
            self._forward(line)
            return line
        self._check_eof()
        raise StopIteration

    def close(self):
        try:
            self._base.close()
        except Exception:
            pass

    def flush(self):
        try:
            self._base.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._base, name)


def _drain_channel(session_id, channel, stream_name):
    """后台线程：持续读取 channel 输出并采集（capture_unread=True 时使用）"""
    try:
        while True:
            chunk = channel.recv(4096)
            if not chunk:
                break
            emit_event("output", session_id, stream=stream_name, data=chunk.decode("utf-8", errors="replace"))
    except Exception:
        pass
    finally:
        emit_event("stream_eof", session_id, stream=stream_name)


# ============ 登记与事件关联 ============

def _register(host, port, username, kind, client=None, conn=None) -> Optional[str]:
    with _registry_lock:
        c = Connection(host, port, username, kind, client=client, conn=conn)
        _registry[c.id] = c
        cid = c.id
    try:
        if _web_bridge:
            _register_web_mirror(c)
    except Exception as e:
        print(f"[ssh-monkeypatch] Web 桥接注册失败: {e}")
    emit_event("connect", cid, host=host, port=port, username=username or "", kind=kind)
    print(f"[ssh-monkeypatch] 已登记 {kind} 连接: {username}@{host}:{port} (id={cid})")
    return cid


def _unregister(cid: str):
    with _registry_lock:
        c = _registry.pop(cid, None)
    if c is not None:
        c.mark_closed()
        emit_event("close", cid)
        print(f"[ssh-monkeypatch] 连接已关闭并移除登记: {c.username}@{c.host}:{c.port} (id={cid})")
    try:
        if _web_bridge:
            _unregister_web_mirror(cid)
    except Exception:
        pass


def _hook_close(obj, cid: str):
    if getattr(obj, '_ssh_monkeypatch_close_hooked', False):
        return
    original_close = obj.close

    def patched_close(*args, **kwargs):
        try:
            return original_close(*args, **kwargs)
        finally:
            _unregister(cid)

    obj.close = patched_close
    obj._ssh_monkeypatch_close_hooked = True


# ============ 可选 Web 桥接（ssh_web_tool 镜像会话） ============

def bridge_to_web(enable: bool = True) -> bool:
    global _web_bridge
    if enable:
        try:
            from ssh_web_tool.sessions import session_manager  # noqa: F401
            _web_bridge = True
            print("[ssh-monkeypatch] 已开启 ssh_web_tool 桥接（镜像会话同步到 Web UI）")
            return True
        except ImportError:
            print("[ssh-monkeypatch] 未安装 ssh_web_tool，无法开启 Web 桥接（pip install ssh-monkeypatch[web]）")
            _web_bridge = False
            return False
    _web_bridge = False
    print("[ssh-monkeypatch] 已关闭 Web 桥接")
    return True


def _register_web_mirror(c: Connection):
    from ssh_web_tool.sessions import session_manager
    sid = session_manager.create_session(c.host, c.port, c.username,
                                         terminal_name=f"{c.kind}-{c.host}")
    session = session_manager.get_session(sid)
    session._external = True
    if c.kind == 'paramiko':
        session._paramiko_client = c.client
    else:
        session._asyncssh_conn = c.conn
    session._connected = True
    session.last_active = time.time()
    c._web_session_id = sid


def _unregister_web_mirror(cid: str):
    from ssh_web_tool.sessions import session_manager
    c = get_connection(cid)
    sid = getattr(c, '_web_session_id', None) if c else None
    if sid:
        session_manager.remove_session_sync(sid)


# ============ Registry 查询 ============

def list_connections() -> list:
    with _registry_lock:
        conns = sorted(_registry.values(), key=lambda x: x.connected_at)
    return [c.to_dict() for c in conns]


def get_connection(cid: str) -> Optional[Connection]:
    with _registry_lock:
        return _registry.get(cid)


def clear_registry():
    with _registry_lock:
        _registry.clear()


# ============ 劫持实现 ============

def patch_paramiko() -> bool:
    """
    劫持 paramiko.SSHClient：
    - connect：登记连接 + 采集 connect 事件
    - exec_command：采集命令 + 包装 stdout/stderr（读到的输出采集为 output 事件）
    - invoke_shell：记录发送的命令 + 包装 recv 采集输出
    - close：采集 close 事件并移除登记
    """
    global _original_paramiko_connect, _patched_paramiko
    try:
        import paramiko
    except ImportError:
        print("[ssh-monkeypatch] paramiko 未安装，无法劫持（pip install ssh-monkeypatch[paramiko]）")
        return False

    if _original_paramiko_connect is None:
        _original_paramiko_connect = paramiko.SSHClient.connect

    # 复用原始 exec_command（可能已被上层库替换过）
    _original_exec_command = paramiko.SSHClient.exec_command
    _original_invoke_shell = paramiko.SSHClient.invoke_shell

    def patched_connect(self, hostname, port=22, username=None, password=None,
                        pkey=None, key_filename=None, timeout=None,
                        allow_agent=True, look_for_keys=True, compress=False,
                        sock=None, **kwargs):
        result = _original_paramiko_connect(
            self, hostname, port=port, username=username, password=password,
            pkey=pkey, key_filename=key_filename, timeout=timeout,
            allow_agent=allow_agent, look_for_keys=look_for_keys,
            compress=compress, sock=sock, **kwargs,
        )
        try:
            cid = _register(hostname, port, username or "", 'paramiko', client=self)
            setattr(self, '_ssh_monkeypatch_sid', cid)
            _hook_close(self, cid)
        except Exception as e:
            print(f"[ssh-monkeypatch] 登记 paramiko 连接失败: {e}")
        return result

    def patched_exec_command(self, command, *args, **kwargs):
        sid = getattr(self, '_ssh_monkeypatch_sid', None)
        if sid:
            emit_event("command", sid, command=command)
        result = _original_exec_command(self, command, *args, **kwargs)
        if sid:
            try:
                stdin, stdout, stderr = result
                channel = getattr(stdout, 'channel', None)
                f_stdout = _ForwardStream(stdout, sid, "stdout", channel)
                f_stderr = _ForwardStream(stderr, sid, "stderr", channel)
                if _capture_unread and channel is not None:
                    threading.Thread(target=_drain_channel, args=(sid, channel, "stdout"),
                                     daemon=True).start()
                result = (stdin, f_stdout, f_stderr)
            except Exception as e:
                print(f"[ssh-monkeypatch] 包装 exec_command 输出失败: {e}")
        return result

    def patched_invoke_shell(self, *args, **kwargs):
        sid = getattr(self, '_ssh_monkeypatch_sid', None)
        channel = _original_invoke_shell(self, *args, **kwargs)
        if sid:
            try:
                orig_recv = channel.recv
                orig_send = channel.send

                def patched_recv(n=4096):
                    data = orig_recv(n)
                    if data:
                        emit_event("output", sid, stream="shell",
                                   data=data.decode("utf-8", errors="replace"))
                    return data

                def patched_send(s):
                    emit_event("command", sid, command=s)
                    return orig_send(s)

                channel.recv = patched_recv
                channel.send = patched_send
            except Exception as e:
                print(f"[ssh-monkeypatch] 包装 invoke_shell 失败: {e}")
        return channel

    paramiko.SSHClient.connect = patched_connect
    paramiko.SSHClient.exec_command = patched_exec_command
    paramiko.SSHClient.invoke_shell = patched_invoke_shell
    _patched_paramiko = True
    print("[ssh-monkeypatch] paramiko 已劫持（连接登记 + 命令/输出采集）")
    return True


def patch_asyncssh() -> bool:
    """
    劫持 asyncssh.connect。

    采集 connect / close 事件（命令与输出采集：asyncssh 的 run / create_process
    输出通过流对象读取，为控制复杂度暂不包装，事件采集不丢失连接级信息）。
    """
    global _original_asyncssh_connect, _patched_asyncssh
    try:
        import asyncssh
    except ImportError:
        print("[ssh-monkeypatch] asyncssh 未安装，无法劫持（pip install ssh-monkeypatch[asyncssh]）")
        return False

    if _original_asyncssh_connect is None:
        _original_asyncssh_connect = asyncssh.connect

    async def patched_connect(host, port=22, username=None, password=None,
                              client_keys=None, known_hosts=None, **kwargs):
        conn = await _original_asyncssh_connect(
            host, port=port, username=username, password=password,
            client_keys=client_keys, known_hosts=known_hosts, **kwargs,
        )
        try:
            cid = _register(host, port, username or "", 'asyncssh', conn=conn)
            _hook_close(conn, cid)
        except Exception as e:
            print(f"[ssh-monkeypatch] 登记 asyncssh 连接失败: {e}")
        return conn

    asyncssh.connect = patched_connect
    _patched_asyncssh = True
    print("[ssh-monkeypatch] asyncssh 已劫持（连接登记 + 事件采集）")
    return True


def patch_all() -> bool:
    """
    一次调用，劫持当前进程内所有主流 SSH 连接入口：
    - paramiko（SSHClient.connect / exec_command / invoke_shell）
    - asyncssh（asyncssh.connect）

    之后当前进程中发起的 SSH 连接都会自动登记，并采集
    连接 / 命令 / stdout/stderr 输出 / 关闭 事件到包内缓冲
    （get_events / list_events 可查；start_streamer 可推送至可视化服务）。
    """
    results = []
    try:
        results.append(patch_paramiko())
    except Exception as e:
        print(f"[ssh-monkeypatch] 劫持 paramiko 失败: {e}")
    try:
        results.append(patch_asyncssh())
    except Exception as e:
        print(f"[ssh-monkeypatch] 劫持 asyncssh 失败: {e}")

    ok = sum(1 for r in results if r)
    if ok == len(results) and ok > 0:
        print("[ssh-monkeypatch] 已劫持当前进程所有 SSH 连接入口（paramiko + asyncssh）")
    elif ok > 0:
        print(f"[ssh-monkeypatch] 部分劫持成功（{ok}/{len(results)}）")
    else:
        print("[ssh-monkeypatch] 劫持失败：paramiko 与 asyncssh 均未安装")
    return ok > 0


def unpatch():
    """恢复原始函数，停止劫持（已登记的连接与已采集的事件保留）"""
    global _original_paramiko_connect, _original_asyncssh_connect
    global _patched_paramiko, _patched_asyncssh
    if _patched_paramiko:
        try:
            import paramiko
            if _original_paramiko_connect is not None:
                paramiko.SSHClient.connect = _original_paramiko_connect
        except ImportError:
            pass
        _patched_paramiko = False
        print("[ssh-monkeypatch] paramiko 劫持已解除")
    if _patched_asyncssh:
        try:
            import asyncssh
            if _original_asyncssh_connect is not None:
                asyncssh.connect = _original_asyncssh_connect
        except ImportError:
            pass
        _patched_asyncssh = False
        print("[ssh-monkeypatch] asyncssh 劫持已解除")


# ============ WebSocket 推送（跨进程） ============

class _WsStreamer:
    """WebSocket 客户端推送：把采集事件推送到 ssh_web_tool 可视化服务"""

    def __init__(self, ws_url: str, reconnect_interval: float = 3.0):
        self.ws_url = ws_url
        self.reconnect_interval = reconnect_interval
        self._ws = None
        self._stop = threading.Event()
        self._pending: List[dict] = []
        self._pending_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def send(self, ev: dict):
        with self._pending_lock:
            self._pending.append(ev)
            if len(self._pending) > 10000:
                del self._pending[: len(self._pending) - 10000]

    def _run(self):
        while not self._stop.is_set():
            try:
                self._connect_and_drain()
            except Exception as e:
                print(f"[ssh-monkeypatch] 推送连接异常（{self.reconnect_interval}s 后重连）: {e}")
            self._stop.wait(self.reconnect_interval)

    def _connect_and_drain(self):
        import websocket  # websocket-client
        ws = websocket.create_connection(self.ws_url, timeout=10)
        self._ws = ws
        print(f"[ssh-monkeypatch] 事件流已连接: {self.ws_url}")
        while not self._stop.is_set():
            with self._pending_lock:
                if not self._pending:
                    pending = None
                else:
                    pending = self._pending
                    self._pending = []
            if pending:
                for ev in pending:
                    try:
                        import json
                        ws.send(json.dumps(ev, ensure_ascii=False))
                    except Exception:
                        with self._pending_lock:
                            self._pending[:0] = pending
                        raise
            else:
                time.sleep(0.05)


def start_streamer(ws_url: str, reconnect_interval: float = 3.0) -> bool:
    """
    启动 WebSocket 推送：把采集到的 SSH 事件实时推送到可视化服务
    （如 ws://127.0.0.1:8765/ws/stream）。

    测试进程与可视化服务完全分离：本包只负责 patch + 采集 + 推送，
    可视化服务负责接收 / 按 session_id 区分 / 转发前端展示与历史回放。

    Returns:
        True=已启动；False=websocket-client 未安装（pip install ssh-monkeypatch[stream]）
    """
    global _streamer
    if _streamer is not None:
        print("[ssh-monkeypatch] 推送已运行，先 stop_streamer() 再重启")
        return False
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("[ssh-monkeypatch] 未安装 websocket-client，无法推送（pip install ssh-monkeypatch[stream]）")
        return False
    _streamer = _WsStreamer(ws_url, reconnect_interval)
    _streamer.start()
    return True


def stop_streamer():
    """停止 WebSocket 推送（已采集事件保留在缓冲）"""
    global _streamer
    if _streamer is not None:
        _streamer.stop()
        _streamer = None
        print("[ssh-monkeypatch] 事件推送已停止")
