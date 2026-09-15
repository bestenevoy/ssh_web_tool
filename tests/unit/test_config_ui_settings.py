"""前端 UI 设置持久化（config.json 的 ui_settings 段）单测

覆盖：默认值、逐键校验回退、部分合并（缺失键保留默认）、save/load roundtrip。
"""

import json

import pytest

from ssh_web_tool import config as cfg_mod


@pytest.fixture
def tmp_app_dir(monkeypatch, tmp_path):
    """把配置目录隔离到临时目录，避免污染 ~/.ai4one/sshtool/config.json"""
    monkeypatch.setattr(cfg_mod, "get_app_dir", lambda: tmp_path)
    return tmp_path


def test_default_ui_settings(tmp_app_dir):
    """无配置文件时 ui_settings 为全默认值"""
    got = cfg_mod.get_ui_settings()
    assert got == cfg_mod.UI_SETTINGS_DEFAULTS
    assert got["theme"] == "light"
    assert got["block_auto_fold"] is False


def test_validate_ui_setting_each_key():
    """逐键校验：合法值原样返回，非法值返回 None"""
    v = cfg_mod._validate_ui_setting
    assert v("theme", "dark") == "dark"
    assert v("theme", "blue") is None
    assert v("font_family", "JetBrains Mono") == "JetBrains Mono"
    assert v("font_family", "   ") is None
    assert v("font_size", 16) == 16
    assert v("font_size", 99) is None
    assert v("font_size", True) is None  # bool 不是合法 int
    assert v("block_bar", False) is False
    assert v("block_bar", "yes") is None
    assert v("block_auto_fold", True) is True
    assert v("block_max_lines", 50) == 50
    assert v("block_max_lines", 33) is None  # 不在可选档位
    assert v("unknown_key", 1) is None  # 未知键一律忽略


def test_load_config_merges_ui_settings_per_key(tmp_app_dir):
    """ui_settings 只写部分键时其余键保留默认值（子字典逐键合并，不整体替换）"""
    (tmp_app_dir / "config.json").write_text(
        json.dumps({"ui_settings": {"theme": "dark", "font_size": 18}}), encoding="utf-8"
    )
    cfg = cfg_mod.load_config()
    assert cfg["ui_settings"]["theme"] == "dark"
    assert cfg["ui_settings"]["font_size"] == 18
    # 未写的键回退默认
    assert cfg["ui_settings"]["block_bar"] is True
    assert cfg["ui_settings"]["block_auto_fold"] is False
    assert cfg["ui_settings"]["block_max_lines"] == 30


def test_get_ui_settings_invalid_values_fall_back(tmp_app_dir):
    """非法值在校验层回退默认（load_config 保留原始值，get_ui_settings 负责校验）"""
    (tmp_app_dir / "config.json").write_text(
        json.dumps({"ui_settings": {"theme": "solarized", "font_size": 999}}), encoding="utf-8"
    )
    got = cfg_mod.get_ui_settings()
    assert got["theme"] == "light"
    assert got["font_size"] == 13


def test_ui_settings_roundtrip(tmp_app_dir):
    """save_config 写入后 load_config + get_ui_settings 能读到同一值"""
    cfg = cfg_mod.load_config()
    cfg["ui_settings"]["theme"] = "dark"
    cfg["ui_settings"]["font_size"] = 20
    assert cfg_mod.save_config(cfg) is True
    again = cfg_mod.get_ui_settings()
    assert again["theme"] == "dark"
    assert again["font_size"] == 20


def test_ui_settings_non_dict_ignored(tmp_app_dir):
    """ui_settings 不是对象时整体忽略（回退全默认）"""
    (tmp_app_dir / "config.json").write_text(json.dumps({"ui_settings": "dark"}), encoding="utf-8")
    assert cfg_mod.get_ui_settings() == cfg_mod.UI_SETTINGS_DEFAULTS
