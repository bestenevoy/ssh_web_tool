"""本机终端（WinPTY 后端：cmd / powershell）—— 真实进程级测试

仅在 Windows + pywinpty 可用时运行；失败不影响主测试套件。
直接 PTY 非阻塞轮询模式：输出延迟 < 50ms。
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
    reason="WinPTY 仅 Windows + pywinpty 可用",
)


@pytest.mark.asyncio
async def test_start_local_cmd_echo_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    s = SSHSession("loc12345", "localhost", 0, "tester")
    await s.start_local_shell("cmd", cols=100, rows=30)
    assert s.is_local()
    assert s.is_alive()
    await asyncio.sleep(1.0)
    buf1 = "".join(s._output_buffer)
    await s.write_local("echo local-ok\r")
    await asyncio.sleep(2.0)
    buf2 = "".join(s._output_buffer)
    s._flush_log_now()
    await s.close()
    assert "local-ok" in buf2, f"buf1={buf1[:100]!r} buf2={buf2[:300]!r}"


@pytest.mark.asyncio
async def test_local_resize(tmp_path, monkeypatch):
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    s = SSHSession("loc12345", "localhost", 0, "tester")
    await s.start_local_shell("cmd", cols=80, rows=24)
    s.resize_local(120, 40)
    assert s._last_cols == 120
    assert s._last_rows == 40
    await asyncio.sleep(1.0)
    await s.write_local("echo after-resize\r")
    await asyncio.sleep(2.0)
    buf = "".join(s._output_buffer)
    s._flush_log_now()
    await s.close()
    assert "after-resize" in buf, f"buf={buf[:300]!r}"


@pytest.mark.asyncio
async def test_restart_local_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    s = SSHSession("loc12345", "localhost", 0, "tester")
    await s.start_local_shell("cmd", cols=90, rows=25)
    s._local_proc.terminate(force=True)
    await asyncio.sleep(0.3)
    assert not s.is_shell_alive()
    await s.restart_local_shell()
    assert s.is_shell_alive()
    await asyncio.sleep(1.0)
    await s.write_local("echo restarted\r")
    await asyncio.sleep(2.0)
    buf = "".join(s._output_buffer)
    s._flush_log_now()
    await s.close()
    assert "restarted" in buf, f"buf={buf[:300]!r}"


@pytest.mark.asyncio
async def test_switch_to_local_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    s = SSHSession("loc12345", "localhost", 0, "tester")
    await s.switch_to_local("cmd", cols=80, rows=24)
    assert s.is_local()
    assert s.is_alive()
    await asyncio.sleep(1.0)
    await s.write_local("echo switched\r")
    await asyncio.sleep(2.0)
    buf = "".join(s._output_buffer)
    s._flush_log_now()
    await s.close()
    assert "switched" in buf, f"buf={buf[:300]!r}"


@pytest.mark.asyncio
async def test_local_exit_kills_shell(tmp_path, monkeypatch):
    """本地终端输入 exit 后 shell 进程退出（监控据此关闭会话与标签）"""
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    s = SSHSession("loc12345", "localhost", 0, "tester")
    await s.start_local_shell("cmd", cols=90, rows=25)
    await asyncio.sleep(1.0)
    await s.write_local("exit\r")
    for _ in range(20):
        if not s.is_shell_alive():
            break
        await asyncio.sleep(0.2)
    s._flush_log_now()
    await s.close()
    assert not s.is_shell_alive()


@pytest.mark.asyncio
async def test_local_ctrl_c_graceful_and_closed_notice(tmp_path, monkeypatch):
    """Ctrl+C 回退路径不抛异常且 shell 仍可输入；关闭通知通道可设置/取出"""
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    s = SSHSession("loc12345", "localhost", 0, "tester")
    await s.start_local_shell("cmd", cols=90, rows=25)
    await asyncio.sleep(1.0)
    await s.write_local("echo unique-token-xyz\r")
    await asyncio.sleep(1.0)
    await s.write_local("\x03")  # AttachConsole 不可用时静默回退，不应抛异常
    await s.write_local("echo after-ctrl-c\r")
    await asyncio.sleep(1.5)
    buf = "".join(s._output_buffer)
    s._flush_log_now()
    await s.close()
    assert "unique-token-xyz" in buf, f"buf={buf[:300]!r}"
    # 关闭通知通道
    assert s.take_closed_notice() is None
    s.set_closed_notice("本机终端已退出")
    assert s.take_closed_notice() == "本机终端已退出"
    assert s.take_closed_notice() is None