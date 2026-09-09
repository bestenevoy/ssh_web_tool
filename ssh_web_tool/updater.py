# -*- coding: utf-8 -*-
"""程序自动更新：检查 GitHub Release → 提示（断开所有 SSH 连接）→ 下载 → 替换重启

设计：
- 版本源：GitHub Releases API（公开仓库，无需认证）
- 下载：流式写入 exe 同目录 SSHWebTool_new.exe（保证替换脚本同盘 rename 原子）
- 替换：更新脚本（bat）在旧进程退出后 del 旧 exe → move 新 exe → 启动 → 自删
- 安全：更新前弹窗确认，明确告知"将断开所有 SSH 连接"（进程重启，会话内存态全丢）
"""
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from ssh_web_tool.version import APP_VERSION

# GitHub 仓库与资产名
REPO = "bestenevoy/ssh_web_tool"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "SSHWebTool.exe"

# 当前可执行文件
def _current_exe() -> Optional[Path]:
    """当前运行的 EXE 路径（PyInstaller onefile：sys.executable）；源码模式返回 None"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def parse_version(v: str) -> Tuple[int, ...]:
    """解析版本号 v0.1.28 / 0.1.28 → (0, 1, 28)；非法返回 (0,)"""
    m = re.search(r"(\d+(?:\.\d+){1,3})", v or "")
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新（语义化比较，0.1.9 < 0.1.10）"""
    return parse_version(latest) > parse_version(current)


def get_latest_release(timeout: float = 15.0) -> Optional[dict]:
    """获取最新 Release 信息，返回 {version, download_url, size, url}；失败返回 None"""
    try:
        req = urllib.request.Request(
            RELEASE_API,
            headers={"User-Agent": "ssh-web-tool-updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        import json
        rel = json.loads(data)
        version = rel.get("tag_name", "")
        asset_url = None
        size = 0
        for asset in rel.get("assets", []):
            if asset.get("name") == ASSET_NAME:
                asset_url = asset.get("browser_download_url")
                size = asset.get("size", 0)
                break
        if not asset_url:
            return None
        return {"version": version, "download_url": asset_url, "size": size,
                "url": rel.get("html_url", "")}
    except Exception:
        return None


def download_exe(url: str, dest: Path, timeout: float = 60.0) -> bool:
    """流式下载到 dest（覆盖已存在）；失败清理残留返回 False"""
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ssh-web-tool-updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
        # 校验非空后 rename（临时 → 目标）
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, dest)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


def build_update_script(exe_dir: Path, old_name: str, new_name: str) -> Path:
    """生成替换重启脚本（bat），返回脚本路径

    脚本流程：等待 2 秒（旧进程已退出）→ 删旧 → 移新 → 启动 → 自删
    """
    script = exe_dir / "_wstool_update.bat"
    # chcp 65001 + batch 内中文避免乱码（脚本本身用 ASCII 更稳）
    content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'del /f /q "%~dp0{old_name}" >nul 2>&1\r\n'
        f'move /y "%~dp0{new_name}" "%~dp0{old_name}" >nul 2>&1\r\n'
        f'start "" "%~dp0{old_name}"\r\n'
        "del /f /q \"%~f0\" >nul 2>&1\r\n"
    )
    script.write_text(content, encoding="ascii")
    return script


def check_for_update_quiet(show_dialog=None) -> None:
    """启动时静默检查：有新版才提示（不自动更新）

    show_dialog: 显示提示的函数（默认 Windows MessageBox）
    """
    rel = get_latest_release()
    if not rel:
        return  # 网络失败/无资产：静默
    if not is_newer(rel["version"], APP_VERSION):
        return
    msg = (
        f"发现新版本 {rel['version']}（当前 {APP_VERSION}）\n\n"
        f"是否现在更新？\n"
        f"更新将停止服务并断开所有 SSH 连接，请先保存工作。\n\n"
        f"{rel.get('url', '')}"
    )
    _ask_and_apply(msg, rel, show_dialog)


def check_and_update(show_dialog=None) -> None:
    """托盘"检查更新"：无新版提示已最新；有新版走确认+下载+重启"""
    rel = get_latest_release()
    if not rel:
        _info("检查更新失败：无法访问 GitHub（请检查网络）。", show_dialog)
        return
    if not is_newer(rel["version"], APP_VERSION):
        _info(f"当前已是最新版本 v{APP_VERSION}。", show_dialog)
        return
    msg = (
        f"发现新版本 {rel['version']}（当前 {APP_VERSION}）\n\n"
        f"⚠️ 更新将停止服务，断开所有 SSH 连接（进程重启后需重新连接主机）。\n"
        f"确认开始下载并更新吗？"
    )
    _ask_and_apply(msg, rel, show_dialog)


def _ask_and_apply(msg: str, rel: dict, show_dialog=None) -> None:
    if not _confirm(msg, show_dialog):
        return
    exe = _current_exe()
    if exe is None:
        _info("当前为源码模式运行，无法自我更新。请用 EXE 版本。", show_dialog)
        return
    exe_dir = exe.parent
    new_path = exe_dir / ("SSHWebTool_new.exe")
    _info(f"正在下载新版本 {rel['version']}（约 {rel.get('size', 0) // 1024 // 1024} MB）...", show_dialog)
    if not download_exe(rel["download_url"], new_path):
        _info("下载失败，已取消更新（请检查网络后重试）。", show_dialog)
        return
    script = build_update_script(exe_dir, exe.name, new_path.name)
    # 启动更新脚本（独立进程，不等）后立即退出当前程序
    try:
        subprocess.Popen(
            [str(script)],
            cwd=str(exe_dir),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
    except Exception:
        new_path.unlink(missing_ok=True)
        _info("启动更新脚本失败，已取消更新。", show_dialog)
        return
    os._exit(0)  # noqa: PLR1722


# ---------- Windows 弹窗（默认实现） ----------
def _confirm(msg: str, show_dialog=None) -> bool:
    if show_dialog is not None:
        return bool(show_dialog(msg))
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(
            None, msg, "SSH Web Tool - 更新", 0x4 | 0x20  # MB_YESNO | MB_ICONQUESTION
        ) == 6  # IDYES
    except Exception:
        return False


def _info(msg: str, show_dialog=None) -> None:
    if show_dialog is not None:
        show_dialog(msg)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, msg, "SSH Web Tool", 0x40  # MB_ICONINFORMATION
        )
    except Exception:
        print(msg)
