# -*- coding: utf-8 -*-
"""模拟前端完整连接流程：POST from-host → ws(resize) → 实时输出 + logs 历史，看 banner 显示顺序"""
import asyncio
import json
import time
import urllib.request

import websockets

BASE = "http://127.0.0.1:8765"
HOST_ID = "24ae9ee4"


def http(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=25) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


async def main():
    st, res = http("POST", "/api/sessions/from-host", {"host_id": HOST_ID})
    sid = res["session_id"]
    print("[1] from-host:", st, sid)

    # ws 连接（模拟前端 onopen：先 fit/resize）
    ws_out = []
    async with websockets.connect(f"ws://127.0.0.1:8765/ws/ssh/{sid}", max_size=2**24, open_timeout=10) as ws:
        await ws.send(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
        await asyncio.sleep(0.5)
        # 收实时输出（模拟前端等待）
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.8))
                if m.get("type") == "output":
                    ws_out.append(m["data"])
        except asyncio.TimeoutError:
            pass
        # 前端随后调 getHistoryLogs
        st2, logs = http("GET", f"/api/sessions/{sid}/logs?offset=999999&limit=200000")
        hist = logs.get("content", "")
        await ws.close()

    live = "".join(ws_out)
    print("[2] 实时输出前 300 字符:")
    print("   ", repr(live[:300]))
    print("[3] 历史 logs 前 300 字符:")
    print("   ", repr(hist[:300]))
    print("[4] 实时含 banner:", "PASS" if ("Ubuntu" in live or "Welcome" in live) else "FAIL")
    print("[5] 历史含 banner:", "PASS" if ("Ubuntu" in hist or "Welcome" in hist) else "FAIL")
    print("[6] 历史含清屏序列:", "FAIL" if "\x1b[2J" in hist or "\x1b[3J" in hist or "\x0c" in hist else "NONE(OK)")


asyncio.run(main())
