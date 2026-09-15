"""终端输出广播组件：有界监听器队列 + 输出/清洗缓冲区维护

职责（原 SSHSession 中"输出广播机制"一节的所有逻辑）：
- 把输出广播给所有监听器：有界队列（约 100 块 ≈ 400KB），消费慢的监听器
  （前端 ws 慢）不会无限堆积内存，队列满时丢最旧保最新
- 维护输出缓冲区（增量清洗 ANSI 后的 _clean_buffer），供终端状态分析使用

不负责：回显解析、日志持久化（由 SSHSession 编排调用 EchoParser / SessionLog 完成）。
"""

import asyncio
from collections.abc import Callable


class OutputBus:
    """终端输出广播总线（每个会话一个实例）"""

    QUEUE_MAX = 100  # 监听器队列上限（块）
    BUF_LIMIT = 8000  # 输出/清洗缓冲区字符上限

    def __init__(self, clean_ansi_fn: Callable[[str], str]) -> None:
        self._clean_text = clean_ansi_fn
        self._listeners: list[asyncio.Queue] = []
        self._buffer: list[str] = []  # 原始输出块（用于状态检测）
        self._clean_buffer = ""  # 已清洗 ANSI 的输出缓冲区（增量维护，避免全量正则）
        self._clean_dirty = False  # 标记是否有新输出需要重新计算状态

    # ---------- 监听器管理 ----------

    def add_listener(self) -> asyncio.Queue:
        """注册输出监听器，返回一个 Queue，后续输出会写入这个队列"""
        q: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAX)
        self._listeners.append(q)
        return q

    def remove_listener(self, q: asyncio.Queue) -> None:
        """移除输出监听器"""
        if q in self._listeners:
            self._listeners.remove(q)

    @staticmethod
    def _drop_oldest(q: asyncio.Queue, data: str) -> None:
        """队列满时丢弃最旧的一条再放入最新数据（终端语义：保留最新内容）"""
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(data)
        except Exception:
            pass

    # ---------- 广播 ----------

    def broadcast(self, data: str) -> None:
        """广播输出给所有监听器，并维护输出/清洗缓冲区"""
        # 维护缓冲区（保留最后 BUF_LIMIT 字符）
        self._buffer.append(data)
        total = sum(len(s) for s in self._buffer)
        if total > self.BUF_LIMIT:
            combined = "".join(self._buffer)
            self._buffer = [combined[-self.BUF_LIMIT :]]
            # 缓冲区压缩后同步更新清洗缓冲区
            self._clean_buffer = self._clean_text(combined)[-self.BUF_LIMIT :]
        else:
            # 增量清洗：只清洗新增数据块，避免每次全量正则
            self._clean_buffer += self._clean_text(data)
            if len(self._clean_buffer) > self.BUF_LIMIT:
                self._clean_buffer = self._clean_buffer[-self.BUF_LIMIT :]
        self._clean_dirty = True
        # 广播给所有监听器：put_nowait 不阻塞；队列满时丢最旧保最新，
        # 避免慢监听器（前端 ws 阻塞）拖住整个广播链（日志/echo 解析/其他监听器）
        for q in self._listeners:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                self._drop_oldest(q, data)

    # ---------- 状态检测访问接口 ----------

    @property
    def dirtied(self) -> bool:
        """是否有新输出（终端状态需要重算）"""
        return self._clean_dirty

    def cleaned_text(self) -> str:
        """增量清洗后的缓冲区文本（终端状态分析用）"""
        return self._clean_buffer

    def raw_chunks(self) -> list[str]:
        """原始输出块快照（测试/外部校验用）"""
        return list(self._buffer)
