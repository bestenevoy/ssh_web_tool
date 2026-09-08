# -*- coding: utf-8 -*-
"""会话 API 回归测试（Bug2：注入命令并发锁 / Bug3：广播队列有界）

不发起真实 SSH：FakeSession 提供可注入的 stdin 与 _inject_and_wait。
"""
import asyncio

import pytest


class FakeStdin:
    """模拟 asyncssh 的 stdin：同步收集写入的命令"""

    def __init__(self):
        self.writes = []

    def write(self, data: str):
        self.writes.append(data)


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()


def make_session_with_shell(session_id="sess-1"):
    from ssh_web_tool.sessions import SSHSession
    s = SSHSession(session_id, "10.0.0.1", 22, "root")
    s.process = FakeProcess()
    s._has_shell = True
    s._connected = True  # is_connected 是只读 property，直接设底层字段
    return s


@pytest.fixture()
def shell_session(fake_sessions):
    s = make_session_with_shell()
    fake_sessions.sessions[s.session_id] = s
    return s


# ---------- Bug2：注入命令并发锁 ----------

@pytest.mark.asyncio
async def test_inject_command_writes_stdin(shell_session, monkeypatch, fake_history_db):
    """inject_command 把命令写入 stdin，并记录历史"""
    await shell_session.inject_command("ls -la")
    assert shell_session.process.stdin.writes == ["ls -la\n"]
    recent = await fake_history_db.list_recent_commands(limit=10)
    assert any(r["command"] == "ls -la" for r in recent)


@pytest.mark.asyncio
async def test_inject_and_capture_serializes_with_inject_command(shell_session, monkeypatch):
    """回归 Bug2：inject_and_capture 整个事务（写+等输出）持锁期间，
    inject_command 必须等待，不能插入其中导致输出串台"""
    events = []

    async def fake_wait(cmd, **kwargs):
        events.append(("wait-start", cmd))
        await asyncio.sleep(0.2)
        events.append(("wait-end", cmd))
        return "output"

    # 直接赋实例属性：调用时收到 (cmd, **kwargs)
    shell_session._inject_and_wait = fake_wait

    async def capture():
        return await shell_session.inject_and_capture("CMD1")

    async def plain_inject():
        # 记录写入顺序
        shell_session.process.stdin.writes.clear()
        await shell_session.inject_command("CMD2")
        events.append(("inject-written",))

    await asyncio.gather(capture(), plain_inject())

    # 有锁：CMD2 的写必须发生在 CMD1 的 wait 结束之后（串行）
    wi = events.index(("wait-start", "CMD1"))
    we = events.index(("wait-end", "CMD1"))
    inj = events.index(("inject-written",))
    assert wi < we < inj, f"注入命令打断了正在捕获的命令: {events}"


@pytest.mark.asyncio
async def test_concurrent_inject_commands_no_interleave(shell_session, monkeypatch, fake_history_db):
    """并发 20 次注入：每条命令完整写入 stdin（不交错、不丢失）"""
    await asyncio.gather(*[shell_session.inject_command(f"cmd-{i}") for i in range(20)])
    writes = shell_session.process.stdin.writes
    assert len(writes) == 20
    for i in range(20):
        assert f"cmd-{i}\n" in writes


# ---------- Bug3：输出广播有界队列 ----------

@pytest.fixture()
def quiet_session(shell_session):
    """去掉回显解析与日志副作用，专注广播逻辑"""
    shell_session._parse_echo_line = lambda data: None
    shell_session._write_log = lambda data: None
    return shell_session


def test_listener_queue_is_bounded(quiet_session):
    """回归 Bug3：监听器队列必须有界（maxsize=100），防止慢监听器无限堆积内存"""
    q = quiet_session.add_output_listener()
    assert q.maxsize == 100


@pytest.mark.asyncio
async def test_broadcast_drops_oldest_when_listener_slow(quiet_session):
    """回归 Bug3：慢监听器（不消费）下灌 300 块输出：
    - 广播不抛错、不阻塞
    - 队列长度不超过上限
    - 队列里保留的是最新内容"""
    q = quiet_session.add_output_listener()

    for i in range(300):
        await quiet_session._broadcast_output(f"block-{i}\n")

    assert q.qsize() <= q.maxsize
    # 队尾应是最新的内容（丢旧保新）
    tail = q.get_nowait()
    while q.qsize() > 0:
        tail = q.get_nowait()
    assert tail == "block-299\n"


@pytest.mark.asyncio
async def test_broadcast_still_reaches_fast_listener(quiet_session):
    """正常消费的监听器不受影响：所有输出按序可达"""
    q = quiet_session.add_output_listener()
    for i in range(10):
        await quiet_session._broadcast_output(f"n-{i}\n")

    got = []
    while q.qsize() > 0:
        got.append(q.get_nowait())
    assert got == [f"n-{i}\n" for i in range(10)]


# ---------- 会话 API 路由 ----------

def test_run_command_route_injects(shell_session, monkeypatch):
    """POST /api/sessions/{id}/run 走注入链路（mock 捕获层，不真实等终端输出）"""
    from fastapi.testclient import TestClient
    from main import app

    async def fake_capture(cmd, **kwargs):
        return 0, f"out-{cmd}", ""

    monkeypatch.setattr(shell_session, "inject_and_capture", fake_capture)
    with TestClient(app) as c:
        r = c.post("/api/sessions/sess-1/run", json={"command": "echo hi", "timeout": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "inject"
    assert "echo hi" in body["stdout"]


def test_run_command_process_mode_when_no_shell(fake_sessions, monkeypatch, tmp_path):
    """无交互式终端时 run 走独立进程模式"""
    from fastapi.testclient import TestClient
    from main import app

    s = make_session_with_shell("sess-noshell")
    s._has_shell = False
    fake_sessions.sessions["sess-noshell"] = s

    async def fake_run(cmd, timeout=30):
        return 0, "proc-out", ""

    monkeypatch.setattr(s, "run_command", fake_run)
    with TestClient(app) as c:
        r = c.post("/api/sessions/sess-noshell/run", json={"command": "ls", "timeout": 5})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "process"


def test_close_session_route(fake_sessions, shell_session):
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        r = c.delete("/api/sessions/sess-1")
    assert r.status_code == 200
    assert fake_sessions.get_session("sess-1") is None
