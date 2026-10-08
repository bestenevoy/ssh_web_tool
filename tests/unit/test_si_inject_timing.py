"""S1 注入时机：等输出流静止 + 避开用户未提交的半行命令 + 633;SI;ready 回执检测"""

import asyncio
import time

import pytest

import ssh_web_tool.sessions as sessions
from ssh_web_tool.sessions import SHELL_INTEGRATION_SNIPPET, SSHSession


class _FakeStdin:
    def __init__(self):
        self.written: list[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()


def _make_session() -> SSHSession:
    s = SSHSession("t", "10.0.0.1", 22, "root")
    s.process = _FakeProcess()  # type: ignore[assignment]
    return s


@pytest.fixture()
def fast_timings(monkeypatch):
    """把等待常量压到毫秒级，测试不真等；集成开关固定为开，不读用户 config.json"""
    monkeypatch.setattr(sessions, "_SI_MIN_DELAY", 0.01)
    monkeypatch.setattr(sessions, "_SI_QUIET_WINDOW", 0.02)
    monkeypatch.setattr(sessions, "_SI_MAX_WAIT", 0.1)
    monkeypatch.setattr(sessions, "_SI_READY_TIMEOUT", 0.05)
    monkeypatch.setattr(sessions, "get_shell_integration", lambda: True)


def test_note_remote_input_pending_semantics():
    s = _make_session()
    s.note_remote_input("l")
    assert s._si_input_pending is True
    s.note_remote_input("s")
    assert s._si_input_pending is True
    s.note_remote_input("\r")  # 回车提交：命令行已清空
    assert s._si_input_pending is False
    s.note_remote_input("vim")
    s.note_remote_input("\x03")  # Ctrl-C 中断输入
    assert s._si_input_pending is False
    s.note_remote_input("abc\x7f")  # 退格后仍可能有残留字符：保守视为待提交
    assert s._si_input_pending is True
    s.note_remote_input("")  # 空输入不改变状态
    assert s._si_input_pending is True
    s.note_remote_input("\x15")  # Ctrl-U 清行
    assert s._si_input_pending is False


def test_note_remote_input_multiline_paste():
    """粘贴多行：看最后一段是否已提交，整体含换行不等于命令行已清空"""
    s = _make_session()
    s.note_remote_input("ls\r\n")
    assert s._si_input_pending is False
    s.note_remote_input("cd /tmp\r\nvim a.txt")
    assert s._si_input_pending is True


def test_wait_quiescent_true_when_output_idle(fast_timings):
    s = _make_session()
    s._si_out_seen = True
    s._si_out_ts = time.monotonic() - 5  # 首屏已过、早已静止
    assert asyncio.run(s._wait_shell_quiescent()) is True


def test_wait_quiescent_waits_for_first_output(fast_timings):
    """一个字节都没来过 ≠ 静止：reader 还没读出首屏提示符就注入，
    回显会与提示符重绘交错（真机实测片段因此露出来）"""
    s = _make_session()
    s._si_out_ts = time.monotonic() - 5  # 看似静止，实则 _si_out_seen 仍为 False
    t0 = time.monotonic()
    assert asyncio.run(s._wait_shell_quiescent()) is True
    assert time.monotonic() - t0 >= 0.09  # 撑到 _SI_MAX_WAIT 上限才注入


def test_wait_quiescent_injects_after_output_settles(fast_timings, monkeypatch):
    """首屏输出到达并静止后立即注入，不必等到上限"""
    monkeypatch.setattr(sessions, "_SI_MAX_WAIT", 0.4)
    s = _make_session()

    async def run():
        async def feed():
            await asyncio.sleep(0.03)
            s._si_out_seen = True
            s._si_out_ts = time.monotonic()

        feeder = asyncio.create_task(feed())
        t0 = time.monotonic()
        ok = await s._wait_shell_quiescent()
        await feeder
        return ok, time.monotonic() - t0

    ok, elapsed = asyncio.run(run())
    assert ok is True
    assert elapsed < 0.3


def test_wait_quiescent_skips_when_user_typing(fast_timings):
    """用户有半行未提交：等到上限也放弃注入（不能把片段拼进他的命令）"""
    s = _make_session()
    s._si_input_pending = True
    assert asyncio.run(s._wait_shell_quiescent()) is False


def test_wait_quiescent_skips_when_output_still_streaming(fast_timings, monkeypatch):
    """到上限还在刷屏（登录脚本/vim/make 在跑）：放弃注入，不能把片段喂给前台程序"""
    monkeypatch.setattr(sessions, "_SI_QUIET_WINDOW", 3600)
    s = _make_session()
    s._si_out_seen = True
    s._si_out_ts = time.monotonic()
    assert asyncio.run(s._wait_shell_quiescent()) is False


def test_wait_quiescent_injects_when_shell_totally_silent(fast_timings, monkeypatch):
    """始终没有输出（无 reader 收流）：到上限照旧注入，注入无人观看也无害"""
    monkeypatch.setattr(sessions, "_SI_QUIET_WINDOW", 3600)
    s = _make_session()
    assert asyncio.run(s._wait_shell_quiescent()) is True


def test_ready_marker_detected_in_output_stream():
    s = _make_session()

    async def run():
        await s.broadcast_output("\x1b[?2004l\r\x1b]633;SI;ready\x07\x1b]133;A\x07")

    asyncio.run(run())
    assert s._si_ready is True


def test_inject_writes_snippet_after_quiescence(fast_timings):
    s = _make_session()
    s._si_out_seen = True
    s._si_out_ts = time.monotonic() - 5

    async def run():
        await s._inject_shell_integration()

    asyncio.run(run())
    assert s.process.stdin.written == [SHELL_INTEGRATION_SNIPPET + "\n"]  # type: ignore[attr-defined]
    # 回显吞噬器已激活过：没等到 ready 回执超时后也不影响片段已写入的事实
    assert s._si_ready is False


def test_inject_skipped_when_user_has_pending_input(fast_timings):
    s = _make_session()
    s._si_input_pending = True

    async def run():
        await s._inject_shell_integration()

    asyncio.run(run())
    assert s.process.stdin.written == []  # type: ignore[attr-defined]
    assert s._si_echo_guard is None


def test_arm_resets_injection_state(fast_timings):
    s = _make_session()
    s._si_ready = True
    s._si_input_pending = True
    s._si_ready_event.set()

    async def run():
        s.arm_shell_integration()
        task = s._si_task
        assert task is not None  # 远端会话 + 集成开关开 → 已调度注入
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert s._si_ready is False
    assert s._si_input_pending is False
    assert s._si_out_seen is False
    assert not s._si_ready_event.is_set()
    assert s._si_active is False
