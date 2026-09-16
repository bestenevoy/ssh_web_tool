"""配置域路由：全局配置读取 / 回退 shell 设置 / UI 设置 / 配置重载"""

import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.models import ConnectTimeoutRequest, FallbackShellRequest, UiSettingsRequest
from ssh_web_tool.config import (
    _validate_ui_setting,
    detect_local_shells,
    find_config_file,
    get_app_dir,
    get_connect_timeout,
    get_fallback_local_shell,
    get_ui_settings,
    load_config,
    save_config,
    validate_local_shell_value,
)
from ssh_web_tool.deps import get_storage

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def api_get_config():
    """返回前端展示所需的全局配置（含 UI 设置与本机 shell 检测结果）"""
    cfg = load_config()
    return {
        "fallback_local_shell": get_fallback_local_shell(cfg),
        # 本机 shell 动态检测（rssh 同款）：短标识或可执行文件完整路径；
        # 检测很快（已知路径 + which），每次读取现扫即可，无需单独刷新接口
        "local_shell_choices": detect_local_shells(),
        "connect_timeout": get_connect_timeout(cfg),
        "ui_settings": get_ui_settings(cfg),
        "config_file": str(find_config_file() or (get_app_dir() / "config.json")),
    }


@router.post("/config/fallback-shell")
async def api_set_fallback_shell(req: FallbackShellRequest):
    """保存默认本机终端 shell（默认终端条目 + SSH 断开后自动进入共用）

    取值可为短标识（cmd/powershell/pwsh）或本机检测到的 shell 完整路径。
    """
    shell = validate_local_shell_value(req.shell)
    if shell is None:
        raise HTTPException(status_code=400, detail=f"不支持的 shell: {req.shell!r}（短标识或可执行文件完整路径）")
    cfg = load_config()
    cfg["fallback_local_shell"] = shell
    if not save_config(cfg):
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"status": "ok", "fallback_local_shell": shell}


@router.post("/config/connect-timeout")
async def api_set_connect_timeout(req: ConnectTimeoutRequest):
    """保存 SSH 连接超时（秒，1-300），持久化到 config.json 顶层"""
    cfg = load_config()
    cfg["connect_timeout"] = req.seconds
    if not save_config(cfg):
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"status": "ok", "connect_timeout": get_connect_timeout(cfg)}


@router.post("/config/ui-settings")
async def api_update_ui_settings(req: UiSettingsRequest):
    """部分更新前端 UI 设置（theme/字体/命令块等），持久化到 config.json 的 ui_settings 段"""
    changes = req.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="没有需要更新的设置项")
    # 保存前逐键校验：非法值直接 400（避免"保存值与返回值不一致"）
    for key, value in changes.items():
        if _validate_ui_setting(key, value) is None:
            raise HTTPException(status_code=400, detail=f"非法的设置项 {key}={value!r}")
    cfg = load_config()
    merged = get_ui_settings(cfg)
    merged.update(changes)
    cfg["ui_settings"] = merged
    if not save_config(cfg):
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"status": "ok", "ui_settings": get_ui_settings(cfg)}


@router.post("/config/open-dir")
async def api_open_config_dir():
    """在系统文件管理器中打开配置文件所在目录（设置弹窗「打开目录」按钮）"""
    cfg_path = find_config_file() or (get_app_dir() / "config.json")
    directory = cfg_path.parent
    # 目录不存在时先建（首启尚未写过配置的场景），避免资源管理器打开报错
    directory.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(directory))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(directory)])
        else:
            subprocess.Popen(["xdg-open", str(directory)])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开目录失败: {e}") from e
    return {"status": "ok", "dir": str(directory)}


@router.post("/config/reload")
async def api_reload_config():
    """扫描并重新加载配置文件（data.json / config.json）与 scripts 脚本目录，
    供 Web 界面/托盘"检查配置更新"使用（外部手动编辑配置或添加脚本后刷新）"""
    storage = get_storage()
    cfg = load_config()
    data_summary = storage.reload()
    scripts_dir = get_app_dir() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
    return {
        "status": "reloaded",
        "config": {"port": cfg.get("server", {}).get("port"), "open_browser": cfg.get("open_browser")},
        "data": data_summary,
        "scripts_dir": str(scripts_dir),
        "scripts": scripts,
    }
