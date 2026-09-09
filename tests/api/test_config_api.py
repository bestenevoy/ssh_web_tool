# -*- coding: utf-8 -*-
"""/api/config 与 /api/config/fallback-shell 接口测试

save_config 被 monkeypatch，避免测试写入真实 ~/.ai4one/wstool/config.json。
"""
from fastapi.testclient import TestClient

import main


def test_get_config_returns_fallback_shell():
    c = TestClient(main.app)
    r = c.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "fallback_local_shell" in body
    assert body["fallback_local_shell"] in ("cmd", "powershell", "pwsh")
    assert "local_shell_choices" in body


def test_set_fallback_shell_saves(monkeypatch):
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(main, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "powershell"})
    assert r.status_code == 200
    assert r.json()["fallback_local_shell"] == "powershell"
    assert saved.get("fallback_local_shell") == "powershell"


def test_set_fallback_shell_rejects_invalid(monkeypatch):
    def fake_save(cfg):
        return True

    monkeypatch.setattr(main, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "bash"})
    assert r.status_code == 400


def test_set_fallback_shell_case_insensitive(monkeypatch):
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(main, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "PowerShell"})
    assert r.status_code == 200
    assert r.json()["fallback_local_shell"] == "powershell"
    assert saved.get("fallback_local_shell") == "powershell"
