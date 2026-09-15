"""PSReadLine 历史采集组件测试（ps_history.py）

- 采集：Tab 补全/预测后的最终命令由 ConsoleHost_history.txt 权威记录，
  采集器在回车时点增量读取并补记（前缀匹配 + 单行宽松兜底）
- 补写：被拦截的 ssh 命令追加进历史文件后推进基线，不被误采集
- 会话委托：note_local_command / record_intercepted_ssh_command 挂接正确，
  cmd 会话（无采集器）为空操作
"""

import asyncio
import logging

import pytest

from ssh_web_tool.ps_history import PSReadlineTailer, _norm
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


@pytest.fixture()
def hist_file(tmp_path):
    """模拟 ConsoleHost_history.txt：先写入历史内容，返回文件路径"""
    p = tmp_path / "ConsoleHost_history.txt"
    p.write_text("old-cmd-1\nold-cmd-2\n", encoding="utf-8")
    return str(p)


@pytest.fixture()
def poll_delay(monkeypatch):
    """加速采集轮询"""
    monkeypatch.setattr(PSReadlineTailer, "POLL_DELAY", 0.05)
    return None


class Recorder:
    def __init__(self):
        self.cmds: list[str] = []

    async def __call__(self, cmd: str) -> None:
        self.cmds.append(cmd)


def _append(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- 基础归一化 ----------


def test_norm_unifies_slashes_and_case():
    assert _norm("CD /Va") == _norm("cd \\va")
    assert _norm("  ls   -la ") == _norm("ls -la")


# ---------- 采集：Tab 补全场景 ----------


def test_tailer_records_completed_line(hist_file, poll_delay):
    "键入 cd /va + Tab 补全成 cd \var：PSReadLine 文件记录最终文本，采集器补记"
    rec = Recorder()
    t = PSReadlineTailer(rec, history_file=hist_file)

    async def main():
        t.note_command("cd /va")
        await asyncio.sleep(0.02)  # 等 note_command 先建基线后再模拟 shell 追加
        _append(hist_file, "cd \\var")
        await asyncio.sleep(0.15)

    asyncio.run(main())
    assert rec.cmds == ["cd \\var"]


def test_tailer_skips_exact_match(hist_file, poll_delay):
    "执行命令与键入完全一致：前端已即时记录，采集器不重复记录"
    rec = Recorder()
    t = PSReadlineTailer(rec, history_file=hist_file)

    async def main():
        t.note_command("git status")
        await asyncio.sleep(0.02)
        _append(hist_file, "git status")
        await asyncio.sleep(0.15)

    asyncio.run(main())
    assert rec.cmds == []


def test_tailer_ignores_foreign_lines(hist_file, poll_delay):
    "多条新增行且无前缀匹配（其他 PowerShell 窗口写入）：不污染应用历史"
    rec = Recorder()
    t = PSReadlineTailer(rec, history_file=hist_file)

    async def main():
        t.note_command("cd /va")
        await asyncio.sleep(0.02)
        _append(hist_file, "Get-Process")
        _append(hist_file, "docker ps")
        await asyncio.sleep(0.15)

    asyncio.run(main())
    assert rec.cmds == []


def test_tailer_loose_accepts_single_new_line(hist_file, poll_delay):
    "恰有一条新增行但前缀不匹配（预测接受等转换场景）：宽松接受"
    rec = Recorder()
    t = PSReadlineTailer(rec, history_file=hist_file)

    async def main():
        t.note_command("cd /va")
        await asyncio.sleep(0.02)
        _append(hist_file, "cd C:\\var\\log")
        await asyncio.sleep(0.15)

    asyncio.run(main())
    assert rec.cmds == ["cd C:\\var\\log"]


# ---------- 补写：拦截的 ssh 命令 ----------


def test_append_command_writes_file(hist_file):
    "拦截的 ssh 命令补写进历史文件，供新 shell 会话 ↑ 召回"
    t = PSReadlineTailer(Recorder(), history_file=hist_file)
    t.append_command("ssh root:wrz@1234@8.137.52.71")
    with open(hist_file, encoding="utf-8") as f:
        assert "ssh root:wrz@1234@8.137.52.71" in f.read().splitlines()


def test_appended_command_not_recollected(hist_file, poll_delay):
    "补写推进读取基线：补写的行不会被采集器当成新执行命令误记录"
    rec = Recorder()
    t = PSReadlineTailer(rec, history_file=hist_file)
    t.append_command("ssh root:wrz@1234@8.137.52.71")

    async def main():
        t.note_command("dir")
        _append(hist_file, "dir")
        await asyncio.sleep(0.15)

    asyncio.run(main())
    assert "ssh root:wrz@1234@8.137.52.71" not in rec.cmds
    assert rec.cmds == []  # dir 与键入一致：前端已记，不重复


def test_append_empty_command_noop(hist_file):
    t = PSReadlineTailer(Recorder(), history_file=hist_file)
    with open(hist_file, encoding="utf-8") as f:
        before = f.read()
    t.append_command("   ")
    with open(hist_file, encoding="utf-8") as f:
        assert f.read() == before


# ---------- 会话委托 ----------


def test_session_noop_without_tailer():
    "cmd 会话（未启用采集器）：两个公开方法为空操作，不报错"
    s = SSHSession("psnotail", "8.137.52.71", 22, "root")
    assert s._ps_tailer is None
    s.note_local_command("dir")
    s.record_intercepted_ssh_command("ssh root:x@1.2.3.4")


def test_session_delegates_to_tailer():
    "PowerShell 会话：公开方法正确委托给采集器"
    s = SSHSession("pstail", "8.137.52.71", 22, "root")
    calls = []

    class FakeTailer:
        def note_command(self, typed: str) -> None:
            calls.append(("note", typed))

        def append_command(self, command: str) -> None:
            calls.append(("append", command))

    s._ps_tailer = FakeTailer()
    s.note_local_command("ls -la")
    s.record_intercepted_ssh_command("ssh root:x@1.2.3.4")
    assert calls == [("note", "ls -la"), ("append", "ssh root:x@1.2.3.4")]


def test_session_powershell_gets_tailer_cmd_not(tmp_path, monkeypatch):
    "start_local_shell 装配：powershell/pwsh 启用采集器，cmd 不启用"
    import sys
    import types

    from ssh_web_tool import sessions as sessions_mod

    s = SSHSession("pstail2", "8.137.52.71", 22, "root")
    created: list[str] = []

    class FakePTY:
        def __init__(self, *a, **k):
            pass

        def spawn(self, *a, **k):
            pass

    fake_inner = types.ModuleType("winpty._winpty")
    fake_inner.PTY = FakePTY  # type: ignore[attr-defined]
    fake_winpty = types.ModuleType("winpty")
    fake_winpty._winpty = fake_inner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)
    monkeypatch.setitem(sys.modules, "winpty._winpty", fake_inner)
    monkeypatch.setattr(sessions_mod, "_DirectPtyWrapper", lambda pty: object())
    monkeypatch.setattr(
        sessions_mod, "PSReadlineTailer", lambda cb: created.append(cb.__self__.__class__.__name__) or created
    )

    for shell, expect in (("powershell", True), ("pwsh", True), ("cmd", False)):
        created.clear()
        asyncio.run(s.start_local_shell(shell, start_reader=False))
        assert (len(created) == 1) is expect, shell
        s._local_proc = None
