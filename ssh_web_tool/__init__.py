"""
SSH Web Tool - 网页版 SSH 连接管理工具

支持：
- 本地 Server 统一管理所有 SSH 连接
- 网页 UI 操作终端（类似 Xshell）
- Python 库嵌入式使用
- CLI / HTTP API 调用
- 存储阵列管理页面自动登录
"""

__version__ = "0.1.0"
__author__ = "bestenevoy"

# 导出主要类和函数
from .ssh_tool import (
    SSHWebTool,
    patch_paramiko,
    patch_asyncssh,
    unpatch,
    quick_connect,
    get_manager,
)

from .sessions import session_manager, SSHSession, SessionManager
from .storage import storage, Storage

__all__ = [
    "SSHWebTool",
    "patch_paramiko",
    "patch_asyncssh",
    "unpatch",
    "quick_connect",
    "get_manager",
    "session_manager",
    "SSHSession",
    "SessionManager",
    "storage",
    "Storage",
    "__version__",
]
