"""/api/files/pick 测试：系统对话框是阻塞 UI，不真实弹窗——monkeypatch 掉 _win_file_dialog"""

import sys

import pytest
from fastapi.testclient import TestClient

import main
from ssh_web_tool.api.routers import files

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="系统文件对话框仅 Windows 提供")


def test_pick_returns_picked_path(monkeypatch):
    """选择成功：透传 _win_file_dialog 返回的绝对路径"""
    seen: dict = {}

    def fake_dialog(mode, initial_dir, title):
        seen["mode"] = mode
        seen["initial_dir"] = initial_dir
        return r"C:\tmp\picked.zs"

    monkeypatch.setattr(files, "_win_file_dialog", fake_dialog)
    c = TestClient(main.app)
    r = c.post("/api/files/pick", json={"mode": "open", "initial_dir": "~"})
    assert r.status_code == 200
    assert r.json() == {"status": "picked", "path": r"C:\tmp\picked.zs"}
    assert seen["mode"] == "open"


def test_pick_canceled_on_cancel(monkeypatch):
    """用户取消：返回 canceled 而非错误"""
    monkeypatch.setattr(files, "_win_file_dialog", lambda *a: None)
    c = TestClient(main.app)
    r = c.post("/api/files/pick", json={"mode": "save"})
    assert r.status_code == 200
    assert r.json() == {"status": "canceled", "path": ""}


def test_pick_normalizes_unknown_mode(monkeypatch):
    """mode 非 open/save 时按 open 处理（不会误弹保存框）"""
    got: list = []

    def fake_dialog(mode, initial_dir, title):
        got.append(mode)
        return None

    monkeypatch.setattr(files, "_win_file_dialog", fake_dialog)
    c = TestClient(main.app)
    r = c.post("/api/files/pick", json={"mode": "weird"})
    assert r.status_code == 200
    assert got == ["open"]
