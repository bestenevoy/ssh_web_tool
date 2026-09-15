"""终端提示符/状态检测工具（纯函数，无状态）

供会话的命令完成检测（inject_and_capture）、回显解析、终端状态分析复用。
"""

import re

# 通用提示符模式列表（按优先级排序，匹配到任意一个即认为命令完成）
PROMPT_PATTERNS: list[str] = [
    r"[\w.-]+@[\w.-]+:.+[#$]\s*$",  # shell: root@host:~# 或 user@host:~$
    r"^>>>\s*$",  # Python: >>>
    r"^\.\.\.\s*$",  # Python 续行: ...
    r"^mysql>\s*$",  # MySQL: mysql>
    r"^sqlite>\s*$",  # SQLite: sqlite>
    r"^[\d.]+:\d+>\s*$",  # Redis: 127.0.0.1:6379>
    r"^>\s*$",  # Node.js: >
    r"^\w+>\s*$",  # 通用: xxx> (mongo, postgres 等)
]

# CSI / OSC 转义序列（清 ANSI 用；_ANSI_SEQ_RE 是输入流拼行用的更激进版本）
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_OSC_RE = re.compile(r"\x1b\][\s\S]*?(\x07|\x1b\\)")


def clean_ansi(text: str) -> str:
    """清理 ANSI 转义序列（颜色、光标移动、OSC 等）"""
    text = _CSI_RE.sub("", text)
    text = _OSC_RE.sub("", text)
    text = re.sub(r"\x1b[=><]", "", text)
    text = re.sub(r"\r", "", text)
    return text


def match_any_prompt(text: str) -> bool:
    """检查文本是否匹配任意一种已知提示符"""
    return any(re.search(pattern, text, re.MULTILINE) for pattern in PROMPT_PATTERNS)


def is_prompt_line(line: str) -> bool:
    """检查单行是否是提示符行"""
    return any(re.match(pattern, line.strip()) for pattern in PROMPT_PATTERNS)
