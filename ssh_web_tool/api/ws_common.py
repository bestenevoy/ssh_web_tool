"""WebSocket 接收循环公共样板

事件订阅 / 外部会话等"客户端 → 服务端"的轻量 WS 入口共用同一套结构：
accept → （订阅/回放）→ 循环收 JSON 消息分发 → 断开与异常兜底 → finally 清理。
本模块收敛该样板，消除路由间的重复实现。

交互式终端 /ws/ssh 不走这里：它需要把非 JSON 文本直写 stdin、处理首个 resize
握手并挂输出转发任务，主循环结构由 ws.py 自行维护。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ssh_web_tool.ws_protocol import CLIENT_PING, SERVER_PONG

# 单条客户端 JSON 消息处理器
MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def serve_messages(
    websocket: WebSocket,
    on_message: MessageHandler | None = None,
    *,
    on_error: Callable[[Exception], None] | None = None,
    on_disconnect: Callable[[], None] | None = None,
) -> None:
    """循环接收客户端 JSON 消息并分发，直至对端断开。

    - ping 消息统一在此回 pong（保活），不进入 on_message
    - 非 JSON 文本 / 非 dict 的 JSON：静默忽略
    - on_message 抛异常：不中断循环，交给 on_error 记录（默认静默），
      保证单个坏事件不影响后续事件流
    - WebSocketDisconnect / 其他异常：静默结束，最后执行 on_disconnect 清理
    """
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == CLIENT_PING:
                await websocket.send_json({"type": SERVER_PONG})
            elif on_message is not None:
                try:
                    await on_message(msg)
                except Exception as e:
                    if on_error is not None:
                        on_error(e)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if on_disconnect is not None:
            on_disconnect()
