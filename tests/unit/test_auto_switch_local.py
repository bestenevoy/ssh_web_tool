# -*- coding: utf-8 -*-
"""SSH 退出自动切换本机终端（exit/logout → 本机 cmd/powershell）

- shell EOF（用户 exit）→ 自动切换本机 shell（幂等，防与自动重连并发冲突）
- 切换提示写入终端输出与会话日志（同一会话一份记录）
- 监控兜底：传输层仍在但 shell 已死 → 切本机；传输层断开 → 走原重连逻辑
"""
import asyncio
import logging
import os
import sys

import pytest

from ssh_web_tool import sessions as sessions_mod
from ssh_web_tool.sessions import SSHSession


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """把 LOG_DIR 指到临时目录 + 清空 logger 缓存与全局 handlers，避免跨测试污染"""
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("ssh_session."):
            lg = logging.getLogger(name)
            for h in lg.handlers[:]:
                lg.removeHandler(h)
    yield
    SSHSession._logger_cache.clear()


def _mk_session(session_id: str = "sw12345", host: str = "8.137.52.71") -> SSHSession:
    return SSHSession(session_id, host, 22, "root")


def _read_log(s: SSHSession) -> str:
    try:
        with open(s._log_file, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


# ---------- _auto_switch_to_local ----------

def test_auto_switch_uses_configured_shell_and_broadcasts(tmp_path, monkeypatch):
    """自动切换：使用 fallback_local_shell 配置；提示进入终端输出与会话日志"""
    s = _mk_session()
    started = []

    async def _fake_start_local_shell(shell, cols=120, rows=40):
        started.append((shell, cols, rows))
        s._local_proc = object()  # 模拟已切到本机
        s._local_shell = shell
        s._connected = True
        s._has_shell = True

    monkeypatch.setattr(s, "start_local_shell", _fake_start_local_shell)
    monkeypatch.setattr(sessions_mod, "_fallback_shell", lambda: "powershell")

    async def _run():
        s._has_shell = True
        s.process = object()  # 模拟 SSH shell 存在
        await s._auto_switch_to_local()
        await asyncio.sleep(0)  # 让广播协程跑完

    asyncio.run(_run())

    assert started == [("powershell", s._last_cols, s._last_rows)]
    assert s._switching_local is False  # 完成后复位
    # 提示进入输出缓冲（终端可见）
    assert any("已切换到本机 PowerShell" in chunk for chunk in s._output_buffer)
    # 提示写入会话日志（同一会话一份记录）
    s._flush_log_now()
    assert "已切换到本机 PowerShell" in _read_log(s)


def test_auto_switch_guard_when_already_local(monkeypatch):
    s = _mk_session()
    s._local_proc = object()  # 已在本机

    called = []

    async def _fake_start(shell, cols=120, rows=40):
        called.append(shell)

    monkeypatch.setattr(s, "start_local_shell", _fake_start)

    asyncio.run(s._auto_switch_to_local())
    assert called == []


def test_auto_switch_guard_while_reconnecting_or_switching(monkeypatch):
    s = _mk_session()
    called = []

    async def _fake_start(shell, cols=120, rows=40):
        called.append(shell)

    monkeypatch.setattr(s, "start_local_shell", _fake_start)

    s._reconnecting = True
    asyncio.run(s._auto_switch_to_local())
    assert called == []

    s._reconnecting = False
    s._switching_local = True
    asyncio.run(s._auto_switch_to_local())
    assert called == []


def test_reconnect_bails_while_switching():
    """切换进行中禁止自动重连并发启动"""
    s = _mk_session()
    s._switching_local = True
    assert asyncio.run(s.reconnect()) is False
    assert s._reconnect_count == 0  # 未消耗重连次数


# ---------- shell EOF 触发自动切换 ----------

class _FakeStdout:
    """模拟 asyncssh stdout：先输出数据，然后 EOF（clean eof）"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        await asyncio.sleep(0.05)
        return ""


def test_reader_eof_triggers_auto_switch(monkeypatch):
    """stdout 读到 EOF（用户 exit）→ 触发 _auto_switch_to_local"""
    s = _mk_session()
    s.process = type("P", (), {"stdout": _FakeStdout(["hello\n"]), "stdin": None})()
    s._has_shell = True
    s._connected = True
    switch_calls = []

    async def _fake_switch():
        switch_calls.append(1)

    monkeypatch.setattr(s, "_auto_switch_to_local", _fake_switch)

    async def _run():
        await s.start_output_reader()
        await asyncio.sleep(0.3)  # 等读取器读完 + EOF 触发

    asyncio.run(_run())
    assert switch_calls == [1]


def test_reader_cancelled_does_not_trigger_switch(monkeypatch):
    """主动 stop_output_reader（关闭/重连路径）不得触发自动切换"""
    s = _mk_session()

    class _SlowStdout:
        async def read(self, n):
            await asyncio.sleep(10)  # 永远读不到数据，只能被 cancel
            return ""

    s.process = type("P", (), {"stdout": _SlowStdout(), "stdin": None})()
    s._has_shell = True
    switch_calls = []

    async def _fake_switch():
        switch_calls.append(1)

    monkeypatch.setattr(s, "_auto_switch_to_local", _fake_switch)

    async def _run():
        await s.start_output_reader()
        await asyncio.sleep(0.1)
        s.stop_output_reader()
        await asyncio.sleep(0.2)

    asyncio.run(_run())
    assert switch_calls == []


# ---------- is_transport_alive ----------

def test_is_transport_alive_variants():
    s = _mk_session()
    # 本机会话 / 无连接 → False
    assert s.is_transport_alive() is False
    s._local_proc = object()
    assert s.is_transport_alive() is False
    s._local_proc = None

    class _T:
        def __init__(self, closing):
            self._c = closing

        def is_closing(self):
            return self._c

    class _Conn:
        def __init__(self, closing):
            self._transport = _T(closing)

    s.conn = _Conn(False)
    assert s.is_transport_alive() is True
    s.conn = _Conn(True)
    assert s.is_transport_alive() is False
    s.conn = None


# ---------- 日志：切换后仍写同一会话日志 ----------

def test_switch_keeps_same_session_log_file(monkeypatch):
    """SSH → 本机切换后日志延续同一份文件（以会话为主，不拆分新文件）"""
    s = _mk_session()
    log_before = s._log_file

    async def _fake_start_local_shell(shell, cols=120, rows=40):
        s._local_proc = object()
        s._local_shell = shell
        s._connected = True
        s._has_shell = True

    monkeypatch.setattr(s, "start_local_shell", _fake_start_local_shell)
    monkeypatch.setattr(sessions_mod, "_fallback_shell", lambda: "cmd")

    async def _run():
        s._has_shell = True
        s.process = object()
        s._feed_log("ssh phase output\r\n")
        s._flush_log_now()
        await s._auto_switch_to_local()
        s._feed_log("local phase output\r\n")
        s._flush_log_now()

    asyncio.run(_run())

    assert s._log_file == log_before  # 同一会话同一份日志
    content = _read_log(s)
    assert "ssh phase output" in content
    assert "local phase output" in content
    assert "已切换到本机 cmd" in content
    # 会话名保持 {host}_..._{sid}.log（未变成 localhost）
    assert os.path.basename(s._log_file).startswith("8.137.52.71_")
    assert s._log_file.endswith("_sw12345.log")


# ---------- 真实进程级回归（Windows + pywinpty） ----------

try:
    import winpty  # noqa: F401
    HAVE_WINPTY = True
except ImportError:
    HAVE_WINPTY = False


@pytest.mark.skipif(
    not sys.platform.startswith("win") or not HAVE_WINPTY,
    reason="winpty ConPTY 仅 Windows + pywinpty 可用",
)
def test_eof_auto_switch_to_real_cmd(tmp_path, monkeypatch):
    """端到端：SSH stdout EOF → 自动切换到真实本机 cmd（ConPTY），同一会话日志延续"""
    monkeypatch.setattr(sessions_mod, "_fallback_shell", lambda: "cmd")
    s = _mk_session("eof12345")
    s.process = type("P", (), {"stdout": _FakeStdout(["remote$ exit\r\n"]), "stdin": None})()
    s._has_shell = True
    s._connected = True

    async def _run():
        await s.start_output_reader()
        await asyncio.sleep(1.0)  # EOF → 切换任务启动 → cmd 提示符
        assert s.is_local()
        assert s.is_alive()
        await s.write_local("echo eof-switched\r")
        await asyncio.sleep(1.0)
        s._flush_log_now()
        content = _read_log(s)
        await s.close()
        return content

    content = asyncio.run(_run())
    assert "eof-switched" in content
    assert "已切换到本机 cmd" in content
    assert "remote$ exit" in content  # SSH 阶段输出在同一份日志
