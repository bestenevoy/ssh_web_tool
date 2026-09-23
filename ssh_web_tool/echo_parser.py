"""命令回显解析组件：从终端输出流提取"提示符后的实际命令"

职责（原 SSHSession 中 _parse_echo_line 一带的所有逻辑）：
- 维护行缓冲（数据块可能截断命令行，留到下一块补齐）
- 识别提示符并剥离，提取实际执行的命令（含 Tab 补全/历史翻查后的最终内容）
- 3 秒同命令去重（防止与前端/CLI 注入重复记录）

只做解析：记录动作由调用方（SSHSession 的 _record_echo）负责。
"""

import re
import time

# ---------- 已知提示符模式（按优先级排列，行首匹配） ----------
# 每个模式匹配"提示符 + 其后空白"，match.end() 即命令开始位置。
# 覆盖：bash/debian 默认、python、mysql/sqlite/redis/mongo 等交互程序、
# zsh %（macOS 默认，过程式匹配见 _match_zsh_percent）、oh-my-zsh ➜、
# starship/纯箭头 ❯、fish、Windows PowerShell/cmd。
# 不匹配单独的 ">"（node 提示符，太通用容易误判普通输出行）。
_PROMPT_PATTERNS: list[re.Pattern[str]] = [
    # shell: user@host:path$ 或 user@host:path#（bash/debian 默认）
    re.compile(r"[\w.-]+@[\w.-]+:[^#$\n]*[#$]\s*"),
    re.compile(r">>>\s*"),  # python
    re.compile(r"\.\.\.\s*"),  # python 续行（跳过，不作为独立命令）
    re.compile(r"mysql>\s*"),  # mysql
    re.compile(r"sqlite>\s*"),  # sqlite
    re.compile(r"[\d.]+:\d+>\s*"),  # redis: 127.0.0.1:6379>
    re.compile(r"[a-zA-Z_][\w.-]*>\s*"),  # 通用: xxx> (mongo, postgres, mini 等)
    # fish 默认提示符：user@host ~/path> （含全路径 user@host /a/b>）
    re.compile(r"[\w.-]+@[\w.-]+[ ]+[^\s>]*>\s*"),
    # Windows PowerShell：PS C:\Users\x>（续行提示符 >> 一并吃掉）
    re.compile(r"PS [^>\n]*>+\s*"),
    # Windows cmd：C:\Users\x>
    re.compile(r"[A-Za-z]:\\[^>\n]*>+\s*"),
    # oh-my-zsh：➜  dir [git:(branch)] [✗/✘]；路径段允许 ~ / \ 等路径字符；
    # 段间空白用 \s*（提示符独占行时无尾随空格）
    re.compile(r"➜\s+(?:[\w~./@\\-]+\s*)?(?:git:(?:\([^)]*\)|[\w-]+)\s*)?(?:[✗✘]\s*)?"),
    # starship / 纯箭头提示符：❯ cmd（p10k 两行式第二行也命中）
    re.compile(r"❯\s*"),
    # starship / p10k 两行式第一行（提示符独占一行）：~/repo ❯ ——要求路径段
    # 含 ~ / @（纯单词+❯ 的输出行如 "error ❯ fail" 不算提示符）
    re.compile(r"[\[\]~\w@. \-/\\]*[~/@][\[\]~\w@. \-/\\]*❯\s*"),
]

# zsh % 提示符段长度上限（超过视为普通输出行）
_ZSH_MAX_PROMPT_LEN = 120


def _match_zsh_percent(s: str, prompt_only: bool = False) -> int | None:
    """zsh 风格 % 提示符匹配（macOS 默认 user@host ~ %、zsh 默认 hostname%、~/path %）

    误报防线（普通输出行大量含百分比）：
    - 提示符段（% 之前）必须含字母且长度受限
    - 提示符段最后一个空白分隔 token 非纯数字
      （"50% done"、"100% packet loss"、"/dev/sda 45% used"、"[ 50%] Building"
       等输出行均被拒绝）
    - % 之后必须紧跟空白或行尾（"50%of" 不算）
    - 仅提示符行（prompt_only）额外要求提示符段含 @ 或路径字符：裸 hostname%
      空回车不触发"下一行是命令"捕获，避免把后续输出误记为命令
    返回提示符结束位置（含其后空白）；非提示符返回 None
    """
    i = s.find("%")
    if i <= 0:
        return None  # 无 % 或 % 在行首（空提示符段，太宽松不识别）
    head = s[:i].rstrip()
    if len(head) > _ZSH_MAX_PROMPT_LEN or not re.search(r"[A-Za-z]", head):
        return None
    tokens = head.split()
    if tokens and tokens[-1].isdigit():
        return None
    rest = s[i + 1 :]
    if rest and not rest[0].isspace():
        return None
    if prompt_only and not re.search(r"[@~/\\]", head):
        return None
    end = i + 1
    while end < len(s) and s[end] in " \t":
        end += 1
    return end


def _strip_one_prompt(s: str, prompt_only: bool = False) -> int | None:
    """若 s 以一个已知提示符开头，返回提示符（含其后空白）结束位置；否则 None

    供 extract_echo_command（循环剥离）与 is_prompt_only（整行判断）共用。
    边界校验：提示符与后续文本之间必须有空白（提示符独占行时允许直接到行尾），
    防止输出行被误剥（如 vite/nuxi 输出 "➜  Local:   http://..." 不算提示符）。
    """
    for pat in _PROMPT_PATTERNS:
        m = pat.match(s)
        if m:
            end = m.end()
            if end >= len(s) or s[end] in " \t" or s[end - 1] in " \t":
                return end
    return _match_zsh_percent(s, prompt_only=prompt_only)


def _arms_next_line_capture(s: str) -> bool:
    """提示符独占整行时，是否武装"下一行是命令回显"捕获

    仅自研/简易 shell 需要该机制：通用 xxx> 提示符（mini 等）与只能靠宽松
    PS1 兜底识别的自定义提示符（[myshell]$ 等）。
    标准 shell（bash/zsh %、fish、PS/cmd、箭头）虽也可能 fullmatch 宽松 PS1
    正则（如 root@host:~#），但它们能被精确模式识别 → 同行回显，不武装
    （防误记空回车后的输出）。
    """
    if _PROMPT_PATTERNS[6].fullmatch(s):  # 通用 xxx>（mongo/postgres/mini 等同形态）
        return True
    if re.fullmatch(r"[\[\]~\w@.\- :/\\]*?[#$]\s*", s):  # 宽松 PS1 兜底形态
        # 能被精确提示符模式识别 = 标准 shell，不武装
        return _strip_one_prompt(s, prompt_only=True) is None
    return False


# 清 ANSI 但保留 \r\n（回显解析需要 \r 判断行内覆盖）。CSI 参数区按标准取
# 0x30-0x3F（数字与 : ; < = ?），覆盖私有 \x1b[?2004h、kitty \x1b[>1u 等
_ANSI_KEEP_CR_RE = re.compile(r"\x1b\[[0-9;:<=>?]*[a-zA-Z]")
_OSC_KEEP_CR_RE = re.compile(r"\x1b\][\s\S]*?(\x07|\x1b\\)")
# DCS/SOS/PM/APC（\x1bP/X/^/_ 起，ST/BEL 止）
_DCS_KEEP_CR_RE = re.compile(r"\x1b[PX^_][\s\S]*?(?:\x1b\\|\x07)")
# 双字符转义（ESC ( B 字符集指定 / Fe 序列）：须在删 ESC 控制符前整体剥离，
# 否则残留下 "(B" 字面量被当作命令记录（tput sgr0/提示符重置常见输出）
_ESC2_KEEP_CR_RE = re.compile(r"\x1b[()\)][0-9A-Za-z]|\x1b[=><78cM]")
# 列对齐输出形态：词间 3+ 连续空白（free -h/ps 等表格输出特征，提交的命令不会出现）
_OUTPUT_TABLE_RE = re.compile(r"\S\s{3,}\S")


class EchoParser:
    """回显解析状态机（每个会话一个实例）

    支持两类回显形态：
    - 标准 readline shell（bash/zsh）：提示符与命令同行，Tab 补全增量直接拼在行内
    - 简易/自研 shell（如 mini>）：提示符独立成行，命令回显在下一行
      （is_prompt_only + _await_cmd 状态机捕获，否则这类 shell 完全无法从
      回显记录命令，只剩前端键入版——Tab 补全场景会记录补全前文本）
    """

    # 异常情况下行缓冲上限，超过直接丢弃防止无限增长
    BUFFER_LIMIT = 4000
    # 同命令去重窗口（秒）
    DEDUP_WINDOW = 3.0
    # 提示符独立成行时的行长度上限（超过视为普通输出行，防误记）
    PROMPT_LINE_MAX = 120

    def __init__(self) -> None:
        self.buffer = ""  # 输出行缓冲（可能被数据块截断，留到下一块补齐）
        self.last_cmd = ""  # 最近一次解析记录的命令（去重防重复记录）
        self.last_time = 0.0  # 最近一次记录时间
        self._await_cmd = False  # 提示符独立成行后，等待下一非空行作为命令回显

    @classmethod
    def clean_ansi_keep_cr(cls, text: str) -> str:
        """清理 ANSI 转义序列，但保留 \r\n（区别于 prompt_detect.clean_ansi 会去掉 \r）"""
        text = _ANSI_KEEP_CR_RE.sub("", text)
        text = _OSC_KEEP_CR_RE.sub("", text)
        text = _DCS_KEEP_CR_RE.sub("", text)
        text = _ESC2_KEEP_CR_RE.sub("", text)
        return text

    @staticmethod
    def _strip_control_chars(s: str) -> str:
        """清除残留控制字符（bell \x07、\b 之外的不可见字符）"""
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)

    @staticmethod
    def _apply_backspaces(line: str) -> str:
        """回放行内退格擦除（readline 退格编辑/↑ 召回时输出 \b \b 或 \b）

        逐字符重放：\b 弹出一个字符，\b \b（退格-空格-退格）自然等效于擦除。
        """
        if "\x08" not in line:
            return line
        out: list[str] = []
        for ch in line:
            if ch == "\x08":
                if out:
                    out.pop()
            else:
                out.append(ch)
        return "".join(out)

    @classmethod
    def is_prompt_only(cls, line: str) -> bool:
        """该行是否为"只有提示符"的行（命令回显在下一行，如 mini> 自研 shell）"""
        s = line.strip()
        if not s or len(s) > cls.PROMPT_LINE_MAX:
            return False
        # python 续行提示符不是新命令的起点（其后无独立命令回显行）
        if s.startswith("..."):
            return False
        n = _strip_one_prompt(s, prompt_only=True)
        if n is not None and not s[n:].strip():
            return True
        # 宽松兜底与 extract_echo_command 保持一致（[user@host ~]$ 等）
        return bool(re.fullmatch(r"[\[\]~\w@.\- :/\\]*?[#$]\s*", s))

    @classmethod
    def extract_echo_command(cls, line: str) -> str:
        """从一行输出中提取提示符之后的命令文本；非提示符行返回空串
        - 已知提示符**循环剥离**：阿里云等 shell 每次 readline 重绘都会清屏重写，
          一行内可能出现多个连续提示符（root@host:~# root@host:~# cmd）
        - zsh % 提示符（macOS 默认等）由 _match_zsh_percent 处理（带误报防线）
        - 自定义 PS1 宽松兜底（仅当未识别出已知提示符时）：行首到第一个 $/# 之间
          为提示符标识（如 root@host#、[user@host ~]$），其后是命令；
          宽松匹配失败 = 普通输出行，不记录
        """
        s = line.strip()
        # 清除残留控制字符（bell \x07 等，readline 补全失败/响铃会混进行尾）
        s = cls._strip_control_chars(s).strip()
        if not s:
            return ""
        # python 续行提示符(...)：是上一命令的延续内容，不作为独立命令记录
        if s.startswith("..."):
            return ""
        # 1. 精确已知提示符：循环剥离（readline 清屏重绘可能一行内多个提示符）
        stripped = False
        cmd = s
        while True:
            n = _strip_one_prompt(cmd)
            if n is None:
                break
            stripped = True
            rest = cmd[n:]
            if not rest.strip():
                return ""  # 只剩提示符（空命令回车）
            cmd = rest
        # 2. 未识别出已知提示符时，才尝试自定义 PS1 宽松匹配；失败 = 普通输出行
        if not stripped:
            m2 = re.match(r"^[\[\]~\w@.\- :/\\]*?[#$]\s*", cmd)
            if not m2:
                return ""
            rest2 = cmd[m2.end() :].strip()
            if not rest2:
                return ""  # 提示符后无内容（空命令回车）
            cmd = rest2
        cmd = cmd.strip()
        if not cmd or len(cmd) > 500:
            return ""
        return cmd

    def feed(self, data: str) -> list[str]:
        """喂入一段输出，返回本次解析出的命令列表（已做 3 秒去重）"""
        cmds: list[str] = []
        try:
            text = self.clean_ansi_keep_cr(data)
            self.buffer += text
            if len(self.buffer) > self.BUFFER_LIMIT:
                self.buffer = ""  # 异常情况下防止无限增长
                return cmds
            if "\n" not in self.buffer:
                return cmds
            parts = self.buffer.split("\n")
            self.buffer = parts[-1]  # 最后一段不完整，留到下一块
            for raw in parts[:-1]:
                line = raw.rstrip("\r")
                if "\r" in line:
                    # 行内多次 \r 覆盖（Tab 补全会重写整行）：只保留最后一次覆盖后的内容
                    line = line.rsplit("\r", 1)[-1]
                line = self._apply_backspaces(line)
                cmd = self.extract_echo_command(line)
                if cmd:
                    self._await_cmd = False
                    self._dedup_append(cmds, cmd)
                    continue
                # 无命令的行：仅自研/简易 shell（通用 xxx>、宽松 PS1 兜底）的提示符
                # 独立成行时，下一个非空行是该提示符的命令回显；标准 shell
                # （bash/zsh/fish/PS/cmd/箭头/python）均同行回显，提示符独占行只会
                # 出现在空回车场景，武装捕获只会把后续输出误记为命令
                if self.is_prompt_only(line) and _arms_next_line_capture(line.strip()):
                    self._await_cmd = True
                elif self._await_cmd and line.strip():
                    self._await_cmd = False
                    candidate = self._strip_control_chars(line).strip()
                    # 下一行捕获输出形态过滤：捕获意图是"提示符独立成行后用户敲的命令"，
                    # 表格对齐形态（free -h 类输出行被误武装的场景）一律不收——
                    # 这是"回显内容被当成命令"的最大来源
                    if candidate and len(candidate) <= 500 and not _OUTPUT_TABLE_RE.search(candidate):
                        self._dedup_append(cmds, candidate)
                # 其余（空行/普通输出行）忽略
        except Exception:
            pass
        return cmds

    def _dedup_append(self, cmds: list[str], cmd: str) -> None:
        """去重后收集：与 3 秒内刚记录过的相同命令（防止与前端/CLI 注入重复记录）"""
        now = time.time()
        if cmd == self.last_cmd and now - self.last_time < self.DEDUP_WINDOW:
            return
        self.last_cmd = cmd
        self.last_time = now
        cmds.append(cmd)
