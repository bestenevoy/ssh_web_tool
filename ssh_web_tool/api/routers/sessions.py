"""会话域路由：SSH 会话创建/管理/执行/状态/历史日志"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.helpers import _sanitize_host
from ssh_web_tool.api.models import (
    CreateRawSessionRequest,
    CreateSessionFromHostRequest,
    CreateSessionRequest,
    RunCommandRequest,
    SessionRecordRequest,
)
from ssh_web_tool.api.routers.ws import _ACTIVE_WS
from ssh_web_tool.config import (
    get_connect_timeout,
    get_fallback_local_shell,
    load_config,
    validate_local_shell_value,
)
from ssh_web_tool.deps import get_event_bus, get_history_db, get_session_manager, get_ssh_session_cls, get_storage

router = APIRouter(prefix="/api", tags=["sessions"])


@router.post("/sessions")
async def api_create_session(req: CreateSessionRequest):
    """创建 SSH 会话并连接，自动启动交互式 shell（会显示在 Web UI 中）"""
    session_manager = get_session_manager()
    event_bus = get_event_bus()
    session_id = session_manager.create_session(req.host, req.port, req.username)
    session = session_manager.get_session(session_id)
    assert session is not None
    try:
        # 外层总超时兜底：目标不可达/认证卡住时不至于让前端请求无限挂起
        async with asyncio.timeout(max(30.0, get_connect_timeout() * 3)):  # type: ignore[attr-defined]
            await session.connect(password=req.password, private_key=req.private_key, passphrase=req.passphrase)
            # 不在此启动交互式 shell：shell 由前端 WebSocket 连接后启动
            # （listener 先注册再启动），否则 banner/MOTD 会广播给空监听器而丢失
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


@router.post("/sessions/raw")
async def api_create_raw_session(req: CreateRawSessionRequest):
    """从原始连接信息创建 SSH 会话并启动 shell

    本地终端拦截 ssh user:pass@host 命令后的"重连"入口：这类会话没有关联已保存
    主机，前端用会话内保存的连接信息（ssh_connected 消息带回）重新建立终端。
    """
    session_manager = get_session_manager()
    event_bus = get_event_bus()
    session_id = session_manager.create_session(req.host, req.port, req.username, terminal_name=req.terminal_name or "")
    session = session_manager.get_session(session_id)
    assert session is not None
    try:
        # 外层总超时兜底：重连目标不可达时不至于让前端请求无限挂起
        async with asyncio.timeout(max(30.0, get_connect_timeout() * 3)):  # type: ignore[attr-defined]
            await session.connect(password=req.password or None)
            # shell 由前端 WebSocket 连接后启动（保证登录 banner 不丢失）
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


@router.post("/sessions/from-host")
async def api_create_session_from_host(req: CreateSessionFromHostRequest):
    """从保存的主机配置快速创建 SSH 会话（支持多终端）"""
    session_manager = get_session_manager()
    event_bus = get_event_bus()
    storage = get_storage()
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
        async with asyncio.timeout(max(30.0, get_connect_timeout() * 3)):  # type: ignore[attr-defined]
            await session.connect(
                # 密码覆盖：重连时弹窗输入的密码优先于已保存密码（连接成功后由前端保存到主机配置）
                password=req.password if req.password else (host.get("password") or None),
                private_key=host.get("private_key") or None,
                passphrase=host.get("passphrase") or None,
            )
            # shell 由前端 WebSocket 连接后启动（保证登录 banner 不丢失）
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


@router.post("/local/session")
async def api_create_local_session(req: dict | None = None):
    """创建本机终端（cmd / powershell，WinPTY 交互式 shell，不经过 SSH）

    前端"本机终端"入口调用；会话协议与 SSH 终端完全一致（output/input/resize）。
    """
    session_manager = get_session_manager()
    event_bus = get_event_bus()
    req = req or {}
    # shell 未指定时使用配置的默认本机终端（默认终端条目与断开后共用同一配置）；
    # 取值可为短标识或本机检测到的 shell 完整路径，非法时回退默认
    default_shell = get_fallback_local_shell(load_config())
    shell = validate_local_shell_value(str(req.get("shell") or default_shell)) or default_shell
    import getpass

    if shell == "cmd":
        tname = "本机 cmd"
    elif shell == "powershell":
        tname = "本机 PowerShell"
    elif shell == "pwsh":
        tname = "本机 pwsh"
    else:
        tname = f"本机 {Path(shell).stem}"
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


@router.get("/sessions")
async def api_list_sessions():
    """列出所有活动会话"""
    return {"sessions": get_session_manager().list_sessions()}


@router.get("/sessions/active")
async def api_list_active_terminals():
    """获取所有活跃终端（用于页面重开时恢复）"""
    session_manager = get_session_manager()
    storage = get_storage()
    terminals = session_manager.get_active_terminals()
    result = []
    for s in terminals:
        host_info = None
        if s.host_id:
            host_info = storage.get_host(s.host_id)
        # 本地终端拦截 SSH 命令建立的会话（host_id 为空、无已保存主机）：把原始连接
        # 信息随活跃列表下发，前端恢复终端时保存为 ssh_conn，重连不再报"找不到该主机信息"
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
                "ssh_conn": s.get_conn_info(),
                "created_at": s.created_at,
                "last_active": s.last_active,
                "ws_active": s.session_id in _ACTIVE_WS,
            }
        )
    return {"terminals": result}


@router.get("/terminals/saved")
async def api_list_saved_terminals():
    """获取所有持久化的终端（可恢复的终端列表）"""
    storage = get_storage()
    saved = storage.list_saved_terminals()
    # 标记哪些终端当前活跃
    active_ids = {s["session_id"] for s in get_session_manager().list_sessions()}
    for t in saved:
        t["is_active"] = t["session_id"] in active_ids
    return {"terminals": saved}


@router.post("/terminals/{session_id}/restore")
async def api_restore_terminal(session_id: str):
    """恢复持久化终端（重新建立 SSH 连接，复用会话ID）"""
    session_manager = get_session_manager()
    storage = get_storage()
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


@router.delete("/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """关闭并删除会话"""
    session_manager = get_session_manager()
    event_bus = get_event_bus()
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


@router.post("/sessions/{session_id}/disconnect")
async def api_disconnect_session(session_id: str):
    """手动断开 SSH 连接并自动切回本机终端（顶部「断开」按钮）

    与连接意外断开同路径（_auto_switch_to_local → switch_to_local）：
    关闭 SSH 连接/进程，启动本地 shell，广播切换提示并通知前端更新 UI。
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.is_local():
        return {"status": "already_local"}
    await session._auto_switch_to_local("SSH 连接已断开")
    return {"status": "switched_local"}


@router.post("/sessions/{session_id}/reconnect")
async def api_reconnect_session(session_id: str, req: CreateSessionRequest):
    """手动重连：从本机 shell 切回 SSH（复用当前会话，终端历史/日志不丢）

    与本地拦截 SSH 同路径（switch_to_ssh 复用会话）；连接结果经 WebSocket 推送
    ssh_connected / 失败提示，前端更新终端类型与重连凭据，不重建终端实例。
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    status = await session.reconnect_ssh(
        req.host, req.port, req.username, req.password, req.private_key, req.passphrase
    )
    if status == "failed":
        # 连接失败：本地 shell 保持可用（switch_to_ssh 已恢复），前端提示失败原因
        raise HTTPException(status_code=400, detail="重连失败，请检查主机/凭据后重试")
    return {"status": status}


@router.post("/sessions/{session_id}/run")
async def api_run_command(session_id: str, req: RunCommandRequest):
    """执行命令
    - 默认（process=False）：如果会话有交互式终端，将命令注入到终端 stdin，
      命令和输出实时显示在 Web 终端中，同时捕获输出返回给调用方。
      不发送任何标记命令，适用于任何交互程序（shell/Python/MySQL/vim）。
      捕获策略：连续2秒无新输出即认为完成，总超时30秒。
      如果没有交互式终端，则回退到独立进程模式。
    - process=True：强制独立进程执行，返回 stdout/stderr，不显示在 Web 终端。
    """
    session_manager = get_session_manager()
    event_bus = get_event_bus()
    history_db = get_history_db()
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


@router.get("/sessions/{session_id}/state")
async def api_get_session_state(session_id: str):
    """获取终端当前状态（前台进程、是否在Python/MySQL/分页器、最后一行等）"""
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not session.has_shell:
        return {"state": "no_shell", "message": "该会话没有交互式终端，请先在 Web 端打开"}
    state = session.get_terminal_state()
    state["session_id"] = session_id
    state["connected"] = session.is_connected
    return state


@router.post("/sessions/states")
async def api_get_session_states(session_ids: list[str]):
    """批量获取多个终端状态（合并为单一 HTTP 请求，减少轮询开销）

    优化：原前端每个终端每 3 秒单独轮询 /api/sessions/{id}/state，
    5 个终端 = 每秒约 1.7 次 HTTP 请求；改为一次 POST 批量获取。
    """
    session_manager = get_session_manager()
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


@router.get("/sessions/{session_id}/cwd")
async def api_get_session_cwd(session_id: str):
    """获取 SSH 会话当前目录（cd 命令跟踪；SFTP 打开时定位初始目录）

    cd 跟踪为 None（未跟踪到有效路径）时，优先用连接后种入的登录 home（_home_dir，
    来自 SFTP getcwd，协议上恒等于登录 home）；尚未种上则现场取一次 getcwd；
    仍失败返回 None，由前端回退默认目录。
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    cwd = getattr(session, "current_dir", None)
    if cwd is None and session.is_connected and not session.is_local():
        cwd = getattr(session, "_home_dir", None)
    if cwd is None and session.is_connected and not session.is_local():
        try:
            sftp = await session.get_sftp()
            # 注意：asyncssh 2.24 的 SFTPClient 没有 normalize()，用 getcwd()
            home = await sftp.getcwd()
            if home:
                cwd = home
        except Exception:
            pass  # 取不到保持 None，前端走默认目录回退
    return {"cwd": cwd}


@router.get("/sessions/{session_id}/logs")
async def api_get_session_logs(session_id: str, offset: int = 0, limit: int = 2000):
    """获取终端历史日志（分页加载，offset 从最近开始倒数）"""
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    logs = session.get_history_logs(offset=offset, limit=limit)
    return {"session_id": session_id, "offset": offset, "limit": limit, "content": logs}


@router.get("/sessions/{session_id}/record")
async def api_get_session_record(session_id: str):
    """获取当前会话日志记录开关状态"""
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_id": session_id, "recording": session.is_logging()}


@router.post("/sessions/{session_id}/record")
async def api_set_session_record(session_id: str, req: SessionRecordRequest):
    """开启/暂停当前会话日志记录（默认不记录；开启时可指定保存目录，省略用默认目录）"""
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        session.set_logging(req.enabled, req.log_dir)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"日志保存目录不可用: {e}")
    return {"session_id": session_id, "recording": session.is_logging()}


@router.get("/logs/{session_id}")
async def api_get_session_logs_by_file(session_id: str, offset: int = 0, limit: int = 200000):
    """按会话ID从日志文件读取完整历史（会话删除后仍可读，用于重连保留历史）

    - 会话还在：优先走 get_history_logs（含未 flush 缓冲）
    - 会话已删除（如手动断开）：按日志文件名匹配（文件名含 session_id），从文件读，
      保证重连时旧会话的 banner/命令输出仍能回放到新终端（用户要求：重连不清空）
    """
    session_manager = get_session_manager()
    ssh_session_cls = get_ssh_session_cls()
    session = session_manager.get_session(session_id)
    if session:
        return {"session_id": session_id, "content": session.get_history_logs(offset=offset, limit=limit)}
    log_dir = Path(ssh_session_cls.LOG_DIR)
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
        return {"session_id": session_id, "content": ssh_session_cls._format_history_logs(content)}
    except Exception as e:
        print(f"[logs] 读取日志文件失败 {f}: {e}")
        return {"session_id": session_id, "content": ""}
