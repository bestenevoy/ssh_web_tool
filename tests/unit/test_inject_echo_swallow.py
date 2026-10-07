"""S1 注入片段回显吞噬：注入行在终端上不可见（输出侧精确剥离）

背景：集成片段以一行命令注入远端 pty，readline 会把它回显到输出流（终端上
出现一坨脚本文本）。修复：_InjectEchoGuard 在广播前把回显（片段原文 + 换行）
精确匹配并吞掉；匹配失败/超时一律原样回放，绝不误吞真实输出。
"""

import asyncio

from ssh_web_tool.sessions import SHELL_INTEGRATION_SNIPPET, SSHSession, _InjectEchoGuard

# 2026-10-07 实机抓取（bash on Linux，readline）：回显区段后紧跟的真实序列
REAL_TAIL = "\x1b[?2004l\r\x1b]633;SI;ready\x07\x1b[01;32mwrz@yqy2025\x1b[00m:~$ "


def test_full_match_swallowed_with_tail():
    """整段回显+CRLF 被吞，后续 OSC 事件/提示符原样保留"""
    g = _InjectEchoGuard(SHELL_INTEGRATION_SNIPPET)
    assert g.filter(SHELL_INTEGRATION_SNIPPET + "\r\n" + REAL_TAIL) == REAL_TAIL
    assert g.finished


def test_cross_chunk_partial_then_rest():
    """回显跨输出块：前半暂存不转发，后半到齐后整体吞掉"""
    g = _InjectEchoGuard("ABCDEF")
    assert g.filter("ABC") == ""
    assert g.filter("DEF\r\nX") == "X"
    assert g.finished


def test_lone_lf_and_cr_accepted_as_newline():
    """换行变体（LF/CR）同样吞掉，提高非 bash 场景兼容性"""
    g1 = _InjectEchoGuard("ABCDEF")
    assert g1.filter("ABCDEF\nX") == "X"
    g2 = _InjectEchoGuard("ABCDEF")
    assert g2.filter("ABCDEF\rX") == "X"


def test_non_newline_after_snippet_passes_through():
    """片段后直接跟非换行内容（不同 shell 重绘形态）：仅吞片段本身"""
    g = _InjectEchoGuard("ABCDEF")
    assert g.filter("ABCDEFX") == "X"
    assert g.finished


def test_mismatch_first_chunk_replays_all():
    """首块即不匹配（如注入前恰好有其他输出）：全部原样放行，不丢字节"""
    g = _InjectEchoGuard("ABCDEF")
    assert g.filter("ABCDEX") == "ABCDEX"
    assert g.finished


def test_mismatch_after_partial_replays_staged():
    """暂存后再不匹配：已暂存字节回放，一个不丢"""
    g = _InjectEchoGuard("ABCDEF")
    assert g.filter("ABC") == ""
    assert g.filter("QQ") == "ABCQQ"
    assert g.finished


def test_timeout_replays_staged():
    """防呆：迟迟等不到回显（超时）放弃吞噬并回放暂存字节"""
    g = _InjectEchoGuard("ABCDEF")
    assert g.filter("ABC") == ""
    g._started -= 11  # 模拟超时
    assert g.filter("DEF") == "ABCDEF"
    assert g.finished


def test_broadcast_output_swallows_injected_echo():
    """集成：guard 挂在会话上时，广播输出（监听器视角）不含注入行"""

    async def run():
        s = SSHSession("t", "10.0.0.1", 22, "root")
        listener = s.add_output_listener()
        s._si_echo_guard = _InjectEchoGuard(SHELL_INTEGRATION_SNIPPET)
        await s.broadcast_output(SHELL_INTEGRATION_SNIPPET + "\r\nHELLO")
        return listener.get_nowait(), s._si_echo_guard

    got, guard = asyncio.run(run())
    assert got == "HELLO"
    assert guard is None  # 流程结束后引用被清
