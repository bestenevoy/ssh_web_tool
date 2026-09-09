# -*- coding: utf-8 -*-
"""自动更新模块单元测试：版本比较 / Release 解析 / 下载 / 更新脚本生成

网络请求全部 mock（不依赖真实 GitHub），保证测试可重复。
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from ssh_web_tool import updater


# ---------- 版本比较 ----------

def test_parse_version():
    assert updater.parse_version("v0.1.28") == (0, 1, 28)
    assert updater.parse_version("0.1.9") == (0, 1, 9)
    assert updater.parse_version("v1.0") == (1, 0)
    assert updater.parse_version("垃圾") == (0,)


def test_is_newer_semver():
    assert updater.is_newer("v0.1.28", "0.1.27")
    assert updater.is_newer("0.1.10", "0.1.9")  # 不按字典序
    assert not updater.is_newer("0.1.27", "0.1.28")
    assert not updater.is_newer("0.1.28", "0.1.28")
    assert updater.is_newer("0.2.0", "0.1.99")


# ---------- Release 解析 ----------

def _fake_response(payload: dict):
    class FakeResp:
        def read(self):
            return json.dumps(payload).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return FakeResp()


def test_get_latest_release_picks_exe_asset():
    payload = {
        "tag_name": "v0.1.28",
        "html_url": "https://github.com/x/y/releases/tag/v0.1.28",
        "assets": [
            {"name": "ssh-web-tool.zip", "browser_download_url": "https://x/zip"},
            {"name": "SSHWebTool.exe", "browser_download_url": "https://x/exe", "size": 22000000},
        ],
    }
    with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)) as m:
        rel = updater.get_latest_release()
    assert rel is not None
    assert rel["version"] == "v0.1.28"
    assert rel["download_url"] == "https://x/exe"
    assert rel["size"] == 22000000
    m.assert_called_once()


def test_get_latest_release_no_exe_asset_returns_none():
    payload = {"tag_name": "v0.1.28", "assets": [{"name": "other.zip"}]}
    with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
        assert updater.get_latest_release() is None


def test_get_latest_release_network_error_returns_none():
    with mock.patch("urllib.request.urlopen", side_effect=Exception("net down")):
        assert updater.get_latest_release() is None


# ---------- 下载 ----------

def test_download_exe_success(tmp_path):
    dest = tmp_path / "new.exe"
    chunks = [b"part1", b"part2"]

    class FakeResp:
        def read(self, n):
            return chunks.pop(0) if chunks else b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        assert updater.download_exe("https://x/exe", dest) is True
    assert dest.read_bytes() == b"part1part2"
    assert not list(tmp_path.glob("*.part"))  # 临时文件已清理


def test_download_exe_empty_fails(tmp_path):
    dest = tmp_path / "new.exe"

    class FakeResp:
        def read(self, n):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
        assert updater.download_exe("https://x/exe", dest) is False
    assert not dest.exists()


def test_download_exe_error_cleans_partial(tmp_path):
    dest = tmp_path / "new.exe"
    with mock.patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        assert updater.download_exe("https://x/exe", dest) is False
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


# ---------- 更新脚本 ----------

def test_build_update_script(tmp_path):
    script = updater.build_update_script(tmp_path, "SSHWebTool.exe", "SSHWebTool_new.exe", pid=12345)
    assert script == tmp_path / "_wstool_update.bat"
    content = script.read_text(encoding="ascii")
    assert 'taskkill /f /pid 12345' in content  # 指定 PID 兜底强杀
    assert 'del /f /q "%~dp0SSHWebTool.exe"' in content
    assert 'move /y "%~dp0SSHWebTool_new.exe" "%~dp0SSHWebTool.exe"' in content
    assert 'start "" "%~dp0SSHWebTool.exe"' in content
    assert 'if exist "%~dp0SSHWebTool.exe" goto wait' in content  # 轮询等待旧进程退出
    assert "if %N% gtr 30 goto fail" in content
    assert 'goto fail' in content
    assert '_wstool_update.log' in content  # 失败日志便于排查


def test_build_update_script_no_pid(tmp_path):
    script = updater.build_update_script(tmp_path, "A.exe", "A_new.exe")
    content = script.read_text(encoding="ascii")
    assert "taskkill" not in content


# ---------- 网络重试与下载校验 ----------

def test_get_latest_release_retries_on_network_error():
    """网络异常自动重试：前 2 次失败，第 3 次成功"""
    payload = {
        "tag_name": "v0.1.30",
        "html_url": "https://github.com/x/y/releases/tag/v0.1.30",
        "assets": [{"name": "SSHWebTool.exe", "browser_download_url": "https://x/exe", "size": 100}],
    }
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("net down")
        return _fake_response(payload)

    with mock.patch("urllib.request.urlopen", side_effect=flaky):
        rel = updater.get_latest_release()
    assert rel is not None and rel["version"] == "v0.1.30"
    assert calls["n"] == 3


def test_get_latest_release_all_retries_fail_returns_none():
    calls = {"n": 0}

    def always_fail(*a, **k):
        calls["n"] += 1
        raise Exception("net down")

    with mock.patch("urllib.request.urlopen", side_effect=always_fail):
        assert updater.get_latest_release() is None
    assert calls["n"] == updater.RETRY_TIMES  # 重试满次数


def _chunk_reader(chunks):
    class FakeResp:
        def read(self, n):
            return chunks.pop(0) if chunks else b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return FakeResp()


def test_download_exe_size_mismatch_retries_then_success(tmp_path):
    """下载大小与预期不符时重试（防半截文件），第二次成功"""
    dest = tmp_path / "new.exe"
    attempts = {"n": 0}

    def flaky(*a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _chunk_reader([b"short"])  # 大小不匹配
        return _chunk_reader([b"0123456789ab"])  # 12 字节，匹配预期

    with mock.patch("urllib.request.urlopen", side_effect=flaky):
        assert updater.download_exe("https://x/exe", dest, expected_size=12) is True
    assert dest.read_bytes() == b"0123456789ab"
    assert attempts["n"] == 2
    assert not list(tmp_path.glob("*.part"))


def test_download_exe_size_mismatch_all_fail(tmp_path):
    dest = tmp_path / "new.exe"
    with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _chunk_reader([b"tiny"])):
        assert updater.download_exe("https://x/exe", dest, expected_size=99999) is False
    assert not dest.exists()


def test_download_exe_retries_on_network_error(tmp_path):
    dest = tmp_path / "new.exe"
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("timeout")
        return _chunk_reader([b"ok"])

    with mock.patch("urllib.request.urlopen", side_effect=flaky):
        assert updater.download_exe("https://x/exe", dest, expected_size=2) is True
    assert dest.read_bytes() == b"ok"
    assert calls["n"] == 3


# ---------- 流程（源码模式不自我更新） ----------

def test_apply_update_source_mode_no_os_exit(monkeypatch):
    """源码模式（非 frozen）：提示不支持自我更新，不触发下载/退出"""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(updater, "_current_exe", lambda: None)
    monkeypatch.setattr(updater, "get_latest_release", lambda: {
        "version": "v9.9.9", "download_url": "https://x", "size": 1, "url": ""
    })
    monkeypatch.setattr(updater, "download_exe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应下载")))
    monkeypatch.setattr(updater, "_confirm", lambda *a, **k: True)
    info_shown = []
    monkeypatch.setattr(updater, "_info", lambda msg, *a, **k: info_shown.append(msg))
    updater._ask_and_apply("msg", {"version": "v9.9.9"}, None)
    assert any("源码模式" in m for m in info_shown)


def test_apply_update_confirm_false_no_download(monkeypatch):
    """用户取消确认：不下载不退出"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "_current_exe", lambda: Path("C:/app/SSHWebTool.exe"))
    monkeypatch.setattr(updater, "download_exe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应下载")))
    monkeypatch.setattr(updater, "_confirm", lambda *a, **k: False)
    updater._ask_and_apply("msg", {"version": "v9.9.9"}, None)
