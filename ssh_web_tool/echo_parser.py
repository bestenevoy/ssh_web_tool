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


# 清 ANSI 但保留 \r\n（回显解析需要 \r 判断行内覆盖）。CSI 参数区按标准取
# 0x30-0x3F（数字与 : ; < = ?），覆盖私有 \x1b[?2004h、kitty \x1b[>1u 等
_ANSI_KEEP_CR_RE = re.compile(r"\x1b\[[0-9;:<=>?]*[a-zA-Z]")
_OSC_KEEP_CR_RE = re.compile(r"\x1b\][\s\S]*?(\x07|\x1b\\)")
# DCS/SOS/PM/APC（\x1bP/X/^/_ 起，ST/BEL 止）
_DCS_KEEP_CR_RE = re.compile(r"\x1b[PX^_][\s\S]*?(?:\x1b\\|\x07)")
# 双字符转义（ESC ( B 字符集指定 / Fe 序列）：须在删 ESC 控制符前整体剥离，
# 否则残留下 "(B" 字面量被当作命令记录（tput sgr0/提示符重置常见输出）
_ESC2_KEEP_CR_RE = re.compile(r"\x1b[()\)][0-9A-Za-z]|\x1b[=><78cM]")


class EchoParser:
    """回显解析状态机（每个会话一个实例）——S3 兜底通道

    仅处理"提示符与命令同行"的标准 readline 回显（bash/zsh/fish/PS/cmd/
    自研 xxx> 同行形态）。原"提示符独占行→武装下一行捕获"机制已整体退役：
    它是"回显内容被当成命令"的最大误报源（任何输出行撞宽松 PS1 形态，其后
    第一行程序输出即入库）；自研 shell 的下一行回显、↑ 历史召回、整段粘贴
    等场景统一改由输入快照通道承担（前端 xterm 缓冲 captureTypedLine，
    headless 会话的后端镜像快照随二期事件总线上线）。
    """

    # 异常情况下行缓冲上限，超过直接丢弃防止无限增长
    BUFFER_LIMIT = 4000
    # 同命令去重窗口（秒）
    DEDUP_WINDOW = 3.0

    def __init__(self) -> None:
        self.buffer = ""  # 输出行缓冲（可能被数据块截断，留到下一块补齐）
        self.last_cmd = ""  # 最近一次解析记录的命令（去重防重复记录）
        self.last_time = 0.0  # 最近一次记录时间

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
                    self._dedup_append(cmds, cmd)
                # 其余行（空行/普通输出行/提示符独占行）一律忽略：S3 只认与提示符
                # 同行的回显；无键入缓冲的行（↑ 召回/粘贴/自研 shell 下行回显）
                # 由输入快照通道负责，不再从输出流猜
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
