"""OpenSSH client config 最小解析器：自己用 UTF-8 读 ~/.ssh/config，绕开 asyncssh 的 GBK 编码坑

支持 Host 通配块 + HostName/User/Port/IdentityFile/ProxyJump 五个键，
first-match-wins 语义；Match 块整体忽略并标记；ProxyCommand 只标记不执行。
"""

import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

# 键名统一小写比较（OpenSSH 键大小写不敏感）
_KEY_HOSTNAME = "hostname"
_KEY_USER = "user"
_KEY_PORT = "port"
_KEY_IDENTITYFILE = "identityfile"
_KEY_PROXYJUMP = "proxyjump"
_KEY_PROXYCOMMAND = "proxycommand"

# `Key value` 或 `Key=value` 两种写法（OpenSSH 都接受）
_LINE_RE = re.compile(r"^([^\s=]+)\s*[=\s]\s*(.*)$")


@dataclass
class ConfigBlock:
    """一个 Host/Match 块；Match 块的 patterns 保存原始条件文本（仅供调试，不做匹配）"""

    kind: str  # "host" 或 "match"
    patterns: list[str]
    options: list[tuple[str, str]]  # (小写键名, 已去注释/去引号的原始值)


@dataclass
class ResolvedHost:
    """resolve_host 的结果：只包含本项目接线需要的字段，取不到的键保持 None/空"""

    hostname: str | None = None
    user: str | None = None
    port: int | None = None
    identity_files: list[str] = field(default_factory=list)
    proxy_jump: str | None = None
    has_proxy_command: bool = False  # 匹配块里出现 ProxyCommand：上层应显式报错而非静默直连
    ignored_match_blocks: bool = False  # 文件里有 Match 块被忽略：上层可写日志提示
    matched_patterns: list[str] = field(default_factory=list)


def _strip_inline_comment(line: str) -> str:
    # 简化取舍：引号外的第一个 # 即视为注释起点。OpenSSH 实际只在"词首 #"才算注释
    # （foo#bar 不是注释），但 ssh config 的值里几乎不会出现裸 #，简化处理够用
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _clean_value(raw: str) -> str:
    # 去引号：直接删掉所有双引号。带空格的路径通常整体加引号（"C:\My Keys\id"），
    # 删引号后按单值保留；不支持引号内嵌引号等极端写法
    return raw.replace('"', "").strip()


def parse_config_text(text: str) -> list[ConfigBlock]:
    """纯函数解析：文本 -> 块列表（保持文件顺序）。首个 Host 之前的选项归入隐式 `Host *` 块"""
    blocks: list[ConfigBlock] = []
    current: ConfigBlock | None = None
    for raw_line in text.splitlines():
        line = _strip_inline_comment(raw_line).strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if m is None:
            continue
        key = m.group(1).lower()
        value = _clean_value(m.group(2))
        if key == "host":
            current = ConfigBlock(kind="host", patterns=value.split(), options=[])
            blocks.append(current)
        elif key == "match":
            # Match 条件不求值：整块收集但解析结果阶段会跳过并标记
            current = ConfigBlock(kind="match", patterns=value.split(), options=[])
            blocks.append(current)
        elif current is None:
            # OpenSSH 语义：Host 之前的全局选项对所有主机生效
            current = ConfigBlock(kind="host", patterns=["*"], options=[])
            blocks.insert(0, current)
            current.options.append((key, value))
        else:
            current.options.append((key, value))
    return blocks


def _pattern_matches(patterns: list[str], target: str) -> bool:
    """Host pattern 匹配：fnmatch 语义（* / ?），大小写不敏感；`!pat` 表示排除"""
    target_lower = target.lower()
    has_positive = False
    for pat in patterns:
        pat_lower = pat.lower()
        if pat_lower.startswith("!"):
            if fnmatch(target_lower, pat_lower[1:]):
                return False
        else:
            has_positive = True
            if fnmatch(target_lower, pat_lower):
                return True
    # 全是 ! 排除模式且都没命中时也算匹配（OpenSSH 语义）
    return not has_positive


def _expand_percent(value: str, target: str) -> str:
    """只展开 %h（目标主机名）和 %%；其他 % 占位符原样保留"""

    def _sub(m: re.Match[str]) -> str:
        ch = m.group(1)
        if ch == "h":
            return target
        if ch == "%":
            return "%"
        return m.group(0)

    return re.sub(r"%(.)", _sub, value)


def _read_config_text(config_path: Path) -> str | None:
    # errors="replace"：文件即使混入非法 UTF-8 字节也不能让连接流程崩掉
    try:
        return config_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def resolve_host(target: str, config_path: Path | None = None) -> ResolvedHost:
    """按 OpenSSH first-match-wins 语义解析 target 在 ~/.ssh/config 里的有效配置。

    任何异常（文件不存在/不可读/值非法）都退化为"无配置"结果，绝不抛出。
    """
    if config_path is None:
        config_path = Path.home() / ".ssh" / "config"
    text = _read_config_text(config_path)
    if text is None:
        return ResolvedHost()

    try:
        blocks = parse_config_text(text)
    except Exception:
        return ResolvedHost()

    result = ResolvedHost()
    for block in blocks:
        if block.kind == "match":
            result.ignored_match_blocks = True
            continue
        if not _pattern_matches(block.patterns, target):
            continue
        result.matched_patterns.extend(block.patterns)
        for key, value in block.options:
            if key == _KEY_HOSTNAME and result.hostname is None:
                result.hostname = _expand_percent(value, target)
            elif key == _KEY_USER and result.user is None:
                result.user = value
            elif key == _KEY_PORT and result.port is None:
                try:
                    result.port = int(value)
                except ValueError:
                    pass  # 非法端口值忽略，继续找后续块
            elif key == _KEY_IDENTITYFILE:
                # IdentityFile 允许重复出现，按顺序全部保留；%h 也可出现在路径里
                result.identity_files.append(os.path.expanduser(_expand_percent(value, target)))
            elif key == _KEY_PROXYJUMP and result.proxy_jump is None:
                result.proxy_jump = value
            elif key == _KEY_PROXYCOMMAND:
                result.has_proxy_command = True
    return result
