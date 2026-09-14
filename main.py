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
import re
import sys
import time
import unicodedata
from collections import deque
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ssh_web_tool import history_db
from ssh_web_tool.config import (
    LOCAL_SHELL_CHOICES,
    find_config_file,
    get_app_dir,
    get_fallback_local_shell,
    load_config,
    save_config,
)
from ssh_web_tool.external_sessions import external_hub
from ssh_web_tool.playwright_mgmt import auto_login_storage, close_browser, list_active_browsers
from ssh_web_tool.sessions import SSHSession, session_manager
from ssh_web_tool.storage import storage
from ssh_web_tool.version import APP_VERSION

app = FastAPI(title="SSH Web Tool", version=APP_VERSION)


@app.on_event("shutdown")
async def _shutdown_cleanup():
    """程序退出前关闭数据库单例连接。

    history_db 使用全局单例 aiosqlite 连接（非 daemon worker 线程），
    若不显式关闭，进程异常退出时可能残留 -wal/-shm 文件。
    """
    try:
        await history_db.close_db()
    except Exception:
        pass


# PyInstaller 打包兼容：静态文件从临时目录读取，数据文件保存在 EXE 所在目录
def get_resource_path(relative_path: str) -> Path:
    """获取资源文件路径（兼容 PyInstaller 打包）"""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 打包后，资源文件在临时目录
        return Path(sys._MEIPASS) / relative_path  # type: ignore[attr-defined]
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
            "type": "event",
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


# ============ 请求模型 ============


class CreateSessionRequest(BaseModel):
    host: str
    port: int = 22
    username: str
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


class CreateRawSessionRequest(BaseModel):
    """原始连接信息创建会话（本地终端拦截 SSH 后的"重连"入口，无已保存主机）"""

    host: str
    port: int = 22
    username: str
    password: str | None = None
    terminal_name: str | None = ""


class CreateSessionFromHostRequest(BaseModel):
    host_id: str
    terminal_name: str | None = ""  # 可选：指定终端名称，不填则自动生成


class RunCommandRequest(BaseModel):
    command: str
    timeout: int = 30
    process: bool = False  # True=强制独立进程执行（不注入到Web终端），False=默认自动（有shell就注入+捕获）
    capture_exit_code: bool = True  # False=跳过 echo $? 获取退出码（节省1-2秒），退出码返回 None


class HostRequest(BaseModel):
    name: str | None = ""
    host: str
    port: int = 22
    username: str = "root"
    password: str | None = ""
    private_key: str | None = ""
    passphrase: str | None = ""
    type: str | None = "other"
    group: str | None = ""
    # 设备类型：linux（普通主机）/ storage（存储阵列）
    device_type: str | None = "linux"
    # 存储阵列管理页面配置
    mgmt_port: int | None = 8088
    mgmt_username: str | None = ""
    mgmt_password: str | None = ""
    # Playwright 自动登录选择器配置
    pw_username_selector: str | None = ""
    pw_password_selector: str | None = ""
    pw_login_btn_selector: str | None = ""
    pw_old_password_selector: str | None = ""
    pw_new_password_selector: str | None = ""
    pw_confirm_password_selector: str | None = ""
    pw_confirm_btn_selector: str | None = ""
    pw_success_selector: str | None = ""
    pw_headless: bool | None = False


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
    cfg = load_config()
    return {
        "fallback_local_shell": get_fallback_local_shell(cfg),
        "local_shell_choices": list(LOCAL_SHELL_CHOICES),
        "config_file": str(find_config_file() or (get_app_dir() / "config.json")),
    }


class FallbackShellRequest(BaseModel):
    """设置 SSH 断开后切换的本机 shell"""

    shell: str


@app.post("/api/config/fallback-shell")
async def api_set_fallback_shell(req: FallbackShellRequest):
    """保存"断开后切换本机终端"的 shell 选择（cmd / powershell / pwsh）"""
    shell = req.shell.strip().lower()
    if shell not in LOCAL_SHELL_CHOICES:
        raise HTTPException(status_code=400, detail=f"不支持的 shell: {shell}，可选 {list(LOCAL_SHELL_CHOICES)}")
    cfg = load_config()
    cfg["fallback_local_shell"] = shell
    if not save_config(cfg):
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"status": "ok", "fallback_local_shell": shell}


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
    assert session is not None
    try:
        # 外层总超时兜底：目标不可达/认证卡住时不至于让前端请求无限挂起
        async with asyncio.timeout(30):  # type: ignore[attr-defined]
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
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {e!s}")
    await event_bus.publish(
        "session_create",
        "api",
        f"创建SSH会话 {session_id} -> {req.host}:{req.port}",
        session_id=session_id,
        host=req.host,
        port=req.port,
    )
    return {"session_id": session_id, "status": "connected", "has_shell": True}


@app.post("/api/sessions/raw")
async def api_create_raw_session(req: CreateRawSessionRequest):
    """从原始连接信息创建 SSH 会话并启动 shell

    本地终端拦截 ssh user:pass@host 命令后的"重连"入口：这类会话没有关联已保存
    主机，前端用会话内保存的连接信息（ssh_connected 消息带回）重新建立终端。
    """
    session_id = session_manager.create_session(
        req.host, req.port, req.username, terminal_name=req.terminal_name or ""
    )
    session = session_manager.get_session(session_id)
    assert session is not None
    try:
        # 外层总超时兜底：重连目标不可达时不至于让前端请求无限挂起
        async with asyncio.timeout(30):  # type: ignore[attr-defined]
            await session.connect(password=req.password or None)
            await session.start_interactive_shell(cols=120, rows=40)
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {e!s}")
    await event_bus.publish(
        "session_create",
        "raw",
        f"原始连接创建 SSH 会话 {session_id} -> {req.host}:{req.port} ({session.terminal_name})",
        session_id=session_id,
        host=req.host,
        port=req.port,
        terminal_name=session.terminal_name,
    )
    return {"session_id": session_id, "status": "connected", "terminal_name": session.terminal_name}


@app.post("/api/sessions/from-host")
async def api_create_session_from_host(req: CreateSessionFromHostRequest):
    """从保存的主机配置快速创建 SSH 会话（支持多终端）"""
    host = storage.get_host(req.host_id)
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")
    session_id = session_manager.create_session(
        host["host"], host["port"], host["username"], host_id=req.host_id, terminal_name=req.terminal_name or ""
    )
    session = session_manager.get_session(session_id)
    assert session is not None
    try:
        # 外层总超时兜底：目标不可达/认证卡住时不至于让前端请求无限挂起
        async with asyncio.timeout(30):  # type: ignore[attr-defined]
            await session.connect(
                password=host.get("password") or None,
                private_key=host.get("private_key") or None,
                passphrase=host.get("passphrase") or None,
            )
            # 自动启动交互式 shell，使用默认尺寸 120x40
            await session.start_interactive_shell(cols=120, rows=40)
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {e!s}")
    await event_bus.publish(
        "session_create",
        "api",
        f"创建SSH会话 {session_id} -> {host['host']}:{host['port']} ({session.terminal_name})",
        session_id=session_id,
        host=host["host"],
        port=host["port"],
        host_id=req.host_id,
        terminal_name=session.terminal_name,
    )
    # 持久化终端信息（会话ID复用，重启后可恢复）
    storage.save_terminal(session_id, req.host_id, session.terminal_name)
    return {
        "session_id": session_id,
        "status": "connected",
        "terminal_name": session.terminal_name,
        "host": _sanitize_host(host),
    }


@app.post("/api/local/session")
async def api_create_local_session(req: dict | None = None):
    """创建本机终端（cmd / powershell，WinPTY 交互式 shell，不经过 SSH）

    前端"本机终端"入口调用；会话协议与 SSH 终端完全一致（output/input/resize）。
    """
    req = req or {}
    shell = str(req.get("shell") or "cmd").lower()
    if shell not in ("cmd", "powershell", "pwsh"):
        shell = "cmd"
    import getpass

    tname = "本机 cmd" if shell == "cmd" else ("本机 PowerShell" if shell == "powershell" else "本机 pwsh")
    session_id = session_manager.create_session(
        host="localhost", port=0, username=getpass.getuser(), terminal_name=tname
    )
    session = session_manager.get_session(session_id)
    assert session is not None
    try:
        await session.start_local_shell(shell=shell, cols=120, rows=40, start_reader=False)
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"启动本地 shell 失败: {e!s}")
    await event_bus.publish(
        "session_create", "api", f"创建本地终端 {session_id} ({tname})", session_id=session_id, host="localhost", port=0
    )
    return {
        "session_id": session_id,
        "status": "connected",
        "has_shell": True,
        "terminal_name": tname,
        "host": "localhost",
        "shell": shell,
    }


@app.get("/api/sessions")
async def api_list_sessions():
    """列出所有活动会话"""
    return {"sessions": session_manager.list_sessions()}


@app.get("/api/terminals/saved")
async def api_list_saved_terminals():
    """获取所有持久化的终端（可恢复的终端列表）"""
    saved = storage.list_saved_terminals()
    # 标记哪些终端当前活跃
    active_ids = {s["session_id"] for s in session_manager.list_sessions()}
    for t in saved:
        t["is_active"] = t["session_id"] in active_ids
    return {"terminals": saved}


@app.post("/api/terminals/{session_id}/restore")
async def api_restore_terminal(session_id: str):
    """恢复持久化终端（重新建立 SSH 连接，复用会话ID）"""
    saved = storage.get_saved_terminal(session_id)
    if not saved:
        raise HTTPException(status_code=404, detail="终端不存在")
    host = storage.get_host(saved["host_id"])
    if not host:
        raise HTTPException(status_code=404, detail="主机不存在")

    # 如果会话已存在，直接返回
    existing = session_manager.get_session(session_id)
    if existing and existing.is_connected:
        return {"session_id": session_id, "status": "already_connected", "terminal_name": saved["terminal_name"]}

    # 创建会话（复用 session_id）
    session_manager.create_session_with_id(
        session_id,
        host["host"],
        host["port"],
        host["username"],
        host_id=saved["host_id"],
        terminal_name=saved["terminal_name"],
    )
    session = session_manager.get_session(session_id)
    assert session is not None
    try:
        await session.connect(
            password=host.get("password") or None,
            private_key=host.get("private_key") or None,
            passphrase=host.get("passphrase") or None,
        )
    except Exception as e:
        await session_manager.remove_session(session_id)
        raise HTTPException(status_code=400, detail=f"SSH 连接失败: {e!s}")

    storage.save_terminal(session_id, saved["host_id"], saved["terminal_name"])
    return {"session_id": session_id, "status": "connected", "terminal_name": saved["terminal_name"]}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """关闭并删除会话"""
    session = session_manager.get_session(session_id)
    ok = await session_manager.remove_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    await event_bus.publish(
        "session_close",
        "api",
        f"关闭SSH会话 {session_id}" + (f" ({session.host})" if session else ""),
        session_id=session_id,
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
        "command_run",
        "api",
        f"[{session_id}] {'注入' if use_inject else '执行'}命令: {req.command[:80]}"
        + ("..." if len(req.command) > 80 else ""),
        session_id=session_id,
        command=req.command[:200],
    )

    if use_inject:
        # 注入+捕获模式：命令注入到终端，输出显示在终端，同时捕获返回
        try:
            # 确保输出读取器在运行
            await session.start_output_reader()
            code, stdout, stderr = await session.inject_and_capture(
                req.command, total_timeout=req.timeout, capture_exit_code=req.capture_exit_code
            )
            return {"returncode": code, "stdout": stdout, "stderr": stderr, "mode": "inject"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="命令执行超时")
        except Exception:
            # 注入失败，回退到独立进程模式
            try:
                code, stdout, stderr = await session.run_command(req.command, req.timeout)
                return {"returncode": code, "stdout": stdout, "stderr": stderr, "mode": "fallback_process"}
            except Exception as e2:
                raise HTTPException(status_code=500, detail=f"执行失败: {e2!s}")
    else:
        # 独立进程模式（process=True 或没有交互式终端）
        try:
            code, stdout, stderr = await session.run_command(req.command, req.timeout)
            return {"returncode": code, "stdout": stdout, "stderr": stderr, "mode": "process"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="命令执行超时")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"执行失败: {e!s}")


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
async def api_get_session_states(session_ids: list[str]):
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


@app.get("/api/logs/{session_id}")
async def api_get_session_logs_by_file(session_id: str, offset: int = 0, limit: int = 200000):
    """按会话ID从日志文件读取完整历史（会话删除后仍可读，用于重连保留历史）

    - 会话还在：优先走 get_history_logs（含未 flush 缓冲）
    - 会话已删除（如手动断开）：按日志文件名匹配（文件名含 session_id），从文件读，
      保证重连时旧会话的 banner/命令输出仍能回放到新终端（用户要求：重连不清空）
    """
    session = session_manager.get_session(session_id)
    if session:
        return {"session_id": session_id, "content": session.get_history_logs(offset=offset, limit=limit)}
    log_dir = Path(SSHSession.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    matches = sorted(
        (p for p in log_dir.glob(f"*{session_id}*.log") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        return {"session_id": session_id, "content": ""}
    f = matches[0]
    try:
        size = f.stat().st_size
        start_pos = max(0, size - offset - limit)
        read_size = min(limit, size - start_pos)
        with open(f, encoding="utf-8", errors="replace") as fh:
            fh.seek(start_pos)
            content = fh.read(read_size)
        return {"session_id": session_id, "content": SSHSession._format_history_logs(content)}
    except Exception as e:
        print(f"[logs] 读取日志文件失败 {f}: {e}")
        return {"session_id": session_id, "content": ""}


# ============ 主机配置 API（持久化） ============


def _sanitize_host(h: dict) -> dict:
    """主机信息（本地工具：密码等敏感字段明文下发，便于前端展示/编辑确认）"""
    out = dict(h)
    for key in ("password", "mgmt_password", "private_key", "passphrase"):
        out[f"has_{key}"] = bool((h.get(key) or "").strip())
    return out


def _parse_ssh_command(data: str) -> tuple[str, int, str, str] | None:
    """解析本地终端输入的 ssh 命令，提取连接信息

    支持格式：ssh user:password@host  或  ssh user:password@host:port
    冒号前是用户名，冒号后到最后一个 @ 前是密码，最后一个 @ 后是 IP（可带端口）

    返回 (host, port, username, password) 或 None（不匹配）
    """
    # 取回车前的命令行
    line = data.split("\r")[0].split("\n")[0].strip()
    # 清洗 ANSI 转义序列（PowerShell PSReadLine 会混入光标控制/行重绘序列）
    line = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line)
    line = re.sub(r"\x1b\][\s\S]*?(\x07|\x1b\\)", "", line)
    line = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", line)
    # NFKC 兼容归一化：中文输入法全角标点/字母数字（：＠．１９２ 等）转半角，
    # 否则全角字符的 host 会让 socket 的 idna 编码报 "label empty" 等晦涩错误
    line = unicodedata.normalize("NFKC", line)
    # 匹配 ssh 前缀（允许前面有空白）
    m = re.match(r"^ssh\s+(.+)$", line)
    if not m:
        return None
    rest = m.group(1).strip()
    # 必须包含 @（最后一个 @ 分割：前面是 user:password，后面是 host[:port]）
    at_idx = rest.rfind("@")
    if at_idx <= 0:
        return None
    user_pass = rest[:at_idx]
    host_port = rest[at_idx + 1 :]
    # user:password 分割（第一个冒号）
    colon_idx = user_pass.find(":")
    if colon_idx <= 0:
        return None
    username = user_pass[:colon_idx]
    password = user_pass[colon_idx + 1 :]
    if not username or not password or not host_port:
        return None
    # 解析 host:port
    port = 22
    # IPv6 地址中可能有多个冒号，但本项目场景以 IPv4/域名为主
    # 只在 host_port 中最后一个冒号后为纯数字时视为端口
    if host_port.count(":") == 1:
        h, p = host_port.rsplit(":", 1)
        if p.isdigit():
            host_port, port = h, int(p)
    # host 严格校验：只接受合法 IPv4 / 域名（逗号、连续点号、空格等直接判不匹配，
    # 交给本地 shell 原生 ssh 处理，避免 idna 编码抛异常显示晦涩报错）
    if not _is_valid_host(host_port):
        return None
    return host_port, port, username, password


def _is_valid_host(host: str) -> bool:
    """校验 host 是否为合法 IPv4 地址或域名（不含 IPv6）"""
    if not host or len(host) > 253:
        return False
    parts = host.split(".")
    # IPv4：四段且每段 0-255；四段全数字但越界（如 256.1.1.1）直接非法，
    # 不得落入域名分支（域名 TLD 标签不允许纯数字）
    if all(p.isdigit() for p in parts):
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    # 域名：标签 1-63 字符，字母数字开头/结尾，中间可含连字符；
    # 允许单标签主机名（localhost / 局域网机器名），纯数字单标签已在上面拦截
    label_re = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    return all(label_re.match(p) for p in parts)


async def _run_ssh_switch(
    websocket: WebSocket,
    session: SSHSession,
    ssh_host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_pass: str,
) -> None:
    """后台执行"本地终端拦截 SSH"的切换（WebSocket 消息循环创建，不阻塞接收）

    - 成功：发送 ssh_connected（含连接信息；前端据此保存重连凭据，重连不再依赖已保存主机）
    - 失败：提示错误并恢复本地 shell（switch_to_ssh 已恢复读取）
    - 取消（用户 Ctrl+C）：提示已取消，本地 shell 保持可用
    """
    try:
        # 总超时兜底：连接阶段最多 30s（connect 内部还有 TCP connect_timeout）
        async with asyncio.timeout(30):  # type: ignore[attr-defined]
            await session.switch_to_ssh(ssh_host, ssh_port, ssh_user, ssh_pass)
        await websocket.send_json(
            {
                "type": "ssh_connected",
                "host": ssh_host,
                "port": ssh_port,
                "username": ssh_user,
                "password": ssh_pass,  # 本地工具：用户刚在终端输入过，带回供"重连"使用
                "terminal_name": f"{ssh_user}@{ssh_host}",
            }
        )
        await event_bus.publish(
            "session_create",
            "local-ssh",
            f"本地终端拦截 SSH 连接 {session.session_id} -> {ssh_user}@{ssh_host}:{ssh_port}",
            session_id=session.session_id,
            host=ssh_host,
            port=ssh_port,
        )
        # 切换成功：更新会话终端名（重连/恢复页面时标签显示 root@host，而非"本机 cmd"）
        session.terminal_name = f"{ssh_user}@{ssh_host}"
    except asyncio.CancelledError:
        # 用户 Ctrl+C 取消连接：switch_to_ssh 已恢复本地 shell 读取
        try:
            await session._broadcast_output("\r\n\x1b[33m[已取消连接]\x1b[0m\r\n")
        except Exception:
            pass
    except Exception as e:
        # 连接失败：本地 shell 仍可用。发 ESC 清掉 shell 里残留的半行命令
        # （cmd 丢弃整行、PSReadLine RevertLine 清空输入），给用户干净的提示符重试
        try:
            await session.write_local("\x1b")
        except Exception:
            pass
        try:
            await session._broadcast_output(f"\r\n\x1b[31m[SSH 连接失败: {e}]\x1b[0m\r\n")
        except Exception:
            pass
    finally:
        session._ssh_switch_task = None


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
    ids: list[str]


class ReorderGroupsRequest(BaseModel):
    names: list[str]


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
        # 本地终端拦截 SSH 命令建立的会话（host_id 为空、无已保存主机）：把原始连接
        # 信息随活跃列表下发，前端恢复终端时保存为 ssh_conn，重连不再报"找不到该主机信息"
        ssh_conn = None
        if not s.host_id and getattr(s, "_password", None):
            ssh_conn = {
                "host": s.host,
                "port": s.port,
                "username": s.username,
                "password": s._password,
            }
        result.append(
            {
                "session_id": s.session_id,
                "host_id": s.host_id,
                "host": s.host,
                "port": s.port,
                "username": s.username,
                "terminal_name": s.terminal_name,
                "host_name": (host_info.get("name") or s.host) if host_info else s.host,
                "host_type": host_info["type"] if host_info else "other",
                "ssh_conn": ssh_conn,
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
        )
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
    await event_bus.publish(
        "storage_auto_login", "WEB", f"主机 {host.get('name', host['host'])} 管理页面自动登录: {result['status']}"
    )

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
        if not kw or kw in qc.get("name", "").lower() or kw in qc.get("command", "").lower():
            results.append(
                {
                    "type": "quick",
                    "id": qc.get("id", ""),
                    "name": qc.get("name", ""),
                    "command": qc.get("command", ""),
                    "cmd_type": qc.get("type", "direct"),  # direct=直接执行 / param=输入到终端后编辑
                    "pre_ops": qc.get("pre_ops", []),  # 预操作：搜索执行与点击快捷指令行为一致
                }
            )

    # 2. 搜索历史命令（按使用频次排序，SQLite，已忽略的不返回）
    history_commands = await history_db.search_commands(keyword, limit)
    for hc in history_commands:
        # 避免和快捷命令重复（相同命令只显示一次，优先显示快捷命令）
        if not any(r["type"] == "quick" and r["command"] == hc["command"] for r in results):
            results.append(
                {
                    "type": "history",
                    "command": hc["command"],
                    "count": hc.get("count", 1),
                    "last_used": hc.get("last_used", 0),
                }
            )

    return {"keyword": keyword, "results": results[:limit]}


# ============ 快速指令 API ============


@app.get("/api/quick-commands")
async def api_list_quick_commands():
    """获取所有快速指令"""
    return {"commands": storage.list_quick_commands()}


@app.post("/api/quick-commands")
async def api_add_quick_command(req: QuickCommandRequest):
    """新增快速指令"""
    qc = storage.add_quick_command(req.name, req.command, req.description, req.type, req.pre_ops)
    return qc


class ReorderQuickCommandsRequest(BaseModel):
    ids: list[str]


@app.put("/api/quick-commands/reorder")
async def api_reorder_quick_commands(req: ReorderQuickCommandsRequest):
    """按拖拽后的顺序保存快捷指令"""
    storage.reorder_quick_commands(req.ids)
    return {"status": "ok"}


@app.put("/api/quick-commands/{qc_id}")
async def api_update_quick_command(qc_id: str, req: QuickCommandRequest):
    """更新快速指令"""
    qc = storage.update_quick_command(qc_id, req.name, req.command, req.description, req.type, req.pre_ops)
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
            "sftp_list",
            "api",
            f"[{session_id}] SFTP 列目录: {req.path} ({len(items)}项)",
            session_id=session_id,
            path=req.path,
            count=len(items),
        )
        return {"path": req.path, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"列出目录失败: {e!s}")


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
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e!s}")


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
            "sftp_download",
            "api",
            f"[{session_id}] SFTP 下载: {path} ({len(data)} bytes)",
            session_id=session_id,
            path=path,
            size=len(data),
        )
        from fastapi.responses import Response

        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {e!s}")


@app.post("/api/sftp/{session_id}/write")
async def api_sftp_write(session_id: str, req: SftpWriteRequest):
    """写入远程文件内容"""
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    try:
        await session.write_file(req.path, req.content)
        await event_bus.publish(
            "sftp_upload",
            "api",
            f"[{session_id}] SFTP 上传/写入: {req.path} ({len(req.content)} bytes)",
            session_id=session_id,
            path=req.path,
            size=len(req.content),
        )
        return {"status": "written", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e!s}")


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
        raise HTTPException(status_code=500, detail=f"删除失败: {e!s}")


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
            "sftp_upload",
            "api",
            f"[{session_id}] SFTP 上传: {remote_path} ({len(content)} bytes)",
            session_id=session_id,
            path=remote_path,
            size=len(content),
        )
        return {"status": "uploaded", "path": remote_path, "size": len(content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")


class PreopUploadRequest(BaseModel):
    session_id: str
    source: str  # 源文件：本机绝对路径，或 scripts 目录下的文件名
    source_type: str = "path"  # path=本机绝对路径 / script=scripts 目录文件
    remote: str  # 远端目标路径


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
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")


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

    # 先注册输出监听器再启动 reader：banner/MOTD/提示符等首包输出不丢失
    # 本地终端场景尤其关键：WinPTY read() 阻塞等待，初始提示符只输出一次，
    # 若 listener 未注册就启动 reader，提示符被广播到空 listener 列表后丢失，
    # 前端永远等不到首包（local_starting 无法清除）。
    listener = session.add_output_listener()
    await session.start_output_reader()

    async def read_output():
        try:
            while True:
                # 会话级通知（shell 异常等）优先推送，发送后清空（take 只取一次）
                notice = session.take_shell_notice()
                if notice:
                    await websocket.send_json({"type": "info", "data": notice})
                # 会话级切换通知：SSH 退出/断开后自动切换到本机 shell，
                # 通知前端更新终端类型为 local（隐藏断开按钮、更新标签等）
                switch = session.take_switch_notice()
                if switch:
                    label = "PowerShell" if switch in ("powershell", "pwsh") else "cmd"
                    await websocket.send_json(
                        {
                            "type": "switched_to_local",
                            "shell": switch,
                            "terminal_name": f"本机 {label}",
                        }
                    )
                # 会话关闭通知（本机终端 exit 等）：推送 closed 后结束输出转发，
                # 前端收到后关闭标签与连接
                closed = session.take_closed_notice()
                if closed:
                    await websocket.send_json({"type": "closed", "data": closed})
                    return
                # 带超时等待输出：本机 shell exit 后可能不再有输出，若无超时，
                # 循环会永久阻塞在 listener.get() 上，closed 通知永远送不出去
                try:
                    data = await asyncio.wait_for(listener.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                await websocket.send_json({"type": "output", "data": data})
        except Exception:
            pass

    read_task = asyncio.create_task(read_output())

    # 全局连接监控已由 SessionManager 统一管理（start_global_monitor），
    # 不再为每个 WebSocket 连接创建独立监控协程
    session_manager.start_global_monitor()
    monitor_task = None  # 保留变量名以兼容 finally 块的 cancel

    # 检测 SSH 连接是否真的活着，如果断开了自动重连（本地 shell 会话直接跳过）
    if session.is_local():
        pass
    elif not session.is_alive():
        # SSH 连接已断开（页面重开恢复时命中）：不再自动重连（需求：断开后手动重连），
        # 直接切换到本机 shell，由用户点击「重连」按钮手动恢复
        await websocket.send_json({"type": "info", "data": "检测到连接断开，已切换本机终端"})
        try:
            await session._auto_switch_to_local("SSH 连接断开")
        except Exception as e:
            await websocket.send_json({"type": "error", "data": f"切换本机 shell 失败: {e!s}"})
            await websocket.close()
            return

    # 如果会话已有交互式终端（页面重开恢复），直接复用 process
    # 注意：必须验证 shell 真实存活（channel/进程未关闭），否则 transport 还活着但
    # channel 已死（远端 shell 被 kill/网络异常）时会误报"已恢复"，实际无法输入
    # 不发送"已恢复已有终端会话"提示（用户反馈无意义；恢复内容由前端历史输出直接体现）
    pending_msg = None  # 初始化：如果第一个消息不是 resize，保存下来在消息循环中处理
    if session.is_local() and session.is_shell_alive():
        # 本机 shell 已就绪：等待前端 resize 消息，用正确尺寸调整 PTY
        # 原因：PTY 在 API 调用时以默认 120x40 创建，但前端 xterm 实际尺寸可能不同，
        # 尺寸不一致会导致 cmd 的绝对光标定位序列（\x1b[NG）在 xterm 中错位
        # 若尺寸有变化，额外发送 Ctrl+L 让 cmd 用新尺寸重绘提示符（光标位置正确）
        try:
            async with asyncio.timeout(0.5):  # type: ignore[attr-defined]
                raw = await websocket.receive_text()
                try:
                    first_msg = json.loads(raw)
                    if first_msg.get("type") == "resize":
                        new_cols = first_msg.get("cols", 120)
                        new_rows = first_msg.get("rows", 40)
                        size_changed = new_cols != session._last_cols or new_rows != session._last_rows
                        session.resize_local(new_cols, new_rows)
                        if size_changed:
                            # 尺寸有变化：发送 Ctrl+L 让 cmd 用新尺寸重绘提示符
                            await session.write_local("\x0c")
                    else:
                        pending_msg = raw
                except json.JSONDecodeError:
                    pending_msg = raw
        except asyncio.TimeoutError:
            pass
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
            async with asyncio.timeout(0.5):  # type: ignore[attr-defined]
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
            await websocket.send_json({"type": "error", "data": f"启动 shell 失败: {e!s}"})
            await websocket.close()
            return

    # 确保输出读取器在运行（start_interactive_shell 会自动启动，但恢复场景需要确认）
    # 注意：listener/read_task 已在 shell 启动前注册（见上方），此处仅兜底确保 reader 在跑
    await session.start_output_reader()

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
                # 本地终端 SSH 拦截：xterm 逐字符发送输入，服务端先拼行，
                # 回车成行后若匹配 ssh user:pass@host[:port] 则拦截（不写入本地
                # shell 的回车），直接建立 SSH 连接切换到远端终端
                if session.is_local():
                    line = session.feed_local_input(data)
                    if line is not None:
                        parsed = _parse_ssh_command(line)
                        if parsed:
                            ssh_host, ssh_port, ssh_user, ssh_pass = parsed
                            await session._broadcast_output(
                                f"\r\n\x1b[36m[正在连接 {ssh_user}@{ssh_host}:{ssh_port} ...]\x1b[0m\r\n"
                            )
                            # 后台任务执行连接：不阻塞 WebSocket 消息循环。此前同步等待时
                            # 连接最长卡 30s，期间界面无响应、无法 Ctrl+C 取消，观感"卡死"。
                            # 现在连接期间可随时 Ctrl+C 取消、继续输入/操作其他界面。
                            old = session._ssh_switch_task
                            if old is not None and not old.done():
                                old.cancel()
                            session._ssh_switch_task = asyncio.create_task(
                                _run_ssh_switch(websocket, session, ssh_host, ssh_port, ssh_user, ssh_pass)
                            )
                            return  # 回车已消费（连接结果由后台任务推送），不写入本地 shell
                    # Ctrl+C：取消正在进行的 SSH 连接（"正在连接..."时按 Ctrl+C 中止）
                    if data == "\x03":
                        task = session._ssh_switch_task
                        if task is not None and not task.done():
                            task.cancel()
                            return
                # 本地 shell（WinPTY）直接写入；SSH shell 直接尝试写入。
                # SSH 已退出/断开时（自动切换本机终端或自动重连进行中）先等状态收敛：
                # 期间 process 可能是已关闭的通道，直接写会报 "Channel not open for sending"
                try:
                    if session.is_local():
                        await session.write_local(data)
                    else:
                        for _ in range(50):
                            if session.process is not None and not session.switching_local:
                                break
                            await asyncio.sleep(0.1)
                        if session.switching_local:
                            raise RuntimeError("正在切换本机终端，请稍候再输入")
                        if session.process is None:
                            raise RuntimeError("终端 shell 尚未就绪，请稍候再输入")
                        stdin = session.process.stdin
                        if getattr(stdin, "is_closing", None) and stdin.is_closing():
                            # 通道已关闭（连接断开，监控周期未到）：不写死通道，给友好提示；
                            # 同时立即触发切回本机终端（_auto_switch_to_local 幂等，
                            # 已在切换/重连/本机时自行跳过），用户下一次输入即可正常进行
                            session._switch_task = asyncio.create_task(
                                session._auto_switch_to_local("SSH 连接断开")
                            )
                            raise RuntimeError("SSH 通道已关闭，正在切换本机终端，请稍候再输入")
                        stdin.write(data)
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


def _acquire_single_instance() -> bool:
    """单实例检查：已有一个实例在运行时返回 False（Windows 命名 Mutex）

    注意：必须用 use_last_error=True + ctypes.get_last_error()。
    ctypes.windll.kernel32.GetLastError() 的返回值会被 ctypes 自身的
    内部调用覆盖，导致单实例检查误判（多个实例同时通过检查）。
    """
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Local\\SSHWebTool_SingleInstance")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _exit_quietly(code: int = 0) -> None:
    """启动阶段的提前退出。

    history_db.init_db() 已打开 aiosqlite 单例连接，其内部工作线程是
    非守护线程（aiosqlite/core.py Thread 无 daemon=True），main() 直接
    return 会留下一个无窗口僵尸进程（用户看到"启动了但没反应"），
    必须先关闭数据库再硬退出。
    """
    try:
        asyncio.run(history_db.close_db())
    except Exception:
        pass
    # os._exit 不刷新缓冲：管道/终端下提前 print 的提示会丢失，先手动 flush
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    os._exit(code)


def _check_webview2_runtime() -> bool:
    """检查 WebView2 Runtime 是否已安装。

    pywebview 在 Windows 上优先使用 EdgeChromium（WebView2）内核，
    但运行时缺失时会静默回退到 MSHTML（IE 内核）——现代前端页面将完全无法渲染。
    这里提前检测，缺失时给出明确提示而不是让用户面对白屏。

    注意：机器级安装的 WebView2 注册在 HKLM 的 32 位视图（WOW6432Node）下，
    64 位 Python 默认视图看不到，必须显式用 KEY_WOW64_32KEY 读取。
    """
    import winreg

    # WebView2 Runtime 稳定版的注册表 GUID（与 pywebview 内部检测一致）
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    root, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}", 0, winreg.KEY_READ | view
                ) as key:
                    pv = winreg.QueryValueEx(key, "pv")[0]
                    if pv and pv != "0.0.0.0":
                        return True
            except OSError:
                continue

    # 兜底：注册表异常时直接找安装目录下的 msedgewebview2.exe（覆盖非常规安装方式）
    import glob
    import os

    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(env)
        if base and glob.glob(os.path.join(base, "Microsoft", "EdgeWebView", "Application", "*", "msedgewebview2.exe")):
            return True
    return False


def main():
    """启动 SSH Web Tool 服务（pywebview 桌面窗口版）"""
    # PyInstaller 打包后多进程支持
    if hasattr(sys, "frozen"):
        import multiprocessing

        multiprocessing.freeze_support()

    import logging
    import threading

    import uvicorn

    from ssh_web_tool.config import (
        CONFIG_FILE_NAME,
        ensure_config_file,
        ensure_data_dir,
        get_app_dir,
        load_config,
        migrate_legacy_data,
        resolve_server_config,
    )

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

    # 单实例：已有实例在运行则退出（windowed EXE 无控制台，print 不可见，需弹窗提示）
    if not _acquire_single_instance():
        msg = "SSH Web Tool 已在运行，请勿重复启动。\n\n（如需重新启动，请先退出已运行的实例）"
        print(msg)
        if hasattr(sys, "frozen"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, "SSH Web Tool", 0x40)  # MB_ICONINFORMATION
        _exit_quietly(0)

    # WebView2 Runtime 缺失时 pywebview 会静默回退 IE 内核，页面无法渲染；提前拦截并指引安装
    if sys.platform == "win32" and not _check_webview2_runtime():
        msg = (
            "未检测到 Microsoft WebView2 Runtime，桌面窗口无法启动。\n\n"
            "请安装后重试：\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
            "（Win10/11 通常已内置，多数情况只需下载 Evergreen Bootstrapper 一键安装）"
        )
        print(msg)
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, "SSH Web Tool", 0x30)  # MB_ICONWARNING
        _exit_quietly(1)

    # 加载配置
    ensure_config_file()
    cfg = load_config()
    try:
        server = resolve_server_config(cfg)
    except (RuntimeError, ValueError) as e:
        print(f"\n启动失败：{e}")
        print(f"提示：可编辑 {CONFIG_FILE_NAME} 修改 server.port / server.host 后重启")
        if hasattr(sys, "frozen"):
            input("\n按回车键退出...")
        return

    host, port = server["host"], server["port"]
    base_url = f"http://{host}:{port}"

    # 记录实际使用的端口
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
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
        root_logger.addHandler(stream_handler)

    print("=" * 50)
    print(f"SSH Web Tool v{APP_VERSION} 启动中...")
    print(f"配置文件: {CONFIG_FILE_NAME}")
    print(f"本地服务: {base_url}")
    print(f"API 文档: {base_url}/docs")
    print("数据文件:", storage.data_file)
    print("日志目录:", SSHSession.LOG_DIR)
    print("请求日志:", server_log)
    print("=" * 50)

    # 清理超过 30 天的旧会话日志
    try:
        n = SSHSession.cleanup_old_logs(days=30)
        if n:
            print(f"[日志] 已清理 {n} 个超过 30 天的旧会话日志")
    except Exception as e:
        print(f"[日志] 清理旧日志失败: {e}")

    # 启动时后台静默检查更新（有新版才弹提示，不自动更新）
    try:
        from ssh_web_tool.updater import check_for_update_quiet

        threading.Thread(target=check_for_update_quiet, daemon=True).start()
    except Exception as e:
        print(f"[updater] 启动更新检查失败: {e}")

    # 在后台线程启动 uvicorn（FastAPI 服务）
    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": host, "port": port, "log_level": "info", "workers": 1, "log_config": None},
        daemon=True,
    )
    server_thread.start()

    # 等待服务就绪
    import urllib.request

    for _ in range(30):
        try:
            urllib.request.urlopen(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)

    # 启动 pywebview 窗口
    import webview

    # 允许网页触发下载（SFTP 下载走 <a download>，pywebview 默认禁止下载）
    # 开启后 WebView2 会弹原生"另存为"对话框
    webview.settings["ALLOW_DOWNLOADS"] = True

    # 原生剪贴板桥（pywebview js_api）：前端调用 window.pywebview.api.copy_text(text)
    # WebView2 下 navigator.clipboard.writeText 常被安全策略/焦点要求拒绝而静默失败，
    # 终端复制走这里最可靠。用 Win32 SetClipboardData 直接写系统剪贴板，无第三方依赖。

    class ClipboardApi:
        def copy_text(self, text: str) -> bool:
            """把文本写入 Windows 系统剪贴板（CF_UNICODETEXT）"""
            try:
                import ctypes

                u32 = ctypes.windll.user32
                k32 = ctypes.windll.kernel32
                CF_UNICODETEXT = 13
                GMEM_MOVEABLE = 0x0002
                # 显式 64 位签名：GlobalAlloc/GlobalLock 返回句柄 (HANDLE)，
                # ctypes 默认 restype=c_int 会截断 64 位指针导致 Operation 失败/崩溃
                vt = ctypes.c_void_p
                k32.GlobalAlloc.restype = vt
                k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
                k32.GlobalLock.restype = vt
                k32.GlobalLock.argtypes = [vt]
                k32.GlobalUnlock.argtypes = [vt]
                k32.GlobalFree.argtypes = [vt]
                u32.SetClipboardData.argtypes = [ctypes.c_uint, vt]
                u32.SetClipboardData.restype = vt
                data = text.encode("utf-16-le") + b"\x00\x00"  # 含结尾 NUL
                h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h_mem:
                    return False
                ok = False
                try:
                    ptr = k32.GlobalLock(h_mem)
                    if ptr:
                        ctypes.memmove(ptr, data, len(data))
                        k32.GlobalUnlock(h_mem)
                        if u32.OpenClipboard(None):
                            try:
                                u32.EmptyClipboard()
                                set_ok = u32.SetClipboardData(CF_UNICODETEXT, h_mem)
                                ok = bool(set_ok)
                                if ok:
                                    h_mem = None  # 系统已接管，交由系统释放
                            finally:
                                u32.CloseClipboard()
                finally:
                    if h_mem:  # 未成功（系统未接管）才手动释放，避免 double-free
                        k32.GlobalFree(h_mem)
                return ok
            except Exception:
                return False

    def on_closing():
        """窗口关闭确认"""
        import ctypes

        result = ctypes.windll.user32.MessageBoxW(
            0,
            "确定要退出 SSH Web Tool 吗？\n所有 SSH 连接将被断开。",
            "确认退出",
            0x04 | 0x30 | 0x00,  # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON1
        )
        if result == 6:  # IDYES
            # 关闭历史数据库
            try:
                asyncio.run(history_db.close_db())
            except Exception:
                pass
            # 硬杀进程：os._exit 走 C exit() 仍会等待全部 DLL 卸载（WebView2 等），
            # 实测点"是"后窗口要卡 ~2s 才消失；TerminateProcess 跳过卸载立即退出。
            # 必须显式声明 64 位类型：GetCurrentProcess 返回伪句柄 (HANDLE)-1，
            # ctypes 默认 restype=c_int 会截断成 0xFFFFFFFF，TerminateProcess
            # 收到错误句柄静默失败（ERROR_INVALID_HANDLE），表现为点退出无反应
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
            # 极端情况下终止异步生效，os._exit 兜底（正常不会走到）
            os._exit(0)
        return False  # 阻止默认关闭行为（只在确认后退出）

    window = webview.create_window(
        title=f"SSH Web Tool v{APP_VERSION}",
        url=base_url,
        width=1280,
        height=800,
        min_size=(800, 500),
        text_select=False,  # CSS 层面精细控制：标题/标签禁止选中，终端/输入框允许选中
        js_api=ClipboardApi(),
    )
    # pywebview 事件挂在 window.events 上；closing 处理器返回 False 可取消关闭
    window.events.closing += on_closing
    # debug 由配置文件决定：config.json 中设置 "debug": true 即开启（打包版也可用 F12 开发者工具），
    # false / 缺省则关闭，减少内存与 CPU 占用
    is_debug = bool(cfg.get("debug", False))
    webview.start(debug=is_debug)


if __name__ == "__main__":
    main()
