"""SFTP 传输任务注册表：上传/下载的进度、取消与历史记录。

四个传输端点（面板上传/下载、工作台 preop 上传/下载到本机）在分块循环里
经 task.add_done() 上报字节数；取消 = 置 canceled 标志，循环下一次迭代抛
TransferCanceled 中断并清理半成品。前端轮询 GET /api/sftp/transfers 渲染
传输列表（速度由前端按两次采样的字节差计算，后端不存速率状态）。

进程内内存态：应用重启即清空，历史只保留最近 MAX_HISTORY 条完成态任务。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

MAX_HISTORY = 50


class TransferCanceled(Exception):
    """分块循环检测到取消请求（调用方负责清理半成品并置 canceled 状态）"""


@dataclass
class TransferTask:
    id: str
    session_id: str
    direction: str  # "upload" | "download"
    filename: str
    src: str
    dst: str
    host: str = ""  # 远端主机显示名（上传=目标主机，下载=来源主机）
    total: int = -1  # 总字节数，-1 表示未知
    done: int = 0
    status: str = "running"  # running / done / failed / canceled
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    canceled: bool = False

    def add_done(self, n: int) -> None:
        """上报已传字节；若已请求取消则抛 TransferCanceled 让调用方中断循环。"""
        self.done += n
        if self.canceled:
            raise TransferCanceled()

    def finish(self, status: str = "done", error: str = "") -> None:
        self.status = status
        self.error = error[:300]
        self.finished_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "direction": self.direction,
            "filename": self.filename,
            "src": self.src,
            "dst": self.dst,
            "host": self.host,
            "total": self.total,
            "done": self.done,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class TransferRegistry:
    """全局传输任务表（按创建顺序存储，列表输出活动任务在前、完成按时间倒序）"""

    def __init__(self) -> None:
        self._tasks: dict[str, TransferTask] = {}

    def create(
        self,
        session_id: str,
        direction: str,
        filename: str,
        src: str,
        dst: str,
        host: str = "",
        total: int = -1,
    ) -> TransferTask:
        task = TransferTask(
            id=uuid.uuid4().hex[:12],
            session_id=session_id,
            direction=direction,
            filename=filename,
            src=src,
            dst=dst,
            host=host,
            total=total,
        )
        self._tasks[task.id] = task
        self._prune()
        return task

    def get(self, task_id: str) -> TransferTask | None:
        return self._tasks.get(task_id)

    def list(self, session_id: str | None = None) -> list[TransferTask]:
        self._prune()
        tasks = [t for t in self._tasks.values() if session_id is None or t.session_id == session_id]
        tasks.sort(key=lambda t: (t.status != "running", -t.started_at))
        return tasks

    def cancel(self, task_id: str) -> tuple[bool, str]:
        """请求取消。返回 (是否受理, 原因)。完成态任务不可取消。"""
        task = self._tasks.get(task_id)
        if task is None:
            return False, "任务不存在"
        if task.status != "running":
            return False, "任务已结束"
        task.canceled = True
        return True, ""

    def _prune(self) -> None:
        finished = [t for t in self._tasks.values() if t.status != "running"]
        if len(finished) <= MAX_HISTORY:
            return
        finished.sort(key=lambda t: t.started_at)
        for t in finished[: len(finished) - MAX_HISTORY]:
            self._tasks.pop(t.id, None)


transfer_registry = TransferRegistry()
