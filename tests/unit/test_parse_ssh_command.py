"""解析本地终端 ssh user:password@host 命令的单元测试"""

from main import _parse_ssh_command


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
    host, port, user, password = result
    assert host == "host.com"
    assert user == "admin"
    assert password == "p@ss"


def test_password_with_colon():
    """密码中包含冒号：第一个冒号分割 user 和 password"""
    result = _parse_ssh_command("ssh user:pass:word@192.168.1.1\r")
    assert result is not None
    host, port, user, password = result
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
    host, port, user, password = result
    assert host == "host"
    assert user == "root"
    assert password == "pass"


def test_domain_name():
    """域名作为 host"""
    result = _parse_ssh_command("ssh admin:admin@example.server.com\r")
    assert result is not None
    host, port, user, password = result
    assert host == "example.server.com"
    assert user == "admin"
    assert password == "admin"


def test_empty_input():
    """空输入"""
    assert _parse_ssh_command("") is None
    assert _parse_ssh_command("\r") is None
    assert _parse_ssh_command("ssh\r") is None
