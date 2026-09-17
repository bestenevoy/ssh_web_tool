"""终端镜像转录组件：用 pyte 把输出流回放到虚拟屏幕，按"显示内容"提取文本

参照 rssh（xterm.js 工作台）的核心思路：终端模拟器解析后的 buffer 是唯一事实源，
提取文本与显示构造性一致。相比此前的正则清洗（clean_ansi + \\r 覆盖合并）：

- 退格 \\x08、\\r 行内覆盖、光标移动重绘、进度条刷新 → 自然收敛为屏幕当前状态
  （flush 时提取屏幕文本 = 用户看到的内容）
- bell \\x07 等控制字符不会进入文本
- 顶行滚出时在丢弃前捕获（长输出不丢；比 rssh 的整屏快照更完整）
- 清屏（ED2）前捕获被擦行（clear 后先前显示的内容保留在日志中）
- alt-screen（vim/htop/less 等全屏应用）期间的内容不混入转录

flush 模型（由 SessionLog 驱动）：
- feed(data)：回放到屏幕（alt 期间喂给 scratch 屏幕吸收，不采集）
- drain()：按显示顺序产出"新出现"的行——滚出行 → clear 被擦行 → 屏幕脏行，
  逐行快照去重（重绘相同内容不重复记录；快照随滚动同步移位保持对齐）

注意：pyte 不实现 alt buffer 切换，alt 守卫在流层面完成（按 CSI ?1049/1047/47
h/l 序列切分数据段，alt 段喂给 scratch 屏幕吸收）；CSI L/M（插删行）导致的内容
出屏与 rssh 行为一致（屏幕上已不可见，不转录）。
"""

import re
from collections.abc import Callable

from pyte.screens import Margins, wcwidth
from pyte.screens import Screen as _PyteScreen
from pyte.streams import Stream

# alt-screen 切换序列（xterm 1049 / 经典 1047、47；允许与其他私有参数合并出现，
# 如 \x1b[?1000;1049h；?1000h/?2004h/?25l 等其他序列不会被误匹配）
_ALT_TOKEN_RE = re.compile(r"\x1b\[\?[\d;]*(?:1049|1047|47)[hl]")

# 默认屏幕尺寸（会话 resize 前的兜底；与 SSHSession._last_cols/_last_rows 一致）
DEFAULT_COLUMNS = 120
DEFAULT_ROWS = 40

# 未转录滚出行上限（镜像始终 feed：会话未开启记录时不 drain，滚出行持续累积；
# 上限防长期会话内存无限增长——超限丢最老行，极端长输出场景放弃更早的历史）
_MAX_SCROLLED_LINES = 20000


class TranscriptScreen(_PyteScreen):
    """带转录捕获的 pyte 屏幕

    通过回调上抛两类"内容即将离开屏幕"事件，由 TerminalMirror 决定是否转录：
    - on_scroll_top(text)：顶行滚出（index 时光标在滚动区底边）
    - on_erase_line(y, text)：整屏擦除（ED2/ED3，clear 命令）前逐行回调
    """

    def __init__(
        self,
        columns: int,
        lines: int,
        on_scroll_top: Callable[[str], None] | None = None,
        on_erase_line: Callable[[int, str], None] | None = None,
    ) -> None:
        self.on_scroll_top = on_scroll_top
        self.on_erase_line = on_erase_line
        self.scrolled_off: list[str] = []  # 已捕获的滚出行（消费后由镜像清空）
        self.erased_lines: list[tuple[int, str]] = []  # 已捕获的被擦行 (y, text)
        super().__init__(columns, lines)

    def render_line(self, y: int) -> str:
        """渲染单行显示文本（跳过宽字符占位单元；去除行尾空白，对齐 rssh 语义）"""
        line = self.buffer.get(y)
        if not line:
            return ""
        out: list[str] = []
        wide = False
        for x in range(self.columns):
            if wide:  # 宽字符占位单元（width=0 continuation）不重复输出
                wide = False
                continue
            data = line[x].data
            if wcwidth(data[0]) == 2:
                wide = True
            out.append(data)
        return "".join(out).rstrip()

    def index(self) -> None:
        _, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and self.on_scroll_top is not None:
            # 顶行即将滚出：先上抛再交给 pyte 丢弃
            self.on_scroll_top(self.render_line(0))
        super().index()

    def erase_in_display(self, how: int = 0, *args: object, **kwargs: object) -> None:
        # clear/ED2：被擦内容用户已看到，逐行上抛后再擦（ED0/ED1 属于局部
        # 重绘，内容仍留屏幕上或马上重画，不转录避免重复）
        if (how == 2 or how == 3) and self.on_erase_line is not None:
            for y in range(self.lines):
                self.on_erase_line(y, self.render_line(y))
        super().erase_in_display(how, *args, **kwargs)


class TerminalMirror:
    """单会话终端镜像：feed 输出流 → drain 提取"新显示"的行

    每个会话一个实例，会话创建起就持续 feed（开启记录晚于连接时，连接信息
    banner 等早期输出也能随首次 flush 进入日志——用户要求"这部分必须要有"）；
    未开启记录期间只累积不 drain（滚出行有上限兜底），开启后一次转录全程。
    """

    def __init__(self, columns: int = DEFAULT_COLUMNS, rows: int = DEFAULT_ROWS) -> None:
        self._columns = columns
        self._rows = rows
        self._alt = False  # 当前是否处于 alt-screen
        # 逐行快照：snapshot[y] = 行 y 上次已转录的内容（None=尚未转录）。
        # index 滚动时同步移位，保证脏行去重与滚出/擦除捕获判断始终逐行对齐
        self._snapshot: list[str | None] = [None] * rows
        self._has_new = False  # 自上次 drain 后主屏是否有新输出进入
        self._pending_out: list[str] = []  # resize 前补转录的行（下次 drain 产出）
        self.screen = self._build_screen()
        # scratch 屏幕：alt 期间吸收输出（内容不进入转录）
        self._scratch = _PyteScreen(self._columns, self._rows)
        # Stream 必须持久复用：pyte 的解析状态（不完整转义序列）跨 feed 保存，
        # 逐段新建会截断跨 chunk 的序列
        self._stream = Stream(self.screen)
        self._scratch_stream = Stream(self._scratch)

    def _build_screen(self) -> TranscriptScreen:
        return TranscriptScreen(
            self._columns, self._rows, on_scroll_top=self._on_scroll_top, on_erase_line=self._on_erase_line
        )

    # ---------- 输入 ----------

    def feed(self, data: str) -> None:
        """喂入一段输出流（按 alt 切换序列切分，各段喂给对应屏幕）"""
        pos = 0
        alt = self._alt
        for m in _ALT_TOKEN_RE.finditer(data):
            if m.start() > pos:
                self._feed_segment(data[pos : m.start()], alt)
            alt = m.group(0).endswith("h")
            pos = m.end()
        if pos < len(data):
            self._feed_segment(data[pos:], alt)
        self._alt = alt

    def _feed_segment(self, data: str, to_alt: bool) -> None:
        if not data:
            return
        try:
            if to_alt:
                self._scratch_stream.feed(data)  # alt 内容被 scratch 吸收，不采集
            else:
                self._stream.feed(data)
                self._has_new = True
        except Exception:
            pass  # 镜像回放失败不影响主流程（输出仍会广播给前端）

    # ---------- 捕获回调 ----------

    def _on_scroll_top(self, text: str) -> None:
        """顶行滚出：内容与快照一致说明已转录过，跳过；否则捕获（可能尚未落盘）"""
        if text and text != self._snapshot[0]:
            off = self.screen.scrolled_off
            if len(off) >= _MAX_SCROLLED_LINES:
                off.pop(0)  # 超限丢最老行（长期未 drain 的内存防护）
            off.append(text)
        # 快照随屏幕滚动同步移位（新滚入的底行视为未转录）
        self._snapshot.pop(0)
        self._snapshot.append(None)

    def _on_erase_line(self, y: int, text: str) -> None:
        """clear/ED2 被擦行：内容未转录过才捕获（已转录过的不重复记录）"""
        if text and y < len(self._snapshot) and text != self._snapshot[y]:
            self.screen.erased_lines.append((y, text))

    # ---------- 提取 ----------

    def drain(self) -> list[str]:
        """提取自上次 drain 以来新显示的行（显示顺序；重绘相同内容不重复）

        顺序：滚出行（最早） → resize 前补转录行 → clear 被擦行 → 当前屏幕脏行（最新）
        """
        if not self._has_new:
            return []
        self._has_new = False
        out: list[str] = []
        # 1. 滚出顶部的行（长输出转录）
        if self.screen.scrolled_off:
            out.extend(self.screen.scrolled_off)
            self.screen.scrolled_off = []
        # 2. resize 前补转录的行
        if self._pending_out:
            out.extend(self._pending_out)
            self._pending_out = []
        # 3. clear/ED2 被擦除的行（快照去重：已转录过的不重复）
        if self.screen.erased_lines:
            batch = self.screen.erased_lines
            self.screen.erased_lines = []
            for y, text in batch:
                if text != self._snapshot[y]:
                    self._snapshot[y] = text
                    out.append(text)
        # 4. 当前屏幕上的脏行（逐行快照去重）
        dirty = self.screen.dirty
        self.screen.dirty = set()
        for y in sorted(dirty):
            if y >= self._rows:
                continue  # resize 中间态保护
            text = self.screen.render_line(y)
            if text and text != self._snapshot[y]:
                self._snapshot[y] = text
                out.append(text)
        return out

    # ---------- 尺寸/重置 ----------

    def resize(self, columns: int, rows: int) -> None:
        """同步终端尺寸（与 PTY resize 一致；相同尺寸时无操作）

        pyte 的行重排/游标恢复行为不可靠（可能乱序、游标越界），故尺寸变化时
        先把当前屏幕上未转录的行补转录（重排前行号对应关系可靠），再按新尺寸
        重建屏幕。缩小后顶部被丢弃的行不转录（屏幕上已不可见，与 rssh 一致）。
        重建屏幕时保留未转录的滚出行（未开启记录时长期累积的连接历史不丢）；
        被擦行缓存丢弃（旧 y 坐标在新尺寸下不可靠，其内容大部分已在滚出行中）。
        """
        if columns <= 0 or rows <= 0 or (columns == self._columns and rows == self._rows):
            return
        for y in range(self._rows):
            try:
                text = self.screen.render_line(y)
            except Exception:
                break  # 屏幕状态异常时放弃补转录，保重建后正常
            if text and text != self._snapshot[y]:
                self._snapshot[y] = text
                self._pending_out.append(text)
        kept_scrolled = self.screen.scrolled_off  # 未转录滚出行跨重建保留
        alt = self._alt  # alt 期间 resize：保持 alt 状态，重绘继续被 scratch 吸收
        self._columns, self._rows = columns, rows
        self._rebuild()
        self._alt = alt
        self.screen.scrolled_off = kept_scrolled
        if self._pending_out or kept_scrolled:
            # 有待产出行时唤醒 drain（_rebuild 会清 _has_new，不清会滞留到下次输出）
            self._has_new = True

    def reset(self) -> None:
        """完全重建镜像（清空全部状态，从空白屏幕重新开始）"""
        self._pending_out = []
        self._rebuild()

    def _rebuild(self) -> None:
        """按当前尺寸重建屏幕与解析流（alt 状态/快照/待产出一并清空）"""
        self._alt = False
        self._snapshot = [None] * self._rows
        self._has_new = False
        self.screen = self._build_screen()
        self._scratch = _PyteScreen(self._columns, self._rows)
        self._stream = Stream(self.screen)
        self._scratch_stream = Stream(self._scratch)
