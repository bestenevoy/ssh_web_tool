# -*- coding: utf-8 -*-
"""SSH 断开后本地终端 shell 配置（fallback_local_shell）单测

覆盖：默认值、合法/非法取值、保存后读取一致、config.example.json 合法性。
"""
import json
from pathlib import Path

import pytest

from ssh_web_tool import config as cfg_mod


@pytest.fixture
def tmp_app_dir(monkeypatch, tmp_path):
    """把配置目录隔离到临时目录，避免污染 ~/.ai4one/wstool/config.json"""
    monkeypatch.setattr(cfg_mod, "get_app_dir", lambda: tmp_path)
    return tmp_path


def test_default_fallback_shell_is_cmd(tmp_app_dir):
    """默认（无配置/空配置）为 cmd"""
    assert cfg_mod.get_fallback_local_shell({}) == "cmd"
    assert cfg_mod.get_fallback_local_shell(None) == "cmd"
    # 真实 load_config（隔离目录无配置文件 → 全默认）
    assert cfg_mod.get_fallback_local_shell(cfg_mod.load_config()) == "cmd"


def test_valid_shell_values(tmp_app_dir):
    assert cfg_mod.get_fallback_local_shell({"fallback_local_shell": "powershell"}) == "powershell"
    assert cfg_mod.get_fallback_local_shell({"fallback_local_shell": "pwsh"}) == "pwsh"


def test_invalid_shell_falls_back_to_cmd(tmp_app_dir):
    """非法取值回退 cmd（不抛异常）"""
    for bad in ("bash", "zsh", "PowerShell.exe", "", None, 123, "cmd.exe"):
        assert cfg_mod.get_fallback_local_shell({"fallback_local_shell": bad}) == "cmd", bad


def test_save_then_load_roundtrip(tmp_app_dir):
    """save_config 写入后 load_config 能读到同一值"""
    cfg = cfg_mod.load_config()
    cfg["fallback_local_shell"] = "powershell"
    assert cfg_mod.save_config(cfg) is True
    again = cfg_mod.load_config()
    assert again["fallback_local_shell"] == "powershell"
    assert (tmp_app_dir / "config.json").is_file()


def test_save_config_keeps_other_keys(tmp_app_dir):
    cfg = cfg_mod.load_config()
    cfg["open_browser"] = False
    cfg["fallback_local_shell"] = "cmd"
    cfg_mod.save_config(cfg)
    again = cfg_mod.load_config()
    assert again["open_browser"] is False
    assert again["server"]["port"] == 8765


def test_example_json_is_valid_and_has_shell(tmp_app_dir):
    """config.example.json 必须可被 json 解析（曾因非法尾逗号直接失败），且包含新配置项"""
    p = Path(__file__).resolve().parent.parent.parent / "config.example.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["fallback_local_shell"] == "cmd"
