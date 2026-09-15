"""主机域路由：主机 CRUD / 排序 / 复制 / 主机类型管理"""

import time

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.helpers import _sanitize_host
from ssh_web_tool.api.models import HostRequest, HostTypeRequest, ReorderHostsRequest
from ssh_web_tool.deps import get_event_bus, get_session_manager, get_storage

router = APIRouter(prefix="/api", tags=["hosts"])


@router.get("/hosts")
async def api_list_hosts():
    """获取所有保存的主机（含实时连接状态与连接信息；密码等敏感字段已脱敏）"""
    storage = get_storage()
    session_manager = get_session_manager()
    hosts = storage.list_hosts()
    now = time.time()
    result = []
    # 为每个主机附加连接状态
    for h in hosts:
        h["terminal_count"] = session_manager.get_host_terminal_count(h["id"])
        h["is_connected"] = h["terminal_count"] > 0
        # 连接信息：最早创建且仍存活的会话时间作为"连接时间"
        sessions = session_manager.get_sessions_by_host(h["id"])
        alive = [s for s in sessions if s.is_alive()]
        if alive:
            created = min(s.created_at for s in alive)
            h["connected_since"] = created
            h["connected_duration"] = max(0, int(now - created))
        else:
            h["connected_since"] = None
            h["connected_duration"] = None
        result.append(_sanitize_host(h))
    return {"hosts": result, "groups": storage.list_groups(), "host_types": storage.list_host_types()}


@router.post("/hosts/reorder")
async def api_reorder_hosts(req: ReorderHostsRequest):
    """按给定顺序重排主机列表"""
    get_storage().reorder_hosts(req.ids)
    return {"status": "reordered"}


@router.post("/hosts")
async def api_add_host(req: HostRequest):
    """新增主机"""
    host = get_storage().add_host(req.dict())
    return _sanitize_host(host)


@router.put("/hosts/{host_id}")
async def api_update_host(host_id: str, req: HostRequest):
    """更新主机（密码/私钥等字段留空表示不修改，保持原值）"""
    data = req.dict()
    # 敏感字段为空字符串时不更新（前端编辑弹窗不回显密码，留空=不修改）
    for key in ("password", "mgmt_password", "private_key", "passphrase"):
        if key in data and not (data.get(key) or "").strip():
            data.pop(key)
    host = get_storage().update_host(host_id, data)
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")
    return _sanitize_host(host)


@router.delete("/hosts/{host_id}")
async def api_delete_host(host_id: str):
    """删除主机"""
    ok = get_storage().delete_host(host_id)
    if not ok:
        raise HTTPException(status_code=404, detail="主机不存在")
    return {"status": "deleted"}


@router.post("/hosts/{host_id}/duplicate")
async def api_duplicate_host(host_id: str):
    """复制主机（生成新 ID，名称加"副本"后缀；密码在后端随原主机复制，不下发明文）"""
    new_host = get_storage().duplicate_host(host_id)
    if not new_host:
        raise HTTPException(status_code=404, detail="主机不存在")
    await get_event_bus().publish("host_add", "WEB", f"复制主机: {new_host.get('name', new_host['host'])}")
    return _sanitize_host(new_host)


# ============ 主机类型 API ============


@router.get("/host-types")
async def api_list_host_types():
    """获取所有主机类型"""
    return {"types": get_storage().list_host_types()}


@router.post("/host-types")
async def api_add_host_type(req: HostTypeRequest):
    """新增或更新主机类型"""
    ht = get_storage().add_host_type(req.key, req.label, req.color)
    if not ht:
        raise HTTPException(status_code=400, detail="类型 key 不能为空")
    return ht


@router.delete("/host-types/{key}")
async def api_delete_host_type(key: str):
    """删除主机类型"""
    ok = get_storage().delete_host_type(key)
    if not ok:
        raise HTTPException(status_code=404, detail="主机类型不存在")
    return {"status": "deleted"}
