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
    assert "if %N% gtr 60 goto fail" in content  # 最长约 60s
    assert "ping -n 2 127.0.0.1 >nul" in content  # 延时用 ping：timeout 在 stdin 重定向/无控制台下立即失败
    assert "goto fail" in content
    assert "_wstool_update.log" in content  # 失败日志便于排查
    assert "old exe locked" in content  # 等待时写日志，可区分"锁没释放"与"脚本没跑"


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


# ---------- 更新脚本启动 flag（Windows 专用） ----------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专用：验证 CreateProcess flag 互斥")
def test_update_script_launch_flags(tmp_path):
    """CREATE_NEW_CONSOLE | DETACHED_PROCESS 互斥（WinError 87）——
    v0.1.31 引入的 bug 导致更新脚本从未启动、永远提示"启动更新脚本失败"。
    回归：新组合（仅 DETACHED_PROCESS）必须能启动 bat。"""
    import subprocess

    bat = tmp_path / "_t.bat"
    bat.write_text("@echo off\r\necho ok > %~dp0out.txt\r\n", encoding="ascii")

    CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
    assert CREATE_NEW_CONSOLE and DETACHED

    # 旧组合（互斥 flag）必须抛异常——这正是用户遇到的"启动更新脚本失败"
    with pytest.raises(OSError):
        subprocess.Popen(
            [str(bat)], cwd=str(tmp_path),
            creationflags=CREATE_NEW_CONSOLE | DETACHED, close_fds=True,
        )

    # 新组合（仅 DETACHED_PROCESS）必须成功启动 bat
    p = subprocess.Popen([str(bat)], cwd=str(tmp_path), creationflags=DETACHED, close_fds=True)
    p.wait(timeout=15)
    assert (tmp_path / "out.txt").exists()


def test_update_script_launch_strategy_source_guard(tmp_path):
    """启动策略防回归（源码检查）：
    - 禁止 CREATE_NEW_CONSOLE（与 DETACHED_PROCESS 互斥，v0.1.31 曾因此永远失败）
    - 必须存在多策略启动器 _launch_update_script 且被 _ask_and_apply 调用
    - 必须包含 cmd /c 显式解释 + CREATE_NO_WINDOW（打包版无控制台的兜底主力）"""
    src = Path(updater.__file__).read_text(encoding="utf-8")
    bad = [l for l in src.splitlines() if "CREATE_NEW_CONSOLE" in l and not l.strip().startswith("#")]
    assert not bad
    assert "def _launch_update_script" in src
    assert "_launch_update_script(script, exe_dir)" in src
    assert "CREATE_NO_WINDOW" in src
    assert '"shell": True' in src  # 最后兜底方式存在


# ---------- 启动脚本环境变量清洗（PyInstaller onefile 安全校验规避） ----------

def test_clean_launcher_env_strips_pyi_and_mei_vars(monkeypatch):
    """_clean_launcher_env 必须剥离 _PYI_* / _MEI* 变量，保留正常变量

    背景：PyInstaller>=6.22.1 onefile bootloader 校验父进程可执行路径，
    继承的 _PYI_ARCHIVE_FILE 会让重启后的新版 EXE 误判为 onefile 子进程，
    父进程是 cmd（或已退出）→ "Security validation failure: fail to obtain
    executable path for parent process"，升级后无法自启。
    """
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\Users\t\AppData\Local\Temp\_MEI12345\app.exe")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\app")
    monkeypatch.setenv("_MEIPASS2", r"C:\Users\t\AppData\Local\Temp\_MEI12345")
    monkeypatch.setenv("_MEI12345", "junk")
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\system32\\cmd.exe")
    monkeypatch.setenv("PATH", r"C:\Windows")

    env = updater._clean_launcher_env()

    stripped = [k for k in env if k.upper().startswith("_PYI_") or k.upper().startswith("_MEI")]
    assert stripped == []
    assert env["COMSPEC"] == "C:\\Windows\\system32\\cmd.exe"
    assert env["PATH"] == r"C:\Windows"


def test_launch_update_script_passes_clean_env(tmp_path, monkeypatch):
    """三种启动方式统一传清洗后的 env（截获 Popen 断言）"""
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "should-not-leak")
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\system32\\cmd.exe")

    captured = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        captured["kw"] = kw
        return mock.Mock()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    updater._launch_update_script(tmp_path / "_wstool_update.bat", tmp_path)

    env = captured["kw"]["env"]
    assert "_PYI_ARCHIVE_FILE" not in env
    assert env["COMSPEC"] == "C:\\Windows\\system32\\cmd.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专用")
def test_launch_update_script_falls_back_on_failure(tmp_path, monkeypatch):
    """前两种启动方式失败时自动兜底第三种，全部失败才抛异常"""
    calls = {"n": 0}

    def flaky_popen(args, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(f"blocked (attempt {calls['n']})")
        return mock.Mock()

    monkeypatch.setattr(updater.subprocess, "Popen", flaky_popen)
    updater._launch_update_script(tmp_path / "s.bat", tmp_path)
    assert calls["n"] == 3  # 三种方式都尝试了


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专用")
def test_launch_update_script_all_fail_raises(tmp_path, monkeypatch):
    def always_fail(args, **kw):
        raise OSError("blocked")

    monkeypatch.setattr(updater.subprocess, "Popen", always_fail)
    with pytest.raises(OSError):
        updater._launch_update_script(tmp_path / "s.bat", tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专用")
def test_launch_update_script_real(tmp_path):
    """真实启动：_launch_update_script 必须能把 bat 跑起来（cmd /c + CREATE_NO_WINDOW + DEVNULL 主路径）"""
    import time as _t

    bat = tmp_path / "_t.bat"
    bat.write_text("@echo off\r\nping -n 2 127.0.0.1 >nul\r\necho ok > %~dp0out.txt\r\n", encoding="ascii")
    updater._launch_update_script(bat, tmp_path)  # 内部 Popen 成功即返回
    for _ in range(150):  # 轮询 15s 等 bat 产物
        if (tmp_path / "out.txt").exists():
            break
        _t.sleep(0.1)
    assert (tmp_path / "out.txt").exists()


def test_ask_and_apply_launch_failure_keeps_new_exe(monkeypatch, tmp_path):
    """启动脚本失败：报错带真实异常 + 保留已下载新包（供重试/手动替换），不删"""

    def fake_download(url, dest, expected_size=0, timeout=60.0):
        dest.write_bytes(b"new exe bytes")
        return True

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe = tmp_path / "SSHWebTool.exe"
    exe.write_bytes(b"old")
    monkeypatch.setattr(updater, "_current_exe", lambda: exe)
    monkeypatch.setattr(updater, "_confirm", lambda *a, **k: True)
    monkeypatch.setattr(updater, "download_exe", fake_download)
    monkeypatch.setattr(updater, "_launch_update_script",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blocked by av")))
    info = []
    monkeypatch.setattr(updater, "_info", lambda msg, *a, **k: info.append(msg))
    updater._ask_and_apply("msg", {
        "version": "v9.9.9", "download_url": "https://x/exe", "size": 13, "url": "",
    }, None)
    assert any("启动更新脚本失败" in m and "blocked by av" in m for m in info)
    assert any("SSHWebTool_new.exe" in m for m in info)  # 提示手动替换路径
    assert (tmp_path / "SSHWebTool_new.exe").exists()  # 新包未被删除
