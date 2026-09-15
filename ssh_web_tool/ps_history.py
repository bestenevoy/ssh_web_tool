"""PSReadLine 历史文件组件：本地 PowerShell 真实命令采集 + 拦截命令补写

两个职责：

1. 采集（修复"Tab 补全记录的是补全前文本"）：
   前端行缓冲只能记录"键入的文本"，Tab 补全 / 预测（Prediction）接受后的最终
   命令由 shell（PSReadLine）渲染，键入层与回显层都拿不到可靠文本。
   PSReadLine 会把每条**实际执行**的命令（补全后的最终文本）追加到
   ConsoleHost_history.txt——这是权威数据源。本组件在会话回车时点增量读取该
   文件，把与键入前缀匹配的新增行作为真实命令补记（record_echo_command 的
   前缀清理/去重逻辑负责与前端记录合并）。
   其他 PowerShell 窗口写入的行不匹配待定前缀，自动忽略，不会污染应用历史。

2. 补写（修复"拦截的 ssh 命令不在 shell 自身历史里"）：
   本地终端输入 ssh 命令被应用拦截时，回车未送达本地 shell，shell 没执行过
   这条命令，其自身历史（↑ 召回）自然没有。连接发起时把命令行追加进
   PSReadLine 历史文件，之后新开的 shell 会话即可 ↑ 召回。
   限制：已运行的 shell 进程内存历史无法外部注入（PSReadLine 机制限制），
   当前会话需用应用内的重连/历史搜索；cmd 无历史文件机制（doskey 仅进程内），
   无法补写，跳过。
"""

import asyncio
import os
import re
from collections import deque
from typing import Protocol


class TailerLike(Protocol):
    """采集器最小接口（便于测试替身/类型约束）"""

    def note_command(self, typed: str) -> None: ...

    def append_command(self, command: str) -> None: ...


def get_history_file() -> str:
    """PSReadLine 历史文件路径（Windows PowerShell 5.1 与 pwsh 共用同一文件）"""
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Microsoft", "Windows", "PowerShell", "PSReadLine", "ConsoleHost_history.txt")


def _norm(s: str) -> str:
    """比较用归一化：统一 / 与 \\、压缩空白、忽略大小写

    PowerShell Tab 补全会把 /va 补成 \\var（路径分隔符转换），预测接受、
    大小写差异同理，直接字符串比对会漏配。
    """
    return re.sub(r"\s+", " ", s.strip().replace("/", "\\")).lower()


class PSReadlineTailer:
    """PSReadLine 历史文件增量采集器（每个本地 PowerShell 会话一个实例）"""

    POLL_DELAY = 0.3  # 秒：回车后延迟读取（PSReadLine 执行命令后立即追加）
    MAX_PENDING = 8  # 待定前缀上限（快速连续执行/多行粘贴场景），超出丢最旧

    def __init__(self, record_cb, history_file: str | None = None) -> None:
        """record_cb: async (cmd: str) -> None，采集到真实命令后的记录回调"""
        self._record_cb = record_cb
        self._file = history_file or get_history_file()
        try:
            self._offset = os.path.getsize(self._file) if os.path.isfile(self._file) else 0
        except OSError:
            self._offset = 0
        self._pending: deque[str] = deque()
        self._poll_task: asyncio.Task | None = None
        self._record_task: asyncio.Task | None = None

    # ---------- 会话侧入口 ----------

    def note_command(self, typed: str) -> None:
        """回车成行时调用：记录键入文本为待定前缀，并调度一次增量读取"""
        if not typed.strip():
            return
        self._pending.append(typed)
        while len(self._pending) > self.MAX_PENDING:
            self._pending.popleft()
        self._schedule_poll()

    def append_command(self, command: str) -> None:
        """把被拦截未执行的命令（如 ssh 连接行）补写进历史文件（尽力而为）

        追加后同步推进读取基线，避免补写的行被采集器当成"新执行的命令"误记录。
        """
        line = command.strip()
        if not line:
            return
        try:
            parent = os.path.dirname(self._file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._offset = os.path.getsize(self._file)
        except OSError:
            pass  # 补写失败不影响主流程

    # ---------- 采集 ----------

    def _schedule_poll(self) -> None:
        """调度延迟读取（已调度则不重复；无事件循环时同步执行，测试场景兜底）"""
        if self._poll_task and not self._poll_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._poll_sync()
            return
        self._poll_task = asyncio.create_task(self._poll())

    async def _poll(self) -> None:
        try:
            await asyncio.sleep(self.POLL_DELAY)
        except asyncio.CancelledError:
            return
        try:
            new_lines = self._read_new_lines()
        except Exception:
            new_lines = []
        if not new_lines:
            return
        if len(new_lines) == 1:
            # 恰有一条新增行：大概率就是本会话刚执行的命令（其他窗口恰好在这
            # 0.3s 窗口内写历史的概率极低）。前缀匹配失败也接受，兜底
            # 预测接受（内联建议可能不保序）等转换场景
            self._consume(new_lines[0], allow_loose=True)
        else:
            for line in new_lines:
                self._consume(line, allow_loose=False)

    def _poll_sync(self) -> None:
        """无事件循环时的同步采集（测试兜底）"""
        try:
            new_lines = self._read_new_lines()
        except Exception:
            new_lines = []
        for line in new_lines:
            self._consume(line, allow_loose=len(new_lines) == 1)

    def _read_new_lines(self) -> list[str]:
        """读取自上次基线以来的新增行，并推进基线"""
        if not os.path.isfile(self._file):
            return []
        size = os.path.getsize(self._file)
        if size < self._offset:
            self._offset = 0  # 文件被重建/清空：重置基线
        if size == self._offset:
            return []
        with open(self._file, encoding="utf-8", errors="replace") as f:
            f.seek(self._offset)
            data = f.read()
        self._offset = size
        return [ln for ln in data.splitlines() if ln.strip()]

    def _consume(self, line: str, allow_loose: bool) -> None:
        """把一条新增行与待定前缀比对：完全一致=前端已记（去重）；更长=补全结果（补记）"""
        norm = _norm(line)
        for i, typed in enumerate(self._pending):
            tnorm = _norm(typed)
            if not tnorm:
                continue
            if norm == tnorm:
                del self._pending[i]  # 与键入一致：前端已即时记录，无需补记
                return
            if norm.startswith(tnorm) and len(norm) > len(tnorm):
                del self._pending[i]  # Tab 补全/预测扩展：以 shell 执行的为准补记
                self._fire_record(line)
                return
        if allow_loose and self._pending:
            self._pending.popleft()
            self._fire_record(line)

    def _fire_record(self, cmd: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._record_task = asyncio.create_task(self._record_cb(cmd))
