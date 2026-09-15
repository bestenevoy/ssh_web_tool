"""WebSocket 域路由：交互式终端 / 事件订阅（均为长连接）"""

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ssh_web_tool.api.helpers import _parse_ssh_command
from ssh_web_tool.api.ws_common import serve_messages
from ssh_web_tool.deps import get_event_bus, get_session_manager
from ssh_web_tool.ws_protocol import (
    CLIENT_INPUT,
    CLIENT_PING,
    CLIENT_RESIZE,
    SERVER_CLOSED,
    SERVER_ERROR,
    SERVER_INFO,
    SERVER_OUTPUT,
    SERVER_PONG,
    SERVER_SWITCHED_TO_LOCAL,
)

router = APIRouter(prefix="", tags=["websocket"])


async def _await_first_resize(websocket: WebSocket) -> tuple[int, int, str | None]:
    """等待前端发来的首个消息（最多 500ms）：resize 则取尺寸，否则原文返回待处理。

    返回 (cols, rows, pending_raw)：pending_raw 是收到的非 resize 消息原文，
    超时未收到消息则为 None。默认尺寸 120x40 与 PTY 创建值一致。
    """
    try:
        async with asyncio.timeout(0.5):  # type: ignore[attr-defined]
            raw = await websocket.receive_text()
            try:
                first_msg = json.loads(raw)
                if first_msg.get("type") == CLIENT_RESIZE:
                    return first_msg.get("cols", 120), first_msg.get("rows", 40), None
                return 120, 40, raw
            except json.JSONDecodeError:
                return 120, 40, raw
    except asyncio.TimeoutError:
        return 120, 40, None


@router.websocket("/ws/ssh/{session_id}")
async def websocket_ssh(websocket: WebSocket, session_id: str):
    """WebSocket 交互式终端（后端统一维护连接，前端断开不影响）
    输出通过广播机制：sessions.py 统一读 stdout，广播给 WebSocket 和 CLI 注入捕获
    """
    session_manager = get_session_manager()
    await websocket.accept()

    session = session_manager.get_session(session_id)
    if not session:
        await websocket.send_json({"type": SERVER_ERROR, "data": "会话不存在"})
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
                    await websocket.send_json({"type": SERVER_INFO, "data": notice})
                # 会话级切换通知：SSH 退出/断开后自动切换到本机 shell，
                # 通知前端更新终端类型为 local（隐藏断开按钮、更新标签等）
                switch = session.take_switch_notice()
                if switch:
                    label = "PowerShell" if switch in ("powershell", "pwsh") else "cmd"
                    await websocket.send_json(
                        {
                            "type": SERVER_SWITCHED_TO_LOCAL,
                            "shell": switch,
                            "terminal_name": f"本机 {label}",
                        }
                    )
                # 会话关闭通知（本机终端 exit 等）：推送 closed 后结束输出转发，
                # 前端收到后关闭标签与连接
                closed = session.take_closed_notice()
                if closed:
                    await websocket.send_json({"type": SERVER_CLOSED, "data": closed})
                    return
                # 带超时等待输出：本机 shell exit 后可能不再有输出，若无超时，
                # 循环会永久阻塞在 listener.get() 上，closed 通知永远送不出去
                try:
                    data = await asyncio.wait_for(listener.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                await websocket.send_json({"type": SERVER_OUTPUT, "data": data})
        except Exception:
            pass

    read_task = asyncio.create_task(read_output())

    # 全局连接监控由 SessionManager 统一管理（start_global_monitor），
    # 不为每个 WebSocket 连接创建独立监控协程
    session_manager.start_global_monitor()

    # 检测 SSH 连接是否真的活着，如果断开了自动重连（本地 shell 会话直接跳过）
    if session.is_local():
        pass
    elif not session.is_alive():
        # SSH 连接已断开（页面重开恢复时命中）：不再自动重连（需求：断开后手动重连），
        # 直接切换到本机 shell，由用户点击「重连」按钮手动恢复
        await websocket.send_json({"type": SERVER_INFO, "data": "检测到连接断开，已切换本机终端"})
        try:
            await session.auto_switch_to_local("SSH 连接断开")
        except Exception as e:
            await websocket.send_json({"type": SERVER_ERROR, "data": f"切换本机 shell 失败: {e!s}"})
            await websocket.close()
            return

    # 如果会话已有交互式终端（页面重开恢复），直接复用 process
    # 注意：必须验证 shell 真实存活（channel/进程未关闭），否则 transport 还活着但
    # channel 已死（远端 shell 被 kill/网络异常）时会误报"已恢复"，实际无法输入
    # 不发送"已恢复已有终端会话"提示（用户反馈无意义；恢复内容由前端历史输出直接体现）
    pending_msg = None  # 第一个消息不是 resize 时保存下来，在消息循环中处理
    if session.is_local() and session.is_shell_alive():
        # 本机 shell 已就绪：等待前端 resize，用正确尺寸调整 PTY
        # 原因：PTY 在 API 调用时以默认 120x40 创建，但前端 xterm 实际尺寸可能不同，
        # 尺寸不一致会导致 cmd 的绝对光标定位序列（\x1b[NG）在 xterm 中错位；
        # 若尺寸有变化，额外发送 Ctrl+L 让 cmd 用新尺寸重绘提示符（光标位置正确）
        cols, rows, pending_msg = await _await_first_resize(websocket)
        if pending_msg is None:
            size_changed = session.update_local_size(cols, rows)
            if size_changed:
                await session.write_local("\x0c")
    elif session.has_shell and session.is_shell_alive():
        pass
    elif session.is_local():
        # 本机 shell 已退出：重启（沿用原 shell 类型与最近尺寸）
        await websocket.send_json({"type": SERVER_INFO, "data": "检测到本机 shell 已退出，正在重新启动..."})
        await session.restart_local_shell()
    elif session.has_shell:
        # shell 标志在但实际已死：重启 shell（恢复场景无运行中程序，安全）
        await websocket.send_json({"type": SERVER_INFO, "data": "检测到终端已断开，正在重新启动 shell..."})
        await session.restart_shell()
    else:
        # shell 未启动，需要启动
        if session.shell_flag_stale():
            await websocket.send_json({"type": SERVER_INFO, "data": "检测到终端已断开，正在重新启动 shell..."})

        # 关键修复：在启动 shell 前先等待前端的第一个 resize 消息（最多 500ms）
        # 因为 start_interactive_shell 在消息循环开始前调用，如果在里面等待 resize 事件会导致死锁
        # 所以在这里先接收第一个 resize 消息，获取正确的终端尺寸，再启动 shell
        initial_cols, initial_rows, pending_msg = await _await_first_resize(websocket)

        try:
            await session.start_interactive_shell(cols=initial_cols, rows=initial_rows)
        except Exception as e:
            await websocket.send_json({"type": SERVER_ERROR, "data": f"启动 shell 失败: {e!s}"})
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
        if msg_type == CLIENT_INPUT:
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
                            await session.broadcast_output(
                                f"\r\n\x1b[36m[正在连接 {ssh_user}@{ssh_host}:{ssh_port} ...]\x1b[0m\r\n"
                            )
                            # 拦截时回车未送达本地 shell，shell 自身历史不会有这条
                            # 命令：补写进 PSReadLine 历史文件（cmd 无历史文件，空操作）
                            session.record_intercepted_ssh_command(line)
                            # 后台任务执行连接：不阻塞 WebSocket 消息循环。此前同步等待时
                            # 连接最长卡 30s，期间界面无响应、无法 Ctrl+C 取消，观感"卡死"。
                            # 现在连接期间可随时 Ctrl+C 取消、继续输入/操作其他界面。
                            session.begin_ssh_switch(websocket, ssh_host, ssh_port, ssh_user, ssh_pass)
                            return  # 回车已消费（连接结果由后台任务推送），不写入本地 shell
                        # 正常命令：交 PSReadLine 采集器补记 Tab 补全/预测后的最终命令
                        session.note_local_command(line)
                    # Ctrl+C：取消正在进行的 SSH 连接（"正在连接..."时按 Ctrl+C 中止）
                    if data == "\x03" and session.cancel_ssh_switch():
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
                            session.schedule_auto_switch_local("SSH 连接断开")
                            raise RuntimeError("SSH 通道已关闭，正在切换本机终端，请稍候再输入")
                        stdin.write(data)
                        session.last_active = time.time()
                except Exception as e:
                    # 不再自动重连：重连会中断正在运行的全屏程序（如 vi/vim），
                    # 且用户已确认不想要自动重连行为；只提示错误，让用户手动处理
                    print(f"[WebSocket] 输入失败: {e}")
                    await websocket.send_json({"type": SERVER_ERROR, "data": f"终端输入失败: {e}"})
        elif msg_type == CLIENT_RESIZE:
            cols = msg.get("cols", 120)
            rows = msg.get("rows", 40)
            if session.is_local():
                session.resize_local(cols, rows)
            else:
                await session.resize_pty(cols, rows)
        elif msg_type == CLIENT_PING:
            await websocket.send_json({"type": SERVER_PONG})

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
            await websocket.send_json({"type": SERVER_ERROR, "data": str(e)})
        except Exception:
            pass
    finally:
        read_task.cancel()
        try:
            await read_task
        except (asyncio.CancelledError, Exception):
            pass
        # 移除监听器，但不停止输出读取器（可能有其他监听器如 CLI 注入捕获）
        session.remove_output_listener(listener)
        # 注意：不关闭 session，由后端统一维护，24小时无活动自动清理


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket 事件订阅 - 前端通过此连接实时观察所有操作"""
    event_bus = get_event_bus()
    await websocket.accept()
    await event_bus.subscribe(websocket)
    # 客户端消息仅 ping 保活，收发样板统一走 serve_messages
    await serve_messages(websocket, on_disconnect=lambda: event_bus.unsubscribe(websocket))
