# -*- coding: utf-8 -*-
"""
ssh-monkeypatch
===============

Monkey-patch 劫持当前 Python 进程内所有 paramiko / asyncssh 的 SSH 连接，
自动登记连接状态，并**采集 SSH 事件**（连接 / 命令 / stdout/stderr 输出 / 关闭）。

采集的事件可保留在包内缓冲（get_events / list_events），也可通过 WebSocket
推送到 ssh_web_tool 可视化服务（测试进程与可视化服务完全分离、跨进程实时展示
与历史回放）。

安装：`pip install ssh-monkeypatch`；推送采集需要 `pip install ssh-monkeypatch[stream]`。

快速开始（测试侧）：

    import ssh_monkeypatch
    ssh_monkeypatch.patch_all()
    # 推送事件到可视化服务（如本机 ssh_web_tool）：
    ssh_monkeypatch.start_streamer("ws://127.0.0.1:8765/ws/stream")

    # 之后原有测试业务代码正常发起 SSH 连接（不修改业务逻辑）：
    import paramiko
    c = paramiko.SSHClient()
    c.connect("192.168.1.10", username="root", password="xxx")
    stdin, stdout, stderr = c.exec_command("uname -a")
    print(stdout.read())          # 读到的内容同时被采集并推送

    c.close()                     # 连接登记自动移除，close 事件推送

    # 不推送时也可直接查包内缓冲：
    print(ssh_monkeypatch.list_events("session_id"))

Web UI 桥接（可选，同进程场景）：

    ssh_monkeypatch.bridge_to_web(True)   # 需要 pip install ssh_web_tool
    ssh_monkeypatch.patch_all()
"""
from .patch import (
    Connection,
    registry,
    list_connections,
    get_connection,
    clear_registry,
    bridge_to_web,
    patch_paramiko,
    patch_asyncssh,
    patch_all,
    unpatch,
    set_capture_unread,
    get_events,
    list_events,
    clear_events,
    start_streamer,
    stop_streamer,
)

__version__ = "0.2.0"
__author__ = "bestenevoy"

__all__ = [
    "Connection",
    "registry",
    "list_connections",
    "get_connection",
    "clear_registry",
    "bridge_to_web",
    "patch_paramiko",
    "patch_asyncssh",
    "patch_all",
    "unpatch",
    "set_capture_unread",
    "get_events",
    "list_events",
    "clear_events",
    "start_streamer",
    "stop_streamer",
    "__version__",
]
