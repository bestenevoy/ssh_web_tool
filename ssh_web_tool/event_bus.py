"""事件总线 - 所有操作发布事件，前端通过 WebSocket 订阅观察"""

import asyncio
import time
from collections import deque

from fastapi import WebSocket

from .ws_protocol import SERVER_EVENT


class EventBus:
    """事件总线 - 所有操作发布事件，前端通过 WebSocket 订阅观察"""

    def __init__(self):
        self._subscribers: set[WebSocket] = set()
        self._history: deque[dict] = deque(maxlen=500)
        self._max_history = 500

    async def subscribe(self, ws: WebSocket):
        """订阅事件"""
        self._subscribers.add(ws)
        # 发送历史事件（deque 不支持切片，取最后 100 条）
        for event in list(self._history)[-100:]:
            try:
                await ws.send_json(event)
            except Exception:
                pass

    def unsubscribe(self, ws: WebSocket):
        """取消订阅"""
        self._subscribers.discard(ws)

    async def publish(self, event_type: str, source: str, detail: str, **kwargs):
        """发布事件并广播给所有订阅者

        Args:
            event_type: 事件类型 (session_create / session_close / command_run / sftp_list / sftp_download / sftp_upload / host_add / host_update / host_delete / terminal_connect)
            source: 事件来源 (web / cli / sdk / internal)
            detail: 事件描述
            **kwargs: 附加数据
        """
        event = {
            "type": SERVER_EVENT,
            "event_type": event_type,
            "source": source,
            "detail": detail,
            "timestamp": time.time(),
            "time_str": time.strftime("%H:%M:%S"),
            **kwargs,
        }
        self._history.append(event)  # deque(maxlen=500) 自动淘汰旧事件，无需手动截断
        # 并行广播给所有订阅者（原串行 await 会因慢客户端阻塞其他客户端）
        if not self._subscribers:
            return
        results = await asyncio.gather(*[ws.send_json(event) for ws in self._subscribers], return_exceptions=True)
        # 清理失败的订阅者
        dead = set()
        for ws, result in zip(self._subscribers, results, strict=False):
            if isinstance(result, Exception):
                dead.add(ws)
        for ws in dead:
            self._subscribers.discard(ws)


event_bus = EventBus()
