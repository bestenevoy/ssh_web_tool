"""/api/config 与 /api/config/fallback-shell 接口测试

save_config 被 monkeypatch，避免测试写入真实 ~/.ai4one/sshtool/config.json。
（save_config 由路由从 ssh_web_tool.config 模块级绑定，须 patch 该模块）
"""

from fastapi.testclient import TestClient

import main
from ssh_web_tool import config as config_module
from ssh_web_tool.api.routers import config as config_router

# 路由在模块级绑定 save_config（from ssh_web_tool.config import save_config），
# 测试须 patch 路由模块的绑定才能拦截写入
_SAVE_TARGET = config_router


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

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "powershell"})
    assert r.status_code == 200
    assert r.json()["fallback_local_shell"] == "powershell"
    assert saved.get("fallback_local_shell") == "powershell"


def test_set_fallback_shell_rejects_invalid(monkeypatch):
    def fake_save(cfg):
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "bash"})
    assert r.status_code == 400


def test_set_fallback_shell_case_insensitive(monkeypatch):
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "PowerShell"})
    assert r.status_code == 200
    assert r.json()["fallback_local_shell"] == "powershell"
    assert saved.get("fallback_local_shell") == "powershell"


# ============ ui_settings：前端 UI 设置持久化到 config.json ============


def test_get_config_returns_ui_settings(monkeypatch, tmp_path):
    # 隔离真实 ~/.ai4one/sshtool/config.json：断言的是默认值，不能被用户本机配置影响
    monkeypatch.setattr(config_module, "get_app_dir", lambda: tmp_path)
    c = TestClient(main.app)
    r = c.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    ui = body["ui_settings"]
    assert ui["theme"] in ("light", "dark")
    assert isinstance(ui["font_size"], int)
    assert "font_family" in ui
    assert ui["block_auto_fold"] is False  # 折叠默认关闭


def test_update_ui_settings_partial(monkeypatch):
    """部分更新：只传部分键，返回完整 ui_settings 且保存值含本次修改"""
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/ui-settings", json={"theme": "dark", "font_size": 16})
    assert r.status_code == 200
    ui = r.json()["ui_settings"]
    assert ui["theme"] == "dark"
    assert ui["font_size"] == 16
    # 未传的键保留原值
    assert "font_family" in ui
    assert saved["ui_settings"]["theme"] == "dark"
    assert saved["ui_settings"]["font_size"] == 16


def test_update_ui_settings_rejects_invalid(monkeypatch):
    """非法值返回 400 且不落盘"""

    def fake_save(cfg):
        raise AssertionError("非法值不应触发保存")

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/ui-settings", json={"font_size": 999})
    assert r.status_code == 400
    r = c.post("/api/config/ui-settings", json={"theme": "solarized"})
    assert r.status_code == 400


def test_update_ui_settings_empty_body_400(monkeypatch):
    monkeypatch.setattr(_SAVE_TARGET, "save_config", lambda cfg: True)
    c = TestClient(main.app)
    r = c.post("/api/config/ui-settings", json={})
    assert r.status_code == 400


def test_update_ui_settings_block_keys_roundtrip(monkeypatch):
    """切块方式 + 自定义正则：合法值保存并回读一致"""
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post(
        "/api/config/ui-settings",
        json={"block_split_mode": "enter", "custom_prompt_patterns": ["mini>", "db \\d+ =>"]},
    )
    assert r.status_code == 200
    ui = r.json()["ui_settings"]
    assert ui["block_split_mode"] == "enter"
    assert ui["custom_prompt_patterns"] == ["mini>", "db \\d+ =>"]
    assert saved["ui_settings"]["block_split_mode"] == "enter"


def test_update_ui_settings_rejects_invalid_split_mode(monkeypatch):
    """切块方式非法值返回 400 且不落盘"""

    def fake_save(cfg):
        raise AssertionError("非法值不应触发保存")

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/ui-settings", json={"block_split_mode": "auto"})
    assert r.status_code == 400


def test_update_ui_settings_filters_patterns_not_reject(monkeypatch):
    """自定义正则：超长条目被过滤后保存合法项（过滤语义而非 400）"""
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    long_item = "a" * 201
    r = c.post("/api/config/ui-settings", json={"custom_prompt_patterns": ["ok>", long_item]})
    assert r.status_code == 200
    assert r.json()["ui_settings"]["custom_prompt_patterns"] == ["ok>"]
    # 非字符串条目被 Pydantic 拒绝（422）
    r = c.post("/api/config/ui-settings", json={"custom_prompt_patterns": ["ok>", 42]})
    assert r.status_code == 422


# ============ 连接超时 / 本机 shell 检测 / 高亮规则 API ============


def test_get_config_returns_connect_timeout():
    c = TestClient(main.app)
    r = c.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["connect_timeout"], (int, float))
    assert 1 <= body["connect_timeout"] <= 300


def test_set_connect_timeout_saves(monkeypatch):
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    r = c.post("/api/config/connect-timeout", json={"seconds": 45})
    assert r.status_code == 200
    assert r.json()["connect_timeout"] == 45.0
    assert saved["connect_timeout"] == 45


def test_set_connect_timeout_rejects_out_of_range():
    c = TestClient(main.app)
    assert c.post("/api/config/connect-timeout", json={"seconds": 0}).status_code == 422
    assert c.post("/api/config/connect-timeout", json={"seconds": 301}).status_code == 422


def test_get_config_local_shell_choices_dynamic():
    """/api/config 返回动态检测结果（非静态三元组），且至少含内置 shell"""
    c = TestClient(main.app)
    body = c.get("/api/config").json()
    choices = body["local_shell_choices"]
    assert isinstance(choices, list) and choices
    assert "powershell" in choices


def test_set_fallback_shell_accepts_short_id_any_case(monkeypatch):
    monkeypatch.setattr(_SAVE_TARGET, "save_config", lambda cfg: True)
    c = TestClient(main.app)
    r = c.post("/api/config/fallback-shell", json={"shell": "CMD"})
    assert r.status_code == 200
    assert r.json()["fallback_local_shell"] == "cmd"


def test_update_ui_settings_highlight_rules_roundtrip(monkeypatch):
    """高亮规则：合法列表保存并回读一致"""
    saved = {}

    def fake_save(cfg):
        saved.update(cfg)
        return True

    monkeypatch.setattr(_SAVE_TARGET, "save_config", fake_save)
    c = TestClient(main.app)
    rule = {"keyword": "WARN", "name": "警告", "color": "#FFD060", "enabled": True, "is_case_sensitive": False}
    r = c.post("/api/config/ui-settings", json={"highlight_rules": [rule]})
    assert r.status_code == 200
    assert r.json()["ui_settings"]["highlight_rules"] == [rule]
    assert saved["ui_settings"]["highlight_rules"] == [rule]


def test_update_ui_settings_highlight_rules_filtered_not_rejected(monkeypatch):
    """高亮规则：非法条目被过滤而非整键 400；非对象条目 Pydantic 422"""
    monkeypatch.setattr(_SAVE_TARGET, "save_config", lambda cfg: True)
    c = TestClient(main.app)
    rule = {"keyword": "E", "name": "e", "color": "#FF0000", "enabled": True, "is_case_sensitive": False}
    r = c.post("/api/config/ui-settings", json={"highlight_rules": [rule, {**rule, "color": "bad"}]})
    assert r.status_code == 200
    assert r.json()["ui_settings"]["highlight_rules"] == [rule]
    r = c.post("/api/config/ui-settings", json={"highlight_rules": [rule, "junk"]})
    assert r.status_code == 422
