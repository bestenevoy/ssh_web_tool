"""ssh_config.resolve_host 单测：UTF-8 解析 ~/.ssh/config（绕开 asyncssh GBK 坑）"""

import os
from pathlib import Path

from ssh_web_tool.ssh_config import ResolvedHost, parse_config_text, resolve_host


def _write(tmp_path: Path, text: str, newline: str = "\n") -> Path:
    cfg = tmp_path / "config"
    cfg.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return cfg


def test_wildcard_host_match(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host *.example.com web?
    HostName 10.1.2.3
    User alice
    Port 2222
""",
    )
    r = resolve_host("db.example.com", cfg)
    assert r.hostname == "10.1.2.3"
    assert r.user == "alice"
    assert r.port == 2222
    assert "*.example.com" in r.matched_patterns

    # ? 通配也命中
    assert resolve_host("web1", cfg).user == "alice"
    # 不匹配的目标拿不到任何值
    assert resolve_host("other.org", cfg) == ResolvedHost()


def test_first_match_wins(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host myhost
    User first
    Port 1000

Host *
    User second
    Port 2000
    HostName fallback.example.com
""",
    )
    r = resolve_host("myhost", cfg)
    assert r.user == "first"
    assert r.port == 1000
    # 前面的块没给 HostName，后面的 * 块补上（逐键填补，不是整块覆盖）
    assert r.hostname == "fallback.example.com"


def test_multiple_identity_files(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host *
    IdentityFile ~/.ssh/id_first
    IdentityFile ~/.ssh/id_second
""",
    )
    r = resolve_host("any", cfg)
    # expanduser 只替换 ~ 前缀，分隔符原样保留，直接用同一调用做期望值
    assert r.identity_files == [
        os.path.expanduser("~/.ssh/id_first"),
        os.path.expanduser("~/.ssh/id_second"),
    ]


def test_tilde_and_percent_h_expansion(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host %h-alias srv
    HostName %h.internal.example.com
    IdentityFile ~/.ssh/keys/%h_key
""",
    )
    r = resolve_host("srv", cfg)
    assert r.hostname == "srv.internal.example.com"
    assert r.identity_files == [os.path.expanduser("~/.ssh/keys/srv_key")]


def test_comments_and_quotes(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
# 整行注释
Host quoted  # 行尾注释
    User "bob smith"
    HostName host#notcomment.example.com
    Port = 2200
""",
    )
    blocks = parse_config_text(cfg.read_text(encoding="utf-8"))
    assert blocks[0].patterns == ["quoted"]
    r = resolve_host("quoted", cfg)
    assert r.user == "bob smith"  # 去引号
    assert r.port == 2200  # Key=Value 写法
    # 取舍：值中间的裸 # 也被当注释截断（简化处理）
    assert r.hostname == "host"


def test_match_block_ignored_and_flagged(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host target
    User good

Match host target
    User evil
    Port 9999

Host *
    HostName h.example.com
""",
    )
    r = resolve_host("target", cfg)
    assert r.ignored_match_blocks is True
    assert r.user == "good"
    assert r.port is None  # Match 块里的 Port 不生效
    assert r.hostname == "h.example.com"  # Match 块之后的 Host 块仍然解析


def test_proxy_command_flagged(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host jump-needed
    HostName 10.0.0.9
    ProxyCommand ssh -W %h:%p bastion
""",
    )
    r = resolve_host("jump-needed", cfg)
    assert r.has_proxy_command is True
    # 未命中的块里的 ProxyCommand 不标记
    assert resolve_host("other", cfg).has_proxy_command is False


def test_proxy_jump(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host inner
    ProxyJump bastion:2222
""",
    )
    assert resolve_host("inner", cfg).proxy_jump == "bastion:2222"


def test_missing_file_returns_empty(tmp_path: Path):
    r = resolve_host("any", tmp_path / "not_exist" / "config")
    assert r == ResolvedHost()
    assert r.identity_files == []


def test_crlf_line_endings(tmp_path: Path):
    cfg = _write(
        tmp_path,
        """
Host crlf
    HostName 1.2.3.4
    Port 2222
""",
        newline="\r\n",
    )
    r = resolve_host("crlf", cfg)
    assert r.hostname == "1.2.3.4"
    assert r.port == 2222


def test_utf8_chinese_comments(tmp_path: Path):
    # 本模块存在的理由：UTF-8 中文注释绝不能触发解码错误
    cfg = _write(
        tmp_path,
        """
# 生产环境跳板机（重要！）
Host 中文别名 prod  # 备注：数据库服务器
    HostName prod.example.com  # 生产地址
    User 运维账号
    IdentityFile ~/.ssh/生产密钥
""",
    )
    r = resolve_host("prod", cfg)
    assert r.hostname == "prod.example.com"
    assert r.user == "运维账号"
    assert r.identity_files == [os.path.expanduser("~/.ssh/生产密钥")]
    # 中文别名本身也能匹配
    assert resolve_host("中文别名", cfg).hostname == "prod.example.com"
