"""SSH 断开后本地终端 shell 配置（fallback_local_shell）单测

覆盖：默认值、合法/非法取值、保存后读取一致、config.example.json 合法性。
"""

import json
import sys
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


def test_default_debug_is_false(tmp_app_dir):
    """debug 默认关闭（隔离目录无配置文件 → 全默认）"""
    cfg = cfg_mod.load_config()
    assert cfg["debug"] is False


def test_debug_merged_from_user_config(tmp_app_dir):
    """config.json 设置 debug: true 后 load_config 能读到"""
    cfg = cfg_mod.load_config()
    cfg["debug"] = True
    assert cfg_mod.save_config(cfg) is True
    again = cfg_mod.load_config()
    assert again["debug"] is True
    # 顶层白名单：debug 与 open_browser 一样走 _TOP_LEVEL_KEYS 合并
    assert "debug" in cfg_mod._TOP_LEVEL_KEYS


def test_debug_false_roundtrip(tmp_app_dir):
    """显式 debug: false 保存后仍为 False（不丢键）"""
    cfg = cfg_mod.load_config()
    cfg["debug"] = False
    cfg_mod.save_config(cfg)
    again = cfg_mod.load_config()
    assert again["debug"] is False


def test_example_json_has_debug(tmp_app_dir):
    """config.example.json 包含 debug 配置项"""
    p = Path(__file__).resolve().parent.parent.parent / "config.example.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["debug"] is False



# ============ v0.1.48: find_config_file 查找顺序（EXE 目录 → cwd → 项目根 → 数据目录） ============


def test_find_config_file_returns_none_when_absent(tmp_app_dir):
    '四处都没有配置文件 → None（隔离目录）'
    assert cfg_mod.find_config_file() is None


def test_find_config_file_data_dir_fallback(tmp_app_dir):
    '只有数据目录有配置文件 → 返回数据目录'
    (tmp_app_dir / "config.json").write_text('{"fallback_local_shell": "cmd"}', encoding="utf-8")
    found = cfg_mod.find_config_file()
    assert found is not None
    assert found.parent == tmp_app_dir


def test_exe_side_config_wins_over_data_dir(tmp_app_dir, monkeypatch, tmp_path):
    'EXE 旁边有配置时优先于数据目录（文档声明的顺序）'
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    (exe_dir / "config.json").write_text('{"fallback_local_shell": "powershell"}', encoding="utf-8")
    (tmp_app_dir / "config.json").write_text('{"fallback_local_shell": "cmd"}', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "fake_app.exe"), raising=False)
    found = cfg_mod.find_config_file()
    assert found == exe_dir / "config.json"
    assert cfg_mod.load_config()["fallback_local_shell"] == "powershell"


def test_cwd_config_found(tmp_app_dir, monkeypatch, tmp_path):
    '当前工作目录有配置 → 可被找到'
    (tmp_path / "config.json").write_text('{"debug": true}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    found = cfg_mod.find_config_file()
    assert found == tmp_path / "config.json"
    assert cfg_mod.load_config()["debug"] is True


def test_save_config_writes_to_active_exe_side(tmp_app_dir, monkeypatch, tmp_path):
    'save_config 写入当前生效的配置文件（EXE 旁），而不是数据目录'
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    (exe_dir / "config.json").write_text('{"fallback_local_shell": "cmd"}', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "fake_app.exe"), raising=False)
    cfg = cfg_mod.load_config()
    cfg["fallback_local_shell"] = "powershell"
    assert cfg_mod.save_config(cfg) is True
    assert (exe_dir / "config.json").is_file()
    # 数据目录不应被新建（生效文件在 EXE 旁）
    assert not (tmp_app_dir / "config.json").exists()
    assert cfg_mod.load_config()["fallback_local_shell"] == "powershell"
