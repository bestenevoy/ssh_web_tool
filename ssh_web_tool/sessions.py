"""
SSH 会话池管理
- 统一创建、持有、销毁所有 SSH 连接
- 每个会话有唯一 session_id
- 支持交互式 pty shell（供网页 WebSocket 使用）
- 支持非交互命令执行（供 AI / HTTP API 调用）
"""
import asyncio
import os
import sys
import uuid
import time
import re
import threading
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, List, Optional, Tuple
import asyncssh

# 供 asyncssh.connect 劫持（patch_asyncssh）识别"工具内部连接"的标记：
# 工具自身创建会话时置位，劫持层看到标记则直接放行，避免把工具自己的连接
# 重复注册为镜像会话。
_patch_guard = threading.local()


def get_data_dir() -> str:
    """获取数据文件目录（统一在 ~/.ai4one/wstool）"""
    from .config import get_app_dir
    return str(get_app_dir())


class SSHSession:
    """单个 SSH 会话（对应一个 SSH 连接 + 可选的交互式终端）"""

    # 日志目录
    LOG_DIR = os.path.join(get_data_dir(), "logs")
    # 日志轮转保留天数
    LOG_BACKUP_DAYS = 7
    # 已创建的 logger 缓存（避免同一 session_id 重复创建 handler）
    _logger_cache: Dict[str, logging.Logger] = {}

    def __init__(self, session_id: str, host: str, port: int, username: str,
                 host_id: str = "", terminal_name: str = ""):
        self.session_id = session_id
        self.host = host
        self.port = port
        self.username = username
        self.host_id = host_id          # 关联的保存主机 ID
        self.terminal_name = terminal_name  # 终端名称（如 "终端1"、"终端2"）
        self.conn: Optional[asyncssh.SSHClientConnection] = None
        self.process: Optional[asyncssh.SSHClientProcess] = None
        self._sftp = None
        self.created_at = time.time()
        self.last_active = time.time()
        self._connected = False
        self._has_shell = False  # 是否启动了交互式终端
        # 用于非交互命令执行的锁，防止并发冲突
        self._cmd_lock = asyncio.Lock()
        # 输出广播机制：一个协程读 stdout，广播给所有监听器
        self._output_listeners: List[asyncio.Queue] = []
        self._output_buffer: List[str] = []  # 终端输出缓冲区（用于状态检测）
        self._reader_task: Optional[asyncio.Task] = None
        # 日志持久化：同一会话（session_id）固定同一份日志文件
        # 命名：{host}_{start}_running_{session_id}.log，会话关闭时补全结束时间：
        #       {host}_{start}_{end}_{session_id}.log
        # 恢复已有会话时沿用旧文件（同一终端 = 同一份记录）
        self._log_file = self._resolve_existing_log() or self._build_log_file()
        self._logger = self._get_or_create_logger(session_id, self._log_file)
        # 日志聚合缓冲：输出先累积，静默期/超阈值后清洗一次性写入，
        # 避免把每次刷新的中间回显都写进日志（只记录最终显示内容）
        self._log_buf = ''
        self._log_flush_task: Optional[asyncio.Task] = None
        # 本地 shell（winpty ConPTY，本机 cmd/powershell；SSH 断开后可自动切换）
        self._local_proc: Optional[object] = None
        self._local_shell = ''
        self._local_reader_task: Optional[asyncio.Task] = None
        # 待处理的终端尺寸（resize 消息在 PTY 创建前到达时保存）
        self._pending_size: Optional[Tuple[int, int]] = None
        # resize 事件：PTY 创建后等待第一个 resize 消息，确保 shell 第一帧输出使用正确尺寸
        self._resize_event = asyncio.Event()
        # 自动重连相关
        self._password: Optional[str] = None
        self._private_key: Optional[str] = None
        self._passphrase: Optional[str] = None
        self._last_cols = 120
        self._last_rows = 40
        self._keepalive_task: Optional[asyncio.Task] = None
        self._reconnecting = False
        self._reconnect_count = 0
        self._max_reconnect = 5  # 最大自动重连次数
        # 命令回显解析（从终端输出流提取"提示符后的实际命令"，含 Tab 补全/历史翻查结果）
        self._echo_buf = ''          # 输出行缓冲（可能被数据块截断，留到下一块补齐）
        self._echo_last_cmd = ''     # 最近一次解析记录的命令（去重防重复记录）
        self._echo_last_time = 0.0   # 最近一次记录时间
        # 状态检测缓存：增量清洗后的缓冲区（避免每次 get_terminal_state 全量正则）
        self._clean_buffer = ''      # 已清洗 ANSI 的输出缓冲区（与 _output_buffer 同步）
        self._clean_buffer_dirty = False  # 标记是否有新输出需要重新计算状态
        # 会话级通知（如 shell 异常提示）：由全局监控协程写入，WebSocket read_output 循环取出推送。
        # 不直接走 _broadcast_output，避免提示文本混入 run_command 的注入捕获/echo 解析
        self._shell_notice: Optional[str] = None

    # ---------- 日志文件命名 ----------

    _LOG_NAME_SAFE_RE = re.compile(r'[^\w.\-]')
    _LOG_TIME_FMT = '%Y%m%d-%H%M%S'

    @classmethod
    def _sanitize_host(cls, host: str) -> str:
        """主机名清洗为合法文件名（IPv6 冒号、路径分隔符等 → _）"""
        s = cls._LOG_NAME_SAFE_RE.sub('_', host or 'unknown')
        return s[:48] or 'unknown'

    @classmethod
    def _fmt_time(cls, ts: Optional[float]) -> str:
        import datetime
        if not ts:
            return 'running'
        return datetime.datetime.fromtimestamp(ts).strftime(cls._LOG_TIME_FMT)

    def _resolve_existing_log(self) -> Optional[str]:
        """恢复场景：同 session_id 已存在日志文件则沿用（同一终端同一份记录）

        匹配新命名 *_{sid}.log；兼容旧命名 {sid}.log（历史版本遗留）"""
        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
            pat = re.compile(r'.*_{}\.log$'.format(re.escape(self.session_id)))
            cands = [p for p in os.listdir(self.LOG_DIR) if pat.match(p)]
            if not cands and os.path.exists(os.path.join(self.LOG_DIR, f"{self.session_id}.log")):
                return os.path.join(self.LOG_DIR, f"{self.session_id}.log")
            if cands:
                full = [os.path.join(self.LOG_DIR, c) for c in cands]
                return max(full, key=os.path.getmtime)
        except Exception:
            pass
        return None

    def _build_log_file(self) -> str:
        """新会话日志文件名：{host}_{start}_running_{session_id}.log"""
        start = self._fmt_time(self.created_at)
        return os.path.join(self.LOG_DIR, f"{self._sanitize_host(self.host)}_{start}_running_{self.session_id}.log")

    def _close_log_handler(self):
        """关闭并移除当前 logger 的 FileHandler（释放文件句柄，供 rename/清理）"""
        logger = self._logger
        if logger is None:
            return
        for h in logger.handlers[:]:
            try:
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)

    def finalize_log_file(self):
        """会话关闭时补全结束时间：把 _running_ 替换为 {end}_

        文件存在则重命名；不存在（会话无任何输出）也更新路径保持一致"""
        try:
            if not self._log_file or '_running_' not in self._log_file:
                return
            end = self._fmt_time(time.time())
            new_path = self._log_file.replace('_running_', f'_{end}_', 1)
            if new_path == self._log_file:
                return
            if os.path.exists(self._log_file):
                # Windows 下 FileHandler 占用句柄会导致 rename 失败，先释放
                self._close_log_handler()
                try:
                    os.rename(self._log_file, new_path)
                except OSError:
                    pass  # 重命名失败不阻塞关闭（句柄可能仍被占用）
            self._log_file = new_path
        except Exception:
            pass  # 重命名失败不阻塞关闭（文件句柄可能仍被占用）

    @classmethod
    def cleanup_old_logs(cls, days: int = 30) -> int:
        """启动时清理超过 days 天的会话日志（会话日志不再按天轮转，靠启动清理控制体积）

        返回删除的文件数
        """
        removed = 0
        try:
            os.makedirs(cls.LOG_DIR, exist_ok=True)
            cutoff = time.time() - days * 86400
            for name in os.listdir(cls.LOG_DIR):
                if not name.endswith('.log'):
                    continue
                p = os.path.join(cls.LOG_DIR, name)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return removed

    def set_shell_notice(self, msg: str) -> None:
        """设置会话级通知（全局监控协程调用）"""
        self._shell_notice = msg

    def take_shell_notice(self) -> Optional[str]:
        """取出并清空会话级通知（WebSocket read_output 循环调用，只取一次）"""
        n = self._shell_notice
        self._shell_notice = None
        return n

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def has_shell(self) -> bool:
        return self._has_shell and (self.process is not None or self._local_proc is not None)

    async def connect(self, password: Optional[str] = None,
                      private_key: Optional[str] = None,
                      passphrase: Optional[str] = None):
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
            "keepalive_interval": 30,  # 每 30 秒发送 keepalive 包，防止空闲超时断开
            "keepalive_count_max": 3,  # 3 次 keepalive 无响应则认为连接断开
        }
        if password:
            kwargs["password"] = password
        if private_key:
            kwargs["client_keys"] = [asyncssh.import_private_key(private_key, passphrase)]

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
        print(f"[SSHSystem] 连接成功: {self.username}@{self.host}:{self.port} (keepalive=30s)")

    async def start_interactive_shell(self, cols: int = 120, rows: int = 40,
                                      term_type: str = "xterm-256color"):
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
        self.process = await self.conn.create_process(
            term_type=term_type,
            term_size=(cols, rows),
        )
        self._has_shell = True
        self.last_active = time.time()
        # 自动启动输出读取器（广播给所有监听器）
        await self.start_output_reader()
        return self.process

    # ============ 输出广播机制 ============

    def add_output_listener(self) -> asyncio.Queue:
        """注册输出监听器，返回一个 Queue，后续输出会写入这个队列
        有界队列（约 100 块 ≈ 400KB）：消费慢的监听器（前端 ws 慢）不会无限堆积内存"""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._output_listeners.append(q)
        return q

    def remove_output_listener(self, q: asyncio.Queue):
        """移除输出监听器"""
        if q in self._output_listeners:
            self._output_listeners.remove(q)

    def _drop_oldest(self, q: asyncio.Queue, data: str):
        """队列满时丢弃最旧的一条再放入最新数据（终端语义：保留最新内容）"""
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(data)
        except Exception:
            pass

    # ============ 命令回显解析（后端统一记录实际执行的命令） ============

    # 提示符正则：匹配 bash/zsh/sh (user@host:path$ / #)、python (>>>)、
    # mysql/sqlite/redis/mongo/postgres 等交互式程序的提示符。
    # 不匹配单独的 ">"（node 提示符，太通用容易误判普通输出行）。
    _ECHO_PROMPT_RE = re.compile(
        r'^(?:'
        r'[\w.-]+@[\w.-]+:[^#$\n]*[#$]'   # shell: user@host:path$ 或 user@host:path#
        r'|>>>'                            # python
        r'|\.\.\.'                         # python 续行（跳过）
        r'|mysql>'                         # mysql
        r'|sqlite>'                        # sqlite
        r'|[\d.]+:\d+>'                    # redis: 127.0.0.1:6379>
        r'|[a-zA-Z_][\w.-]*>'              # 通用: xxx> (mongo, postgres 等)
        r')\s*'
    )

    # 清 ANSI 但保留 \r\n（回显解析需要 \r 判断行内覆盖）
    _ANSI_KEEP_CR_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
    _OSC_KEEP_CR_RE = re.compile(r'\x1b\][\s\S]*?(\x07|\x1b\\)')

    @classmethod
    def _clean_ansi_keep_cr(cls, text: str) -> str:
        """清理 ANSI 转义序列，但保留 \r\n（区别于 _clean_ansi 会去掉 \r）"""
        text = cls._ANSI_KEEP_CR_RE.sub('', text)
        text = cls._OSC_KEEP_CR_RE.sub('', text)
        text = re.sub(r'\x1b[=><]', '', text)
        return text

    @classmethod
    def _extract_echo_command(cls, line: str) -> str:
        """从一行输出中提取提示符之后的命令文本；非提示符行返回空串
        - 已知提示符**循环剥离**：阿里云等 shell 每次 readline 重绘都会清屏重写，
          一行内可能出现多个连续提示符（root@host:~# root@host:~# cmd）
        - 自定义 PS1 宽松兜底（仅当未识别出已知提示符时）：行首到第一个 $/# 之间
          为提示符标识（如 root@host#、[user@host ~]$），其后是命令；
          宽松匹配失败 = 普通输出行，不记录"""
        s = line.strip()
        if not s:
            return ''
        # python 续行提示符(...)：是上一命令的延续内容，不作为独立命令记录
        if s.startswith('...'):
            return ''
        # 1. 精确已知提示符：循环剥离（readline 清屏重绘可能一行内多个提示符）
        stripped = False
        cmd = s
        while True:
            m = cls._ECHO_PROMPT_RE.match(cmd)
            if not m:
                break
            stripped = True
            rest = cmd[m.end():]
            if not rest.strip():
                return ''  # 只剩提示符（空命令回车）
            cmd = rest
        # 2. 未识别出已知提示符时，才尝试自定义 PS1 宽松匹配；失败 = 普通输出行
        if not stripped:
            m2 = re.match(r'^[\[\]~\w@.\- :/\\]*?[#$]\s*', cmd)
            if not m2:
                return ''
            rest2 = cmd[m2.end():].strip()
            if not rest2:
                return ''  # 提示符后无内容（空命令回车）
            cmd = rest2
        cmd = cmd.strip()
        if not cmd or len(cmd) > 500:
            return ''
        return cmd

    def _parse_echo_line(self, data: str):
        """
        从输出流解析"命令回显行"并记录到全局历史（由后端统一记录）。

        原理：交互式 shell（pty echo / readline）会把用户实际输入的命令回显在
        提示符之后（root@host:~$ cd /var），Tab 补全、历史翻查（方向键/Ctrl+R）
        后的最终内容都会体现在这行里；前端只记录键盘输入，会丢失补全内容。
        """
        if not self._has_shell or self.process is None:
            return
        try:
            text = self._clean_ansi_keep_cr(data)
            self._echo_buf += text
            if len(self._echo_buf) > 4000:
                self._echo_buf = ''  # 异常情况下防止无限增长
                return
            if '\n' not in self._echo_buf:
                return
            parts = self._echo_buf.split('\n')
            self._echo_buf = parts[-1]  # 最后一段不完整，留到下一块
            for raw in parts[:-1]:
                line = raw.rstrip('\r')
                if '\r' in line:
                    # 行内多次 \r 覆盖（Tab 补全会重写整行）：只保留最后一次覆盖后的内容
                    line = line.rsplit('\r', 1)[-1]
                cmd = self._extract_echo_command(line)
                if not cmd:
                    continue
                now = time.time()
                # 去重：与 3 秒内刚记录过的相同命令（防止与前端/CLI 注入重复记录）
                if cmd == self._echo_last_cmd and now - self._echo_last_time < 3:
                    continue
                self._echo_last_cmd = cmd
                self._echo_last_time = now
                try:
                    asyncio.create_task(self._record_echo(cmd))
                except Exception:
                    pass
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
        # 维护缓冲区（保留最后 8000 字符）
        self._output_buffer.append(data)
        total = sum(len(s) for s in self._output_buffer)
        if total > 8000:
            combined = ''.join(self._output_buffer)
            self._output_buffer = [combined[-8000:]]
            # 缓冲区压缩后同步更新清洗缓冲区
            self._clean_buffer = self._clean_ansi(combined)[-8000:]
        else:
            # 增量清洗：只清洗新增数据块，避免每次全量正则
            self._clean_buffer += self._clean_ansi(data)
            if len(self._clean_buffer) > 8000:
                self._clean_buffer = self._clean_buffer[-8000:]
        self._clean_buffer_dirty = True
        # 日志持久化：聚合缓冲，静默期/超阈值后清洗一次性写入
        # （不逐块写：避免每次刷新回显都落盘，只记录最终显示内容）
        self._feed_log(data)
        # 广播给所有监听器：put_nowait 不阻塞；队列满时丢最旧保最新，
        # 避免慢监听器（前端 ws 阻塞）拖住整个广播链（日志/echo 解析/其他监听器）
        for q in self._output_listeners:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                self._drop_oldest(q, data)

    @classmethod
    def _get_or_create_logger(cls, session_id: str, log_file: Optional[str] = None) -> logging.Logger:
        """获取或创建会话专属 logger（FileHandler 追加模式，会话关闭时关闭 handler）

        同一 session_id 同一文件（日志文件路径在 __init__ 已固定：恢复会话沿用旧文件）；
        不做按天轮转（会话日志是一份连续记录，轮转会破坏"同一终端同一份记录"）。
        """
        if session_id in cls._logger_cache:
            return cls._logger_cache[session_id]
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        path = log_file or os.path.join(cls.LOG_DIR, f"{session_id}.log")
        logger = logging.getLogger(f"ssh_session.{session_id}")
        logger.setLevel(logging.INFO)
        # 已有 handler 但指向旧路径（恢复/重建场景路径变化）：先移除重建，避免写错文件
        if logger.handlers:
            first = logger.handlers[0]
            base = getattr(first, "baseFilename", None)
            if not base or os.path.normcase(base) != os.path.normcase(path):
                for h in logger.handlers[:]:
                    try:
                        h.close()
                    except Exception:
                        pass
                    logger.removeHandler(h)
        # 避免重复添加 handler（logger 会被全局注册）
        if not logger.handlers:
            handler = logging.FileHandler(path, mode='a', encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(message)s'))
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)
            # 不向上传播到 root logger（避免重复输出到 server.log）
            logger.propagate = False
        cls._logger_cache[session_id] = logger
        return logger

    # 日志聚合写入：静默期（LOG_FLUSH_DELAY）或缓冲超阈值时一次性清洗写入
    LOG_FLUSH_DELAY = 0.6   # 秒：无新输出多久后 flush
    LOG_FLUSH_MAX = 65536   # 字符：缓冲超过该阈值立即 flush

    def _schedule_log_flush(self):
        """有新输出进入日志缓冲时调度一次延迟 flush（已调度则不重复）"""
        if not self._log_buf:
            return
        if self._log_flush_task and not self._log_flush_task.done():
            return
        try:
            self._log_flush_task = asyncio.create_task(self._log_flusher())
        except RuntimeError:
            # 无运行中的 event loop（同步上下文/测试）：直接同步 flush
            self._flush_log_now()

    async def _log_flusher(self):
        try:
            await asyncio.sleep(self.LOG_FLUSH_DELAY)
        except asyncio.CancelledError:
            pass
        self._flush_log_now()

    def _flush_log_now(self):
        """把日志缓冲清洗后一次性写入（幂等；会话关闭前必须调用以落盘剩余内容）"""
        if not self._log_buf:
            return
        text = self._log_buf
        self._log_buf = ''
        try:
            # 只记录最终显示：清洗 ANSI + 行内 \r 覆盖合并（进度条等只留最后状态）
            text = self._clean_ansi_keep_cr(text)
            text = self._collapse_cr_lines(text)
            if text.strip():
                self._logger.info(text)
        except Exception:
            pass  # 日志写入失败不影响主流程

    @staticmethod
    def _collapse_cr_lines(text: str) -> str:
        """同一物理行内多次 \r 覆盖：只保留最后一次 \r 之后的内容
        （wget/进度条等 \r 刷新场景，日志只记录最终显示状态）

        注意：先把 CRLF 归一化为 LF，避免把 \r\n 换行误判为进度条覆盖
        而丢掉整行内容。
        """
        lines = text.replace('\r\n', '\n').split('\n')
        out = []
        for line in lines:
            if '\r' in line:
                line = line.rsplit('\r', 1)[-1]
            out.append(line)
        return '\n'.join(out)

    def _feed_log(self, data: str):
        """输出进入日志聚合缓冲（替代逐块 _write_log，减少刷新回显碎片）"""
        self._log_buf += data
        if len(self._log_buf) >= self.LOG_FLUSH_MAX:
            self._flush_log_now()
        else:
            self._schedule_log_flush()

    def get_history_logs(self, offset: int = 0, limit: int = 2000) -> str:
        """
        读取历史日志（分页加载）
        - offset: 从文件末尾倒数的字符偏移量（0 表示从最近开始）
        - limit: 最多返回多少字符
        返回清理后的日志文本
        """
        try:
            if not os.path.exists(self._log_file):
                return ""
            file_size = os.path.getsize(self._log_file)
            # 计算读取起始位置（从文件末尾倒数）
            start_pos = max(0, file_size - offset - limit)
            read_size = min(limit, file_size - start_pos)
            with open(self._log_file, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(start_pos)
                content = f.read(read_size)
            # 清理 ANSI 并格式化
            return self._format_history_logs(content)
        except Exception:
            return ""

    @staticmethod
    def _format_history_logs(content: str) -> str:
        """格式化历史日志：清理ANSI、处理回车、压缩多余空格和空行"""
        # 1. 清理 ANSI 转义序列
        text = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', content)
        text = re.sub(r'\x1b\][\s\S]*?(\x07|\x1b\\)', '', text)
        text = re.sub(r'\x1b[=><]', '', text)

        # 2. 处理回车 \r：同一行中 \r 后面的内容覆盖前面的，只保留最后一段
        lines = text.split('\n')
        formatted_lines = []
        for line in lines:
            if '\r' in line:
                # 只保留最后一个 \r 后面的内容
                line = line.rsplit('\r', 1)[-1]
            # 去掉行尾空格
            line = line.rstrip()
            # 压缩行首多余空格（超过8个的压缩为4个，保留有意义的缩进）
            stripped = line.lstrip()
            leading_spaces = len(line) - len(stripped)
            if leading_spaces > 8:
                line = '    ' + stripped
            formatted_lines.append(line)

        # 3. 合并连续空行（最多保留2个连续空行）
        result = []
        empty_count = 0
        for line in formatted_lines:
            if not line.strip():
                empty_count += 1
                if empty_count <= 2:
                    result.append(line)
            else:
                empty_count = 0
                result.append(line)

        return '\n'.join(result)

    async def start_output_reader(self):
        """启动后台协程持续读取 stdout 并广播（幂等，重复调用不会重复启动）

        本地 shell（ConPTY）走 _start_local_reader（to_thread 读取，不阻塞事件循环）
        """
        if self._local_proc is not None:
            self._start_local_reader()
            return
        if self._reader_task and not self._reader_task.done():
            return
        if not self.process:
            raise RuntimeError("没有交互式终端")

        async def _reader():
            try:
                while True:
                    data = await self.process.stdout.read(4096)
                    if not data:
                        break
                    self.last_active = time.time()
                    await self._broadcast_output(data)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._reader_task = asyncio.create_task(_reader())

    def stop_output_reader(self):
        """停止输出读取器"""
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

    # ============ 本地 shell（winpty ConPTY：本机 cmd / powershell） ============

    def is_local(self) -> bool:
        """当前是否运行在本地 shell（非 SSH）"""
        return self._local_proc is not None

    async def start_local_shell(self, shell: str = "cmd", cols: int = 120, rows: int = 40):
        """启动本机交互式 shell（ConPTY）。SSH 断开自动切换或用户主动创建本地终端时调用

        - shell: 'cmd' / 'powershell' / 'pwsh'
        - 复用现有广播/日志/监听机制，前端协议与 SSH 会话完全一致
        """
        if shell == "powershell":
            argv = ["powershell.exe", "-NoLogo"]
        elif shell == "pwsh":
            argv = ["pwsh", "-NoLogo"]
        else:
            argv = ["cmd.exe"]
        try:
            import winpty
        except ImportError:
            raise RuntimeError("本地 shell 需要 pywinpty（pip install pywinpty），当前不可用")
        # 延迟 import：非 Windows/未安装时不影响主程序（本地终端功能按需可用）
        proc = winpty.PtyProcess.spawn(argv, dimensions=(rows, cols), backend=1)
        self._local_proc = proc
        self._local_shell = shell
        self._last_cols = cols
        self._last_rows = rows
        self._connected = True
        self._has_shell = True
        self.last_active = time.time()
        # 与 SSH shell 共用输出广播/日志/监听（输出循环由 stop_local_reader 管理）
        self._start_local_reader()
        return proc

    def _start_local_reader(self):
        """本地 shell 输出读取协程（to_thread 避免阻塞事件循环）"""
        if self._local_reader_task and not self._local_reader_task.done():
            return

        async def _reader():
            try:
                while True:
                    data = await asyncio.to_thread(self._local_proc.read, 4096)
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

    def stop_local_reader(self):
        """停止本地 shell 输出读取器"""
        if self._local_reader_task:
            self._local_reader_task.cancel()
            self._local_reader_task = None

    async def write_local(self, data: str):
        """向前台本地 shell 写入输入（转码为 str；ConPTY 期望 str）"""
        if self._local_proc is not None:
            try:
                self._local_proc.write(data)
                self.last_active = time.time()
            except Exception:
                pass

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
        await self.start_local_shell(shell, cols, rows)

    async def restart_local_shell(self, cols: int = 0, rows: int = 0):
        """本机 shell 已退出时重启（沿用原 shell 类型与最近尺寸）"""
        self.stop_local_reader()
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

    # ============ 注入命令到交互式终端 ============

    @staticmethod
    def _clean_ansi(text: str) -> str:
        """清理 ANSI 转义序列（颜色、光标移动、OSC 等）"""
        text = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', text)
        # OSC 序列支持两种结尾：\x07 (BEL) 和 \x1b\\ (ST)
        text = re.sub(r'\x1b\][\s\S]*?(\x07|\x1b\\)', '', text)
        text = re.sub(r'\x1b[=><]', '', text)
        text = re.sub(r'\r', '', text)
        return text

    # 通用提示符模式列表（按优先级排序，匹配到任意一个即认为命令完成）
    PROMPT_PATTERNS = [
        r'[\w.-]+@[\w.-]+:.+[#$]\s*$',           # shell: root@host:~# 或 user@host:~$
        r'^>>>\s*$',                                 # Python: >>>
        r'^\.\.\.\s*$',                              # Python 续行: ...
        r'^mysql>\s*$',                              # MySQL: mysql>
        r'^sqlite>\s*$',                             # SQLite: sqlite>
        r'^[\d.]+:\d+>\s*$',                        # Redis: 127.0.0.1:6379>
        r'^>\s*$',                                   # Node.js: >
        r'^\w+>\s*$',                                # 通用: xxx> (mongo, postgres 等)
    ]

    @classmethod
    def _match_any_prompt(cls, text: str) -> bool:
        """检查文本是否匹配任意一种已知提示符"""
        for pattern in cls.PROMPT_PATTERNS:
            if re.search(pattern, text, re.MULTILINE):
                return True
        return False

    @classmethod
    def _is_prompt_line(cls, line: str) -> bool:
        """检查单行是否是提示符行"""
        for pattern in cls.PROMPT_PATTERNS:
            if re.match(pattern, line.strip()):
                return True
        return False

    async def _inject_and_wait(self, command: str,
                                idle_timeout: float = 0.5,
                                total_timeout: int = 15) -> str:
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
        cleaned_recent = ''

        try:
            # 发送命令
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
        output = self._clean_ansi(''.join(captured_parts))

        # 去掉第一行命令回显
        lines = output.split('\n')
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        # 去掉末尾的提示符行（多提示符匹配）
        while lines and self._is_prompt_line(lines[-1]):
            lines = lines[:-1]
        return '\n'.join(lines).strip()

    async def inject_and_capture(self, command: str, idle_timeout: float = 0.5,
                                  total_timeout: int = 30,
                                  prompt_pattern: str = r'[\w.-]+@[\w.-]+:.+[#$]\s*$',
                                  capture_exit_code: bool = True) -> Tuple[int, str, str]:
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
            output = await self._inject_and_wait(
                command, idle_timeout=idle_timeout, total_timeout=total_timeout
            )

            # 2. 自动获取退出码（仅在 shell 环境中）
            exit_code = None
            if capture_exit_code:
                try:
                    state = self.get_terminal_state()
                    # 仅在普通 shell 环境中获取退出码（Python/MySQL/vim 等不适用）
                    if state.get('in_shell') and not state.get('in_python') and not state.get('in_mysql') and not state.get('in_pager'):
                        exit_output = await self._inject_and_wait("echo $?", total_timeout=10)
                        # 解析退出码（取最后一行的数字）
                        for line in reversed(exit_output.split('\n')):
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

        优化：使用 _broadcast_output 中增量维护的 _clean_buffer 缓存，
        避免每次调用都对 8000 字符缓冲区做 3 次正则替换。
        """
        # 直接使用增量清洗后的缓冲区（在 _broadcast_output 中维护）
        clean_text = self._clean_buffer

        lines = clean_text.split('\n')
        # 去掉末尾空行（输出通常以 \n 结尾，split 后最后一个元素是空字符串）
        while lines and lines[-1].strip() == '':
            lines = lines[:-1]
        last_line = lines[-1] if lines else ""
        # 取最后两行做更准确的判断
        last_two = '\n'.join(lines[-2:]) if len(lines) >= 2 else last_line

        state = {
            "last_line": last_line[-120:],
            "in_python": bool(re.search(r'>>>\s*$', last_line)),
            "in_mysql": bool(re.search(r'mysql>\s*$', last_line)),
            "in_pager": bool(re.search(r':\s*$', last_line)),  # less/man 分页器
            "in_shell": bool(re.search(r'[#$]\s*$', last_line)),
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
            if re.search(r'-- INSERT --|-- VISUAL --|:\w*$', last_two):
                state["foreground_process"] = "vim"
            elif re.search(r'load average|Tasks:', last_two):
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

    async def run_command(self, command: str, timeout: int = 30) -> Tuple[int, str, str]:
        """
        非交互执行命令（供 AI / HTTP API 调用）
        返回 (退出码, stdout, stderr)
        注意：这是在独立进程中执行，输出不会出现在 Web 交互式终端中
        """
        if not self.is_connected:
            raise RuntimeError("SSH 未连接")
        async with self._cmd_lock:
            self.last_active = time.time()
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
            self._sftp = await self.conn.start_sftp_client()
        return self._sftp

    async def list_directory(self, path: str = "/") -> List[dict]:
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
                result.append({
                    "name": item.filename,
                    "type": "dir" if (attrs.type or 0) == 2 else "file",  # 2=directory
                    "size": attrs.size or 0,
                    "mtime": attrs.mtime or 0,
                    "mode": oct(attrs.permissions) if attrs.permissions else "",
                })
            except Exception:
                result.append({
                    "name": item.filename,
                    "type": "unknown",
                    "size": 0,
                    "mtime": 0,
                    "mode": "",
                })
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
        async with sftp.open(path, 'r') as f:
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
        async with sftp.open(path, 'rb') as f:
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
            async with sftp.open(path, 'wb') as f:
                await f.write(content)
        else:
            async with sftp.open(path, 'w') as f:
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
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

    def is_alive(self) -> bool:
        """检测连接是否真的活着（不只是标志位）；本地 shell 检查 ConPTY 进程"""
        # 本地 shell（winpty ConPTY）
        if self._local_proc is not None:
            try:
                return bool(self._local_proc.isalive())
            except Exception:
                return False
        # 外部镜像会话（paramiko/asyncssh 劫持注册）：基于外部 client 的真实状态
        if getattr(self, '_external', False):
            client = getattr(self, '_paramiko_client', None)
            if client is not None:
                try:
                    transport = client.get_transport()
                    return transport is not None and transport.is_active()
                except Exception:
                    return False
            conn = getattr(self, '_asyncssh_conn', None)
            if conn is not None:
                try:
                    if hasattr(conn, 'is_closing'):
                        return not conn.is_closing()
                    return True
                except Exception:
                    return False
            return False
        if not self._connected or self.conn is None:
            return False
        try:
            # 检查连接对象是否还活着
            if hasattr(self.conn, '_transport') and self.conn._transport:
                if self.conn._transport.is_closing():
                    return False
            # 备用方法：检查 conn 是否有 is_closing 方法
            elif hasattr(self.conn, 'is_closing') and self.conn.is_closing():
                return False
            # 补充：检查 shell channel 是否仍可用（transport 可能短暂保持但 channel 已关）
            if self._has_shell and self.process is not None:
                ch = getattr(self.process, '_channel', None)
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
        """检测 shell 进程/channel 是否真的活着（不只是标志位）；本地 shell 检查 ConPTY"""
        if self._local_proc is not None:
            try:
                return bool(self._local_proc.isalive())
            except Exception:
                return False
        if not self._has_shell or self.process is None:
            return False
        try:
            # 0. channel 层检查：channel 已关闭则 shell 不可用
            ch = getattr(self.process, '_channel', None)
            if ch is not None:
                try:
                    if ch.is_closing():
                        return False
                except Exception:
                    pass
            # 1. 优先检查内部进程对象是否存在
            if hasattr(self.process, '_process') and self.process._process is not None:
                # 检查内部进程是否已退出
                if hasattr(self.process._process, 'returncode'):
                    if self.process._process.returncode is not None:
                        return False
                return True
            # 备用：检查 exit_status
            if hasattr(self.process, 'exit_status'):
                if self.process.exit_status is not None:
                    return False
            return True
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
        if self._reconnecting:
            print(f"[SSHSystem] 重连进行中，跳过: {self.session_id}")
            return False
        if self._reconnect_count >= self._max_reconnect:
            print(f"[SSHSystem] 已达最大重连次数 {self._max_reconnect}，停止重连: {self.session_id}")
            return False

        self._reconnecting = True
        self._reconnect_count += 1
        had_shell = self._has_shell
        cols = self._last_cols
        rows = self._last_rows

        print(f"[SSHSystem] 开始重连 ({self._reconnect_count}/{self._max_reconnect}): {self.username}@{self.host}:{self.port}")

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
            await self.connect(
                password=self._password,
                private_key=self._private_key,
                passphrase=self._passphrase
            )

            # 如果之前有 shell，重新启动
            if had_shell:
                await self.start_interactive_shell(cols=cols, rows=rows)
                # 广播重连成功消息
                await self._broadcast_output(f"\r\n\x1b[33m[连接已恢复] 重连成功 ({self._reconnect_count}次)\x1b[0m\r\n")
                print(f"[SSHSystem] 重连成功，shell 已恢复: {self.session_id}")
            else:
                await self._broadcast_output(f"\r\n\x1b[33m[连接已恢复] SSH 重连成功\x1b[0m\r\n")
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
        self._sessions: Dict[str, SSHSession] = {}
        # 反向索引：host_id -> set of session_id（加速 get_sessions_by_host / get_host_terminal_count）
        self._host_index: Dict[str, set] = {}

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

    def create_session(self, host: str, port: int, username: str,
                       host_id: str = "", terminal_name: str = "") -> str:
        """创建会话（仅分配 ID，尚未连接）"""
        session_id = str(uuid.uuid4())[:8]
        # 如果没指定终端名，自动生成
        if not terminal_name:
            terminal_name = self._get_next_terminal_name(host_id)
        session = SSHSession(session_id, host, port, username, host_id, terminal_name)
        self._sessions[session_id] = session
        self._add_to_index(session)
        return session_id

    def create_session_with_id(self, session_id: str, host: str, port: int, username: str,
                                host_id: str = "", terminal_name: str = "") -> str:
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
        existing = [s.terminal_name for s in self._sessions.values()
                    if s.host_id == host_id and s.terminal_name]
        n = 1
        while f"终端{n}" in existing:
            n += 1
        return f"终端{n}"

    def get_session(self, session_id: str) -> Optional[SSHSession]:
        return self._sessions.get(session_id)

    def get_sessions_by_host(self, host_id: str) -> List[SSHSession]:
        """获取某主机的所有会话（通过反向索引 O(1) 查找）"""
        sids = self._host_index.get(host_id)
        if not sids:
            return []
        return [self._sessions[sid] for sid in sids if sid in self._sessions]

    def get_active_terminals(self) -> List[SSHSession]:
        """获取所有有交互式终端的活跃会话
        - 外部镜像会话（paramiko/asyncssh 劫持）：保持原有判断（is_connected + has_shell）
        - 内部会话：额外验证 shell 真实存活（channel/进程未关闭），避免前端恢复已死的会话"""
        result = []
        for s in self._sessions.values():
            if not (s.is_connected and s.has_shell):
                continue
            if getattr(s, '_external', False) or s.is_shell_alive():
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
    _monitor_task: Optional[asyncio.Task] = None

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
        """全局监控循环：每 15 秒检查所有会话的连接和 shell 状态"""
        try:
            while True:
                await asyncio.sleep(15)
                for session in list(self._sessions.values()):
                    try:
                        if not session.is_alive():
                            print(f"[Monitor] 检测到 SSH 连接断开，尝试自动重连: {session.session_id}")
                            try:
                                if await session.reconnect():
                                    print(f"[Monitor] 自动重连成功: {session.session_id}")
                                else:
                                    print(f"[Monitor] 自动重连失败: {session.session_id}")
                            except Exception as e:
                                print(f"[Monitor] 重连异常: {e}")
                        elif session._has_shell and not session.is_shell_alive():
                            print(f"[Monitor] 检测到 shell 状态异常: {session.session_id}")
                            # 设置会话级通知（不通过 _broadcast_output！否则提示文本会进入
                            # run_command 的注入捕获监听器/echo 解析缓冲，污染命令返回的 stdout）
                            # 由各 WebSocket 的 read_output 循环 take 后单独推送给前端
                            session.set_shell_notice("检测到终端 shell 状态异常，如需恢复请点击顶部重连")
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
            sid for sid, s in self._sessions.items()
            if not getattr(s, '_external', False)
            and now - s.last_active > idle_timeout
        ]
        for sid in to_remove:
            await self.remove_session(sid)


# 全局会话管理器实例
session_manager = SessionManager()
