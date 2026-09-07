# -*- coding: utf-8 -*-
"""
ssh-monkeypatch
===============

Monkey-patch 劫持当前 Python 进程内所有 paramiko / asyncssh 的 SSH 连接，
自动登记连接状态（主机、端口、用户、连接时间、存活状态），
client / 连接对象 close 时自动移除登记。

独立可用的轻量包：`pip install ssh-monkeypatch` 即可，无需 ssh_web_tool。
如需把登记同步到 ssh_web_tool 的 Web UI 镜像会话，安装
`pip install ssh-monkeypatch[web]` 后调用 `bridge_to_web(True)`。

快速开始：

    import ssh_monkeypatch
    ssh_monkeypatch.patch_all()

    # 之后现有代码发起 SSH 连接（paramiko / asyncssh 均可）：
    import paramiko
    c = paramiko.SSHClient()
    c.connect("192.168.1.10", username="root", password="xxx")

    # 连接已自动登记：
    print(ssh_monkeypatch.list_connections())
    # [{'id': 'a1b2c3d4', 'host': '192.168.1.10', 'port': 22, 'username': 'root',
    #   'kind': 'paramiko', 'connected_at': ..., 'is_alive': True}]

    c.close()   # 登记自动移除

Web UI 桥接（可选）：

    import ssh_monkeypatch
    ssh_monkeypatch.bridge_to_web(True)   # 需要 pip install ssh_web_tool
    ssh_monkeypatch.patch_all()

    之后连接会同时出现在 Web UI 的会话列表（镜像会话）。
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
)

__version__ = "0.1.0"
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
    "__version__",
]
