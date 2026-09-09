"""
FastAPI 后端
- 静态页面服务（网页 UI）
- WebSocket：交互式终端
- HTTP REST API：
  - SSH 会话管理（创建、列表、删除、执行命令）
  - 主机配置持久化（增删改查）
  - 分组管理
  - 快速指令管理
  - 主机类型管理
  - SFTP 文件管理（列目录、读文件、写文件、删文件）
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ssh_web_tool.sessions import session_manager, SSHSession
from ssh_web_tool.storage import storage
from ssh_web_tool.playwright_mgmt import auto_login_storage, close_browser, list_active_browsers
from ssh_web_tool.config import load_config, get_app_dir
from ssh_web_tool import history_db
from ssh_web_tool.version import APP_VERSION
from ssh_web_tool.external_sessions import external_hub

app = FastAPI(title="SSH Web Tool", version=APP_VERSION)


@app.on_event("shutdown")
async def _shutdown_cleanup():
    """程序退出前关闭数据库单例连接。

    history_db 使用全局单例 aiosqlite 连接（非 daemon worker 线程），
    若不显式关闭，开发模式 Ctrl+C / uvicorn 异常退出时进程会挂住无法退出。
    （托盘"退出"走 os._exit 不经此路径，由 tray.exit_app 自行清理。）
    """
    try:
        await history_db.close_db()
    except Exception:
        pass

# PyInstaller 打包兼容：静态文件从临时目录读取，数据文件保存在 EXE 所在目录
def get_resource_path(relative_path: str) -> Path:
    """获取资源文件路径（兼容 PyInstaller 打包）"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包后，资源文件在临时目录
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).parent / relative_path

def get_data_path(relative_path: str) -> Path:
    """获取数据文件路径（统一存放在 ~/.ai4one/wstool，不随打包丢失）"""
    return get_app_dir() / relative_path

BASE_DIR = Path(__file__).parent
STATIC_DIR = get_resource_path("static")
DATA_DIR = get_data_path(".")

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============ 事件总线（前端观察 Server 交互过程） ============

class EventBus:
    """事件总线 - 所有操作发布事件，前端通过 WebSocket 订阅观察"""

    def __init__(self):
        self._subscribers: set[WebSocket] = set()
        self._history: list[dict] = []
        self._max_history = 500

    async def subscribe(self, ws: WebSocket):
        """订阅事件"""
        self._subscribers.add(ws)
        # 发送历史事件
        for event in self._history[-100:]:
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
        import time
        event = {
            "type": "event",
            "event_type": event_type,
            "source": source,
            "detail": detail,
            "timestamp": time.time(),
            "time_str": time.strftime('%H:%M:%S'),
            **kwargs
        }
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        # 并行广播给所有订阅者（原串行 await 会因慢客户端阻塞其他客户端）
        if not self._subscribers:
            return
        results = await asyncio.gather(
            *[ws.send_json(event) for ws in self._subscribers],
            return_exceptions=True
        )
        # 清理失败的订阅者
        dead = set()
        for ws, result in zip(self._subscribers, results):
            if isinstance(result, Exception):
                dead.add(ws)
        for ws in dead:
            self._subscribers.discard(ws)


event_bus = EventBus()


# ============ 请求模型 ============

class CreateSessionRequest(BaseModel):
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None
    passphrase: Optional[str] = None


class CreateSessionFromHostRequest(BaseModel):
    host_id: str
    terminal_name: Optional[str] = ""  # 可选：指定终端名称，不填则自动生成


class RunCommandRequest(BaseModel):
    command: str
    timeout: int = 30
    process: bool = False  # True=强制独立进程执行（不注入到Web终端），False=默认自动（有shell就注入+捕获）
    capture_exit_code: bool = True  # False=跳过 echo $? 获取退出码（节省1-2秒），退出码返回 None


class HostRequest(BaseModel):
    name: Optional[str] = ""
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = ""
    private_key: Optional[str] = ""
    passphrase: Optional[str] = ""
    type: Optional[str] = "other"
    group: Optional[str] = ""
    # 设备类型：linux（普通主机）/ storage（存储阵列）
    device_type: Optional[str] = "linux"
    # 存储阵列管理页面配置
    mgmt_port: Optional[int] = 8088
    mgmt_username: Optional[str] = ""
    mgmt_password: Optional[str] = ""
    # Playwright 自动登录选择器配置
    pw_username_selector: Optional[str] = ""
    pw_password_selector: Optional[str] = ""
    pw_login_btn_selector: Optional[str] = ""
    pw_old_password_selector: Optional[str] = ""
    pw_new_password_selector: Optional[str] = ""
    pw_confirm_password_selector: Optional[str] = ""
    pw_confirm_btn_selector: Optional[str] = ""
    pw_success_selector: Optional[str] = ""
    pw_headless: Optional[bool] = False


class GroupRequest(BaseModel):
    name: str


class QuickCommandRequest(BaseModel):
    name: str
    command: str
    description: str = ""
    # 指令类型：direct 直接执行 / param 带参数（输入后不执行，命令含 {args} 供编辑）
    type: str = "direct"
    # 预操作（执行命令前依次执行）：[{"type": "upload", "remote": "/path"}, {"type": "chmod", "mode": "+x", "path": "/path"}, {"type": "env", "key": "VAR", "value": "x"}]
    pre_ops: list = []


class HostTypeRequest(BaseModel):
    key: str
    label: str
    color: str = "#999999"


class SftpListRequest(BaseModel):
    path: str = "/"


class SftpWriteRequest(BaseModel):
    path: str
    content: str


class SftpDeleteRequest(BaseModel):
    path: str


# ============ 页面路由 ============

@app.get("/")
async def index():
    """主页：网页 UI（禁用缓存，确保每次打开/刷新都加载最新构建，避免旧页面缓存导致功能不一致）"""
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


# ============ 配置 API ============

@app.get("/api/config")
async def api_get_config():
    """返回前端展示所需的全局配置"""
    return {}


@app.post("/api/config/reload")
async def api_reload_config():
    """扫描并重新加载配置文件（data.json / config.json）与 scripts 脚本目录，
    供 Web 界面/托盘"检查配置更新"使用（外部手动编辑配置或添加脚本后刷新）"""
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


# ============ SSH 会话 API ============

@app.post("/api/sessions")
async def api_create_session(req: CreateSessionRequest):
    """创建 SSH 会话并连接，自动启动交互式 shell（会显示在 Web UI 中）"""
    session_id = session_manager.create_session(req.host, req.port, req.username)
    session = session_manager.get_session(session_id)
    try:
        await session.connect(
            password=req.password,
            private_key=req.private_key,
            passphrase=req.passphrase,
        )
        # 自动启动交互式 shell，使用默认尺寸 120x40
        # 这样通过 API 创建的会话也会显示在 Web UI 中
        await session.start_interactive_shell(cols=120, rows=40)
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {str(e)}")
    await event_bus.publish(
        "session_create", "api",
        f"创建SSH会话 {session_id} -> {req.host}:{req.port}",
        session_id=session_id, host=req.host, port=req.port
    )
    return {"session_id": session_id, "status": "connected", "has_shell": True}


@app.post("/api/sessions/from-host")
async def api_create_session_from_host(req: CreateSessionFromHostRequest):
    """从保存的主机配置快速创建 SSH 会话（支持多终端）"""
    host = storage.get_host(req.host_id)
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")
    session_id = session_manager.create_session(
        host["host"], host["port"], host["username"],
        host_id=req.host_id, terminal_name=req.terminal_name or ""
    )
    session = session_manager.get_session(session_id)
    try:
        await session.connect(
            password=host.get("password") or None,
            private_key=host.get("private_key") or None,
            passphrase=host.get("passphrase") or None,
        )
        # 自动启动交互式 shell，使用默认尺寸 120x40
        await session.start_interactive_shell(cols=120, rows=40)
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {str(e)}")
    await event_bus.publish(
        "session_create", "api",
        f"创建SSH会话 {session_id} -> {host['host']}:{host['port']} ({session.terminal_name})",
        session_id=session_id, host=host['host'], port=host['port'],
        host_id=req.host_id, terminal_name=session.terminal_name
    )
    # 持久化终端信息（会话ID复用，重启后可恢复）
    storage.save_terminal(session_id, req.host_id, session.terminal_name)
    return {"session_id": session_id, "status": "connected",
            "terminal_name": session.terminal_name, "host": _sanitize_host(host)}


@app.post("/api/local/session")
async def api_create_local_session(req: dict = None):
    """创建本机终端（cmd / powershell，winpty ConPTY 交互式 shell，不经过 SSH）

    前端"本机终端"入口调用；会话协议与 SSH 终端完全一致（output/input/resize）。
    """
    req = req or {}
    shell = str(req.get("shell") or "cmd").lower()
    if shell not in ("cmd", "powershell", "pwsh"):
        shell = "cmd"
    import getpass
    tname = "本机 cmd" if shell == "cmd" else ("本机 PowerShell" if shell == "powershell" else "本机 pwsh")
    session_id = session_manager.create_session(
        host="localhost", port=0, username=getpass.getuser(),
        terminal_name=tname
    )
    session = session_manager.get_session(session_id)
    try:
        await session.start_local_shell(shell=shell, cols=120, rows=40)
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"启动本地 shell 失败: {str(e)}")
    await event_bus.publish(
        "session_create", "api",
        f"创建本地终端 {session_id} ({tname})",
        session_id=session_id, host="localhost", port=0
    )
    return {"session_id": session_id, "status": "connected", "has_shell": True,
            "terminal_name": tname, "host": "localhost", "shell": shell}


@app.get("/api/sessions")
async def api_list_sessions():
    """列出所有活动会话"""
    return {"sessions": session_manager.list_sessions()}


@app.get("/api/terminals/saved")
async def api_list_saved_terminals():
    """获取所有持久化的终端（可恢复的终端列表）"""
    saved = storage.list_saved_terminals()
    # 标记哪些终端当前活跃
    active_ids = {s['session_id'] for s in session_manager.list_sessions()}
    for t in saved:
        t['is_active'] = t['session_id'] in active_ids
    return {"terminals": saved}


@app.post("/api/terminals/{session_id}/restore")
async def api_restore_terminal(session_id: str):
    """恢复持久化终端（重新建立 SSH 连接，复用会话ID）"""
    saved = storage.get_saved_terminal(session_id)
    if not saved:
        raise HTTPException(status_code=404, detail="终端不存在")
    host = storage.get_host(saved['host_id'])
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")

    # 如果会话已存在，直接返回
    existing = session_manager.get_session(session_id)
    if existing and existing.is_connected:
        return {"session_id": session_id, "status": "already_connected",
                "terminal_name": saved['terminal_name']}

    # 创建会话（复用 session_id）
    session_manager.create_session_with_id(
        session_id, host["host"], host["port"], host["username"],
        host_id=saved['host_id'], terminal_name=saved['terminal_name']
    )
    session = session_manager.get_session(session_id)
    try:
        await session.connect(
            password=host.get("password") or None,
            private_key=host.get("private_key") or None,
            passphrase=host.get("passphrase") or None,
        )
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {str(e)}")

    storage.save_terminal(session_id, saved['host_id'], saved['terminal_name'])
    return {"session_id": session_id, "status": "connected",
            "terminal_name": saved['terminal_name']}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """关闭并删除会话"""
    session = session_manager.get_session(session_id)
    ok = await session_manager.remove_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    await event_bus.publish(
        "session_close", "api",
        f"关闭SSH会话 {session_id}" + (f" ({session.host})" if session else ""),
        session_id=session_id
    )
    return {"status": "deleted"}


@app.post("/api/sessions/{session_id}/run")
async def api_run_command(session_id: str, req: RunCommandRequest):
    """执行命令
    - 默认（process=False）：如果会话有交互式终端，将命令注入到终端 stdin，
      命令和输出实时显示在 Web 终端中，同时捕获输出返回给调用方。
      不发送任何标记命令，适用于任何交互程序（shell/Python/MySQL/vim）。
      捕获策略：连续2秒无新输出即认为完成，总超时30秒。
      如果没有交互式终端，则回退到独立进程模式。
    - process=True：强制独立进程执行，返回 stdout/stderr，不显示在 Web 终端。
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not session.is_connected:
        raise HTTPException(status_code=400, detail="SSH 未连接")

    # 记录命令到全局历史（跨终端，按使用频次排序）
    await history_db.record_command(req.command)

    # 判断使用哪种模式
    use_inject = (not req.process) and session.has_shell

    await event_bus.publish(
        "command_run", "api",
        f"[{session_id}] {'注入' if use_inject else '执行'}命令: {req.command[:80]}" + ("..." if len(req.command) > 80 else ""),
        session_id=session_id, command=req.command[:200]
    )

    if use_inject:
        # 注入+捕获模式：命令注入到终端，输出显示在终端，同时捕获返回
        try:
            # 确保输出读取器在运行
            await session.start_output_reader()
            code, stdout, stderr = await session.inject_and_capture(req.command, total_timeout=req.timeout, capture_exit_code=req.capture_exit_code)
            return {"returncode": code, "stdout": stdout, "stderr": stderr, "mode": "inject"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="命令执行超时")
        except Exception as e:
            # 注入失败，回退到独立进程模式
            try:
                code, stdout, stderr = await session.run_command(req.command, req.timeout)
                return {"returncode": code, "stdout": stdout, "stderr": stderr, "mode": "fallback_process"}
            except Exception as e2:
                raise HTTPException(status_code=500, detail=f"执行失败: {str(e2)}")
    else:
        # 独立进程模式（process=True 或没有交互式终端）
        try:
            code, stdout, stderr = await session.run_command(req.command, req.timeout)
            return {"returncode": code, "stdout": stdout, "stderr": stderr, "mode": "process"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="命令执行超时")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"执行失败: {str(e)}")


@app.get("/api/sessions/{session_id}/state")
async def api_get_session_state(session_id: str):
    """获取终端当前状态（前台进程、是否在Python/MySQL/分页器、最后一行等）"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not session.has_shell:
        return {"state": "no_shell", "message": "该会话没有交互式终端，请先在 Web 端打开"}
    state = session.get_terminal_state()
    state["session_id"] = session_id
    state["connected"] = session.is_connected
    return state


@app.post("/api/sessions/states")
async def api_get_session_states(session_ids: List[str]):
    """批量获取多个终端状态（合并为单一 HTTP 请求，减少轮询开销）

    优化：原前端每个终端每 3 秒单独轮询 /api/sessions/{id}/state，
    5 个终端 = 每秒约 1.7 次 HTTP 请求；改为一次 POST 批量获取。
    """
    results = {}
    for sid in session_ids:
        session = session_manager.get_session(sid)
        if not session:
            results[sid] = {"state": "not_found"}
            continue
        if not session.has_shell:
            results[sid] = {"state": "no_shell"}
            continue
        state = session.get_terminal_state()
        state["session_id"] = sid
        state["connected"] = session.is_connected
        results[sid] = state
    return {"states": results}


@app.get("/api/sessions/{session_id}/logs")
async def api_get_session_logs(session_id: str, offset: int = 0, limit: int = 2000):
    """获取终端历史日志（分页加载，offset 从最近开始倒数）"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    logs = session.get_history_logs(offset=offset, limit=limit)
    return {"session_id": session_id, "offset": offset, "limit": limit, "content": logs}


# ============ 主机配置 API（持久化） ============

def _sanitize_host(h: dict) -> dict:
    """主机信息（本地工具：密码等敏感字段明文下发，便于前端展示/编辑确认）"""
    out = dict(h)
    for key in ("password", "mgmt_password", "private_key", "passphrase"):
        out[f"has_{key}"] = bool((h.get(key) or "").strip())
    return out


@app.get("/api/hosts")
async def api_list_hosts():
    """获取所有保存的主机（含实时连接状态与连接信息；密码等敏感字段已脱敏）"""
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


class ReorderHostsRequest(BaseModel):
    ids: List[str]


class ReorderGroupsRequest(BaseModel):
    names: List[str]


@app.post("/api/hosts/reorder")
async def api_reorder_hosts(req: ReorderHostsRequest):
    """按给定顺序重排主机列表"""
    storage.reorder_hosts(req.ids)
    return {"status": "reordered"}


@app.post("/api/groups/reorder")
async def api_reorder_groups(req: ReorderGroupsRequest):
    """按给定顺序重排分组列表"""
    storage.reorder_groups(req.names)
    return {"status": "reordered"}


@app.get("/api/sessions/active")
async def api_list_active_terminals():
    """获取所有活跃终端（用于页面重开时恢复）"""
    terminals = session_manager.get_active_terminals()
    result = []
    for s in terminals:
        host_info = None
        if s.host_id:
            host_info = storage.get_host(s.host_id)
        result.append({
            "session_id": s.session_id,
            "host_id": s.host_id,
            "host": s.host,
            "port": s.port,
            "username": s.username,
            "terminal_name": s.terminal_name,
            "host_name": (host_info.get("name") or s.host) if host_info else s.host,
            "host_type": host_info["type"] if host_info else "other",
            "created_at": s.created_at,
            "last_active": s.last_active,
        })
    return {"terminals": result}


@app.post("/api/hosts")
async def api_add_host(req: HostRequest):
    """新增主机"""
    host = storage.add_host(req.dict())
    return _sanitize_host(host)


@app.put("/api/hosts/{host_id}")
async def api_update_host(host_id: str, req: HostRequest):
    """更新主机（密码/私钥等字段留空表示不修改，保持原值）"""
    data = req.dict()
    # 敏感字段为空字符串时不更新（前端编辑弹窗不回显密码，留空=不修改）
    for key in ("password", "mgmt_password", "private_key", "passphrase"):
        if key in data and not (data.get(key) or "").strip():
            data.pop(key)
    host = storage.update_host(host_id, data)
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")
    return _sanitize_host(host)


@app.delete("/api/hosts/{host_id}")
async def api_delete_host(host_id: str):
    """删除主机"""
    ok = storage.delete_host(host_id)
    if not ok:
        raise HTTPException(status_code=404, detail="主机不存在")
    return {"status": "deleted"}


@app.post("/api/hosts/{host_id}/duplicate")
async def api_duplicate_host(host_id: str):
    """复制主机（生成新 ID，名称加"副本"后缀；密码在后端随原主机复制，不下发明文）"""
    new_host = storage.duplicate_host(host_id)
    if not new_host:
        raise HTTPException(status_code=404, detail="主机不存在")
    await event_bus.publish("host_add", "WEB", f"复制主机: {new_host.get('name', new_host['host'])}")
    return _sanitize_host(new_host)


# ============ 存储阵列管理页面自动登录 API（Playwright） ============

@app.post("/api/hosts/{host_id}/auto-login")
async def api_auto_login_storage(host_id: str):
    """存储阵列管理页面自动登录（启动浏览器，自动填写账号密码并登录）"""
    host = storage.get_host(host_id)
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")
    if host.get("device_type") != "storage":
        raise HTTPException(status_code=400, detail="该主机不是存储阵列类型")

    # 异步执行自动登录，避免阻塞 API
    result = await auto_login_storage(host)

    # 发布事件
    event_bus.publish("storage_auto_login", "WEB",
                      f"主机 {host.get('name', host['host'])} 管理页面自动登录: {result['status']}")

    return result


@app.post("/api/hosts/{host_id}/close-browser")
async def api_close_browser(host_id: str):
    """关闭存储阵列管理页面的浏览器实例"""
    result = await close_browser(host_id)
    return result


@app.get("/api/storage/browsers")
async def api_list_browsers():
    """列出所有运行中的存储阵列管理浏览器实例"""
    return {"browsers": list_active_browsers()}


# ============ 分组管理 API ============

@app.get("/api/groups")
async def api_list_groups():
    """获取所有分组"""
    return {"groups": storage.list_groups()}


@app.post("/api/groups")
async def api_add_group(req: GroupRequest):
    """新增分组"""
    ok = storage.add_group(req.name)
    if not ok:
        raise HTTPException(status_code=400, detail="分组已存在或名称无效")
    return {"status": "added", "name": req.name}


@app.delete("/api/groups/{name}")
async def api_delete_group(name: str):
    """删除分组"""
    ok = storage.delete_group(name)
    if not ok:
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"status": "deleted"}


class RenameGroupRequest(BaseModel):
    new_name: str


@app.put("/api/groups/{name}")
async def api_rename_group(name: str, req: RenameGroupRequest):
    """重命名分组"""
    ok = storage.rename_group(name, req.new_name)
    if not ok:
        raise HTTPException(status_code=400, detail="重命名失败（分组不存在或新名称已存在）")
    return {"status": "renamed", "old_name": name, "new_name": req.new_name}


@app.post("/api/groups/{name}/duplicate")
async def api_duplicate_group(name: str):
    """复制分组（生成"xxx 副本"分组，组内主机一并复制）"""
    result = storage.duplicate_group(name)
    if not result:
        raise HTTPException(status_code=404, detail="分组不存在")
    await event_bus.publish("group_add", "WEB", f"复制分组: {name} -> {result['name']}")
    return {"status": "duplicated", **result}


# ============ 全局命令历史 API（跨终端，按使用频次排序） ============

class RecordCommandRequest(BaseModel):
    command: str


@app.post("/api/history/record")
async def api_record_command(req: RecordCommandRequest):
    """记录一条命令到全局历史（增加使用频次）"""
    await history_db.record_command(req.command)
    return {"status": "ok"}


@app.get("/api/history/search")
async def api_search_commands(keyword: str = "", limit: int = 50, include_ignored: bool = False):
    """搜索全局命令历史（SQLite），按使用频次降序（频次相同按最后使用时间降序）；默认过滤已忽略"""
    results = await history_db.search_commands(keyword, limit, include_ignored=include_ignored)
    return {"keyword": keyword, "commands": results}


@app.get("/api/history/recent")
async def api_recent_commands(limit: int = 100):
    """获取最近使用的命令（按最后使用时间降序），已忽略的不返回"""
    results = await history_db.list_recent_commands(limit)
    return {"commands": results}


@app.get("/api/history/ignored")
async def api_ignored_commands(limit: int = 200):
    """获取已忽略的命令列表（用于恢复管理）"""
    results = await history_db.list_ignored_commands(limit)
    return {"commands": results}


class IgnoreCommandRequest(BaseModel):
    command: str


@app.post("/api/history/ignore")
async def api_ignore_command(req: IgnoreCommandRequest):
    """忽略一条命令（搜索/最近命令不再显示）"""
    ok = await history_db.ignore_command(req.command)
    return {"status": "ok" if ok else "not_found"}


@app.post("/api/history/unignore")
async def api_unignore_command(req: IgnoreCommandRequest):
    """恢复一条被忽略的命令"""
    ok = await history_db.unignore_command(req.command)
    return {"status": "ok" if ok else "not_found"}


@app.get("/api/search")
async def api_unified_search(keyword: str = "", limit: int = 50):
    """
    统一搜索：同时搜索快捷命令和历史命令
    - 快捷命令：按名称和命令内容匹配，排在前面
    - 历史命令：按命令内容匹配，按使用频次排序
    返回合并后的结果，每条结果带 type 标识（quick/history）
    """
    kw = keyword.lower().strip()
    results = []

    # 1. 搜索快捷命令（排在前面）
    quick_commands = storage.list_quick_commands()
    for qc in quick_commands:
        if not kw or kw in qc.get('name', '').lower() or kw in qc.get('command', '').lower():
            results.append({
                'type': 'quick',
                'id': qc.get('id', ''),
                'name': qc.get('name', ''),
                'command': qc.get('command', ''),
                'cmd_type': qc.get('type', 'direct'),  # direct=直接执行 / param=输入到终端后编辑
            })

    # 2. 搜索历史命令（按使用频次排序，SQLite，已忽略的不返回）
    history_commands = await history_db.search_commands(keyword, limit)
    for hc in history_commands:
        # 避免和快捷命令重复（相同命令只显示一次，优先显示快捷命令）
        if not any(r['type'] == 'quick' and r['command'] == hc['command'] for r in results):
            results.append({
                'type': 'history',
                'command': hc['command'],
                'count': hc.get('count', 1),
                'last_used': hc.get('last_used', 0),
            })

    return {"keyword": keyword, "results": results[:limit]}


# ============ 快速指令 API ============

@app.get("/api/quick-commands")
async def api_list_quick_commands():
    """获取所有快速指令"""
    return {"commands": storage.list_quick_commands()}


@app.post("/api/quick-commands")
async def api_add_quick_command(req: QuickCommandRequest):
    """新增快速指令"""
    qc = storage.add_quick_command(req.name, req.command, req.description,
                                   req.type, req.pre_ops)
    return qc


class ReorderQuickCommandsRequest(BaseModel):
    ids: List[str]


@app.put("/api/quick-commands/reorder")
async def api_reorder_quick_commands(req: ReorderQuickCommandsRequest):
    """按拖拽后的顺序保存快捷指令"""
    storage.reorder_quick_commands(req.ids)
    return {"status": "ok"}


@app.put("/api/quick-commands/{qc_id}")
async def api_update_quick_command(qc_id: str, req: QuickCommandRequest):
    """更新快速指令"""
    qc = storage.update_quick_command(qc_id, req.name, req.command, req.description,
                                      req.type, req.pre_ops)
    if not qc:
        raise HTTPException(status_code=404, detail="快速指令不存在")
    return qc


@app.delete("/api/quick-commands/{qc_id}")
async def api_delete_quick_command(qc_id: str):
    """删除快速指令"""
    ok = storage.delete_quick_command(qc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="快速指令不存在")
    return {"status": "deleted"}


# ============ 主机类型 API ============

@app.get("/api/host-types")
async def api_list_host_types():
    """获取所有主机类型"""
    return {"types": storage.list_host_types()}


@app.post("/api/host-types")
async def api_add_host_type(req: HostTypeRequest):
    """新增或更新主机类型"""
    ht = storage.add_host_type(req.key, req.label, req.color)
    if not ht:
        raise HTTPException(status_code=400, detail="类型 key 不能为空")
    return ht


@app.delete("/api/host-types/{key}")
async def api_delete_host_type(key: str):
    """删除主机类型"""
    ok = storage.delete_host_type(key)
    if not ok:
        raise HTTPException(status_code=404, detail="主机类型不存在")
    return {"status": "deleted"}


# ============ SFTP 文件管理 API ============

@app.post("/api/sftp/{session_id}/list")
async def api_sftp_list(session_id: str, req: SftpListRequest):
    """列出远程目录内容"""
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        items = await session.list_directory(req.path)
        await event_bus.publish(
            "sftp_list", "api",
            f"[{session_id}] SFTP 列目录: {req.path} ({len(items)}项)",
            session_id=session_id, path=req.path, count=len(items)
        )
        return {"path": req.path, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"列出目录失败: {str(e)}")


@app.get("/api/sftp/{session_id}/read")
async def api_sftp_read(session_id: str, path: str):
    """读取远程文件内容（文本）"""
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        content = await session.read_file(path)
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")


@app.get("/api/sftp/{session_id}/download")
async def api_sftp_download(session_id: str, path: str):
    """下载远程文件（二进制）"""
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        data = await session.read_file_bytes(path)
        filename = path.split("/")[-1] or "download"
        await event_bus.publish(
            "sftp_download", "api",
            f"[{session_id}] SFTP 下载: {path} ({len(data)} bytes)",
            session_id=session_id, path=path, size=len(data)
        )
        from fastapi.responses import Response
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")


@app.post("/api/sftp/{session_id}/write")
async def api_sftp_write(session_id: str, req: SftpWriteRequest):
    """写入远程文件内容"""
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        await session.write_file(req.path, req.content)
        await event_bus.publish(
            "sftp_upload", "api",
            f"[{session_id}] SFTP 上传/写入: {req.path} ({len(req.content)} bytes)",
            session_id=session_id, path=req.path, size=len(req.content)
        )
        return {"status": "written", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {str(e)}")


@app.post("/api/sftp/{session_id}/delete")
async def api_sftp_delete(session_id: str, req: SftpDeleteRequest):
    """删除远程文件或目录"""
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        await session.delete_file(req.path)
        return {"status": "deleted", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.post("/api/sftp/{session_id}/upload")
async def api_sftp_upload(session_id: str, remote_path: str, file: UploadFile = File(...)):
    """上传文件到远程服务器（multipart，支持二进制文件）

    优化：原代码将 bytes decode 为 utf-8 字符串后写入，
    二进制文件（图片/压缩包/可执行文件）会被损坏。
    改为直接写入 bytes，通过 write_file_bytes 方法。
    """
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        content = await file.read()
        await session.write_file(remote_path, content)
        await event_bus.publish(
            "sftp_upload", "api",
            f"[{session_id}] SFTP 上传: {remote_path} ({len(content)} bytes)",
            session_id=session_id, path=remote_path, size=len(content)
        )
        return {"status": "uploaded", "path": remote_path, "size": len(content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


class PreopUploadRequest(BaseModel):
    session_id: str
    source: str          # 源文件：本机绝对路径，或 scripts 目录下的文件名
    source_type: str = "path"  # path=本机绝对路径 / script=scripts 目录文件
    remote: str          # 远端目标路径


@app.post("/api/preop/upload")
async def api_preop_upload(req: PreopUploadRequest):
    """预操作上传：后端直读本地源文件（浏览器拿不到本地路径，由同机 Server 读取）
    - source_type=path：source 为本机绝对路径（如 D:/scripts/deploy.sh）
    - source_type=script：source 为 ~/.ai4one/wstool/scripts/ 下的文件名"""
    session = session_manager.get_session(req.session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    source = (req.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="未指定源文件")
    if req.source_type == "script":
        # 只接受 scripts 目录内的文件名：拒绝路径穿越（../ 等读任意文件）
        name = Path(source).name
        if name != source:
            raise HTTPException(status_code=400, detail="script 类型只接受文件名，不能包含路径")
        src = get_app_dir() / "scripts" / name
    else:
        src = Path(source).expanduser()
        if not src.is_absolute():
            src = get_app_dir() / "scripts" / src
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"源文件不存在: {source}")
    try:
        # 以 bytes 读取并二进制写入：文本/二进制文件均无损（不再 utf-8 替换损坏）
        data = src.read_bytes()
        await session.write_file(req.remote, data)
        return {"status": "uploaded", "source": str(src), "path": req.remote, "size": len(data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@app.get("/api/scripts")
async def api_list_scripts():
    """列出 ~/.ai4one/wstool/scripts/ 下的脚本文件（预操作上传下拉选择）"""
    scripts_dir = get_app_dir() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
    return {"scripts": scripts, "dir": str(scripts_dir)}


# ============ WebSocket（交互式终端） ============

@app.websocket("/ws/ssh/{session_id}")
async def websocket_ssh(websocket: WebSocket, session_id: str):
    """WebSocket 交互式终端（后端统一维护连接，前端断开不影响）
    输出通过广播机制：sessions.py 统一读 stdout，广播给 WebSocket 和 CLI 注入捕获
    """
    await websocket.accept()

    session = session_manager.get_session(session_id)
    if not session:
        await websocket.send_json({"type": "error", "data": "会话不存在"})
        await websocket.close()
        return

    # 检测 SSH 连接是否真的活着，如果断开了自动重连（本地 shell 会话直接跳过）
    if session.is_local():
        pass
    elif not session.is_alive():
        await websocket.send_json({"type": "info", "data": "检测到连接断开，正在自动重连..."})
        try:
            reconnect_ok = await session.reconnect()
            if not reconnect_ok:
                # SSH 重连失败 → 自动切换到本机 shell（不销毁会话，终端保持可用）
                try:
                    await session.switch_to_local("cmd")
                    await websocket.send_json({"type": "info", "data": "SSH 重连失败，已切换到本机 cmd（可在界面重新连接主机）"})
                except Exception as e:
                    await websocket.send_json({"type": "error", "data": f"切换本机 shell 失败: {str(e)}"})
                    await websocket.close()
                    return
            else:
                await websocket.send_json({"type": "info", "data": "自动重连成功"})
        except Exception as e:
            await websocket.send_json({"type": "error", "data": f"重连异常: {str(e)}"})
            await websocket.close()
            return

    # 如果会话已有交互式终端（页面重开恢复），直接复用 process
    # 注意：必须验证 shell 真实存活（channel/进程未关闭），否则 transport 还活着但
    # channel 已死（远端 shell 被 kill/网络异常）时会误报"已恢复"，实际无法输入
    # 不发送"已恢复已有终端会话"提示（用户反馈无意义；恢复内容由前端历史输出直接体现）
    pending_msg = None  # 初始化：如果第一个消息不是 resize，保存下来在消息循环中处理
    if session.is_local() and session.is_shell_alive():
        pass  # 本机 shell 已就绪，直接复用
    elif session._has_shell and session.process is not None and session.is_shell_alive():
        pass
    elif session.is_local():
        # 本机 shell 已退出：重启（沿用原 shell 类型与最近尺寸）
        await websocket.send_json({"type": "info", "data": "检测到本机 shell 已退出，正在重新启动..."})
        await session.restart_local_shell()
    elif session._has_shell and session.process is not None:
        # shell 标志在但实际已死：重启 shell（恢复场景无运行中程序，安全）
        await websocket.send_json({"type": "info", "data": "检测到终端已断开，正在重新启动 shell..."})
        await session.restart_shell()
    else:
        # shell 未启动，需要启动
        if session._has_shell and session.process is None:
            await websocket.send_json({"type": "info", "data": "检测到终端已断开，正在重新启动 shell..."})
            session._has_shell = False

        # 关键修复：在启动 shell 前先等待前端的第一个 resize 消息（最多 500ms）
        # 因为 start_interactive_shell 在消息循环开始前调用，如果在里面等待 resize 事件会导致死锁
        # 所以在这里先接收第一个 resize 消息，获取正确的终端尺寸，再启动 shell
        initial_cols = 120
        initial_rows = 40
        try:
            async with asyncio.timeout(0.5):
                raw = await websocket.receive_text()
                try:
                    first_msg = json.loads(raw)
                    if first_msg.get("type") == "resize":
                        initial_cols = first_msg.get("cols", 120)
                        initial_rows = first_msg.get("rows", 40)
                    else:
                        # 不是 resize 消息，保存下来
                        pending_msg = raw
                except json.JSONDecodeError:
                    pending_msg = raw
        except asyncio.TimeoutError:
            pass  # 前端 0.5s 内未发 resize，使用默认尺寸
        # 其他异常（如 WebSocketDisconnect）不吞掉，交给外层统一处理

        try:
            await session.start_interactive_shell(cols=initial_cols, rows=initial_rows)
        except Exception as e:
            await websocket.send_json({"type": "error", "data": f"启动 shell 失败: {str(e)}"})
            await websocket.close()
            return

    # 确保输出读取器在运行（start_interactive_shell 会自动启动，但恢复场景需要确认）
    await session.start_output_reader()

    # 注册输出监听器（从广播队列读取，发送给前端）
    listener = session.add_output_listener()

    async def read_output():
        try:
            while True:
                # 会话级通知（shell 异常等）优先推送，发送后清空（take 只取一次）
                notice = session.take_shell_notice()
                if notice:
                    await websocket.send_json({"type": "info", "data": notice})
                data = await listener.get()
                await websocket.send_json({"type": "output", "data": data})
        except Exception:
            pass

    read_task = asyncio.create_task(read_output())

    # 全局连接监控已由 SessionManager 统一管理（start_global_monitor），
    # 不再为每个 WebSocket 连接创建独立监控协程
    session_manager.start_global_monitor()
    monitor_task = None  # 保留变量名以兼容 finally 块的 cancel

    # 处理单条消息的函数
    async def handle_message(raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            # 非 JSON 消息，直接尝试写入
            if session.process:
                try:
                    session.process.stdin.write(raw)
                except Exception:
                    pass
            return

        msg_type = msg.get("type")
        if msg_type == "input":
            data = msg.get("data", "")
            if data:
                # 本地 shell（ConPTY）直接写入；SSH shell 直接尝试写入；
                # 若 shell 尚未就绪（启动中/重启窗口），短暂等待后重试
                try:
                    if session.is_local():
                        await session.write_local(data)
                    else:
                        for _ in range(30):
                            if session.process is not None:
                                break
                            await asyncio.sleep(0.1)
                        if session.process is None:
                            raise RuntimeError("终端 shell 尚未就绪，请稍候再输入")
                        session.process.stdin.write(data)
                        session.last_active = time.time()
                except Exception as e:
                    # 不再自动重连：重连会中断正在运行的全屏程序（如 vi/vim），
                    # 且用户已确认不想要自动重连行为；只提示错误，让用户手动处理
                    print(f"[WebSocket] 输入失败: {e}")
                    await websocket.send_json({"type": "error", "data": f"终端输入失败: {e}"})
        elif msg_type == "resize":
            cols = msg.get("cols", 120)
            rows = msg.get("rows", 40)
            if session.is_local():
                session.resize_local(cols, rows)
            else:
                await session.resize_pty(cols, rows)
        elif msg_type == "ping":
            await websocket.send_json({"type": "pong"})

    try:
        # 先处理保存的 pending_msg（如果第一个消息不是 resize）
        if pending_msg:
            await handle_message(pending_msg)
        while True:
            raw = await websocket.receive_text()
            await handle_message(raw)

    except WebSocketDisconnect:
        pass  # 前端断开，不关闭后端 SSH 会话
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass
    finally:
        read_task.cancel()
        if monitor_task:
            monitor_task.cancel()
        try:
            await read_task
        except (asyncio.CancelledError, Exception):
            pass
        if monitor_task:
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass
        # 移除监听器，但不停止输出读取器（可能有其他监听器如 CLI 注入捕获）
        session.remove_output_listener(listener)
        # 注意：不关闭 session，由后端统一维护，24小时无活动自动清理


# ============ 事件订阅 WebSocket（前端观察 Server 交互过程） ============

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket 事件订阅 - 前端通过此连接实时观察所有操作"""
    await websocket.accept()
    await event_bus.subscribe(websocket)
    try:
        while True:
            # 等待客户端消息（主要是 ping 保活）
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        event_bus.unsubscribe(websocket)


# ============ 外部 SSH 会话（跨进程观测：ssh-monkeypatch 推送） ============

@app.get("/api/external-sessions")
async def api_external_sessions():
    """外部会话列表（测试进程通过 ssh-monkeypatch 推送的 SSH 会话）"""
    return {"sessions": external_hub.list_sessions()}


@app.get("/api/external-sessions/{session_id}")
async def api_external_session_detail(session_id: str, limit: int = 500):
    """外部会话详情 + 历史事件（回放）"""
    meta = external_hub.get_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="外部会话不存在")
    return {"session": meta, "events": external_hub.get_history(session_id, limit)}


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """ssh-monkeypatch（测试进程）推送 SSH 事件的入口。

    客户端：websocket-client 推送 JSON 事件流（connect/command/output/close），
    服务端按 session_id 区分会话，转发给订阅的浏览器并缓存历史。
    """
    await websocket.accept()
    print("[External] 测试进程事件流已接入")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                ev = json.loads(raw)
                await external_hub.handle_event(ev)
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"[External] 事件处理异常: {e}")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        print("[External] 测试进程事件流已断开")


@app.websocket("/ws/external/{session_id}")
async def websocket_external(websocket: WebSocket, session_id: str):
    """浏览器订阅外部会话：先回放历史，再实时接收测试侧推送的 SSH 事件"""
    await websocket.accept()
    ok = await external_hub.subscribe(session_id, websocket)
    if not ok:
        await websocket.send_json({"type": "error", "data": "外部会话不存在"})
        await websocket.close()
        return
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        external_hub.unsubscribe(session_id, websocket)


# ============ 启动 ============

def main():
    """启动 SSH Web Tool 服务"""
    # PyInstaller 打包后多进程支持
    if hasattr(sys, 'frozen'):
        import multiprocessing
        multiprocessing.freeze_support()

    import uvicorn
    import webbrowser
    import threading
    import logging

    from ssh_web_tool.config import (
        load_config, resolve_server_config, ensure_config_file, CONFIG_FILE_NAME,
        find_config_file, get_app_dir, ensure_data_dir, migrate_legacy_data,
    )
    from ssh_web_tool.tray import start_tray, acquire_single_instance

    # 统一数据目录：~/.ai4one/wstool；首次运行迁移旧位置（EXE 目录/项目根）的数据
    ensure_data_dir()
    migrate_legacy_data()

    # 历史命令数据库：建表 + 从 data.json 迁移存量命令（幂等）
    try:
        asyncio.run(history_db.init_db())
        migrated = asyncio.run(history_db.migrate_from_json(get_app_dir() / "data.json"))
        if migrated:
            print(f"[history] 已从 data.json 迁移 {migrated} 条历史命令到 SQLite")
    except Exception as e:
        print(f"[history] 初始化历史数据库失败: {e}")

    # 单实例：已有一个实例在运行则打开其 Web 页面并退出
    if not acquire_single_instance():
        port_file = get_app_dir() / ".running_port"
        port = port_file.read_text().strip() if port_file.is_file() else "8765"
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
        print("SSH Web Tool 已在运行，已打开其 Web 界面，本实例退出")
        return

    # 加载配置：首次运行自动生成 config.json（优先复制 config.example.json 模板）
    ensure_config_file()
    cfg = load_config()
    try:
        server = resolve_server_config(cfg)
    except (RuntimeError, ValueError) as e:
        print(f"\n启动失败：{e}")
        print(f"提示：可编辑 {CONFIG_FILE_NAME} 修改 server.port / server.host 后重启")
        if hasattr(sys, 'frozen'):
            input("\n按回车键退出...")
        return

    host, port = server["host"], server["port"]
    base_url = f"http://{host}:{port}"

    # 记录实际使用的端口（供单实例"打开已运行页面"与外部脚本读取）
    try:
        (get_app_dir() / ".running_port").write_text(str(port), encoding="utf-8")
    except OSError:
        pass

    # ===== 请求日志：同时写入 logs/server.log 与控制台 =====
    log_dir = Path(SSHSession.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    server_log = log_dir / "server.log"
    if not server_log.is_file():
        server_log.write_text("", encoding="utf-8")
    file_handler = logging.FileHandler(str(server_log), encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
        )
        root_logger.addHandler(stream_handler)

    print("=" * 50)
    print(f"SSH Web Tool v{APP_VERSION} 启动中...")
    print(f"配置文件: {CONFIG_FILE_NAME}")
    print(f"网页 UI:  {base_url}")
    print(f"API 文档: {base_url}/docs")
    print("数据文件:", storage.data_file)
    print("日志目录:", SSHSession.LOG_DIR)
    print("请求日志:", server_log)
    print("=" * 50)
    print("提示：程序常驻右下角系统托盘，右键托盘图标可打开界面/配置/日志")
    print()

    # 清理超过 30 天的旧会话日志（会话日志不按天轮转，靠启动清理控制体积）
    try:
        n = SSHSession.cleanup_old_logs(days=30)
        if n:
            print(f"[日志] 已清理 {n} 个超过 30 天的旧会话日志")
    except Exception as e:
        print(f"[日志] 清理旧日志失败: {e}")

    # 启动时后台静默检查更新（有新版才弹提示，不自动更新）
    try:
        import threading as _th
        from ssh_web_tool.updater import check_for_update_quiet
        _th.Thread(target=check_for_update_quiet, daemon=True).start()
    except Exception as e:
        print(f"[updater] 启动更新检查失败: {e}")

    # 系统托盘（EXE 打包后 --noconsole 无窗口，托盘是唯一入口）
    try:
        config_path = find_config_file() or (get_app_dir() / CONFIG_FILE_NAME)
        start_tray(base_url, get_app_dir(), config_path, server_log)
    except Exception as e:
        print(f"[tray] 托盘启动失败（不影响服务）: {e}")

    # 延迟打开浏览器（等服务启动后）
    def open_browser():
        time.sleep(1.5)
        webbrowser.open(base_url)

    if cfg.get("open_browser", True):
        threading.Thread(target=open_browser, daemon=True).start()

    # log_config=None：使用我们自己的 logging 配置（文件 + 控制台），
    # 避免 uvicorn 默认配置在 --noconsole（stderr=None）下报错
    uvicorn.run(app, host=host, port=port, log_level="info", workers=1, log_config=None)


if __name__ == "__main__":
    main()
