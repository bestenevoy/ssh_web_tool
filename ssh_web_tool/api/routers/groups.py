"""分组域路由：分组 CRUD / 排序 / 复制 / 重命名"""

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.models import GroupRequest, RenameGroupRequest, ReorderGroupsRequest
from ssh_web_tool.deps import get_event_bus, get_storage

router = APIRouter(prefix="/api", tags=["groups"])


@router.get("/groups")
async def api_list_groups():
    """获取所有分组"""
    return {"groups": get_storage().list_groups()}


@router.post("/groups")
async def api_add_group(req: GroupRequest):
    """新增分组"""
    ok = get_storage().add_group(req.name)
    if not ok:
        raise HTTPException(status_code=400, detail="分组已存在或名称无效")
    return {"status": "added", "name": req.name}


@router.delete("/groups/{name}")
async def api_delete_group(name: str):
    """删除分组"""
    ok = get_storage().delete_group(name)
    if not ok:
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"status": "deleted"}


@router.put("/groups/{name}")
async def api_rename_group(name: str, req: RenameGroupRequest):
    """重命名分组"""
    ok = get_storage().rename_group(name, req.new_name)
    if not ok:
        raise HTTPException(status_code=400, detail="重命名失败（分组不存在或新名称已存在）")
    return {"status": "renamed", "old_name": name, "new_name": req.new_name}


@router.post("/groups/{name}/duplicate")
async def api_duplicate_group(name: str):
    """复制分组（生成"xxx 副本"分组，组内主机一并复制）"""
    result = get_storage().duplicate_group(name)
    if not result:
        raise HTTPException(status_code=404, detail="分组不存在")
    await get_event_bus().publish("group_add", "WEB", f"复制分组: {name} -> {result['name']}")
    return {"status": "duplicated", **result}


@router.post("/groups/reorder")
async def api_reorder_groups(req: ReorderGroupsRequest):
    """按给定顺序重排分组列表"""
    get_storage().reorder_groups(req.names)
    return {"status": "reordered"}
