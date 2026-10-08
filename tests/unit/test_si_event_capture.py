"""S1 后端事件通道：OSC 133;D 解析 + 命令捕获事件驱动化（省掉 echo $? 往返）"""

import asyncio
import time

import pytest

from ssh_web_tool.sessions import SSHSession, _OscEventScanner


class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()


def _make_session() -> SSHSession:
    s = SSHSession("t-events", "10.0.0.1", 22, "root")
    s.process = _FakeProcess()  # type: ignore[assignment]
    s._has_shell = True
    s._connected = True
    return s


async def _wait_write(s: SSHSession, n: int = 1, timeout: float = 3.0) -> None:
    """等第 n 条命令真的写进 stdin（注入前有输出静止等待，不能按固定睡眠猜时机）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(s.process.stdin.writes) >= n:  # type: ignore[attr-defined]
            return
        await asyncio.sleep(0.01)


# ---------- OSC 事件解析 ----------


def test_scanner_parses_exit_code():
    sc = _OscEventScanner()
    sc.feed("\x1b]133;D;0\x07")
    assert sc.finished.is_set()
    assert sc.exit_code == 0

    sc.begin_command()
    assert not sc.finished.is_set() and sc.exit_code is None
    sc.feed("prefix\x1b]133;D;127\x07suffix")
    assert sc.exit_code == 127


def test_scanner_handles_sequence_split_across_chunks():
    sc = _OscEventScanner()
    sc.feed("ls\r\na.txt\r\n\x1b]133;D;")
    assert not sc.finished.is_set()
    sc.feed("42\x07\x1b]133;A\x07")
    assert sc.finished.is_set()
    assert sc.exit_code == 42


def test_scanner_ignores_other_events():
    """633;E（命令全文）/633;P（cwd）/633;SI;ready 都不是结束信号"""
    sc = _OscEventScanner()
    sc.feed("\x1b]633;E;bHMgLWxh\x07\x1b]633;P;Cwd=/home/x\x07\x1b]633;SI;ready\x07\x1b]133;A\x07")
    assert not sc.finished.is_set()
    assert sc.exit_code is None


def test_scanner_carry_bounded():
    """没有 OSC 的大输出不能把未完成序列缓冲撑大"""
    sc = _OscEventScanner()
    sc.feed("x" * 100000)
    assert len(sc._carry) <= _OscEventScanner._CARRY_MAX
    sc.feed("\x1b]133;D;7\x07")
    assert sc.exit_code == 7


# ---------- 命令捕获：事件驱动 ----------


@pytest.mark.asyncio
async def test_inject_and_wait_returns_on_exit_event():
    s = _make_session()
    s._si_ready = True

    async def feeder():
        await _wait_write(s, 1)
        await s.broadcast_output("ls\r\na.txt\r\nb.txt\r\n")
        await asyncio.sleep(0.05)
        await s.broadcast_output("\x1b]133;D;0\x07\x1b]633;P;Cwd=/home\x07\x1b]133;A\x07root@h:~# ")

    task = asyncio.create_task(feeder())
    out = await s._inject_and_wait("ls", total_timeout=5)
    await task
    assert s.process.stdin.writes == ["ls\n"]  # type: ignore[attr-defined]
    assert s._si_last_exit == 0
    assert "a.txt" in out and "b.txt" in out


@pytest.mark.asyncio
async def test_silent_command_no_longer_waits_1500ms():
    """cd 这类无输出命令：旧路径固定空等 1.5s，事件路径收到 D 立即返回"""
    s = _make_session()
    s._si_ready = True

    async def feeder():
        await _wait_write(s, 1)
        await s.broadcast_output("\x1b]133;D;0\x07\x1b]133;A\x07root@h:~# ")

    task = asyncio.create_task(feeder())
    t0 = time.monotonic()
    await s._inject_and_wait("cd /tmp", total_timeout=5)
    elapsed = time.monotonic() - t0
    await task
    assert s._si_last_exit == 0
    assert elapsed < 1.0, f"事件路径不应空等 1.5s，实测 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_nonzero_exit_code_captured():
    s = _make_session()
    s._si_ready = True

    async def feeder():
        await _wait_write(s, 1)
        await s.broadcast_output("bash: xx: command not found\r\n\x1b]133;D;127\x07\x1b]133;A\x07root@h:~# ")

    task = asyncio.create_task(feeder())
    await s._inject_and_wait("xx", total_timeout=5)
    await task
    assert s._si_last_exit == 127


@pytest.mark.asyncio
async def test_inject_and_capture_skips_exit_probe():
    """集成生效时退出码来自 133;D：不再往终端里追加一条 echo $?"""
    s = _make_session()
    s._si_ready = True

    async def feeder():
        await _wait_write(s, 1)
        await s.broadcast_output("false\r\n\x1b]133;D;1\x07\x1b]133;A\x07root@h:~# ")

    task = asyncio.create_task(feeder())
    code, _out, err = await s.inject_and_capture("false", total_timeout=5)
    await task
    assert code == 1
    assert err == ""
    assert s.process.stdin.writes == ["false\n"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_late_exit_event_from_previous_command_not_misattributed():
    """上一条命令的 133;D 迟到：注入前必须等它落地，否则退出码整体滞后一条

    真机实测（bash on Linux，连续注入不留间隔）：true/false/bash -c 'exit 7'
    拿到 0/0/1——每次都读到了上一条命令的结束信号。
    """
    s = _make_session()
    s._si_ready = True
    await s.broadcast_output("\x1b]133;D;9\x07")  # 上一轮提示符周期刚落地

    async def feeder():
        await asyncio.sleep(0.03)
        await s.broadcast_output("\x1b]133;D;9\x07")  # 迟到的结束信号（抢跑就会误判）
        await _wait_write(s, 1)
        await asyncio.sleep(0.3)  # 命令本身的执行耗时
        await s.broadcast_output("false\r\n\x1b]133;D;1\x07\x1b]133;A\x07root@h:~# ")

    task = asyncio.create_task(feeder())
    code, _out, _err = await s.inject_and_capture("false", total_timeout=5)
    await task
    assert code == 1


@pytest.mark.asyncio
async def test_inject_and_capture_falls_back_to_probe_without_integration():
    """集成未生效：仍走提示符匹配 + echo $? 探针（旧行为不变）"""
    s = _make_session()
    assert s._si_ready is False

    async def feeder():
        await _wait_write(s, 1)
        await s.broadcast_output("false\r\nroot@h:~# ")
        await _wait_write(s, 2)  # 等第二次注入（echo $?）出现
        await s.broadcast_output("echo $?\r\n1\r\nroot@h:~# ")

    task = asyncio.create_task(feeder())
    code, _, _ = await s.inject_and_capture("false", total_timeout=5)
    await task
    assert code == 1
    assert s.process.stdin.writes == ["false\n", "echo $?\n"]  # type: ignore[attr-defined]
