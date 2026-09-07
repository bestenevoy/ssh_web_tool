# -*- coding: utf-8 -*-
"""
外部 SSH 会话中心（跨进程观测）

接收 ssh-monkeypatch（测试进程）通过 WebSocket 推送的 SSH 事件，
按 session_id 区分各个 SSH 会话：
- 维护会话元信息（主机/端口/用户/连接时间/最近活动/状态）
- 环形缓冲历史事件（供新订阅者回放）
- 实时转发给订阅该会话的浏览器 WebSocket（前端 Web 终端展示）

与 sessions.py（本进程内交互式终端会话）完全独立。
"""
import time
from collections import deque
from typing import Deque, Dict, Optional, Set

# 每会话历史事件上限
MAX_HISTORY_PER_SESSION = 2000
# 会话保留时长（秒）：close 后仍可回放一段时间
SESSION_RETENTION = 3600


class ExternalSessionHub:
    """外部会话中心（单例）"""

    def __init__(self):
        # session_id -> {"meta": dict, "history": deque, "subscribers": set[WebSocket], "last_active": float, "closed": bool}
        self._sessions: Dict[str, dict] = {}

    # ---------- 接收测试侧事件 ----------

    async def handle_event(self, ev: dict) -> None:
        """处理 ssh-monkeypatch 推送的一条事件"""
        if not isinstance(ev, dict):
            return
        sid = ev.get("session_id")
        etype = ev.get("type")
        if not sid or not etype:
            return
        sess = self._sessions.get(sid)
        if sess is None:
            if etype != "connect":
                return  # 未登记会话的孤儿事件忽略
            sess = {
                "meta": {
                    "session_id": sid,
                    "host": ev.get("host", ""),
                    "port": ev.get("port", 22),
                    "username": ev.get("username", ""),
                    "kind": ev.get("kind", ""),
                    "connected_at": ev.get("ts", time.time()),
                    "last_active": ev.get("ts", time.time()),
                    "closed": False,
                    "command_count": 0,
                    "output_bytes": 0,
                },
                "history": deque(maxlen=MAX_HISTORY_PER_SESSION),
                "subscribers": set(),
                "closed": False,
            }
            self._sessions[sid] = sess

        meta = sess["meta"]
        now = time.time()
        # 更新元信息
        if etype == "connect":
            meta["host"] = ev.get("host", meta.get("host", ""))
            meta["port"] = ev.get("port", meta.get("port", 22))
            meta["username"] = ev.get("username", meta.get("username", ""))
            meta["kind"] = ev.get("kind", meta.get("kind", ""))
            meta["connected_at"] = ev.get("ts", meta.get("connected_at", now))
            meta["closed"] = False
        elif etype == "close":
            meta["closed"] = True
        elif etype == "command":
            meta["command_count"] = meta.get("command_count", 0) + 1
        elif etype == "output":
            meta["output_bytes"] = meta.get("output_bytes", 0) + len(ev.get("data", ""))
        meta["last_active"] = now
        sess["last_active"] = now

        # 历史缓冲
        sess["history"].append(ev)

        # 实时转发给订阅者
        dead = set()
        for ws in sess["subscribers"]:
            try:
                await ws.send_json(ev)
            except Exception:
                dead.add(ws)
        for ws in dead:
            sess["subscribers"].discard(ws)

        # 清理过期会话
        self._gc()

    # ---------- 浏览器订阅 ----------

    async def subscribe(self, session_id: str, ws) -> bool:
        """浏览器订阅某会话：先回放历史，再实时转发。返回会话是否存在"""
        sess = self._sessions.get(session_id)
        if sess is None:
            return False
        sess["subscribers"].add(ws)
        # 历史回放
        for ev in sess["history"]:
            try:
                await ws.send_json(ev)
            except Exception:
                break
        return True

    def unsubscribe(self, session_id: str, ws) -> None:
        sess = self._sessions.get(session_id)
        if sess:
            sess["subscribers"].discard(ws)

    # ---------- 查询 ----------

    def list_sessions(self) -> list:
        """列出所有外部会话（按连接时间倒序）"""
        now = time.time()
        result = []
        for sid, sess in self._sessions.items():
            meta = dict(sess["meta"])
            meta["session_id"] = sid
            meta["age"] = now - meta.get("connected_at", now)
            meta["subscribers"] = len(sess["subscribers"])
            result.append(meta)
        result.sort(key=lambda x: x.get("connected_at", 0), reverse=True)
        return result

    def get_session(self, session_id: str) -> Optional[dict]:
        sess = self._sessions.get(session_id)
        if sess is None:
            return None
        meta = dict(sess["meta"])
        meta["session_id"] = session_id
        meta["subscribers"] = len(sess["subscribers"])
        return meta

    def get_history(self, session_id: str, limit: int = 500) -> list:
        sess = self._sessions.get(session_id)
        if sess is None:
            return []
        return list(sess["history"])[-limit:]

    # ---------- 内部 ----------

    def _gc(self):
        """清理超过保留期的已关闭会话"""
        now = time.time()
        stale = [
            sid for sid, sess in self._sessions.items()
            if sess["meta"].get("closed") and (now - sess["last_active"]) > SESSION_RETENTION
        ]
        for sid in stale:
            del self._sessions[sid]


external_hub = ExternalSessionHub()
