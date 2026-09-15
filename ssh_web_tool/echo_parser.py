"""命令回显解析组件：从终端输出流提取"提示符后的实际命令"

职责（原 SSHSession 中 _parse_echo_line 一带的所有逻辑）：
- 维护行缓冲（数据块可能截断命令行，留到下一块补齐）
- 识别提示符并剥离，提取实际执行的命令（含 Tab 补全/历史翻查后的最终内容）
- 3 秒同命令去重（防止与前端/CLI 注入重复记录）

只做解析：记录动作由调用方（SSHSession 的 _record_echo）负责。
"""

import re
import time

# 提示符正则：匹配 bash/zsh/sh (user@host:path$ / #)、python (>>>)、
# mysql/sqlite/redis/mongo/postgres 等交互式程序的提示符。
# 不匹配单独的 ">"（node 提示符，太通用容易误判普通输出行）。
_ECHO_PROMPT_RE = re.compile(
    r"^(?:"
    r"[\w.-]+@[\w.-]+:[^#$\n]*[#$]"  # shell: user@host:path$ 或 user@host:path#
    r"|>>>"  # python
    r"|\.\.\."  # python 续行（跳过）
    r"|mysql>"  # mysql
    r"|sqlite>"  # sqlite
    r"|[\d.]+:\d+>"  # redis: 127.0.0.1:6379>
    r"|[a-zA-Z_][\w.-]*>"  # 通用: xxx> (mongo, postgres 等)
    r")\s*"
)

# 清 ANSI 但保留 \r\n（回显解析需要 \r 判断行内覆盖）
_ANSI_KEEP_CR_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_OSC_KEEP_CR_RE = re.compile(r"\x1b\][\s\S]*?(\x07|\x1b\\)")


class EchoParser:
    """回显解析状态机（每个会话一个实例）"""

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
        text = re.sub(r"\x1b[=><]", "", text)
        return text

    @classmethod
    def extract_echo_command(cls, line: str) -> str:
        """从一行输出中提取提示符之后的命令文本；非提示符行返回空串
        - 已知提示符**循环剥离**：阿里云等 shell 每次 readline 重绘都会清屏重写，
          一行内可能出现多个连续提示符（root@host:~# root@host:~# cmd）
        - 自定义 PS1 宽松兜底（仅当未识别出已知提示符时）：行首到第一个 $/# 之间
          为提示符标识（如 root@host#、[user@host ~]$），其后是命令；
          宽松匹配失败 = 普通输出行，不记录
        """
        s = line.strip()
        if not s:
            return ""
        # python 续行提示符(...)：是上一命令的延续内容，不作为独立命令记录
        if s.startswith("..."):
            return ""
        # 1. 精确已知提示符：循环剥离（readline 清屏重绘可能一行内多个提示符）
        stripped = False
        cmd = s
        while True:
            m = _ECHO_PROMPT_RE.match(cmd)
            if not m:
                break
            stripped = True
            rest = cmd[m.end() :]
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
                cmd = self.extract_echo_command(line)
                if not cmd:
                    continue
                now = time.time()
                # 去重：与 3 秒内刚记录过的相同命令（防止与前端/CLI 注入重复记录）
                if cmd == self.last_cmd and now - self.last_time < self.DEDUP_WINDOW:
                    continue
                self.last_cmd = cmd
                self.last_time = now
                cmds.append(cmd)
        except Exception:
            pass
        return cmds
