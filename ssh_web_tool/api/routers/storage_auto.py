"""存储阵列管理页面自动登录路由（Playwright）"""

from fastapi import APIRouter, HTTPException

from ssh_web_tool.deps import get_event_bus, get_storage
from ssh_web_tool.playwright_mgmt import auto_login_storage, close_browser, list_active_browsers

router = APIRouter(prefix="/api", tags=["storage-auto-login"])


@router.post("/hosts/{host_id}/auto-login")
async def api_auto_login_storage(host_id: str):
    """存储阵列管理页面自动登录（启动浏览器，自动填写账号密码并登录）"""
    storage = get_storage()
    host = storage.get_host(host_id)
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")
    if host.get("device_type") != "storage":
        raise HTTPException(status_code=400, detail="该主机不是存储阵列类型")

    # 异步执行自动登录，避免阻塞 API
    result = await auto_login_storage(host)

    # 发布事件
    await get_event_bus().publish(
        "storage_auto_login", "WEB", f"主机 {host.get('name', host['host'])} 管理页面自动登录: {result['status']}"
    )

    return result


@router.post("/hosts/{host_id}/close-browser")
async def api_close_browser(host_id: str):
    """关闭存储阵列管理页面的浏览器实例"""
    return await close_browser(host_id)


@router.get("/storage/browsers")
async def api_list_browsers():
    """列出所有运行中的存储阵列管理浏览器实例"""
    return {"browsers": list_active_browsers()}
