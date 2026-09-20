"""SFTP 传输专用独立连接：懒建、失败回退共享信道、关闭与失效重建

背景：SFTP 与终端曾共用同一条 SSH 连接，上传大流量把连接发送队列灌满，
同会话终端被队头阻塞发卡。现在 get_sftp() 优先另建独立传输连接。
"""

import pytest

from ssh_web_tool import sessions as sessions_mod
from ssh_web_tool.sessions import SSHSession


class _FakeSftp:
    def __init__(self, tag: str):
        self.tag = tag
        self.closed = False

    def close(self):
        self.closed = True


class _FakeXferConn:
    def __init__(self, tag: str):
        self.sftp = _FakeSftp(tag)
        self.closed = False

    async def start_sftp_client(self):
        return self.sftp

    def close(self):
        self.closed = True


class _FakeTermConn:
    def __init__(self):
        self.sftp_started = 0
        self.sftp = _FakeSftp("term")

    async def start_sftp_client(self):
        self.sftp_started += 1
        return self.sftp


def _sess():
    s = SSHSession("t1", "10.0.0.1", 22, "root")
    s._connected = True
    s.conn = _FakeTermConn()  # type: ignore[assignment]
    return s


@pytest.mark.asyncio
async def test_dedicated_conn_preferred(monkeypatch):
    """有凭据时走独立传输连接，终端连接上不开 SFTP 信道"""
    s = _sess()
    s._password = "pw"
    fake = _FakeXferConn("xfer")
    seen: dict = {}

    async def fake_connect(**kwargs):
        seen.update(kwargs)
        return fake

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", fake_connect)
    got = await s.get_sftp()
    assert got is fake.sftp
    assert seen["password"] == "pw" and seen["host"] == "10.0.0.1"
    assert s._sftp_conn is fake
    assert s.conn.sftp_started == 0  # type: ignore[union-attr]

    # 再次取用：复用已建的通道，不重复建连
    assert await s.get_sftp() is fake.sftp
    assert s.conn.sftp_started == 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_fallback_to_shared_channel(monkeypatch):
    """独立连接建立失败（2FA/连接数限制等）：回退终端连接共享信道，文件功能不中断"""
    s = _sess()
    s._password = "pw"

    async def boom_connect(**kwargs):
        raise OSError("MaxStartups 拒连")

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", boom_connect)
    got = await s.get_sftp()
    assert got is s.conn.sftp  # type: ignore[attr-defined]
    assert s.conn.sftp_started == 1  # type: ignore[union-attr]
    assert s._sftp_conn is None


@pytest.mark.asyncio
async def test_no_creds_uses_shared_channel(monkeypatch):
    """无存储凭据（agent 免密会话）不做自动重认证，直接共享信道"""
    s = _sess()

    async def should_not_connect(**kwargs):
        raise AssertionError("无凭据不应尝试建独立连接")

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", should_not_connect)
    got = await s.get_sftp()
    assert got is s.conn.sftp  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_close_sftp_closes_dedicated_conn():
    s = _sess()
    fake = _FakeXferConn("xfer")
    s._sftp = fake.sftp  # type: ignore[assignment]
    s._sftp_conn = fake  # type: ignore[assignment]
    await s.close_sftp()
    assert fake.sftp.closed and fake.closed
    assert s._sftp is None and s._sftp_conn is None


@pytest.mark.asyncio
async def test_dead_dedicated_conn_rebuilt(monkeypatch):
    """独立连接静默断开后再取用：回收旧通道并在连接失败时回退共享信道"""

    class _DeadConn(_FakeXferConn):
        def __init__(self):
            super().__init__("xfer")
            self._transport = self

        def is_closing(self):
            return True

    s = _sess()
    s._password = "pw"
    dead = _DeadConn()
    s._sftp = dead.sftp  # type: ignore[assignment]
    s._sftp_conn = dead  # type: ignore[assignment]

    async def boom_connect(**kwargs):
        raise OSError("仍不可达")

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", boom_connect)
    got = await s.get_sftp()
    assert dead.closed  # 旧连接被回收
    assert got is s.conn.sftp  # type: ignore[attr-defined]
    assert s._sftp_conn is None
