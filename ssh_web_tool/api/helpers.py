"""API 层通用工具函数（从 main.py 拆分：纯函数，无 IO）"""

import re
import unicodedata


def _sanitize_host(h: dict) -> dict:
    """主机信息（本地工具：密码等敏感字段明文下发，便于前端展示/编辑确认）"""
    out = dict(h)
    for key in ("password", "mgmt_password", "private_key", "passphrase"):
        out[f"has_{key}"] = bool((h.get(key) or "").strip())
    return out


def _parse_ssh_command(data: str) -> tuple[str, int, str, str] | None:
    """解析本地终端输入的 ssh 命令，提取连接信息

    支持格式：ssh user:password@host  或  ssh user:password@host:port
    冒号前是用户名，冒号后到最后一个 @ 前是密码，最后一个 @ 后是 IP（可带端口）

    返回 (host, port, username, password) 或 None（不匹配）
    """
    # 取回车前的命令行
    line = data.split("\r")[0].split("\n")[0].strip()
    # 清洗 ANSI 转义序列（PowerShell PSReadLine 会混入光标控制/行重绘序列）
    line = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", line)
    line = re.sub(r"\x1b\][\s\S]*?(\x07|\x1b\\)", "", line)
    line = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", line)
    # NFKC 兼容归一化：中文输入法全角标点/字母数字（：＠．１９２ 等）转半角，
    # 否则全角字符的 host 会让 socket 的 idna 编码报 "label empty" 等晦涩错误
    line = unicodedata.normalize("NFKC", line)
    # 匹配 ssh 前缀（允许前面有空白）
    m = re.match(r"^ssh\s+(.+)$", line)
    if not m:
        return None
    rest = m.group(1).strip()
    # 必须包含 @（最后一个 @ 分割：前面是 user:password，后面是 host[:port]）
    at_idx = rest.rfind("@")
    if at_idx <= 0:
        return None
    user_pass = rest[:at_idx]
    host_port = rest[at_idx + 1 :]
    # user:password 分割（第一个冒号）
    colon_idx = user_pass.find(":")
    if colon_idx <= 0:
        return None
    username = user_pass[:colon_idx]
    password = user_pass[colon_idx + 1 :]
    if not username or not password or not host_port:
        return None
    # 解析 host:port
    port = 22
    # IPv6 地址中可能有多个冒号，但本项目场景以 IPv4/域名为主
    # 只在 host_port 中最后一个冒号后为纯数字时视为端口
    if host_port.count(":") == 1:
        h, p = host_port.rsplit(":", 1)
        if p.isdigit():
            host_port, port = h, int(p)
    # host 严格校验：只接受合法 IPv4 / 域名（逗号、连续点号、空格等直接判不匹配，
    # 交给本地 shell 原生 ssh 处理，避免 idna 编码抛异常显示晦涩报错）
    if not _is_valid_host(host_port):
        return None
    return host_port, port, username, password


def _is_valid_host(host: str) -> bool:
    """校验 host 是否为合法 IPv4 地址或域名（不含 IPv6）"""
    if not host or len(host) > 253:
        return False
    parts = host.split(".")
    # IPv4：四段且每段 0-255；四段全数字但越界（如 256.1.1.1）直接非法，
    # 不得落入域名分支（域名 TLD 标签不允许纯数字）
    if all(p.isdigit() for p in parts):
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    # 域名：标签 1-63 字符，字母数字开头/结尾，中间可含连字符；
    # 允许单标签主机名（localhost / 局域网机器名），纯数字单标签已在上面拦截
    label_re = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    return all(label_re.match(p) for p in parts)
