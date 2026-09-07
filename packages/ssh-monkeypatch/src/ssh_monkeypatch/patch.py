# -*- coding: utf-8 -*-
"""
ssh-monkeypatch 核心：劫持当前进程内 paramiko / asyncssh 的 SSH 连接入口，
自动登记连接状态（主机/端口/用户/连接时间），client 关闭时自动移除。

不依赖 ssh_web_tool 主体即可独立使用；如需把登记同步到
ssh_web_tool 的镜像会话（Web UI 可见），调用 bridge_to_web(True)。
"""
import threading
import time
import uuid
from typing import Dict, Optional

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
]

_registry: Dict[str, "Connection"] = {}
_registry_lock = threading.Lock()
# 对外只读视图（直接访问需自行加锁）
registry = _registry

# 被替换的原始函数（unpatch 时恢复）
_original_paramiko_connect = None
_original_asyncssh_connect = None
_patched_paramiko = False
_patched_asyncssh = False

# Web UI 桥接（可选）：同步注册 ssh_web_tool 镜像会话
_web_bridge = False


class Connection:
    """一条被劫持登记的 SSH 连接"""

    def __init__(self, host: str, port: int, username: str, kind: str,
                 client=None, conn=None):
        self.id = str(uuid.uuid4())[:8]
        self.host = host
        self.port = port
        self.username = username
        self.kind = kind            # 'paramiko' | 'asyncssh'
        self.client = client        # paramiko.SSHClient / asyncssh 连接对象（弱引用语义，仅存引用）
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


def _register(host, port, username, kind, client=None, conn=None) -> Optional[str]:
    """登记连接；若开启 Web 桥接且 ssh_web_tool 可用，同时注册镜像会话"""
    with _registry_lock:
        c = Connection(host, port, username, kind, client=client, conn=conn)
        _registry[c.id] = c
        cid = c.id
    try:
        if _web_bridge:
            _register_web_mirror(c)
    except Exception as e:
        print(f"[ssh-monkeypatch] Web 桥接注册失败: {e}")
    print(f"[ssh-monkeypatch] 已登记 {kind} 连接: {username}@{host}:{port} (id={cid})")
    return cid


def _unregister(cid: str):
    with _registry_lock:
        c = _registry.pop(cid, None)
    if c is not None:
        c.mark_closed()
        print(f"[ssh-monkeypatch] 连接已关闭并移除登记: {c.username}@{c.host}:{c.port} (id={cid})")
    try:
        if _web_bridge:
            _unregister_web_mirror(cid)
    except Exception:
        pass


def _hook_close(obj, cid: str):
    """hook client/conn 的 close，关闭时自动移除登记"""
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
    """
    开启/关闭 ssh_web_tool 桥接。

    开启后，被劫持的连接除了登记在本包 registry，
    还会同步注册为 ssh_web_tool 的镜像会话（Web UI /api/sessions 可见）。
    需要环境中已安装 ssh_web_tool（pip install ssh-monkeypatch[web]）。

    Returns:
        True=桥接状态生效；False=目标未安装，桥接保持关闭
    """
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
    """返回所有已登记的连接（按连接时间正序）"""
    with _registry_lock:
        conns = sorted(_registry.values(), key=lambda x: x.connected_at)
    return [c.to_dict() for c in conns]


def get_connection(cid: str) -> Optional[Connection]:
    with _registry_lock:
        return _registry.get(cid)


def clear_registry():
    """清空登记（不关闭真实连接）"""
    with _registry_lock:
        _registry.clear()


# ============ 劫持实现 ============

def patch_paramiko() -> bool:
    """
    劫持 paramiko.SSHClient.connect。

    之后当前进程内所有 paramiko 连接（含 fabric / scp / pssh 等
    基于 paramiko 的库）都会自动登记到 registry，close 时自动移除。
    """
    global _original_paramiko_connect, _patched_paramiko
    try:
        import paramiko
    except ImportError:
        print("[ssh-monkeypatch] paramiko 未安装，无法劫持（pip install ssh-monkeypatch[paramiko]）")
        return False

    if _original_paramiko_connect is None:
        _original_paramiko_connect = paramiko.SSHClient.connect

    def patched_connect(self, hostname, port=22, username=None, password=None,
                        pkey=None, key_filename=None, timeout=None,
                        allow_agent=True, look_for_keys=True, compress=False,
                        sock=None, **kwargs):
        # 先执行原有连接（失败会向上抛异常，不登记）
        result = _original_paramiko_connect(
            self, hostname, port=port, username=username, password=password,
            pkey=pkey, key_filename=key_filename, timeout=timeout,
            allow_agent=allow_agent, look_for_keys=look_for_keys,
            compress=compress, sock=sock, **kwargs,
        )
        try:
            cid = _register(hostname, port, username or "", 'paramiko', client=self)
            _hook_close(self, cid)
        except Exception as e:
            print(f"[ssh-monkeypatch] 登记 paramiko 连接失败: {e}")
        return result

    paramiko.SSHClient.connect = patched_connect
    _patched_paramiko = True
    print("[ssh-monkeypatch] paramiko 已劫持，所有连接将自动登记")
    return True


def patch_asyncssh() -> bool:
    """
    劫持 asyncssh.connect。

    之后当前进程内所有 asyncssh 连接都会自动登记到 registry，关闭时自动移除。
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
    print("[ssh-monkeypatch] asyncssh 已劫持，所有连接将自动登记")
    return True


def patch_all() -> bool:
    """
    一次调用，劫持当前进程内所有主流 SSH 连接入口：
    - paramiko（SSHClient.connect，含 fabric/scp/pssh 等上层库）
    - asyncssh（asyncssh.connect）

    之后当前进程中发起的 SSH 连接都会自动登记（close 时自动移除）。
    仅对同一 Python 进程内的调用生效；subprocess 调外部 ssh 命令无法劫持。

    用法：
        import ssh_monkeypatch
        ssh_monkeypatch.patch_all()
        # 之后 paramiko.SSHClient().connect(...) / await asyncssh.connect(...)
        # 都会出现在 ssh_monkeypatch.list_connections() 中
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
    """恢复原始函数，停止劫持（已登记的连接保留在 registry）"""
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
