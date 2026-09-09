# -*- coding: utf-8 -*-
"""本机终端（winpty ConPTY：cmd / powershell）—— 真实进程级测试

仅在 Windows + pywinpty 可用时运行；失败不影响主测试套件。
注意：ConPTY 读循环绑定 event loop，测试必须在同一个 loop 内完成
（start → write → sleep → 检查），不能多次 asyncio.run。
日志断言读文件（0.6s 静默期后缓冲已 flush 落盘，_log_buf 会为空）。
"""
import asyncio
import sys

import pytest

from ssh_web_tool.sessions import SSHSession

try:
    import winpty  # noqa: F401
    HAVE_WINPTY = True
except ImportError:
    HAVE_WINPTY = False

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win") or not HAVE_WINPTY,
    reason="winpty ConPTY 仅 Windows + pywinpty 可用",
)


def _mk_local_session(tmp_path, monkeypatch):
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    return SSHSession("loc12345", "localhost", 0, "tester")


def _run_in_loop(coro):
    return asyncio.run(coro)


def _read_log(s) -> str:
    try:
        with open(s._log_file, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def test_start_local_cmd_echo_and_read(tmp_path, monkeypatch):
    s = _mk_local_session(tmp_path, monkeypatch)

    async def _run():
        await s.start_local_shell("cmd", cols=100, rows=30)
        assert s.is_local()
        assert s.is_alive()
        await asyncio.sleep(0.8)  # 等提示符
        await s.write_local("echo local-ok\r")
        await asyncio.sleep(1.0)
        await s.close()
        return _read_log(s)

    out = _run_in_loop(_run())
    assert "local-ok" in out


def test_local_resize(tmp_path, monkeypatch):
    s = _mk_local_session(tmp_path, monkeypatch)

    async def _run():
        await s.start_local_shell("cmd", cols=80, rows=24)
        s.resize_local(120, 40)
        assert s._last_cols == 120
        assert s._last_rows == 40
        await asyncio.sleep(0.5)
        await s.write_local("echo after-resize\r")
        await asyncio.sleep(0.8)
        await s.close()
        return _read_log(s)

    assert "after-resize" in _run_in_loop(_run())


def test_restart_local_shell(tmp_path, monkeypatch):
    s = _mk_local_session(tmp_path, monkeypatch)

    async def _run():
        await s.start_local_shell("cmd", cols=90, rows=25)
        s._local_proc.terminate(force=True)
        await asyncio.sleep(0.3)
        assert not s.is_shell_alive()
        await s.restart_local_shell()
        assert s.is_shell_alive()
        await asyncio.sleep(0.5)
        await s.write_local("echo restarted\r")
        await asyncio.sleep(0.8)
        await s.close()
        return _read_log(s)

    assert "restarted" in _run_in_loop(_run())


def test_switch_to_local_falls_back(tmp_path, monkeypatch):
    s = _mk_local_session(tmp_path, monkeypatch)

    async def _run():
        await s.switch_to_local("cmd", cols=80, rows=24)
        assert s.is_local()
        assert s.is_alive()
        await asyncio.sleep(0.5)
        await s.write_local("echo switched\r")
        await asyncio.sleep(0.8)
        await s.close()
        return _read_log(s)

    assert "switched" in _run_in_loop(_run())
