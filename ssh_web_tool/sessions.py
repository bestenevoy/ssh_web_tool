"""
SSH 会话池管理
- 统一创建、持有、销毁所有 SSH 连接
- 每个会话有唯一 session_id
- 支持交互式 pty shell（供网页 WebSocket 使用）
- 支持非交互命令执行（供 AI / HTTP API 调用）
"""

import asyncio
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, ClassVar

import asyncssh
from fastapi import WebSocket

from . import prompt_detect
from .echo_parser import EchoParser
from .output_bus import OutputBus
from .session_log import (
    SessionLog,
    build_log_file,
    resolve_existing_log,
)
from .ws_protocol import SERVER_SSH_CONNECTED

# 供 asyncssh.connect 劫持（patch_asyncssh）识别"工具内部连接"的标记：
# 工具自身创建会话时置位，劫持层看到标记则直接放行，避免把工具自己的连接
# 重复注册为镜像会话。
_patch_guard = threading.local()

# 输入流中的 ANSI 转义序列（CSI / OSC），拼行前剥离（方向键、PSReadLine 行编辑序列等）
_ANSI_SEQ_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b.")


class _DirectPtyWrapper:
    """直接包装底层 PTY 对象，提供与 PtyProcess 兼容的接口

    绕过 PtyProcess 的 socket 转发层（_read_in_thread），直接使用 PTY 对象的
    read/write/isalive/set_size 方法。read 由 _start_local_reader 中的轮询线程
    直接调用 pty.read(blocking=False)，不经过此 wrapper。
    """

    def __init__(self, pty_obj: Any):
        self.pty = pty_obj
        self.pid = pty_obj.pid

    def write(self, data: str) -> int:
        """写入数据到 PTY"""
        return self.pty.write(data)

    def isalive(self) -> bool:
        """检查进程是否存活"""
        try:
            return bool(self.pty.isalive())
        except Exception:
            return False

    def setwinsize(self, rows: int, cols: int) -> None:
        """调整终端尺寸（PTY.set_size 参数顺序为 cols, rows）"""
        try:
            self.pty.set_size(cols, rows)
        except Exception:
            pass

    def terminate(self, force: bool = False) -> None:
        """终止进程（与 PtyProcess.terminate 逻辑一致）"""
        if not self.isalive():
            return
        import signal

        try:
            os.kill(self.pid, signal.SIGINT)
        except Exception:
            pass
        try:
            self.pty.cancel_io()
        except Exception:
            pass
        time.sleep(0.1)
        if not self.isalive():
            return
        if force:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                pass
            time.sleep(0.1)

    def get_exitstatus(self) -> int | None:
        """获取退出状态"""
        try:
            return self.pty.get_exitstatus()
        except Exception:
            return None


def _send_ctrl_c_to_console(child_pid: int) -> bool:
    """向本地 shell 的控制台发送真实 Ctrl+C（winpty issue #116 的修复方式）

    WinPTY agent 会把写入的 \x03 转成 GenerateConsoleCtrlEvent，但该事件
    不会打断 cmd/PowerShell 的 ReadConsole（等待输入时无 ^C 回显、不清除
    当前行、不出现新提示符）。正确做法是向控制台窗口发送 WM_KEYDOWN/WM_KEYUP
    的 Ctrl+C 键事件（与 winpty 内部 sendKeyMessage 相同），可打断 ReadConsole
    并触发 shell 输出 ^C + 新提示符。

    - 本进程需临时 AttachConsole 到子进程的控制台以取得其窗口句柄；
      失败（如无控制台权限的环境）返回 False，调用方回退写 \x03。
    - 使用 SendMessageTimeoutW + SMTO_ABORTIFHUNG，避免控制台窗口无响应时挂死。
    - 全程 try/except 包裹，任何失败都不抛异常（Ctrl+C 处理必须无副作用）。
    """
    try:
        import ctypes
    except Exception:
        return False
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
    VK_CONTROL = 0x11
    SMTO_ABORTIFHUNG = 0x2
    if child_pid <= 0:
        return False
    had_console = bool(kernel32.GetConsoleWindow())
    if had_console and not kernel32.FreeConsole():
        return False
    attached = False
    ignored_ctrl_c = False
    try:
        if not kernel32.AttachConsole(child_pid):
            return False
        attached = True
        # 本进程已临时加入子控制台进程组：注入的 Ctrl+C 键事件会以
        # CTRL_C_EVENT 控制事件广播给组内所有进程（含本进程），
        # 若不加忽略处理器，python 收到 KeyboardInterrupt 会退出整个应用。
        # 忽略只影响本进程，cmd/PowerShell 的 ReadConsole 中断不受影响。
        ignored_ctrl_c = bool(kernel32.SetConsoleCtrlHandler(None, True))
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return False

        def _send(vk: int, down: bool) -> None:
            scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
            lparam = (scan << 16) | 1 | (0 if down else 0xC0000000)
            msg = WM_KEYDOWN if down else WM_KEYUP
            result = ctypes.c_ulong()
            user32.SendMessageTimeoutW(hwnd, msg, vk, lparam, SMTO_ABORTIFHUNG, 2000, ctypes.byref(result))

        _send(VK_CONTROL, True)
        _send(ord("C"), True)
        _send(ord("C"), False)
        _send(VK_CONTROL, False)
        return True
    except Exception:
        return False
    finally:
        if attached:
            kernel32.FreeConsole()
        if ignored_ctrl_c:
            # 已脱离控制台进程组后再恢复 Ctrl+C 处理器（避免残余事件）
            kernel32.SetConsoleCtrlHandler(None, False)
        if had_console:
            # 恢复自己的控制台（开发模式从终端启动时有控制台）
            kernel32.AttachConsole(0xFFFFFFFF)  # ATTACH_PARENT_PROCESS


def get_data_dir() -> str:
    """获取数据文件目录（统一在 ~/.ai4one/wstool）"""
    from .config import get_app_dir

    return str(get_app_dir())


def _fallback_shell() -> str:
    """读取"SSH 退出后切换本机终端"的 shell 配置（cmd / powershell / pwsh）"""
    from .config import get_fallback_local_shell

    return get_fallback_local_shell()


class SSHSession:
    """单个 SSH 会话（对应一个 SSH 连接 + 可选的交互式终端）"""

    # 日志目录
    LOG_DIR = os.path.join(get_data_dir(), "logs")
    # 日志轮转保留天数
    LOG_BACKUP_DAYS = 7
    # 已创建的 logger 缓存（避免同一 session_id 重复创建 handler）
    _logger_cache: ClassVar[dict[str, logging.Logger]] = {}

    def __init__(
        self, session_id: str, host: str, port: int, username: str, host_id: str = "", terminal_name: str = ""
    ):
        self.session_id = session_id
        self.host = host
        self.port = port
        self.username = username
        self.host_id = host_id  # 关联的保存主机 ID
        self.terminal_name = terminal_name  # 终端名称（如 "终端1"、"终端2"）
        self.conn: asyncssh.SSHClientConnection | None = None
        self.process: asyncssh.SSHClientProcess | None = None
        self._sftp = None
        self.created_at = time.time()
        self.last_active = time.time()
        self._connected = False
        self._has_shell = False  # 是否启动了交互式终端
        # 用于非交互命令执行的锁，防止并发冲突
        self._cmd_lock = asyncio.Lock()
        # 组件装配（职责分解：广播 OutputBus / 回显解析 EchoParser / 日志 SessionLog
        # 各自独立实现，本类只做编排，见对应组件模块）
        self._output_bus = OutputBus(prompt_detect.clean_ansi)
        self._echo_parser = EchoParser()
        # 日志持久化：同一会话（session_id）固定同一份日志文件
        # 命名：{host}_{start}_running_{session_id}.log，会话关闭时补全结束时间：
        #       {host}_{start}_{end}_{session_id}.log
        # 恢复已有会话时沿用旧文件（同一终端 = 同一份记录）
        log_file = resolve_existing_log(self.session_id, self.LOG_DIR) or build_log_file(
            self.session_id, self.host, self.created_at, self.LOG_DIR
        )
        self._logger = self._get_or_create_logger(session_id, log_file)
        self._session_log = SessionLog(session_id, log_file, self._logger, self.LOG_FLUSH_DELAY, self.LOG_FLUSH_MAX)
        self._reader_task: asyncio.Task | None = None
        # 本地 shell（WinPTY 后端，本机 cmd/powershell；SSH 断开后可自动切换）
        self._local_proc: Any = None  # winpty.PtyProcess（backend=1 WinPTY，动态导入，类型标注为 Any）
        # 外部镜像会话属性（paramiko/asyncssh monkey-patch 注册时动态设置）
        self._external: bool = False
        self._paramiko_client: Any = None
        self._asyncssh_conn: Any = None
        self._local_shell = ""
        self._local_reader_task: asyncio.Task | None = None
        # 本地终端输入行缓冲：xterm 前端逐字符发送输入（回车是单独一条 "\r" 消息），
        # 服务端把可打印字符累积成整行，回车时交由 SSH 命令拦截解析（feed_local_input）
        self._local_input_line = ""
        # 待处理的终端尺寸（resize 消息在 PTY 创建前到达时保存）
        self._pending_size: tuple[int, int] | None = None
        # resize 事件：PTY 创建后等待第一个 resize 消息，确保 shell 第一帧输出使用正确尺寸
        self._resize_event = asyncio.Event()
        # 自动重连相关
        self._password: str | None = None
        self._private_key: str | None = None
        self._passphrase: str | None = None
        self._last_cols = 120
        self._last_rows = 40
        self._keepalive_task: asyncio.Task | None = None
        self._reconnecting = False
        self._reconnect_count = 0
        self._max_reconnect = 5  # 最大自动重连次数
        self._switching_local = False  # 正在自动切换本机 shell（防止与自动重连并发冲突）
        self._switch_task: asyncio.Task | None = None  # 持有切换任务引用（loop 只保留弱引用）
        # 本地终端拦截 SSH 命令后建立的后台连接任务（不阻塞 WebSocket 消息循环；
        # 连接期间用户可 Ctrl+C 取消，见 main.py 的 _run_ssh_switch）
        self._ssh_switch_task: asyncio.Task | None = None
        # 会话级通知（如 shell 异常提示）：由全局监控协程写入，WebSocket read_output 循环取出推送。
        # 不直接走 _broadcast_output，避免提示文本混入 run_command 的注入捕获/echo 解析
        self._shell_notice: str | None = None
        # 会话级切换通知：SSH 退出/断开自动切换到本机 shell 时设置，
        # WebSocket read_output 循环取出后发送 switched_to_local 消息给前端，
        # 前端据此更新终端类型和 UI 状态（如隐藏断开按钮）
        self._switch_notice: str | None = None
        self._closed_notice: str | None = None  # 会话关闭通知（本机终端 exit 等）

    # ---------- 日志文件命名/生命周期（逻辑在 SessionLog 组件） ----------

    @property
    def _log_file(self) -> str:
        """当前日志文件路径（单一数据源：SessionLog.log_file，finalize 后自动反映新路径）"""
        return self._session_log.log_file

    def _resolve_existing_log(self) -> str | None:
        """恢复场景：同 session_id 已存在日志文件则沿用（同一终端同一份记录）"""
        return resolve_existing_log(self.session_id, self.LOG_DIR)

    def _build_log_file(self) -> str:
        """新会话日志文件名：{host}_{start}_running_{session_id}.log"""
        return build_log_file(self.session_id, self.host, self.created_at, self.LOG_DIR)

    def finalize_log_file(self):
        """会话关闭时补全结束时间：把 _running_ 替换为 {end}_（逻辑在 SessionLog.finalize）"""
        self._session_log.finalize()

    @classmethod
    def cleanup_old_logs(cls, days: int = 30) -> int:
        """启动时清理超过 days 天的会话日志（会话日志不再按天轮转，靠启动清理控制体积）

        返回删除的文件数（逻辑在 SessionLog.cleanup_old_logs）
        """
        return SessionLog.cleanup_old_logs(days, cls.LOG_DIR)

    def set_shell_notice(self, msg: str) -> None:
        """设置会话级通知（全局监控协程调用）"""
        self._shell_notice = msg

    def take_shell_notice(self) -> str | None:
        """取出并清空会话级通知（WebSocket read_output 循环调用，只取一次）"""
        n = self._shell_notice
        self._shell_notice = None
        return n

    def set_switch_notice(self, shell: str) -> None:
        """设置切换到本机 shell 的通知（_auto_switch_to_local 调用）"""
        self._switch_notice = shell

    def take_switch_notice(self) -> str | None:
        """取出并清空切换通知（WebSocket read_output 循环调用，只取一次）"""
        n = self._switch_notice
        self._switch_notice = None
        return n

    def set_closed_notice(self, msg: str) -> None:
        """设置会话关闭通知（WebSocket 输出循环读取后推送 closed 消息）"""
        self._closed_notice = msg

    def take_closed_notice(self) -> str | None:
        """取出并清空会话关闭通知（read_output 循环调用，只取一次）"""
        n = self._closed_notice
        self._closed_notice = None
        return n

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def has_shell(self) -> bool:
        return self._has_shell and (self.process is not None or self._local_proc is not None)

    # TCP 建连超时：目标主机不可达时（SYN 无响应/防火墙丢弃）asyncssh 默认会
    # 重试很久，导致 WebSocket 消息循环被 await 阻塞、整窗卡死无法操作。
    # connect_timeout 限制 TCP 建连；认证阶段另有 main.py 外层总超时兜底
    CONNECT_TIMEOUT = 10.0

    async def connect(self, password: str | None = None, private_key: str | None = None, passphrase: str | None = None):
        """建立 SSH 连接（带 keepalive 防止空闲超时断开）"""
        # 保存认证信息，用于自动重连
        self._password = password
        self._private_key = private_key
        self._passphrase = passphrase

        kwargs = {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "known_hosts": None,  # 跳过主机密钥校验（本地工具简化处理）
            "keepalive_interval": 10,  # 每 10 秒发送 keepalive 包，防止空闲超时断开
            "keepalive_count_max": 2,  # 2 次 keepalive 无响应（约 20s）即判定连接断开，快速发现静默断线
            "connect_timeout": self.CONNECT_TIMEOUT,  # TCP 建连超时，防不可达主机卡死
        }
        if password:
            kwargs["password"] = password
        if private_key:
            kwargs["client_keys"] = [asyncssh.import_private_key(private_key, passphrase)]  # type: ignore[list-item]

        # 标记为工具内部连接：劫持层（patch_asyncssh）看到该标记直接放行，
        # 不会把工具自身的连接重复注册为镜像会话
        _patch_guard.active = True
        try:
            self.conn = await asyncssh.connect(**kwargs)
        finally:
            _patch_guard.active = False
        self._connected = True
        self._reconnect_count = 0  # 重连成功后重置计数
        self.last_active = time.time()
        print(f"[SSHSystem] 连接成功: {self.username}@{self.host}:{self.port} (keepalive=10s)")

    async def start_interactive_shell(self, cols: int = 120, rows: int = 40, term_type: str = "xterm-256color"):
        """启动交互式 pty shell（供网页终端使用）
        注意：初始尺寸由调用方传入（WebSocket 层先等待前端的 resize 消息），
        不要在这里等待 resize 事件，因为此时消息循环还没开始，会导致死锁。
        """
        if not self.is_connected:
            raise RuntimeError("SSH 未连接")
        # 如果有待处理的尺寸（resize 消息在 PTY 创建前到达），使用保存的尺寸
        if self._pending_size:
            cols, rows = self._pending_size
            self._pending_size = None
        print(f"[SSHSystem] start_interactive_shell: cols={cols}, rows={rows}, term_type={term_type}")
        # 保存终端尺寸，用于重连后恢复
        self._last_cols = cols
        self._last_rows = rows
        # asyncssh 的 term_size 参数顺序是 (cols, rows)
        assert self.conn is not None
        self.process = await self.conn.create_process(
            term_type=term_type,
            term_size=(cols, rows),
        )
        self._has_shell = True
        self.last_active = time.time()
        # 自动启动 SSH 输出读取器（广播给所有监听器）
        # 注意：必须直接启动 SSH 读取器而非走 start_output_reader——
        # switch_to_ssh 流程中此刻 _local_proc 尚未清空（本地 shell 在本方法
        # 返回后才终止），start_output_reader 会误判为本地 shell，
        # 导致 SSH 输出永远无人读取（连接成功但终端无任何输出/回显）
        self._start_ssh_reader()
        return self.process

    # ============ 输出广播机制（逻辑在 OutputBus 组件） ============

    def add_output_listener(self) -> asyncio.Queue:
        """注册输出监听器，返回一个 Queue，后续输出会写入这个队列
        有界队列（约 100 块 ≈ 400KB）：消费慢的监听器（前端 ws 慢）不会无限堆积内存"""
        return self._output_bus.add_listener()

    def remove_output_listener(self, q: asyncio.Queue):
        """移除输出监听器"""
        self._output_bus.remove_listener(q)

    # ============ 命令回显解析（逻辑在 EchoParser 组件，后端统一记录实际执行的命令） ============

    def _parse_echo_line(self, data: str):
        """
        从输出流解析"命令回显行"并记录到全局历史（由后端统一记录）。

        原理：交互式 shell（pty echo / readline）会把用户实际输入的命令回显在
        提示符之后（root@host:~$ cd /var），Tab 补全、历史翻查（方向键/Ctrl+R）
        后的最终内容都会体现在这行里；前端只记录键盘输入，会丢失补全内容。

        解析细节（行缓冲/提示符剥离/去重）在 EchoParser 组件，此处只做调度。
        """
        if not self._has_shell or self.process is None:
            return
        for cmd in self._echo_parser.feed(data):
            try:
                self._echo_task = asyncio.create_task(self._record_echo(cmd))
            except Exception:
                pass

    async def _record_echo(self, cmd: str):
        """异步记录回显命令（不阻塞输出广播循环）
        record_echo_command 内部：清理键盘输入版前缀残留（如 cd /va）、
        3 秒内同命令去重（前端/CLI 已即时记录过则不重复）"""
        try:
            from .history_db import record_echo_command

            await record_echo_command(cmd)
        except Exception:
            pass

    async def _broadcast_output(self, data: str):
        """广播输出给所有监听器，并维护缓冲区（用于状态检测）和日志持久化"""
        # 解析命令回显（实际执行的命令）并记录历史
        self._parse_echo_line(data)
        # 日志持久化：聚合缓冲，静默期/超阈值后清洗一次性写入
        # （不逐块写：避免每次刷新回显都落盘，只记录最终显示内容）
        self._feed_log(data)
        # 广播 + 输出/清洗缓冲区维护（有界队列，慢监听器丢最旧保最新）
        self._output_bus.broadcast(data)

    # ============ 对外公开接口（API 层统一从这里访问，不再直接摸私有属性） ============

    async def broadcast_output(self, data: str) -> None:
        """广播输出到所有监听器（对外的公开接口，内部逻辑走 _broadcast_output）"""
        await self._broadcast_output(data)

    def get_conn_info(self) -> dict[str, str | int] | None:
        """返回本会话可重连的连接信息。

        仅对"本地拦截 SSH 命令建立的会话"（无已保存主机）返回原始凭据；
        已保存主机的会话返回 None（重连走 host_id / 保存的主机配置）。
        """
        if self.host_id or not self._password:
            return None
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self._password,
        }

    def begin_ssh_switch(
        self, websocket: WebSocket, ssh_host: str, ssh_port: int, ssh_user: str, ssh_pass: str
    ) -> asyncio.Task:
        """启动"本地拦截 SSH"的后台连接/切换任务（不阻塞 WebSocket 消息循环）。

        连接期间用户 Ctrl+C 可取消（cancel_ssh_switch）；结果通过 WebSocket 推送
        ssh_connected / 错误 / 已取消 消息。返回创建的 Task（内部持有引用防 GC）。
        """
        old = self._ssh_switch_task
        if old is not None and not old.done():
            old.cancel()
        task = asyncio.create_task(self._do_ssh_switch(websocket, ssh_host, ssh_port, ssh_user, ssh_pass))
        self._ssh_switch_task = task
        return task

    def cancel_ssh_switch(self) -> bool:
        """取消进行中的本地拦截 SSH 连接（用户 Ctrl+C 时调用）；返回是否确实取消了"""
        task = self._ssh_switch_task
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def update_local_size(self, cols: int, rows: int) -> bool:
        """调整本机 shell 的 PTY 尺寸；返回尺寸是否发生变化（ws 层据此发 Ctrl+L 重绘提示符）"""
        changed = cols != self._last_cols or rows != self._last_rows
        self.resize_local(cols, rows)
        return changed

    def shell_flag_stale(self) -> bool:
        """shell 标志遗留但进程已空（恢复场景：_has_shell=True 且 process=None）。
        调用后清除标志（走"全新启动"流程），返回是否命中该场景"""
        if self._has_shell and self.process is None:
            self._has_shell = False
            return True
        return False

    async def auto_switch_to_local(self, reason: str = "SSH 连接断开") -> None:
        """立即切换到本机终端（对外公开接口，内部走幂等的 _auto_switch_to_local）"""
        await self._auto_switch_to_local(reason)

    def schedule_auto_switch_local(self, reason: str = "SSH 连接断开") -> asyncio.Task:
        """后台执行自动切换本机终端（输入时发现通道已关闭等场景），返回创建的 Task"""
        task = asyncio.create_task(self._auto_switch_to_local(reason))
        self._switch_task = task
        return task

    async def _do_ssh_switch(
        self, websocket: WebSocket, ssh_host: str, ssh_port: int, ssh_user: str, ssh_pass: str
    ) -> None:
        """后台执行"本地终端拦截 SSH"的切换（begin_ssh_switch 创建此任务）

        - 成功：发送 ssh_connected（含连接信息；前端据此保存重连凭据，重连不依赖已保存主机）
        - 失败：提示错误并恢复本地 shell（switch_to_ssh 已恢复读取）
        - 取消（用户 Ctrl+C）：提示已取消，本地 shell 保持可用
        """
        from .event_bus import event_bus

        try:
            # 总超时兜底：连接阶段最多 30s（connect 内部还有 TCP connect_timeout）
            async with asyncio.timeout(30):  # type: ignore[attr-defined]
                await self.switch_to_ssh(ssh_host, ssh_port, ssh_user, ssh_pass)
            await websocket.send_json(
                {
                    "type": SERVER_SSH_CONNECTED,
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
                f"本地终端拦截 SSH 连接 {self.session_id} -> {ssh_user}@{ssh_host}:{ssh_port}",
                session_id=self.session_id,
                host=ssh_host,
                port=ssh_port,
            )
            # 切换成功：更新会话终端名（重连/恢复页面时标签显示 root@host，而非"本机 cmd"）
            self.terminal_name = f"{ssh_user}@{ssh_host}"
        except asyncio.CancelledError:
            # 用户 Ctrl+C 取消连接：switch_to_ssh 已恢复本地 shell 读取
            try:
                await self._broadcast_output("\r\n\x1b[33m[已取消连接]\x1b[0m\r\n")
            except Exception:
                pass
        except Exception as e:
            # 连接失败：本地 shell 仍可用。发 ESC 清掉 shell 里残留的半行命令
            # （cmd 丢弃整行、PSReadLine RevertLine 清空输入），给用户干净的提示符重试
            try:
                await self.write_local("\x1b")
            except Exception:
                pass
            try:
                await self._broadcast_output(f"\r\n\x1b[31m[SSH 连接失败: {e}]\x1b[0m\r\n")
            except Exception:
                pass
        finally:
            self._ssh_switch_task = None

    @classmethod
    def _get_or_create_logger(cls, session_id: str, log_file: str | None = None) -> logging.Logger:
        """获取或创建会话专属 logger（逻辑在 SessionLog.get_or_create_logger）"""
        return SessionLog.get_or_create_logger(session_id, log_file, cls.LOG_DIR, cls._logger_cache)

    # 日志聚合写入（逻辑在 SessionLog 组件）：静默期（LOG_FLUSH_DELAY）或缓冲超阈值时一次性清洗写入
    LOG_FLUSH_DELAY = 0.6  # 秒：无新输出多久后 flush
    LOG_FLUSH_MAX = 65536  # 字符：缓冲超过该阈值立即 flush

    @property
    def _log_buf(self) -> str:
        """聚合缓冲当前内容（测试/内部读取用，写入逻辑在 SessionLog）"""
        return self._session_log.buffer

    def _flush_log_now(self):
        """把日志缓冲清洗后一次性写入（幂等；会话关闭前必须调用以落盘剩余内容，逻辑在 SessionLog）"""
        self._session_log.flush_now()

    @staticmethod
    def _collapse_cr_lines(text: str) -> str:
        """同一物理行内多次 \r 覆盖：只保留最后一次 \r 之后的内容（逻辑在 SessionLog）"""
        return SessionLog.collapse_cr_lines(text)

    def _feed_log(self, data: str):
        """输出进入日志聚合缓冲（替代逐块 _write_log，减少刷新回显碎片，逻辑在 SessionLog）"""
        self._session_log.feed(data)

    def get_history_logs(self, offset: int = 0, limit: int = 2000) -> str:
        """
        读取历史日志（分页加载）
        - offset: 从文件末尾倒数的字符偏移量（0 表示从最近开始）
        - limit: 最多返回多少字符
        返回清理后的日志文本（读取前自动落盘聚合缓冲，逻辑在 SessionLog.read_history）
        """
        return self._session_log.read_history(offset, limit)

    @staticmethod
    def _format_history_logs(content: str) -> str:
        """格式化历史日志：清理ANSI、处理回车、压缩多余空格和空行（逻辑在 SessionLog）"""
        return SessionLog.format_history_logs(content)

    async def start_output_reader(self):
        """启动输出读取器（幂等，重复调用不会重复启动）

        按当前 shell 类型分派：本地 shell（WinPTY）走 _start_local_reader
        （to_thread 读取，不阻塞事件循环）；SSH 走 _start_ssh_reader。
        """
        if self._local_proc is not None:
            self._start_local_reader()
            return
        self._start_ssh_reader()

    def _start_ssh_reader(self):
        """启动 SSH stdout 读取协程（幂等）。EOF 且 shell 存活时自动切回本机终端"""
        if self._reader_task and not self._reader_task.done():
            return
        if not self.process:
            raise RuntimeError("没有交互式终端")
        proc = self.process

        async def _reader():
            clean_eof = False
            try:
                while True:
                    data = await proc.stdout.read(4096)
                    if not data:
                        # EOF：远端 shell 已退出（用户输入 exit/logout 等）
                        clean_eof = True
                        break
                    self.last_active = time.time()
                    await self._broadcast_output(data)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            if clean_eof and self._has_shell and not self.is_local():
                # 用户退出远端 shell → 自动切回本机终端。
                # 必须用独立任务：switch_to_local 内部会 stop_output_reader 取消本协程，
                # 若在协程内直接 await，取消会在切换中途抛出 CancelledError 导致切换失败
                self._switch_task = asyncio.create_task(self._auto_switch_to_local())

        self._reader_task = asyncio.create_task(_reader())

    def stop_output_reader(self):
        """停止输出读取器"""
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

    # ============ 本地 shell（WinPTY 后端：本机 cmd / powershell） ============

    def is_local(self) -> bool:
        """当前是否运行在本地 shell（非 SSH）"""
        return self._local_proc is not None

    @property
    def switching_local(self) -> bool:
        """正在自动切换到本机 shell（输入路径据此等待状态收敛，避免写入已关闭的 SSH 通道）"""
        return self._switching_local

    async def start_local_shell(self, shell: str = "cmd", cols: int = 120, rows: int = 40, start_reader: bool = True):
        """启动本机交互式 shell（WinPTY）。SSH 断开自动切换或用户主动创建本地终端时调用

        - shell: 'cmd' / 'powershell' / 'pwsh'
        - start_reader: 是否立即启动输出读取器。HTTP API 创建本机终端时传 False，
          由后续 WebSocket 连接的 start_output_reader 统一启动，确保 listener 先注册，
          避免初始提示符输出在无 listener 时被丢弃导致前端永远等不到首包。
        - 复用现有广播/日志/监听机制，前端协议与 SSH 会话完全一致
        """
        if shell == "powershell":
            argv = ["powershell.exe", "-NoLogo"]
        elif shell == "pwsh":
            argv = ["pwsh", "-NoLogo"]
        else:
            argv = ["cmd.exe"]
        try:
            import winpty  # noqa: F401 — 仅检测可用性，实际使用 winpty._winpty.PTY
        except ImportError as e:
            # 打包后 winpty.dll 加载失败常见原因：目标机器缺 VC++ Redistributable
            raise RuntimeError(
                f"本地 shell 需要 pywinpty，当前不可用。"
                f"如果使用打包版 EXE，请安装 Microsoft Visual C++ Redistributable"
                f"（https://aka.ms/vs/17/release/vc_redist.x64.exe）后重试。"
                f"原始错误: {e}"
            )
        # 延迟 import：非 Windows/未安装时不影响主程序（本地终端功能按需可用）
        # pywinpty 枚举：Backend.ConPTY=0, Backend.WinPTY=1
        # 使用 backend=1（WinPTY 传统控制台代理）：在 Windows 11 26200 上输入回显即时、
        # 启动提示符秒出。backend=0（ConPTY）在本系统上输出通知不可靠：
        # 逐字符输入回显延迟（需下次输入才冲刷）、提示符出现需 3.5s，故不使用 ConPTY。
        #
        # 直接使用底层 PTY 对象（绕过 PtyProcess 的 socket 转发层）：
        # PtyProcess 内部启动 _read_in_thread 线程通过 socket 转发 PTY 输出，
        # 该线程会消费 PTY 数据，导致我们的直接读取竞争丢数据；
        # 且 socket 转发有不可控的缓冲延迟（数秒）。
        # 直接使用 PTY 对象的 read(blocking=False) + write() + isalive() + set_size()，
        # 完全绕过 socket 层，输出延迟 < 50ms。
        from winpty._winpty import PTY

        pty_obj = PTY(cols, rows, backend=1)
        # 构建环境变量字符串（name=value\0name=value\0...\0）
        env_str = "\0".join(f"{k}={v}" for k, v in os.environ.items()) + "\0"
        # PTY.spawn 需要完整路径（不像 PtyProcess.spawn 会搜索 PATH）
        import shutil

        appname = shutil.which(argv[0]) or argv[0]
        cmdline = " ".join(argv[1:]) if len(argv) > 1 else None
        pty_obj.spawn(appname, cmdline=cmdline, cwd=os.getcwd(), env=env_str)
        # 包装为类 PtyProcess 接口的对象（write/isalive/setwinsize/terminate/get_exitstatus）
        proc = _DirectPtyWrapper(pty_obj)
        self._local_proc = proc
        self._local_shell = shell
        self._last_cols = cols
        self._last_rows = rows
        self._connected = True
        self._has_shell = True
        self.last_active = time.time()
        # 与 SSH shell 共用输出广播/日志/监听（输出循环由 stop_local_reader 管理）
        # start_reader=False 时由调用方后续通过 start_output_reader 统一启动
        if start_reader:
            self._start_local_reader()
        return proc

    def _start_local_reader(self):
        """本地 shell 输出读取协程

        直接使用底层 PTY 对象的非阻塞读取（blocking=False），绕过 PtyProcess 的
        socket 转发层（_read_in_thread → socket → recv），该层在 asyncio.to_thread
        中有不可控的缓冲延迟（read() 阻塞数秒才返回）。
        通过独立线程轮询 PTY.read(blocking=False)，有数据时通过 loop.call_soon_threadsafe
        投递到 asyncio.Queue，协程端异步消费并广播。
        """
        if self._local_reader_task and not self._local_reader_task.done():
            return

        pty_obj = getattr(self._local_proc, "pty", None)
        loop = asyncio.get_running_loop()

        if pty_obj is None:
            # 回退路径：直接用 PtyProcess.read（socket 转发，有延迟但兼容旧版 pywinpty）
            async def _reader():
                try:
                    while True:
                        try:
                            data = await asyncio.to_thread(self._local_proc.read, 4096)
                        except EOFError:
                            break
                        if not data:
                            break
                        self.last_active = time.time()
                        await self._broadcast_output(data)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    import traceback

                    print(f"[local-shell] reader 异常: {traceback.format_exc()}")

            self._local_reader_task = asyncio.create_task(_reader())
            return

        # 主路径：独立线程轮询 PTY.read(blocking=False)，实时推送输出
        import threading

        read_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        _stop_flag = threading.Event()
        self._local_poll_stop = _stop_flag  # 保存引用以便 stop_local_reader 能停止线程

        def _poll_pty():
            """轮询 PTY 非阻塞读取，有数据时投递到 asyncio Queue"""
            import os as _os

            _dbg = _os.environ.get("WST_LOCAL_DEBUG")
            _idle_ticks = 0
            while not _stop_flag.is_set():
                try:
                    data = pty_obj.read(blocking=False)
                    if data:
                        if _dbg:
                            print(f"  [poll] got {len(data)} bytes", flush=True)
                        loop.call_soon_threadsafe(_safe_put, read_queue, data)
                    else:
                        _idle_ticks += 1
                        # 每 ~1s 主动探测进程存活：cmd 正常 exit 时 read 不一定抛异常
                        # （winpty agent 可能不立即关闭管道），不探测会永远空转
                        if _idle_ticks % 200 == 0:
                            try:
                                if not pty_obj.isalive():
                                    loop.call_soon_threadsafe(_safe_put, read_queue, None)
                                    break
                            except Exception:
                                pass
                        _stop_flag.wait(0.005)  # 5ms 轮询间隔
                except Exception as e:
                    if _dbg:
                        print(f"  [poll] error: {e}", flush=True)
                    # PTY 关闭/错误：投递 None 通知协程退出
                    try:
                        loop.call_soon_threadsafe(_safe_put, read_queue, None)
                    except Exception:
                        pass
                    break

        def _safe_put(queue: asyncio.Queue, item: str | None):
            """安全放入队列（满时丢弃旧数据避免阻塞事件循环）"""
            try:
                if queue.qsize() >= 256:
                    queue.get_nowait()  # 丢弃最旧的数据
                queue.put_nowait(item)
            except Exception:
                pass

        async def _reader():
            thread = threading.Thread(target=_poll_pty, daemon=True)
            thread.start()
            try:
                while True:
                    data = await read_queue.get()
                    if data is None:
                        # PTY 已关闭（本机 shell 退出）：立即设置关闭通知，
                        # 前端收到后关闭标签；会话移除由全局监控兜底
                        if self.is_local() and not self.is_shell_alive():
                            self.set_closed_notice("本机终端已退出")
                        break  # PTY 关闭
                    if not data:
                        continue
                    self.last_active = time.time()
                    await self._broadcast_output(data)
            except asyncio.CancelledError:
                _stop_flag.set()
            except Exception:
                import traceback

                print(f"[local-shell] reader 异常: {traceback.format_exc()}")
            finally:
                _stop_flag.set()

        self._local_reader_task = asyncio.create_task(_reader())

    def stop_local_reader(self):
        """停止本地 shell 输出读取器"""
        # 停止轮询线程
        poll_stop = getattr(self, "_local_poll_stop", None)
        if poll_stop is not None:
            poll_stop.set()
        if self._local_reader_task:
            self._local_reader_task.cancel()
            self._local_reader_task = None

    async def write_local(self, data: str):
        """向前台本地 shell 写入输入（转码为 str；WinPTY 期望 str）"""
        if self._local_proc is not None:
            try:
                # Ctrl+C：WinPTY 写入 \x03 只会转成 GenerateConsoleCtrlEvent，无法打断
                # cmd/PSReadLine 的 ReadConsole（无 ^C 回显、不清除当前行、无新提示符）。
                # 优先向控制台窗口发送真实 Ctrl+C 键事件（winpty issue #116 修复方式）；
                # 失败（如无控制台权限）则回退写入 \x03（agent 仍会触发运行中程序的中断）
                if "\x03" in data and _send_ctrl_c_to_console(getattr(self._local_proc, "pid", 0)):
                    data = data.replace("\x03", "")
                # 回退路径：\x03 写入时给 PSReadLine 的输入缓冲残留一个字面量 'c'，
                # 补退格吃掉残留字符；cmd 无此问题
                if "\x03" in data and self._local_shell in ("powershell", "pwsh"):
                    data = data.replace("\x03", "\x03\b")
                # cmd.exe 不识别 Ctrl+L（\x0c 换页符），会原样回显 ^L；
                # 转译为 cls 清屏命令（前端重同步/后端 resize 重绘都会发 \x0c）
                if "\x0c" in data and self._local_shell == "cmd":
                    data = data.replace("\x0c", "cls\r")
                if data:
                    self._local_proc.write(data)
                    self.last_active = time.time()
            except Exception:
                pass

    def feed_local_input(self, data: str) -> str | None:
        """累积本地终端键盘输入，回车时返回整行（供 SSH 命令拦截解析）

        xterm 前端逐字符发送 input 消息，回车是单独的 "\\r"，
        服务端必须自行拼行才能在回车时拿到完整命令。

        - 可打印字符（含中文）追加到行缓冲
        - 退格（\\b / \\x7f）删一个字符，Ctrl+C / Ctrl+U 清空当前行
        - 回车（\\r / \\n）返回拼好的整行并清空缓冲；空行返回 None
        - 方向键历史等 ANSI 序列已由调用方剥离；历史翻查的命令行内容服务端
          无法感知（缓冲只反映逐键输入），这类行解析不到属于预期行为

        返回整行（未 strip，由解析函数处理）或 None（尚未成行）
        """
        # 剥离 ANSI 转义序列（方向键/PSReadLine 重绘序列会随输入一起到达）
        data = _ANSI_SEQ_RE.sub("", data)
        for ch in data:
            if ch in ("\r", "\n"):
                line, self._local_input_line = self._local_input_line, ""
                if line.strip():
                    return line
                continue
            if ch in ("\x7f", "\b"):
                self._local_input_line = self._local_input_line[:-1]
                continue
            if ch in ("\x03", "\x15"):  # Ctrl+C / Ctrl+U：清空当前行
                self._local_input_line = ""
                continue
            if ord(ch) < 0x20 or ch == "\x7f":
                continue  # 其余控制字符（Tab/ESC 单字节等）不参与拼行
            self._local_input_line += ch
        # 防御：异常情况下缓冲无界增长（如程序不回车狂刷输入）
        if len(self._local_input_line) > 4096:
            self._local_input_line = self._local_input_line[-4096:]
        return None

    def reset_local_input_line(self):
        """清空本地输入行缓冲（切换/重启 shell 时调用，避免残留半个命令）"""
        self._local_input_line = ""

    def resize_local(self, cols: int, rows: int):
        """调整本地 shell 窗口尺寸（setwinsize 参数顺序 (rows, cols)）"""
        if self._local_proc is not None:
            try:
                self._local_proc.setwinsize(rows, cols)
                self._last_cols = cols
                self._last_rows = rows
            except Exception:
                pass

    async def switch_to_local(self, shell: str = "cmd", cols: int = 0, rows: int = 0):
        """SSH 连接断开时切换到本机 shell（不销毁会话，前端无感知切换）

        关闭 SSH 相关资源，保留日志/广播/监听结构，随后启动本地 ConPTY shell
        """
        self._reconnecting = False
        self.stop_output_reader()
        if cols <= 0:
            cols = self._last_cols
        if rows <= 0:
            rows = self._last_rows
        if self.process:
            try:
                self.process.close()
            except Exception:
                pass
            self.process = None
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        self._connected = False
        self._has_shell = False
        self.reset_local_input_line()
        await self.start_local_shell(shell, cols, rows)

    async def switch_to_ssh(self, host: str, port: int, username: str, password: str, cols: int = 0, rows: int = 0):
        """从本地 shell 切换到 SSH 远端 shell（用户在本地终端输入 ssh user:pass@host 时触发）

        先建立 SSH 连接并启动远端 shell，成功后才关闭本地 shell（连接失败时本地终端
        保持可用，用户可直接修正后重试）。复用同一会话的广播/日志/监听结构。
        """
        if cols <= 0:
            cols = self._last_cols
        if rows <= 0:
            rows = self._last_rows
        # 停止本地输出读取（本地 shell 进程先保留，连接失败时还能继续用）
        self.stop_local_reader()
        # 更新会话连接信息（切换到远端主机）
        self.host = host
        self.port = port
        self.username = username
        try:
            # 建立 SSH 连接（失败抛异常，本地 shell 保留）
            await self.connect(password=password or None)
            await self.start_interactive_shell(cols=cols, rows=rows)
        except BaseException:
            # 连接/shell 启动失败或被取消（Ctrl+C 中止连接）：恢复本地 shell 读取
            if self._local_proc is not None and self._local_proc.isalive():
                self._start_local_reader()
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
            self._connected = False
            raise
        # SSH 就绪：关闭本地 shell，更新状态
        self.reset_local_input_line()
        if self._local_proc is not None:
            try:
                self._local_proc.terminate(force=True)
            except Exception:
                pass
            self._local_proc = None
        self._connected = True
        self._has_shell = True
        self._local_shell = ""

    async def restart_local_shell(self, cols: int = 0, rows: int = 0):
        """本机 shell 已退出时重启（沿用原 shell 类型与最近尺寸）"""
        self.stop_local_reader()
        self.reset_local_input_line()
        if self._local_proc is not None:
            try:
                self._local_proc.terminate(force=True)
            except Exception:
                pass
            self._local_proc = None
        if cols <= 0:
            cols = self._last_cols
        if rows <= 0:
            rows = self._last_rows
        await self.start_local_shell(self._local_shell or "cmd", cols, rows)

    def is_transport_alive(self) -> bool:
        """SSH 传输层是否存活（shell channel 可能已关闭：用户 exit 后连接尚未断开）"""
        if self.is_local() or getattr(self, "_external", False):
            return False
        if self.conn is None:
            return False
        try:
            t = getattr(self.conn, "_transport", None)
            if t is not None:
                return not t.is_closing()
            if hasattr(self.conn, "is_closing"):
                return not self.conn.is_closing()  # type: ignore[attr-defined]
        except Exception:
            pass
        return False

    async def _auto_switch_to_local(self, reason: str = "SSH 已退出"):
        """SSH 连接断开/退出时自动切换到本机 shell

        - 幂等：已在本机 / 正在切换 / 正在重连时直接跳过（防止与自动重连并发冲突）
        - reason: 切换原因提示（如"SSH 已退出"、"SSH 连接断开，重连失败"）
        - 切换提示走 _broadcast_output，同步写入会话日志（同一会话一份记录）
        """
        if self.is_local() or self._switching_local or self._reconnecting:
            return
        self._switching_local = True
        try:
            shell = _fallback_shell()
            label = "PowerShell" if shell in ("powershell", "pwsh") else "cmd"
            await self._broadcast_output(
                f"\r\n\x1b[33m[{reason}，已切换到本机 {label}，可在界面重新连接主机]\x1b[0m\r\n"
            )
            await self.switch_to_local(shell)
            # 通知前端：会话已切换到本机终端，前端据此更新 UI 状态
            self.set_switch_notice(shell)
        except Exception as e:
            try:
                await self._broadcast_output(f"\r\n\x1b[31m[切换本机 shell 失败: {e}]\x1b[0m\r\n")
            except Exception:
                pass
        finally:
            self._switching_local = False

    # ============ 注入命令到交互式终端 ============

    @staticmethod
    def _clean_ansi(text: str) -> str:
        """清理 ANSI 转义序列（颜色、光标移动、OSC 等；逻辑在 prompt_detect.clean_ansi）"""
        return prompt_detect.clean_ansi(text)

    # 通用提示符模式列表（逻辑在 prompt_detect 组件）
    PROMPT_PATTERNS: ClassVar[list[str]] = prompt_detect.PROMPT_PATTERNS

    @classmethod
    def _match_any_prompt(cls, text: str) -> bool:
        """检查文本是否匹配任意一种已知提示符（逻辑在 prompt_detect.match_any_prompt）"""
        return prompt_detect.match_any_prompt(text)

    @classmethod
    def _is_prompt_line(cls, line: str) -> bool:
        """检查单行是否是提示符行（逻辑在 prompt_detect.is_prompt_line）"""
        return prompt_detect.is_prompt_line(line)

    async def _inject_and_wait(self, command: str, idle_timeout: float = 0.5, total_timeout: int = 15) -> str:
        """
        注入命令到交互式终端，等待提示符出现，返回清理后的输出
        - 使用独立的输出监听器捕获新输出，不受历史输出和缓冲区清理影响
        - 多提示符检测：自动识别 shell/Python/MySQL/Redis 等提示符
        - 空闲超时兜底：连续 idle_timeout 秒无新输出即认为完成
        - 通用底层方法，供 inject_and_capture 和获取退出码复用
        - 不自动启动 shell，调用前需确保 shell 已启动
        """
        # 使用独立的输出监听器捕获新输出（不受历史输出影响）
        listener = self.add_output_listener()
        captured_parts = []
        # 增量清洗后的文本（避免每次新输出都全量拼接+清洗 captured_parts）
        cleaned_recent = ""

        try:
            # 发送命令
            assert self.process is not None
            self.process.stdin.write(f"{command}\n")
            self.last_active = time.time()

            # 等待命令完成：多提示符检测 + 空闲超时兜底
            start_time = time.time()
            last_output_time = time.time()
            has_output = False

            while time.time() - start_time < total_timeout:
                try:
                    # 非阻塞读取队列中的新输出
                    data = await asyncio.wait_for(listener.get(), timeout=0.1)
                    captured_parts.append(data)
                    has_output = True
                    last_output_time = time.time()

                    # 增量清洗：只清洗新增的 data 块，与之前结果拼接
                    # 原代码每次都 ''.join(captured_parts)[-2000:] + 全量 _clean_ansi
                    cleaned_recent += self._clean_ansi(data)
                    if len(cleaned_recent) > 2000:
                        cleaned_recent = cleaned_recent[-2000:]
                    if self._match_any_prompt(cleaned_recent):
                        await asyncio.sleep(0.15)  # 确保提示符后内容写入
                        # 读取队列中剩余的输出
                        while not listener.empty():
                            try:
                                extra = listener.get_nowait()
                                captured_parts.append(extra)
                            except Exception:
                                break
                        break
                except asyncio.TimeoutError:
                    # 0.1 秒内没有新输出，检查是否空闲超时
                    pass

                # 空闲超时：连续无新输出即认为完成（即使没有匹配到提示符）
                if has_output and (time.time() - last_output_time > idle_timeout):
                    break
                # 特殊情况：命令无输出（如 cd），发送后 1.5 秒无输出也认为完成
                if not has_output and (time.time() - start_time > 1.5):
                    break

        finally:
            # 移除监听器
            self.remove_output_listener(listener)

        # 提取并清理输出
        output = self._clean_ansi("".join(captured_parts))

        # 去掉第一行命令回显
        lines = output.split("\n")
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        # 去掉末尾的提示符行（多提示符匹配）
        while lines and self._is_prompt_line(lines[-1]):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    async def inject_and_capture(
        self,
        command: str,
        idle_timeout: float = 0.5,
        total_timeout: int = 30,
        prompt_pattern: str = r"[\w.-]+@[\w.-]+:.+[#$]\s*$",
        capture_exit_code: bool = True,
    ) -> tuple[int | None, str, str]:
        """
        注入命令到交互式终端，同时捕获输出返回
        - 命令和输出实时显示在 Web 终端中（通过广播机制）
        - 不发送任何标记命令，不干扰任何交互程序（shell/Python/MySQL/vim）
        - 完成检测：优先匹配 shell 提示符（快速准确），超时后回退到空闲超时
        - 自动获取退出码：shell 环境中执行完毕后自动执行 echo $? 获取真实退出码
        - 适用于 shell 环境中连续执行命令；非 shell 环境退出码返回 None
        返回 (退出码, stdout, stderr)，退出码为 None 表示无法获取（非 shell 环境）
        """
        # 并发安全：注入类命令与独立进程命令共用 _cmd_lock，
        # 防止多个命令同时写 stdin / 输出捕获串台（前端连点/API 并发调用）
        async with self._cmd_lock:
            if not self.has_shell or self.process is None:
                # 自动启动交互式 shell（不依赖前端页面，CLI 可独立使用）
                await self.start_interactive_shell()
                await asyncio.sleep(1.5)

            # 1. 执行命令并捕获输出
            output = await self._inject_and_wait(command, idle_timeout=idle_timeout, total_timeout=total_timeout)

            # 2. 自动获取退出码（仅在 shell 环境中）
            exit_code = None
            if capture_exit_code:
                try:
                    state = self.get_terminal_state()
                    # 仅在普通 shell 环境中获取退出码（Python/MySQL/vim 等不适用）
                    if (
                        state.get("in_shell")
                        and not state.get("in_python")
                        and not state.get("in_mysql")
                        and not state.get("in_pager")
                    ):
                        exit_output = await self._inject_and_wait("echo $?", total_timeout=10)
                        # 解析退出码（取最后一行的数字）
                        for line in reversed(exit_output.split("\n")):
                            line = line.strip()
                            if line.isdigit():
                                exit_code = int(line)
                                break
                except Exception:
                    pass  # 获取退出码失败不影响主流程

            return exit_code, output, ""

    # ============ 终端状态检测 ============

    def get_terminal_state(self) -> dict:
        """
        获取终端当前状态（基于输出缓冲区最后几行分析）
        判断是否在 Python/MySQL/分页器/普通 shell 等交互模式

        优化：使用 _broadcast_output 中增量维护的清洗缓冲区缓存，
        避免每次调用都对 8000 字符缓冲区做 3 次正则替换（组件：OutputBus）。
        """
        # 直接使用增量清洗后的缓冲区（在 OutputBus.broadcast 中维护）
        clean_text = self._output_bus.cleaned_text()

        lines = clean_text.split("\n")
        # 去掉末尾空行（输出通常以 \n 结尾，split 后最后一个元素是空字符串）
        while lines and lines[-1].strip() == "":
            lines = lines[:-1]
        last_line = lines[-1] if lines else ""
        # 取最后两行做更准确的判断
        last_two = "\n".join(lines[-2:]) if len(lines) >= 2 else last_line

        state = {
            "last_line": last_line[-120:],
            "in_python": bool(re.search(r">>>\s*$", last_line)),
            "in_mysql": bool(re.search(r"mysql>\s*$", last_line)),
            "in_pager": bool(re.search(r":\s*$", last_line)),  # less/man 分页器
            "in_shell": bool(re.search(r"[#$]\s*$", last_line)),
            "has_prompt": False,
            "foreground_process": "unknown",
        }

        # 判断是否有提示符（在等待输入）
        if state["in_shell"] or state["in_python"] or state["in_mysql"]:
            state["has_prompt"] = True

        # 推断前台程序
        if state["in_python"]:
            state["foreground_process"] = "python3"
        elif state["in_mysql"]:
            state["foreground_process"] = "mysql"
        elif state["in_pager"] and not state["in_shell"]:
            state["foreground_process"] = "less"
        elif state["in_shell"]:
            state["foreground_process"] = "bash"
        else:
            # 没有识别到提示符，可能在运行程序（如 vim、top、编译中）
            # 检查是否有常见的程序特征
            if re.search(r"-- INSERT --|-- VISUAL --|:\w*$", last_two):
                state["foreground_process"] = "vim"
            elif re.search(r"load average|Tasks:", last_two):
                state["foreground_process"] = "top"
            else:
                state["foreground_process"] = "running"

        return state

    async def resize_pty(self, cols: int, rows: int):
        """调整终端大小"""
        print(f"[SSHSystem] resize_pty: cols={cols}, rows={rows}, has_process={self.process is not None}")
        if self.process:
            try:
                # 注意：asyncssh 的 change_terminal_size 参数顺序是 (cols, rows)，不是 (rows, cols)！
                # 文档写的是 (rows, cols)，但实际行为是 (cols, rows)，传反了会导致 $COLUMNS 错误
                self.process.change_terminal_size(cols, rows)
                print(f"[SSHSystem] change_terminal_size success: cols={cols}, rows={rows}")
            except Exception as e:
                print(f"[SSHSystem] change_terminal_size failed: {e}")
        else:
            # PTY 还未创建，保存待处理的尺寸，创建时自动应用
            self._pending_size = (cols, rows)
        # 通知等待中的 start_interactive_shell：已收到 resize 消息
        self._resize_event.set()

    async def run_command(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """
        非交互执行命令（供 AI / HTTP API 调用）
        返回 (退出码, stdout, stderr)
        注意：这是在独立进程中执行，输出不会出现在 Web 交互式终端中
        """
        if not self.is_connected:
            raise RuntimeError("SSH 未连接")
        async with self._cmd_lock:
            self.last_active = time.time()
            assert self.conn is not None
            process = await self.conn.create_process(command)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                raise
            code = process.returncode if process.returncode is not None else 0
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return code, stdout or "", stderr or ""

    async def inject_command(self, command: str) -> bool:
        """
        将命令写入交互式 shell 的 stdin（命令会像手动输入一样显示在 Web 终端中）
        适用于：需要在 Web 终端中观察命令执行过程的场景
        注意：此方法只负责发送命令，不等待输出，不返回执行结果
        """
        if not self.has_shell or self.process is None:
            raise RuntimeError("该会话没有交互式终端，请先在 Web 端打开终端")
        # 并发安全：与 inject_and_capture / run_command 互斥，避免 stdin 交错
        async with self._cmd_lock:
            self.process.stdin.write(command + "\n")
            self.last_active = time.time()
        try:
            from .history_db import record_command

            await record_command(command)
        except Exception:
            pass  # 历史记录失败不影响命令执行
        return True

    # ============ SFTP 文件管理 ============

    async def get_sftp(self):
        """获取或创建 SFTP 客户端"""
        if not self.is_connected:
            raise RuntimeError("SSH 未连接")
        if self._sftp is None:
            assert self.conn is not None
            self._sftp = await self.conn.start_sftp_client()
        return self._sftp

    async def list_directory(self, path: str = "/") -> list[dict]:
        """列出目录内容

        优化：直接使用 readdir 返回的 item.attrs 字段，不再逐文件 stat。
        原 N+1 问题：readdir 已返回文件属性，却对每个文件再发一次 stat 请求，
        100 个文件 = 101 次 SFTP 往返；现在只需 1 次 readdir。
        """
        sftp = await self.get_sftp()
        try:
            items = await sftp.readdir(path)
        except Exception:
            # 如果路径不存在或无权限，返回空列表
            return []
        result = []
        for item in items:
            try:
                attrs = item.attrs
                result.append(
                    {
                        "name": item.filename,
                        "type": "dir" if (attrs.type or 0) == 2 else "file",  # 2=directory
                        "size": attrs.size or 0,
                        "mtime": attrs.mtime or 0,
                        "mode": oct(attrs.permissions) if attrs.permissions else "",
                    }
                )
            except Exception:
                result.append(
                    {
                        "name": item.filename,
                        "type": "unknown",
                        "size": 0,
                        "mtime": 0,
                        "mode": "",
                    }
                )
        # 目录排在前面，然后按名称排序
        result.sort(key=lambda x: (x["type"] != "dir", x["name"]))
        return result

    async def read_file(self, path: str, max_size: int = 10 * 1024 * 1024) -> str:
        """读取文件内容（文本，限制大小）"""
        sftp = await self.get_sftp()
        # 先检查文件大小
        try:
            stat = await sftp.stat(path)
            if stat.size and stat.size > max_size:
                raise RuntimeError(f"文件过大 ({stat.size} bytes)，超过限制 {max_size} bytes")
        except Exception as e:
            raise RuntimeError(f"无法读取文件: {e}")
        async with sftp.open(path, "r") as f:
            data = await f.read()
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    async def read_file_bytes(self, path: str, max_size: int = 100 * 1024 * 1024) -> bytes:
        """读取文件内容（二进制，用于下载）"""
        sftp = await self.get_sftp()
        try:
            stat = await sftp.stat(path)
            if stat.size and stat.size > max_size:
                raise RuntimeError(f"文件过大 ({stat.size} bytes)，超过限制 {max_size} bytes")
        except Exception as e:
            raise RuntimeError(f"无法读取文件: {e}")
        async with sftp.open(path, "rb") as f:
            data = await f.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return data

    async def write_file(self, path: str, content) -> bool:
        """写入文件内容
        - content 为 str：文本模式写入（自动 UTF-8 编码）
        - content 为 bytes：二进制模式写入（不做解码，避免二进制文件损坏）"""
        sftp = await self.get_sftp()
        if isinstance(content, bytes):
            async with sftp.open(path, "wb") as f:
                await f.write(content)
        else:
            async with sftp.open(path, "w") as f:
                await f.write(str(content))
        return True

    async def delete_file(self, path: str) -> bool:
        """删除文件或目录"""
        sftp = await self.get_sftp()
        try:
            stat = await sftp.stat(path)
            if stat.type == 2:  # directory
                await sftp.rmdir(path)
            else:
                await sftp.remove(path)
            return True
        except Exception as e:
            raise RuntimeError(f"删除失败: {e}")

    async def close_sftp(self):
        """关闭 SFTP 连接"""
        if self._sftp:
            try:
                self._sftp.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._sftp = None

    def is_alive(self) -> bool:
        """检测连接是否真的活着（不只是标志位）；本地 shell 检查 WinPTY 进程"""
        # 本地 shell（WinPTY）
        if self._local_proc is not None:
            try:
                return bool(self._local_proc.isalive())
            except Exception:
                return False
        # 外部镜像会话（paramiko/asyncssh 劫持注册）：基于外部 client 的真实状态
        if getattr(self, "_external", False):
            client = getattr(self, "_paramiko_client", None)
            if client is not None:
                try:
                    transport = client.get_transport()
                    return transport is not None and transport.is_active()
                except Exception:
                    return False
            conn = getattr(self, "_asyncssh_conn", None)
            if conn is not None:
                try:
                    if hasattr(conn, "is_closing"):
                        return not conn.is_closing()
                    return True
                except Exception:
                    return False
            return False
        if not self._connected or self.conn is None:
            return False
        try:
            # 检查连接对象是否还活着
            # 关键：asyncssh 连接断开后会把 _transport 置为 None（连接对象本身仍在）。
            # 旧代码 hasattr 为 True 但值为 None 时跳过 if 分支，elif 又因 asyncssh
            # 无 is_closing 方法而失效，断线会话被误判存活 → 监控永不切换、
            # 输入永远报"SSH 通道已关闭"。_transport 为 None 即连接已死。
            if hasattr(self.conn, "_transport"):
                transport = self.conn._transport
                if transport is None or transport.is_closing():
                    return False
            # 备用方法：检查 conn 是否有 is_closing 方法（非 asyncssh 连接类型）
            elif hasattr(self.conn, "is_closing") and self.conn.is_closing():  # type: ignore[attr-defined]
                return False
            # 补充：检查 shell channel 是否仍可用（transport 可能短暂保持但 channel 已关）
            if self._has_shell and self.process is not None:
                ch = getattr(self.process, "_chan", None)  # asyncssh 真实属性是 _chan
                if ch is not None:
                    try:
                        if ch.is_closing():
                            return False
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    def is_shell_alive(self) -> bool:
        """检测 shell 进程/channel 是否真的活着（不只是标志位）；本地 shell 检查 WinPTY"""
        if self._local_proc is not None:
            try:
                return bool(self._local_proc.isalive())
            except Exception:
                return False
        if not self._has_shell or self.process is None:
            return False
        try:
            # 0. channel 层检查：channel 已关闭则 shell 不可用
            #    注意：asyncssh 进程的真实通道属性是 _chan（旧代码用 _channel 永远取不到，
            #    断线后 _chan 标记 closing 或置空，误判 shell 存活）；通道对象不存在即已死
            ch = getattr(self.process, "_chan", None)
            if ch is not None:
                if ch.is_closing():
                    return False
            else:
                return False
            # 1. 优先检查内部进程对象是否存在
            if hasattr(self.process, "_process") and self.process._process is not None:  # type: ignore[attr-defined]
                # 检查内部进程是否已退出
                return not (
                    hasattr(self.process._process, "returncode") and self.process._process.returncode is not None  # type: ignore[attr-defined]
                )
            # 备用：检查 exit_status
            return not (hasattr(self.process, "exit_status") and self.process.exit_status is not None)
        except Exception as e:
            print(f"[SSHSystem] is_shell_alive 检测异常: {e}")
            # 检测异常时默认认为活着，避免阻止输入
            return True

    async def restart_shell(self, cols: int = 0, rows: int = 0) -> bool:
        """
        重启交互式 shell（仅用于恢复场景：页面重开/重新连接时检测到 shell 已死，
        此时用户看不到终端、没有正在运行的程序，重启是安全的）
        """
        if self.process:
            try:
                self.process.close()
            except Exception:
                pass
            self.process = None
        self._has_shell = False
        if not cols or not rows:
            cols, rows = self._last_cols, self._last_rows
        try:
            await self.start_interactive_shell(cols=cols, rows=rows)
            await self._broadcast_output("\r\n\x1b[33m[终端已重新启动]\x1b[0m\r\n")
            return True
        except Exception as e:
            print(f"[SSHSystem] 重启 shell 失败: {e}")
            await self._broadcast_output(f"\r\n\x1b[31m[终端重启失败] {e}\x1b[0m\r\n")
            return False

    async def reconnect(self) -> bool:
        """自动重连 SSH 并恢复 shell（如果之前有 shell）"""
        if self._reconnecting or self._switching_local:
            print(f"[SSHSystem] 重连/切换进行中，跳过: {self.session_id}")
            return False
        if self._reconnect_count >= self._max_reconnect:
            print(f"[SSHSystem] 已达最大重连次数 {self._max_reconnect}，停止重连: {self.session_id}")
            return False

        self._reconnecting = True
        self._reconnect_count += 1
        had_shell = self._has_shell
        cols = self._last_cols
        rows = self._last_rows

        print(
            f"[SSHSystem] 开始重连 ({self._reconnect_count}/{self._max_reconnect}): {self.username}@{self.host}:{self.port}"
        )

        try:
            # 清理旧连接
            self.stop_output_reader()
            if self.process:
                try:
                    self.process.close()
                except Exception:
                    pass
                self.process = None
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
            self._connected = False
            self._has_shell = False

            # 等待一下再重连
            await asyncio.sleep(1)

            # 重新连接
            await self.connect(password=self._password, private_key=self._private_key, passphrase=self._passphrase)

            # 如果之前有 shell，重新启动
            if had_shell:
                await self.start_interactive_shell(cols=cols, rows=rows)
                # 广播重连成功消息
                await self._broadcast_output(
                    f"\r\n\x1b[33m[连接已恢复] 重连成功 ({self._reconnect_count}次)\x1b[0m\r\n"
                )
                print(f"[SSHSystem] 重连成功，shell 已恢复: {self.session_id}")
            else:
                await self._broadcast_output("\r\n\x1b[33m[连接已恢复] SSH 重连成功\x1b[0m\r\n")
                print(f"[SSHSystem] 重连成功: {self.session_id}")

            self._reconnecting = False
            return True

        except Exception as e:
            print(f"[SSHSystem] 重连失败: {e}")
            self._reconnecting = False
            await self._broadcast_output(f"\r\n\x1b[31m[重连失败] {e}\x1b[0m\r\n")
            return False

    async def close(self):
        """关闭会话"""
        self._reconnecting = False
        self.stop_output_reader()
        self.stop_local_reader()
        await self.close_sftp()
        if self._local_proc is not None:
            try:
                self._local_proc.terminate(force=True)
            except Exception:
                pass
            self._local_proc = None
        if self.process:
            try:
                self.process.close()
            except Exception:
                pass
            self.process = None
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        self._connected = False
        self._has_shell = False
        # 日志：先落盘剩余缓冲，再关闭 handler，最后补全结束时间重命名
        self._flush_log_now()
        self._close_logger()
        self.finalize_log_file()

    def _close_logger(self):
        """关闭会话专属 logger 的 handler 并清理缓存"""
        sid = self.session_id
        logger = self.__class__._logger_cache.pop(sid, None)
        if logger:
            for h in logger.handlers[:]:
                try:
                    h.close()
                except Exception:
                    pass
                logger.removeHandler(h)


class SessionManager:
    """SSH 会话池（后端统一维护所有连接，前端断开不影响）

    优化：维护 host_id -> List[session_id] 反向索引，
    get_sessions_by_host / get_host_terminal_count 从 O(N) 遍历降为 O(1) 查找。
    """

    def __init__(self):
        self._sessions: dict[str, SSHSession] = {}
        # 反向索引：host_id -> set of session_id（加速 get_sessions_by_host / get_host_terminal_count）
        self._host_index: dict[str, set] = {}

    def _add_to_index(self, session: SSHSession):
        """将会话加入反向索引"""
        if session.host_id:
            self._host_index.setdefault(session.host_id, set()).add(session.session_id)

    def _remove_from_index(self, session: SSHSession):
        """从反向索引移除会话"""
        if session.host_id:
            sids = self._host_index.get(session.host_id)
            if sids:
                sids.discard(session.session_id)
                if not sids:
                    del self._host_index[session.host_id]

    def create_session(self, host: str, port: int, username: str, host_id: str = "", terminal_name: str = "") -> str:
        """创建会话（仅分配 ID，尚未连接）"""
        session_id = str(uuid.uuid4())[:8]
        # 如果没指定终端名，自动生成
        if not terminal_name:
            terminal_name = self._get_next_terminal_name(host_id)
        session = SSHSession(session_id, host, port, username, host_id, terminal_name)
        self._sessions[session_id] = session
        self._add_to_index(session)
        return session_id

    def create_session_with_id(
        self, session_id: str, host: str, port: int, username: str, host_id: str = "", terminal_name: str = ""
    ) -> str:
        """创建会话（复用指定的 session_id，用于恢复终端）"""
        if session_id in self._sessions:
            return session_id  # 已存在则不重复创建
        if not terminal_name:
            terminal_name = self._get_next_terminal_name(host_id)
        session = SSHSession(session_id, host, port, username, host_id, terminal_name)
        self._sessions[session_id] = session
        self._add_to_index(session)
        return session_id

    def _get_next_terminal_name(self, host_id: str) -> str:
        """获取某主机的下一个终端名称"""
        existing = [s.terminal_name for s in self._sessions.values() if s.host_id == host_id and s.terminal_name]
        n = 1
        while f"终端{n}" in existing:
            n += 1
        return f"终端{n}"

    def get_session(self, session_id: str) -> SSHSession | None:
        return self._sessions.get(session_id)

    def get_sessions_by_host(self, host_id: str) -> list[SSHSession]:
        """获取某主机的所有会话（通过反向索引 O(1) 查找）"""
        sids = self._host_index.get(host_id)
        if not sids:
            return []
        return [self._sessions[sid] for sid in sids if sid in self._sessions]

    def get_active_terminals(self) -> list[SSHSession]:
        """获取所有有交互式终端的活跃会话
        - 外部镜像会话（paramiko/asyncssh 劫持）：保持原有判断（is_connected + has_shell）
        - 内部会话：额外验证 shell 真实存活（channel/进程未关闭），避免前端恢复已死的会话"""
        result = []
        for s in self._sessions.values():
            if not (s.is_connected and s.has_shell):
                continue
            if getattr(s, "_external", False) or s.is_shell_alive():
                result.append(s)
        return result

    def get_host_terminal_count(self, host_id: str) -> int:
        """获取某主机的活跃终端数（检测真实连接状态，不只是标志位）
        优化：通过反向索引直接获取该主机会话，避免遍历全部会话
        """
        sids = self._host_index.get(host_id)
        if not sids:
            return 0
        count = 0
        for sid in sids:
            s = self._sessions.get(sid)
            if s and s.is_alive():
                count += 1
        return count

    def list_sessions(self):
        """列出所有会话摘要"""
        return [
            {
                "session_id": s.session_id,
                "host": s.host,
                "port": s.port,
                "username": s.username,
                "host_id": s.host_id,
                "terminal_name": s.terminal_name,
                "connected": s.is_connected,
                "has_shell": s.has_shell,
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
            for s in self._sessions.values()
        ]

    # 全局连接监控协程（单例，避免每个 WebSocket 连接创建一个监控任务）
    _monitor_task: asyncio.Task | None = None

    def start_global_monitor(self):
        """启动全局连接监控协程（只启动一次，检查所有会话）

        优化：原每个 WebSocket 连接创建一个 monitor_task，N 个连接 = N 个监控协程；
        改为全局单例，每 15 秒遍历所有会话检查连接和 shell 状态。
        shell 异常通知通过 _broadcast_output 推送给所有监听者。
        """
        if self._monitor_task is not None and not self._monitor_task.done():
            return  # 已在运行
        self._monitor_task = asyncio.create_task(self._global_monitor_loop())

    async def _global_monitor_loop(self):
        """全局监控循环：每 15 秒检查所有会话的连接和 shell 状态

        - 本机 shell 会话：退出即自动重启，保持终端可用
        - SSH 会话 shell 已退出（用户 exit/logout，传输层仍在）→ 自动切换本机 shell
        - SSH 传输层断开（网络异常/服务器重启等）→ 不自动重连，直接切换到本机终端
        """

        async def _try_reconnect(session: SSHSession):
            print(f"[Monitor] 检测到 SSH 连接断开，尝试自动重连: {session.session_id}")
            try:
                if await session.reconnect():
                    print(f"[Monitor] 自动重连成功: {session.session_id}")
                else:
                    # 重连失败（达到最大次数）→ 自动切换到本机终端
                    print(f"[Monitor] 自动重连失败，切换到本机终端: {session.session_id}")
                    await session._auto_switch_to_local("SSH 连接断开，重连失败")
            except Exception as e:
                print(f"[Monitor] 重连异常: {e}")
                # 重连异常也切换到本机终端，保证终端始终可用
                try:
                    await session._auto_switch_to_local("SSH 连接异常")
                except Exception:
                    pass

        try:
            while True:
                await asyncio.sleep(15)
                for session in list(self._sessions.values()):
                    try:
                        shell_dead = session._has_shell and not session.is_shell_alive()
                        if getattr(session, "_external", False):
                            # 外部镜像会话（paramiko/asyncssh 劫持）：保持原有行为
                            # （断开时重连；shell 异常仅提示，不做本机切换）
                            if not session.is_alive():
                                await _try_reconnect(session)
                            elif shell_dead:
                                session.set_shell_notice("检测到终端 shell 状态异常，如需恢复请点击顶部重连")
                            continue
                        if session.is_local():
                            # 本机 shell 已退出（用户输入 exit 等）：直接关闭会话并通知
                            # 前端关闭标签（不自动重启，用户需求：exit 即关闭连接）
                            if not session.is_shell_alive():
                                print(f"[Monitor] 本机 shell 已退出，关闭会话: {session.session_id}")
                                session.set_closed_notice("本机终端已退出")
                                await self.remove_session(session.session_id)
                            continue
                        if not session.is_alive() or shell_dead:
                            if shell_dead and session.is_transport_alive():
                                # 传输层仍在、仅 shell 退出（用户输入 exit）→ 切回本机终端，
                                # 不做 SSH 自动重连（重连会回到远端，违背"退出即回本机"预期）
                                print(f"[Monitor] SSH shell 已退出，自动切换本机 shell: {session.session_id}")
                                await session._auto_switch_to_local()
                            else:
                                # SSH 连接断开（网络异常/服务器重启/静默断线）：不再自动重连，
                                # 直接切回本机终端并提示；用户点击「重连」按钮手动恢复
                                print(f"[Monitor] SSH 连接断开，切换本机 shell: {session.session_id}")
                                await session._auto_switch_to_local("SSH 连接断开")
                    except Exception as e:
                        print(f"[Monitor] 监控异常 {session.session_id}: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Monitor] 全局监控异常: {e}")

    async def remove_session(self, session_id: str) -> bool:
        """移除并关闭会话"""
        session = self._sessions.pop(session_id, None)
        if session:
            self._remove_from_index(session)
            await session.close()
            return True
        return False

    def remove_session_sync(self, session_id: str) -> bool:
        """
        同步移除会话（供 paramiko/asyncssh 劫持的 close 回调使用）。

        外部镜像会话没有 asyncssh 资源，直接移出并标记断开即可。
        """
        session = self._sessions.pop(session_id, None)
        if session:
            self._remove_from_index(session)
            session._connected = False
            session.last_active = time.time()
            return True
        return False

    async def cleanup_idle(self, idle_timeout: int = 86400):
        """清理超时空闲会话（默认24小时，前端关闭不影响）
        外部镜像会话（paramiko/asyncssh 劫持）由对应 client 的 close 回调负责移除，不在此清理。
        """
        now = time.time()
        to_remove = [
            sid
            for sid, s in self._sessions.items()
            if not getattr(s, "_external", False) and now - s.last_active > idle_timeout
        ]
        for sid in to_remove:
            await self.remove_session(sid)


# 全局会话管理器实例
session_manager = SessionManager()
