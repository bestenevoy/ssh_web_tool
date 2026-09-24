"""S1 shell 集成（后端侧）单元测试：注入片段不变量 + 注入调度 + Cwd 权威回填"""

import asyncio

import pytest

import ssh_web_tool.sessions as sessions_mod
from ssh_web_tool.config import get_shell_integration
from ssh_web_tool.sessions import SHELL_INTEGRATION_SNIPPET, SSHSession

# ---------- 注入片段文本不变量 ----------


def test_snippet_emits_all_authoritative_events():
    """片段必须上报全部五类 OSC 事件（前端 shellIntegration.ts 按此解析）"""
    for marker in (
        r"\033]633;E;%s\007",  # 命令全文（base64）
        r"\033]133;D;%s\007",  # 上一条命令退出码
        r"\033]633;P;Cwd=%s\007",  # 工作目录
        r"\033]133;A\007",  # 提示符开始
        r"\033]633;SI;ready\007",  # 集成生效回执
    ):
        assert marker in SHELL_INTEGRATION_SNIPPET, marker


def test_snippet_is_single_physical_line():
    """注入经 stdin.write 一次性下发：片段含真实换行会把后续用户键入吞进
    引号续行（Git bash 实测踩坑），必须是单物理行"""
    assert "\n" not in SHELL_INTEGRATION_SNIPPET
    assert "\r" not in SHELL_INTEGRATION_SNIPPET


def test_snippet_chains_existing_prompt_command():
    """不得覆盖用户已有 PROMPT_COMMAND：以 "; $PROMPT_COMMAND" 追加旧值"""
    assert 'PROMPT_COMMAND="__wst_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"' in SHELL_INTEGRATION_SNIPPET
    # zsh 走 add-zsh-hook precmd，失败退路 precmd()
    assert "add-zsh-hook" in SHELL_INTEGRATION_SNIPPET


def test_snippet_empty_enter_and_self_line_guards():
    """空回车去重靠 history 条目编号比对；注入行自过滤且从 bash 历史删除"""
    assert "history -d" in SHELL_INTEGRATION_SNIPPET
    assert "__wst_ln" in SHELL_INTEGRATION_SNIPPET  # 上次上报的条目编号
    assert "__wst_lc" in SHELL_INTEGRATION_SNIPPET  # 文本弱判定回退（history 不可用时）


# ---------- 注入调度 arm / _inject ----------


def _make_ssh_session() -> SSHSession:
    s = SSHSession("si-t", "10.0.0.1", 22, "root")
    return s


@pytest.mark.asyncio
async def test_arm_skips_local_and_disabled(monkeypatch: pytest.MonkeyPatch):
    """本机会话（无 SFTP/无远端 shell 概念）与配置关闭时不调度注入"""
    s = _make_ssh_session()
    s._local_proc = object()  # is_local() True
    s.arm_shell_integration()
    assert s._si_task is None

    s2 = _make_ssh_session()
    monkeypatch.setattr(sessions_mod, "get_shell_integration", lambda cfg=None: False)
    s2.arm_shell_integration()
    assert s2._si_task is None


@pytest.mark.asyncio
async def test_arm_schedules_injection_and_resets_flag():
    s = _make_ssh_session()
    s._si_active = True  # 旧 shell 的激活状态：arm 必须复位（换 shell 后重新等回执）
    s.arm_shell_integration()
    assert s._si_active is False
    assert s._si_task is not None
    s._si_task.cancel()
    try:
        await s._si_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_injection_writes_snippet_once_ready(monkeypatch: pytest.MonkeyPatch):
    """就绪的 SSH shell：注入写 stdin 一次，内容为片段+回车"""
    written: list[str] = []
    s = _make_ssh_session()
    s.process = type("P", (), {"stdin": type("S", (), {"write": lambda self, d: written.append(d)})()})()  # type: ignore[arg-type]

    async def no_sleep(_t: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await s._inject_shell_integration()
    assert written == [SHELL_INTEGRATION_SNIPPET + "\n"]


@pytest.mark.asyncio
async def test_injection_skips_when_shell_gone(monkeypatch: pytest.MonkeyPatch):
    """等待期会话切本机/断开（_switching_local）：静默放弃，不抛错"""
    written: list[str] = []
    s = _make_ssh_session()
    s.process = type("P", (), {"stdin": type("S", (), {"write": lambda self, d: written.append(d)})()})()  # type: ignore[arg-type]
    s._switching_local = True

    async def no_sleep(_t: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await s._inject_shell_integration()
    assert written == []


# ---------- Cwd 权威回填 ----------


def test_set_cwd_authoritative_updates_and_arms_gate():
    s = _make_ssh_session()
    s.set_cwd("/var/log/../app")
    assert s.current_dir == "/var/app"
    assert s._si_active is True  # echo 观察通道的 cd 跟踪随之让位


def test_set_cwd_rejects_invalid_paths():
    s = _make_ssh_session()
    for bad in ("", "relative/path", "/x" * 300):
        s.set_cwd(bad)
    assert s.current_dir is None
    assert s._si_active is False
    # 本机会话完全不接受远端绝对路径口径的回填
    s._local_proc = object()  # type: ignore[assignment]
    s.set_cwd("/tmp")
    assert s._si_active is False
    assert s.current_dir is None


@pytest.mark.asyncio
async def test_echo_cd_tracking_yields_when_si_active():
    """S1 激活后 633;P;Cwd 是 cd 跟踪权威源：echo 观察通道不再驱动 _track_cd"""
    s = SSHSession("si-cd", "10.0.0.1", 22, "root")
    s._has_shell = True
    s.process = object()  # type: ignore[assignment]
    s._echo_parser.buffer = ""
    s._echo_parser.last_cmd = ""
    s._echo_parser.last_time = 0
    s._si_active = True
    s._parse_echo_line("root@host:~# cd /var\n")
    await asyncio.sleep(0.05)
    assert s.current_dir is None  # 观察通道让位，cd 由 set_cwd 负责


# ---------- 配置开关 ----------


def test_get_shell_integration_default_on_and_override():
    assert get_shell_integration({}) is True
    assert get_shell_integration({"shell_integration": False}) is False
    assert get_shell_integration({"shell_integration": True}) is True
    assert get_shell_integration({"shell_integration": "no"}) is True  # 非 bool 忽略
