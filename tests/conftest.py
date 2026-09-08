# -*- coding: utf-8 -*-
"""pytest 公共配置：测试隔离 + 共享 fixtures

隔离原则：
- storage.data_file 在 import main 之前指向临时目录，避免污染 ~/.ai4one/wstool/data.json
- history_db.get_db_path 由各测试 monkeypatch 到临时目录
- API 测试通过替换 main.session_manager 为 FakeSessionManager，不发起真实 SSH
"""
import sys
import os
import tempfile
from pathlib import Path

import pytest

# 项目根目录加入 sys.path（pytest 不会自动加入 rootdir）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 在 import main 之前替换数据文件路径（会话级临时目录）----
_TMP_DATA_DIR = Path(tempfile.mkdtemp(prefix="wstool-test-"))

from ssh_web_tool.storage import storage  # noqa: E402

storage.data_file = str(_TMP_DATA_DIR / "data.json")
storage._data = storage._load()  # 空数据起步

import main  # noqa: E402


# ---- e2e 开关：默认跳过需要真实 SSH 的测试 ----
def pytest_addoption(parser):
    parser.addoption(
        "--e2e", action="store_true", default=False,
        help="运行需要真实 SSH 连接的端到端测试（需配置测试机）",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--e2e"):
        return
    skip_e2e = pytest.mark.skip(reason="需要 --e2e 参数（真实 SSH）")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def app():
    """FastAPI app 实例（数据目录已隔离）"""
    return main.app


@pytest.fixture()
def client(app, tmp_path):
    """TestClient：每个测试使用全新的数据文件，杜绝测试间数据污染"""
    from fastapi.testclient import TestClient
    storage.data_file = str(tmp_path / "data.json")
    storage._data = storage._load()
    with TestClient(app) as c:
        yield c


class FakeSessionManager:
    """替换 main.session_manager：内存会话，不发真实 SSH"""

    def __init__(self):
        self.sessions = {}

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def create_session(self, session_id, host, port, username, **kwargs):
        from ssh_web_tool.sessions import SSHSession
        s = SSHSession(session_id, host, port, username, **kwargs)
        self.sessions[session_id] = s
        return s

    async def remove_session(self, session_id):
        return self.sessions.pop(session_id, None) is not None

    def get_host_terminal_count(self, host_id):
        return sum(1 for s in self.sessions.values() if s.host_id == host_id)

    def get_sessions_by_host(self, host_id):
        return [s for s in self.sessions.values() if s.host_id == host_id]

    def list_sessions(self):
        return list(self.sessions.values())

    def get_active_terminals(self):
        return [s for s in self.sessions.values() if getattr(s, "is_connected", False)]

    def close_all(self):
        self.sessions.clear()


@pytest.fixture()
def fake_sessions(monkeypatch):
    """替换 main.session_manager 为内存实现，并返回管理器"""
    mgr = FakeSessionManager()
    monkeypatch.setattr(main, "session_manager", mgr)
    return mgr


@pytest.fixture()
def fake_history_db(monkeypatch, tmp_path):
    """把 history.db 指到临时目录并初始化，避免污染真实历史库"""
    import ssh_web_tool.history_db as history_db

    def _fake_path():
        return tmp_path / "history.db"

    monkeypatch.setattr(history_db, "get_db_path", _fake_path)

    async def _init():
        await history_db.init_db()

    import asyncio
    asyncio.run(_init())
    return history_db
