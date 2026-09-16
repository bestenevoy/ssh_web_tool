"""本机 shell 检测（detect_local_shells）与 SSH 连接超时（connect_timeout）单测

覆盖：检测列表非空且含 Windows 内置 shell、短标识/路径校验、
连接超时默认值/合法范围/非法回退、顶层配置合并。
"""

import pytest

from ssh_web_tool import config as cfg_mod


@pytest.fixture
def tmp_app_dir(monkeypatch, tmp_path):
    """把配置目录隔离到临时目录，避免污染 ~/.ai4one/sshtool/config.json"""
    monkeypatch.setattr(cfg_mod, "get_app_dir", lambda: tmp_path)
    return tmp_path


# ============ detect_local_shells：本机 shell 扫描（rssh 同款策略） ============


def test_detect_local_shells_returns_non_empty_strings():
    result = cfg_mod.detect_local_shells()
    assert isinstance(result, list) and result
    assert all(isinstance(s, str) and s for s in result)


def test_detect_local_shells_contains_builtin_ids_on_windows():
    """Windows 测试环境必有 cmd / powershell（System32 固定路径）"""
    result = cfg_mod.detect_local_shells()
    assert "cmd" in result
    assert "powershell" in result


def test_detect_local_shells_builtin_ids_first_and_deduped():
    result = cfg_mod.detect_local_shells()
    ids = [s for s in result if s in cfg_mod.LOCAL_SHELL_CHOICES]
    assert len(ids) == len(set(ids))
    # 短标识排在前（LOCAL_SHELL_CHOICES 顺序），完整路径在后
    first_path_idx = next((i for i, s in enumerate(result) if "\\" in s or "/" in s), len(result))
    assert all(i < first_path_idx for i, s in enumerate(result) if s in cfg_mod.LOCAL_SHELL_CHOICES)
    # 路径条目按文件名字典序
    paths = [s for s in result if s not in cfg_mod.LOCAL_SHELL_CHOICES]
    assert paths == sorted(paths, key=lambda p: (p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower(), p.lower()))


# ============ validate_local_shell_value：短标识 / 完整路径 ============


def test_validate_local_shell_value_short_ids(tmp_path):
    assert cfg_mod.validate_local_shell_value("cmd") == "cmd"
    assert cfg_mod.validate_local_shell_value(" PowerShell ") == "powershell"  # 去空白 + 大小写不敏感
    assert cfg_mod.validate_local_shell_value("PWSH") == "pwsh"


def test_validate_local_shell_value_real_path(tmp_path):
    exe = tmp_path / "mysh.exe"
    exe.write_bytes(b"")
    assert cfg_mod.validate_local_shell_value(str(exe)) == str(exe)


def test_validate_local_shell_value_invalid(tmp_path):
    assert cfg_mod.validate_local_shell_value("bash") is None  # 不存在的相对路径
    assert cfg_mod.validate_local_shell_value(str(tmp_path / "nope.exe")) is None
    assert cfg_mod.validate_local_shell_value("") is None
    assert cfg_mod.validate_local_shell_value("   ") is None
    assert cfg_mod.validate_local_shell_value(None) is None
    assert cfg_mod.validate_local_shell_value(123) is None


def test_get_fallback_local_shell_accepts_path_and_any_case(tmp_app_dir, tmp_path):
    exe = tmp_path / "bash.exe"
    exe.write_bytes(b"")
    assert cfg_mod.get_fallback_local_shell({"fallback_local_shell": str(exe)}) == str(exe)
    assert cfg_mod.get_fallback_local_shell({"fallback_local_shell": "PowerShell"}) == "powershell"
    assert cfg_mod.get_fallback_local_shell({"fallback_local_shell": "not_a_shell"}) == "powershell"


# ============ get_connect_timeout：SSH 连接超时（秒，1-300） ============


def test_connect_timeout_default(tmp_app_dir):
    assert cfg_mod.get_connect_timeout({}) == 10.0
    assert cfg_mod.get_connect_timeout(None) == 10.0
    assert cfg_mod.DEFAULT_CONFIG["connect_timeout"] == 10
    assert "connect_timeout" in cfg_mod._TOP_LEVEL_KEYS


def test_connect_timeout_valid_range(tmp_app_dir):
    assert cfg_mod.get_connect_timeout({"connect_timeout": 30}) == 30.0
    assert cfg_mod.get_connect_timeout({"connect_timeout": 1}) == 1.0
    assert cfg_mod.get_connect_timeout({"connect_timeout": 300}) == 300.0
    assert cfg_mod.get_connect_timeout({"connect_timeout": 12.5}) == 12.5


def test_connect_timeout_invalid_falls_back(tmp_app_dir):
    for bad in (0, -5, 301, 1000, "abc", None, True, False):
        assert cfg_mod.get_connect_timeout({"connect_timeout": bad}) == 10.0, bad


def test_connect_timeout_loaded_from_user_config(tmp_app_dir):
    """顶层白名单合并：config.json 写 connect_timeout 后能读到"""
    (tmp_app_dir / "config.json").write_text('{"connect_timeout": 45}', encoding="utf-8")
    assert cfg_mod.get_connect_timeout() == 45.0
