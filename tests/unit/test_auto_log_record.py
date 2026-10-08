"""远程终端会话连上后自动开启日志记录（设置 log_record_default_on，默认开）

口径：只有"用户在界面里看着的远程终端"才自动落盘——本机终端、脚本 API 单独建的
会话、外部劫持的镜像会话都不自动记录；自动记录写默认目录且不弹目录选择。
"""

import glob
import os

import pytest

from ssh_web_tool.config import get_log_record_default
from ssh_web_tool.sessions import SSHSession


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """LOG_DIR 指到临时目录 + 清 logger 缓存，避免跨测试污染（与 test_logfile 同法）"""
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    import logging

    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("ssh_session."):
            lg = logging.getLogger(name)
            for h in lg.handlers[:]:
                lg.removeHandler(h)
    yield
    SSHSession._logger_cache.clear()


def _mk_session(session_id: str = "auto12345", host: str = "10.0.0.1"):
    s = SSHSession(session_id, host, 22, "root")
    s._connected = True
    s._has_shell = True  # 远程 shell 已建立（本模块不测建连本身）
    return s


def _stub_setting(monkeypatch, enabled=True, log_dir=""):
    """把 sessions 里读到的设置换成本用例的取值（不去碰用户真实 config.json）"""
    monkeypatch.setattr("ssh_web_tool.sessions.get_log_record_default", lambda *a, **kw: (enabled, log_dir))


def _log_files(log_dir):
    return glob.glob(os.path.join(log_dir, "*.log"))


def _read_log(tmp_path) -> str:
    files = _log_files(str(tmp_path))
    assert len(files) == 1, f"应只有一份日志，实际 {files}"
    with open(files[0], encoding="utf-8") as f:
        return f.read()


# ---------- 配置层 ----------


def test_config_default_is_on():
    """不设任何配置时：自动记录开启、目录为空（= 内置 logs 目录）"""
    assert get_log_record_default({}) == (True, "")


def test_config_respects_off_and_dir():
    off = get_log_record_default({"ui_settings": {"log_record_default_on": False}})
    assert off[0] is False
    with_dir = get_log_record_default({"ui_settings": {"log_record_dir": "D:/my/logs"}})
    assert with_dir == (True, "D:/my/logs")
    # 非法类型回退默认（开）
    assert get_log_record_default({"ui_settings": {"log_record_default_on": "yes"}})[0] is True


# ---------- 自动开启 ----------


def test_auto_enable_on_attached_remote_shell(tmp_path, monkeypatch):
    _stub_setting(monkeypatch)
    s = _mk_session()
    s.note_ui_attach()
    assert s.is_logging()
    assert _log_files(str(tmp_path))


def test_no_auto_enable_without_ui_attach(monkeypatch):
    """脚本 API 单独建的会话（终端从未挂载）不凭空写文件"""
    _stub_setting(monkeypatch)
    s = _mk_session()
    assert not s.is_logging()
    s._auto_enable_logging(new_shell=True)
    assert not s.is_logging()


def test_no_auto_enable_for_local_shell_session(tmp_path, monkeypatch):
    """本机终端：远程 shell 从未建立 → 不自动记录（用户口径：只有连到远端才默认记）"""
    _stub_setting(monkeypatch)
    s = SSHSession("local12345", "localhost", 22, "me")
    s._connected = True
    s._local_proc = object()  # 本地 shell 在跑
    s.note_ui_attach()
    assert not s.is_logging()
    assert _log_files(str(tmp_path)) == []


def test_no_auto_enable_for_external_mirror_session(monkeypatch):
    """外部劫持的镜像会话（paramiko/asyncssh 注册）不自动记录"""
    _stub_setting(monkeypatch)
    s = _mk_session()
    s._external = True
    s.note_ui_attach()
    assert not s.is_logging()


def test_no_auto_enable_when_setting_off(tmp_path, monkeypatch):
    """设置里关掉开关 → 回到旧的按需记录模式"""
    _stub_setting(monkeypatch, enabled=False)
    s = _mk_session()
    s.note_ui_attach()
    assert not s.is_logging()
    assert _log_files(str(tmp_path)) == []


def test_auto_enable_uses_configured_dir(tmp_path, monkeypatch):
    target = tmp_path / "自定义" / "logs"  # 含非 ASCII，顺带覆盖中文目录名
    _stub_setting(monkeypatch, log_dir=str(target))
    s = _mk_session(session_id="dir123456")
    s.note_ui_attach()
    assert s.is_logging()
    assert _log_files(str(target))
    assert _log_files(str(tmp_path)) == []


def test_auto_enable_expands_user_in_dir(monkeypatch, tmp_path):
    """~ 前缀设置可用（expanduser 后落到真实 home 目录，而不是字面 ~/）"""
    home = tmp_path / "home"
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home / p[2:]))
    _stub_setting(monkeypatch, log_dir="~/logs")
    s = _mk_session(session_id="usr1234567")
    s.note_ui_attach()
    assert s.is_logging()
    assert os.path.normpath(s._log_file).startswith(os.path.normpath(str(home / "logs")))


def test_auto_enable_failure_does_not_raise(monkeypatch):
    """目录不可用（如空文件名段/权限）：只放弃自动记录，绝不让建连流程炸掉"""
    _stub_setting(monkeypatch, log_dir="\0:not-a-path")
    s = _mk_session()
    s.note_ui_attach()  # 不抛
    assert not s.is_logging()


# ---------- 早期输出与文件头 ----------


@pytest.mark.asyncio
async def test_auto_enable_keps_banner(tmp_path, monkeypatch):
    """连接 banner 先于自动开启产生：仍要进日志（既有不变量，经新路径不回归）"""
    _stub_setting(monkeypatch)
    s = _mk_session(session_id="ban1234567")
    s.note_ui_attach()
    await s._broadcast_output("Last login: ...\r\n")
    s._flush_log_now()
    assert "Last login" in _read_log(tmp_path)


def test_auto_enable_header_is_ssh_remote_during_switch(tmp_path, monkeypatch):
    """本机→远程切换时刻 _local_proc 还没清空：文件头必须按远程会话写，不能写成本机 CMD"""
    _stub_setting(monkeypatch)
    s = _mk_session(session_id="swi1234567")
    s._local_proc = object()
    s._local_shell = r"C:\Windows\System32\cmd.exe"
    s.note_ui_attach()
    content = _read_log(tmp_path)
    assert "SSH 远程会话" in content
    assert "CMD" not in content


# ---------- 与手动暂停的关系 ----------


def test_reattach_respects_manual_pause(tmp_path, monkeypatch):
    """用户在本会话里手动暂停过：只是重新挂载终端（页面刷新）不擅自恢复记录"""
    _stub_setting(monkeypatch)
    s = _mk_session(session_id="pau1234567")
    s.note_ui_attach()
    assert s.is_logging()
    s.set_logging(False)
    assert not s.is_logging()
    s.note_ui_attach()  # 同一会话再次挂载
    assert not s.is_logging()


def test_new_shell_reenables_after_manual_pause(tmp_path, monkeypatch):
    """手动暂停过之后连上新的远程 shell（重连/新建 shell）：按"连上即记录"重新开启"""
    _stub_setting(monkeypatch)
    s = _mk_session(session_id="new1234567")
    s.note_ui_attach()
    s.set_logging(False)
    assert not s.is_logging()
    s._auto_enable_logging(new_shell=True)
    assert s.is_logging()


# ---------- 接线位置 ----------


class _FakeProc:
    pass


class _FakeConn:
    async def create_process(self, *, term_type, term_size):
        return _FakeProc()


@pytest.mark.asyncio
async def test_start_interactive_shell_triggers_auto_enable(monkeypatch):
    """自动开启挂在远程 shell 建立处（覆盖连接/重连/恢复/本机切远程四条路径）"""
    seen = []

    monkeypatch.setattr(SSHSession, "_auto_enable_logging", lambda self, new_shell=False: seen.append(new_shell))
    monkeypatch.setattr(SSHSession, "arm_shell_integration", lambda self: None)
    monkeypatch.setattr(SSHSession, "_start_ssh_reader", lambda self: None)
    s = SSHSession("wiring1234", "10.0.0.2", 22, "root")
    s._connected = True
    s.conn = _FakeConn()  # type: ignore[assignment]
    await s.start_interactive_shell()
    assert seen == [True]
