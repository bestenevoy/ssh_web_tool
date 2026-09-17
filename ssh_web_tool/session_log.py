"""会话日志组件：日志文件命名/管理 + 聚合缓冲延迟落盘 + 历史读取

命名 {host}_{start}_running_{session_id}.log，会话关闭时补全结束时间
（同一终端 = 同一份记录）。
flush 策略：静默期（flush_delay）或缓冲超阈值（flush_max）时一次性写入，
只记录最终显示内容——输出流由 TerminalMirror（pyte 终端镜像）回放到虚拟
屏幕，flush 时提取屏幕上新显示的行（与终端显示构造性一致：退格/\\r 覆盖/
光标重绘自然收敛，bell 等控制字符不进入文本，滚出行与 clear 被擦内容先转录，
alt-screen 全屏应用内容不混入）。

逻辑原为 SSHSession 中的日志一节；SSHSession 保留 LOG_DIR/_logger_cache 等
兼容属性并在 __init__ 装配本组件（测试隔离依赖这些类属性）。
"""

import asyncio
import datetime
import logging
import os
import re
import time

from .terminal_mirror import TerminalMirror

_LOG_NAME_SAFE_RE = re.compile(r"[^\w.\-]")
_LOG_TIME_FMT = "%Y%m%d-%H%M%S"


def sanitize_host(host: str) -> str:
    """主机名清洗为合法文件名（IPv6 冒号、路径分隔符等 → _）"""
    s = _LOG_NAME_SAFE_RE.sub("_", host or "unknown")
    return s[:48] or "unknown"


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "running"
    return datetime.datetime.fromtimestamp(ts).strftime(_LOG_TIME_FMT)


def resolve_existing_log(session_id: str, log_dir: str) -> str | None:
    """恢复场景：同 session_id 已存在日志文件则沿用（同一终端同一份记录）

    匹配新命名 *_{sid}.log；兼容旧命名 {sid}.log（历史版本遗留）
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
        pat = re.compile(rf".*_{re.escape(session_id)}\.log$")
        cands = [p for p in os.listdir(log_dir) if pat.match(p)]
        if not cands and os.path.exists(os.path.join(log_dir, f"{session_id}.log")):
            return os.path.join(log_dir, f"{session_id}.log")
        if cands:
            full = [os.path.join(log_dir, c) for c in cands]
            return max(full, key=os.path.getmtime)
    except Exception:
        pass
    return None


def build_log_file(session_id: str, host: str, created_at: float, log_dir: str) -> str:
    """新会话日志文件名：{host}_{start}_running_{session_id}.log"""
    start = fmt_time(created_at)
    return os.path.join(log_dir, f"{sanitize_host(host)}_{start}_running_{session_id}.log")


class SessionLog:
    """单个会话的日志持久化（每个会话一个实例）

    默认不记录（enabled=False）：仅在用户显式开启（enable_with）后才创建
    日志文件与 FileHandler；开启时可指定保存目录。
    """

    def __init__(
        self,
        session_id: str,
        log_file: str,
        logger: logging.Logger | None = None,
        flush_delay: float = 0.6,
        flush_max: int = 65536,
    ) -> None:
        self._session_id = session_id
        # log_file 恒为预生成路径（构造时确定，恢复会话沿用旧文件）；logger 惰性：
        # 默认不记录时为 None，首次 enable_with 才创建 FileHandler（文件随之落盘）
        self.log_file = log_file
        self._logger = logger
        self.flush_delay = flush_delay  # 秒：无新输出多久后 flush
        self.flush_max = flush_max  # 字符：缓冲超过该阈值立即 flush
        self.buffer = ""  # 聚合缓冲（未落盘内容的 pending 指示与 flush 触发）
        self.enabled = False  # 记录开关：默认不记录，右键「开始记录」才开启
        self._flush_task: asyncio.Task | None = None
        # 终端镜像：输出流回放到虚拟屏幕，flush 时提取"新显示"的行
        # （仅在开启记录后 feed，未开启记录零开销）
        self._mirror = TerminalMirror()

    def enable_with(self, log_file: str, log_dir: str, cache: dict[str, logging.Logger]) -> None:
        """开启记录并落到指定文件（惰性创建文件与 FileHandler；路径变化时重建 handler）

        - log_file 与当前一致（同会话暂停后恢复/重连沿用）→ 直接复用；
        - 变化（换目录）→ 更新路径并重建 handler（get_or_create_logger 内处理）。
        抛出 OSError（目录不可创建/文件不可写）时调用方决定如何提示。
        """
        self._mirror.reset()  # 开启点之前的屏幕状态不转录，从当前显示重新开始
        self.log_file = log_file
        self._logger = self.get_or_create_logger(self._session_id, log_file, log_dir, cache)
        self.enabled = True

    # ---------- logger 工厂（dir 与 cache 由调用方传入，便于测试隔离） ----------

    @classmethod
    def get_or_create_logger(
        cls,
        session_id: str,
        log_file: str | None,
        log_dir: str,
        cache: dict[str, logging.Logger],
    ) -> logging.Logger:
        """获取或创建会话专属 logger（FileHandler 追加模式，会话关闭时关闭 handler）

        同一 session_id 同一文件（日志文件路径在 __init__ 已固定：恢复会话沿用旧文件）；
        不做按天轮转（会话日志是一份连续记录，轮转会破坏"同一终端同一份记录"）。
        """
        if session_id in cache:
            return cache[session_id]
        os.makedirs(log_dir, exist_ok=True)
        path = log_file or os.path.join(log_dir, f"{session_id}.log")
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
            handler = logging.FileHandler(path, mode="a", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)
            # 不向上传播到 root logger（避免重复输出到 server.log）
            logger.propagate = False
        cache[session_id] = logger
        return logger

    @classmethod
    def cleanup_old_logs(cls, days: int, log_dir: str) -> int:
        """清理超过 days 天的会话日志（会话日志不按天轮转，靠启动清理控制体积）

        返回删除的文件数
        """
        removed = 0
        try:
            os.makedirs(log_dir, exist_ok=True)
            cutoff = time.time() - days * 86400
            for name in os.listdir(log_dir):
                if not name.endswith(".log"):
                    continue
                p = os.path.join(log_dir, name)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return removed

    # ---------- 聚合写入 ----------

    def set_enabled(self, enabled: bool) -> None:
        """暂停/恢复当前会话记录（已落盘内容保留）

        - 暂停：先落盘暂停前的尾部内容，再停止记录；暂停期间的输出被丢弃
        - 恢复：重建镜像（暂停期间屏幕持续变化，旧镜像状态已失真），
          从恢复时刻的显示重新开始转录
        """
        if enabled == self.enabled:
            return
        if enabled:
            self.enabled = True
            self._mirror.reset()
        else:
            self.flush_now()  # 先落盘暂停前的尾部，再停
            self.enabled = False
            self.buffer = ""

    def feed(self, data: str) -> None:
        """输出进入日志管道：镜像屏幕回放 + 聚合缓冲触发（替代逐块落盘）"""
        if not self.enabled:
            return
        try:
            self._mirror.feed(data)
        except Exception:
            pass  # 镜像回放失败不影响主流程（输出仍会广播给前端）
        self.buffer += data
        if len(self.buffer) >= self.flush_max:
            self.flush_now()
        else:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        """有新输出进入日志缓冲时调度一次延迟 flush（已调度则不重复）"""
        if not self.buffer:
            return
        if self._flush_task and not self._flush_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中的 event loop（同步上下文/测试）：直接同步 flush
            self.flush_now()
            return
        self._flush_task = asyncio.create_task(self._flusher())

    async def _flusher(self) -> None:
        try:
            await asyncio.sleep(self.flush_delay)
        except asyncio.CancelledError:
            pass
        self.flush_now()

    def flush_now(self) -> None:
        """把镜像屏幕中新显示的内容一次性写入（幂等；会话关闭前必须调用以落盘剩余内容）"""
        self.buffer = ""
        if self._logger is None:
            return
        try:
            # 提取镜像屏幕上"新显示"的行（终端显示一致的转录，见 TerminalMirror）
            lines = self._mirror.drain()
            if lines:
                self._logger.info("\n".join(lines))
        except Exception:
            pass  # 日志写入失败不影响主流程

    @staticmethod
    def collapse_cr_lines(text: str) -> str:
        """同一物理行内多次 \r 覆盖：只保留最后一次 \r 之后的内容
        （wget/进度条等 \r 刷新场景，日志只记录最终显示状态）

        注意：先把 CRLF 归一化为 LF，避免把 \r\n 换行误判为进度条覆盖
        而丢掉整行内容。

        旧版正则清洗路径的遗留工具（flush 已改走 TerminalMirror 镜像），
        保留供 SSHSession._collapse_cr_lines 与测试使用。
        """
        lines = text.replace("\r\n", "\n").split("\n")
        out = []
        for line in lines:
            if "\r" in line:
                line = line.rsplit("\r", 1)[-1]
            out.append(line)
        return "\n".join(out)

    # ---------- 历史读取 ----------

    def resize(self, cols: int, rows: int) -> None:
        """终端尺寸变化时同步镜像屏幕（未开启记录也同步，保证镜像就绪）"""
        try:
            self._mirror.resize(cols, rows)
        except Exception:
            pass  # 镜像尺寸同步失败不影响主流程

    def read_history(self, offset: int = 0, limit: int = 2000) -> str:
        """
        读取历史日志（分页加载）
        - offset: 从文件末尾倒数的字符偏移量（0 表示从最近开始）
        - limit: 最多返回多少字符
        返回清理后的日志文本
        """
        try:
            # 先落盘聚合缓冲：banner/MOTD 等刚产生（未到静默期）的输出
            # 若直接读文件会漏掉，导致"连接后的主机信息没有显示"
            self.flush_now()
            path = self.log_file
            if not os.path.exists(path):
                return ""  # 从未开启记录（文件未创建）或已被清理
            file_size = os.path.getsize(path)
            # 计算读取起始位置（从文件末尾倒数）
            start_pos = max(0, file_size - offset - limit)
            read_size = min(limit, file_size - start_pos)
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(start_pos)
                content = f.read(read_size)
            # 清理 ANSI 并格式化
            return self.format_history_logs(content)
        except Exception:
            return ""

    @staticmethod
    def format_history_logs(content: str) -> str:
        """格式化历史日志：清理ANSI、处理回车、压缩多余空格和空行"""
        # 1. 清理 ANSI 转义序列
        text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", content)
        text = re.sub(r"\x1b\][\s\S]*?(\x07|\x1b\\)", "", text)
        text = re.sub(r"\x1b[=><]", "", text)

        # 2. 处理回车 \r：同一行中 \r 后面的内容覆盖前面的，只保留最后一段
        lines = text.split("\n")
        formatted_lines = []
        for line in lines:
            if "\r" in line:
                # 只保留最后一个 \r 后面的内容
                line = line.rsplit("\r", 1)[-1]
            # 去掉行尾空格
            line = line.rstrip()
            # 压缩行首多余空格（超过8个的压缩为4个，保留有意义的缩进）
            stripped = line.lstrip()
            leading_spaces = len(line) - len(stripped)
            if leading_spaces > 8:
                line = "    " + stripped
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

        return "\n".join(result)

    # ---------- 关闭/重命名 ----------

    def finalize(self) -> None:
        """会话关闭时补全结束时间：把 _running_ 替换为 {end}_

        文件存在则重命名；不存在（会话无任何输出）也更新路径保持一致
        """
        try:
            self.flush_now()  # 落盘镜像中剩余的新显示内容
            if not self.log_file or "_running_" not in self.log_file:
                return
            end = fmt_time(time.time())
            new_path = self.log_file.replace("_running_", f"_{end}_", 1)
            if new_path == self.log_file:
                return
            if os.path.exists(self.log_file):
                # Windows 下 FileHandler 占用句柄会导致 rename 失败，先释放
                self._close_handler()
                try:
                    os.rename(self.log_file, new_path)
                except OSError:
                    pass  # 重命名失败不阻塞关闭（句柄可能仍被占用）
            self.log_file = new_path
        except Exception:
            pass  # 重命名失败不阻塞关闭（文件句柄可能仍被占用）

    def _close_handler(self) -> None:
        """关闭并移除当前 logger 的 FileHandler（释放文件句柄，供 rename/清理）"""
        if self._logger is None:
            return
        for h in self._logger.handlers[:]:
            try:
                h.close()
            except Exception:
                pass
            self._logger.removeHandler(h)
