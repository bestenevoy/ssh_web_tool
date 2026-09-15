"""全局单例的【调用时】间接层（路由层从这里取单例，测试可替换）

历史原因：既有测试通过替换 main 模块的绑定注入假实现——
  - conftest.py 的 fake_sessions 替换 main.session_manager；
  - test_preop_api 替换 main.get_app_dir；
  - test_logs_api 替换 main.SSHSession.LOG_DIR / main.session_manager.get_session。

若路由模块在 import 时直接绑定单例（from ssh_web_tool.sessions import session_manager），
将绕过这些替换、测试注入失效。因此这里统一在【调用时】动态读取 main 的当前绑定。

配合：main.py 顶部 _register_as_main() 把自身注册进 sys.modules 的 "main" 键，
保证 `python main.py` 脚本模式下 `import main` 复用同一模块（避免二次执行
导致路由拿到与运行实例不同的单例）。
"""

import importlib
from typing import Any


def get_main() -> Any:
    """动态获取 main 模块对象（脚本模式与测试模式均唯一）"""
    return importlib.import_module("main")


def get_session_manager():
    """获取会话管理器（测试替换 main.session_manager 后此处始终返回替换后的值）"""
    return get_main().session_manager


def get_storage():
    return get_main().storage


def get_event_bus():
    return get_main().event_bus


def get_history_db():
    return get_main().history_db


def get_ssh_session_cls():
    return get_main().SSHSession


def get_app_dir_fn():
    """获取 main.get_app_dir 绑定（一个函数；test_preop_api 会替换该绑定）"""
    return get_main().get_app_dir


def get_external_hub():
    return get_main().external_hub
