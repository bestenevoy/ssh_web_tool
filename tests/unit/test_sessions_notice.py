# -*- coding: utf-8 -*-
"""会话级通知机制测试（Bug B 回归）

背景：全局连接监控协程原本通过 _broadcast_output 推送 shell 异常提示，
提示文本会进入 run_command 的注入捕获监听器/echo 解析缓冲，污染命令返回的 stdout。
改为 session.set_shell_notice / ws 端 take_shell_notice 后，通知只经 WebSocket 通道。
"""
import pytest

from ssh_web_tool.sessions import SSHSession


@pytest.fixture()
def session():
    return SSHSession("notice-test", "host", 22, "user")


def test_set_and_take_once(session):
    session.set_shell_notice("检测到终端 shell 状态异常")
    assert session.take_shell_notice() == "检测到终端 shell 状态异常"
    # 只取一次，第二次为 None
    assert session.take_shell_notice() is None


def test_no_notice_by_default(session):
    assert session.take_shell_notice() is None


def test_overwrite_notice(session):
    session.set_shell_notice("first")
    session.set_shell_notice("second")
    assert session.take_shell_notice() == "second"
