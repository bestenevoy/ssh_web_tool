"""外部 SSH 会话域路由（ssh-monkeypatch 跨进程观测：列表/详情/事件流订阅）"""

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket

from ssh_web_tool.api.ws_common import serve_messages
from ssh_web_tool.deps import get_external_hub
from ssh_web_tool.ws_protocol import SERVER_ERROR

# 注意：本路由不加 prefix——WS 端点契约是根路径（/ws/stream、/ws/external/{id}，
# ssh-monkeypatch 独立包与 docs/external-observation.md 均按此连接），此前误加
# prefix="/api" 会导致测试进程按文档 URL 推送时被直接关闭（观测功能整体失效）；
# HTTP 接口在路径中显式带上 /api 前缀
router = APIRouter(tags=["external"])


@router.get("/api/external-sessions")
async def api_external_sessions():
    """外部会话列表（测试进程通过 ssh-monkeypatch 推送的 SSH 会话）"""
    return {"sessions": get_external_hub().list_sessions()}


@router.get("/api/external-sessions/{session_id}")
async def api_external_session_detail(session_id: str, limit: int = 500):
    """外部会话详情 + 历史事件（回放）"""
    external_hub = get_external_hub()
    meta = external_hub.get_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="外部会话不存在")
    return {"session": meta, "events": external_hub.get_history(session_id, limit)}


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """ssh-monkeypatch（测试进程）推送 SSH 事件的入口。

    客户端：websocket-client 推送 JSON 事件流（connect/command/output/close），
    服务端按 session_id 区分会话，转发给订阅的浏览器并缓存历史。
    """
    await websocket.accept()
    print("[External] 测试进程事件流已接入")

    async def on_message(ev: dict[str, Any]) -> None:
        await get_external_hub().handle_event(ev)

    def on_error(e: Exception) -> None:
        print(f"[External] 事件处理异常: {e}")

    # 单条事件处理失败不中断事件流（测试侧会持续推送大量事件）
    await serve_messages(websocket, on_message, on_error=on_error)
    print("[External] 测试进程事件流已断开")


@router.websocket("/ws/external/{session_id}")
async def websocket_external(websocket: WebSocket, session_id: str):
    """浏览器订阅外部会话：先回放历史，再实时接收测试侧推送的 SSH 事件"""
    await websocket.accept()
    external_hub = get_external_hub()
    ok = await external_hub.subscribe(session_id, websocket)
    if not ok:
        await websocket.send_json({"type": SERVER_ERROR, "data": "外部会话不存在"})
        await websocket.close()
        return
    await serve_messages(websocket, on_disconnect=lambda: external_hub.unsubscribe(session_id, websocket))
