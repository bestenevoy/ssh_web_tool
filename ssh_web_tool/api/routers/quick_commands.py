"""快速指令域路由：快捷命令 CRUD / 排序"""

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.models import QuickCommandRequest, ReorderQuickCommandsRequest
from ssh_web_tool.deps import get_storage

router = APIRouter(prefix="/api", tags=["quick-commands"])


@router.get("/quick-commands")
async def api_list_quick_commands():
    """获取所有快速指令"""
    return {"commands": get_storage().list_quick_commands()}


@router.post("/quick-commands")
async def api_add_quick_command(req: QuickCommandRequest):
    """新增快速指令"""
    try:
        qc = get_storage().add_quick_command(req.name, req.command, req.description, req.type, req.pre_ops, req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return qc


@router.put("/quick-commands/reorder")
async def api_reorder_quick_commands(req: ReorderQuickCommandsRequest):
    """按拖拽后的顺序保存快捷指令"""
    get_storage().reorder_quick_commands(req.ids)
    return {"status": "ok"}


@router.put("/quick-commands/{qc_id}")
async def api_update_quick_command(qc_id: str, req: QuickCommandRequest):
    """更新快速指令"""
    try:
        qc = get_storage().update_quick_command(
            qc_id, req.name, req.command, req.description, req.type, req.pre_ops, req.key
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not qc:
        raise HTTPException(status_code=404, detail="快速指令不存在")
    return qc


@router.delete("/quick-commands/{qc_id}")
async def api_delete_quick_command(qc_id: str):
    """删除快速指令"""
    ok = get_storage().delete_quick_command(qc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="快速指令不存在")
    return {"status": "deleted"}
