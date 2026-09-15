"""回归测试：SSH 断线后会话必须被正确判死（v0.1.48 修复）

背景：asyncssh 连接断开后会把 conn._transport 置 None、process._chan 标记
closing 或置空；旧代码用 hasattr+真值判断漏掉 None、且用错误的属性名
_channel（真实属性是 _chan），导致断线会话被误判存活：
- 监控永不切换本机 → 输入永远报"SSH 通道已关闭，正在自动切换/重连"
- 服务器恢复后也无法恢复使用
"""

from types import SimpleNamespace

from ssh_web_tool.sessions import SSHSession


def _mk_session() -> SSHSession:
    return SSHSession("alive-t1", "8.137.52.71", 22, "root")


class _FakeTransport:
    def __init__(self, closing: bool = False):
        self._closing = closing

    def is_closing(self) -> bool:
        return self._closing


class _FakeChan:
    def __init__(self, closing: bool = False):
        self._closing = closing

    def is_closing(self) -> bool:
        return self._closing


class _FakeConn:
    """模拟 asyncssh 连接：断开后 _transport 置 None（属性仍在）"""

    def __init__(self, transport):
        self._transport = transport


def _attach_live_ssh(s: SSHSession):
    """构造一个"活着"的 SSH 会话状态"""
    s.conn = _FakeConn(_FakeTransport(closing=False))  # type: ignore[assignment]
    s._connected = True
    s._has_shell = True
    s.process = SimpleNamespace(_chan=_FakeChan(closing=False))  # type: ignore[assignment]


# ---------- is_alive ----------


def test_is_alive_false_when_transport_none():
    """连接断开（_transport 置 None）必须判死——旧代码此场景误判存活"""
    s = _mk_session()
    s.conn = _FakeConn(None)  # asyncssh 断线后的真实状态：_transport=None  # type: ignore[assignment]
    s._connected = True
    s._has_shell = True
    s.process = SimpleNamespace(_chan=_FakeChan(closing=True))  # type: ignore[assignment]
    assert s.is_alive() is False


def test_is_alive_false_when_transport_closing():
    s = _mk_session()
    s.conn = _FakeConn(_FakeTransport(closing=True))  # type: ignore[assignment]
    s._connected = True
    assert s.is_alive() is False


def test_is_alive_false_when_channel_closing():
    """传输层看似存活但 channel 已关（远端 shell 被杀）也要判死"""
    s = _mk_session()
    s.conn = _FakeConn(_FakeTransport(closing=False))  # type: ignore[assignment]
    s._connected = True
    s._has_shell = True
    s.process = SimpleNamespace(_chan=_FakeChan(closing=True))  # type: ignore[assignment]
    assert s.is_alive() is False


def test_is_alive_true_when_healthy():
    s = _mk_session()
    _attach_live_ssh(s)
    assert s.is_alive() is True


def test_is_alive_false_when_conn_missing():
    s = _mk_session()
    s.conn = None
    s._connected = True
    assert s.is_alive() is False


def test_is_alive_false_when_disconnected_flag():
    s = _mk_session()
    s.conn = _FakeConn(_FakeTransport(closing=False))  # type: ignore[assignment]
    s._connected = False
    assert s.is_alive() is False


# ---------- is_shell_alive ----------


def test_is_shell_alive_false_when_chan_none():
    """断线后通道对象不存在：shell 必须判死"""
    s = _mk_session()
    s._has_shell = True
    s.process = SimpleNamespace(_chan=None)  # type: ignore[assignment]
    assert s.is_shell_alive() is False


def test_is_shell_alive_false_when_chan_closing():
    s = _mk_session()
    s._has_shell = True
    s.process = SimpleNamespace(_chan=_FakeChan(closing=True))  # type: ignore[assignment]
    assert s.is_shell_alive() is False


def test_is_shell_alive_true_when_healthy():
    s = _mk_session()
    s._has_shell = True
    s.process = SimpleNamespace(_chan=_FakeChan(closing=False))  # type: ignore[assignment]
    assert s.is_shell_alive() is True


def test_is_shell_alive_false_when_no_process():
    s = _mk_session()
    s._has_shell = True
    s.process = None
    assert s.is_shell_alive() is False


def test_is_shell_alive_false_when_no_shell_flag():
    s = _mk_session()
    s._has_shell = False
    s.process = SimpleNamespace(_chan=_FakeChan(closing=False))  # type: ignore[assignment]
    assert s.is_shell_alive() is False
