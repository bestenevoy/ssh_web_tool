"""配置域路由：全局配置读取 / 回退 shell 设置 / 配置重载"""

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.models import FallbackShellRequest
from ssh_web_tool.config import (
    LOCAL_SHELL_CHOICES,
    find_config_file,
    get_app_dir,
    get_fallback_local_shell,
    load_config,
    save_config,
)
from ssh_web_tool.deps import get_storage

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def api_get_config():
    """返回前端展示所需的全局配置"""
    cfg = load_config()
    return {
        "fallback_local_shell": get_fallback_local_shell(cfg),
        "local_shell_choices": list(LOCAL_SHELL_CHOICES),
        "config_file": str(find_config_file() or (get_app_dir() / "config.json")),
    }


@router.post("/config/fallback-shell")
async def api_set_fallback_shell(req: FallbackShellRequest):
    """保存"断开后切换本机终端"的 shell 选择（cmd / powershell / pwsh）"""
    shell = req.shell.strip().lower()
    if shell not in LOCAL_SHELL_CHOICES:
        raise HTTPException(status_code=400, detail=f"不支持的 shell: {shell}，可选 {list(LOCAL_SHELL_CHOICES)}")
    cfg = load_config()
    cfg["fallback_local_shell"] = shell
    if not save_config(cfg):
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"status": "ok", "fallback_local_shell": shell}


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
