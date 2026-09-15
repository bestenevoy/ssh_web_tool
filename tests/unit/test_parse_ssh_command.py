"""解析本地终端 ssh user:password@host 命令的单元测试"""

from ssh_web_tool.api.helpers import _parse_ssh_command
from ssh_web_tool.sessions import SSHSession


def test_basic_format():
    """ssh user:password@host"""
    result = _parse_ssh_command("ssh root:123456@192.168.1.100\r")
    assert result is not None
    host, port, user, password = result
    assert host == "192.168.1.100"
    assert port == 22
    assert user == "root"
    assert password == "123456"


def test_with_port():
    """ssh user:password@host:port"""
    result = _parse_ssh_command("ssh root:secret@10.0.0.1:2222\r")
    assert result is not None
    host, port, user, password = result
    assert host == "10.0.0.1"
    assert port == 2222
    assert user == "root"
    assert password == "secret"


def test_password_with_at_sign():
    """密码中包含 @：最后一个 @ 后是 host"""
    result = _parse_ssh_command("ssh admin:p@ss@host.com\r")
    assert result is not None
    host, _port, user, password = result
    assert host == "host.com"
    assert user == "admin"
    assert password == "p@ss"


def test_password_with_colon():
    """密码中包含冒号：第一个冒号分割 user 和 password"""
    result = _parse_ssh_command("ssh user:pass:word@192.168.1.1\r")
    assert result is not None
    host, _port, user, password = result
    assert host == "192.168.1.1"
    assert user == "user"
    assert password == "pass:word"


def test_not_ssh_command():
    """非 ssh 命令"""
    assert _parse_ssh_command("echo hello\r") is None
    assert _parse_ssh_command("ls -la\r") is None
    assert _parse_ssh_command("ping 192.168.1.1\r") is None


def test_ssh_without_colon_at():
    """ssh user@host（没有密码）——不匹配，因为必须有 user:password@host 格式"""
    assert _parse_ssh_command("ssh root@192.168.1.1\r") is None


def test_ssh_no_at_sign():
    """没有 @ 符号"""
    assert _parse_ssh_command("ssh root:password\r") is None


def test_leading_spaces():
    """命令前有空格"""
    result = _parse_ssh_command("  ssh root:pass@host\r")
    assert result is not None
    host, _port, user, password = result
    assert host == "host"
    assert user == "root"
    assert password == "pass"


def test_domain_name():
    """域名作为 host"""
    result = _parse_ssh_command("ssh admin:admin@example.server.com\r")
    assert result is not None
    host, _port, user, password = result
    assert host == "example.server.com"
    assert user == "admin"
    assert password == "admin"


def test_empty_input():
    """空输入"""
    assert _parse_ssh_command("") is None
    assert _parse_ssh_command("\r") is None
    assert _parse_ssh_command("ssh\r") is None


# ---------- 中文输入法全角字符 / 非法 host ----------


def test_full_width_punctuation_normalized():
    """全角冒号/＠/点号（中文输入法常见）经 NFKC 归一化后正常解析"""
    result = _parse_ssh_command("ssh root：pw＠192．168．1．1\r")
    assert result is not None
    host, port, user, password = result
    assert host == "192.168.1.1"
    assert port == 22
    assert user == "root"
    assert password == "pw"


def test_full_width_port_normalized():
    """全角数字端口"""
    result = _parse_ssh_command("ssh root:pw＠10.0.0.1：２２２２\r")
    assert result is not None
    host, port, user, password = result
    assert host == "10.0.0.1"
    assert port == 2222
    assert user == "root"
    assert password == "pw"


def test_password_with_at_full_width():
    """密码含 @ 且 host 全角点号：root:wrz@1234＠８．１３７．５２．７１"""
    result = _parse_ssh_command("ssh root：wrz@1234＠８．１３７．５２．７１\r")
    assert result is not None
    host, _port, user, password = result
    assert host == "8.137.52.71"
    assert user == "root"
    assert password == "wrz@1234"


def test_invalid_host_with_comma_returns_none():
    """host 含逗号/连续点号（全角逗号归一化后仍非法）：不拦截，交本地 ssh 处理"""
    # 全角逗号 ，NFKC 后是半角逗号，host 非法
    assert _parse_ssh_command("ssh root:pw＠8，137．52．71\r") is None
    # 连续点号：空 label
    assert _parse_ssh_command("ssh root:pw@8..137.52.71\r") is None
    # 尾点号
    assert _parse_ssh_command("ssh root:pw@8.137.52.71.\r") is None


def test_invalid_ipv4_octet_returns_none():
    """IPv4 段超出 0-255：不拦截"""
    assert _parse_ssh_command("ssh root:pw@256.1.1.1\r") is None


def test_domain_still_valid():
    """多级域名正常解析"""
    result = _parse_ssh_command("ssh root:pw@a-b.example.com.cn:2200\r")
    assert result is not None
    host, port, user, password = result
    assert host == "a-b.example.com.cn"
    assert port == 2200
    assert user == "root"
    assert password == "pw"


# ---------- feed_local_input：逐键拼行（SSH 拦截的前置） ----------


def _make_session(monkeypatch, tmp_path):
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    return SSHSession("locfeed1", "localhost", 0, "tester")


def test_feed_per_keystroke_line(monkeypatch, tmp_path):
    """xterm 逐字符发送：每个字符单独 feed，回车时拼出整行"""
    s = _make_session(monkeypatch, tmp_path)
    for ch in "ssh root:123456@192.168.1.100":
        assert s.feed_local_input(ch) is None
    line = s.feed_local_input("\r")
    assert line == "ssh root:123456@192.168.1.100"
    # 解析联动
    parsed = _parse_ssh_command(line)
    assert parsed is not None
    assert parsed == ("192.168.1.100", 22, "root", "123456")


def test_feed_paste_whole_line(monkeypatch, tmp_path):
    """整行粘贴一次到达（含回车）也能拼行"""
    s = _make_session(monkeypatch, tmp_path)
    line = s.feed_local_input("ssh admin:pw@host.com:2222\r")
    assert line == "ssh admin:pw@host.com:2222"
    assert s._local_input_line == ""


def test_feed_backspace(monkeypatch, tmp_path):
    """退格删除字符：输错后修正仍能拼出正确命令"""
    s = _make_session(monkeypatch, tmp_path)
    # 误输 "bad"，连按三次退格删净，再输入 "x"
    for ch in "ssh root:bad\b\b\bx:pw@1.2.3.4":
        s.feed_local_input(ch)
    assert s.feed_local_input("\r") == "ssh root:x:pw@1.2.3.4"


def test_feed_ctrl_c_clears_line(monkeypatch, tmp_path):
    """Ctrl+C 清空当前行缓冲"""
    s = _make_session(monkeypatch, tmp_path)
    for ch in "ssh root:pw@1.2.3.4":
        s.feed_local_input(ch)
    assert s.feed_local_input("\x03") is None
    assert s._local_input_line == ""
    # 清空后回车是空行
    assert s.feed_local_input("\r") is None


def test_feed_ansi_sequence_stripped(monkeypatch, tmp_path):
    """方向键等 ANSI 序列不污染行缓冲"""
    s = _make_session(monkeypatch, tmp_path)
    s.feed_local_input("ssh root:pw@1.2.3.4")
    s.feed_local_input("\x1b[D")  # 左方向键
    s.feed_local_input("\x1b[C")  # 右方向键
    assert s.feed_local_input("\r") == "ssh root:pw@1.2.3.4"


def test_feed_empty_line_returns_none(monkeypatch, tmp_path):
    """空行（直接回车）返回 None 且不残留"""
    s = _make_session(monkeypatch, tmp_path)
    assert s.feed_local_input("\r") is None
    assert s.feed_local_input("   \r") is None
    assert s._local_input_line == ""


def test_feed_plain_command_still_returned(monkeypatch, tmp_path):
    """普通命令也拼整行返回（由解析层决定是否拦截）"""
    s = _make_session(monkeypatch, tmp_path)
    for ch in "echo hello":
        s.feed_local_input(ch)
    assert s.feed_local_input("\r") == "echo hello"
