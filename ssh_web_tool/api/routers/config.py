"""配置域路由：全局配置读取 / 回退 shell 设置 / UI 设置 / 配置重载"""

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.models import FallbackShellRequest, UiSettingsRequest
from ssh_web_tool.config import (
    LOCAL_SHELL_CHOICES,
    _validate_ui_setting,
    find_config_file,
    get_app_dir,
    get_fallback_local_shell,
    get_ui_settings,
    load_config,
    save_config,
)
from ssh_web_tool.deps import get_storage

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def api_get_config():
    """返回前端展示所需的全局配置（含 UI 设置）"""
    cfg = load_config()
    return {
        "fallback_local_shell": get_fallback_local_shell(cfg),
        "local_shell_choices": list(LOCAL_SHELL_CHOICES),
        "ui_settings": get_ui_settings(cfg),
        "config_file": str(find_config_file() or (get_app_dir() / "config.json")),
    }


@router.post("/config/fallback-shell")
async def api_set_fallback_shell(req: FallbackShellRequest):
    """保存默认本机终端 shell（默认终端条目 + SSH 断开后自动进入共用）"""
    shell = req.shell.strip().lower()
    if shell not in LOCAL_SHELL_CHOICES:
        raise HTTPException(status_code=400, detail=f"不支持的 shell: {shell}，可选 {list(LOCAL_SHELL_CHOICES)}")
    cfg = load_config()
    cfg["fallback_local_shell"] = shell
    if not save_config(cfg):
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"status": "ok", "fallback_local_shell": shell}


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
