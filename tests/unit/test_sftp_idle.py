"""SFTP 通道空闲超时自动关闭（close_sftp_if_idle）"""

import time

import pytest

from ssh_web_tool.sessions import SFTP_IDLE_TIMEOUT, SSHSession


class _FakeSftp:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _sess():
    s = SSHSession("t1", "10.0.0.1", 22, "root")
    s._sftp = _FakeSftp()  # type: ignore[assignment]
    s._sftp_last_used = time.time()
    return s


@pytest.mark.asyncio
async def test_recent_use_keeps_channel():
    s = _sess()
    assert await s.close_sftp_if_idle() is False
    assert s._sftp is not None


@pytest.mark.asyncio
async def test_idle_timeout_closes_channel():
    s = _sess()
    fake = s._sftp
    s._sftp_last_used = time.time() - SFTP_IDLE_TIMEOUT - 1
    assert await s.close_sftp_if_idle() is True
    assert s._sftp is None and fake.closed is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_running_transfer_not_closed():
    """流式传输全程只取一次通道：inuse 计数必须挡住空闲关闭，大文件不被误杀"""
    s = _sess()
    s._sftp_last_used = time.time() - SFTP_IDLE_TIMEOUT - 1
    s._sftp_inuse = 1
    assert await s.close_sftp_if_idle() is False
    assert s._sftp is not None
