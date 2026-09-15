"""命令回显解析测试：历史记录正确性的核心逻辑

覆盖：多提示符循环剥离（阿里云 readline 重绘）、宽松 PS1 兜底、
Tab 补全覆盖行、空命令、普通输出行不误记。
"""

import pytest

from ssh_web_tool.echo_parser import EchoParser
from ssh_web_tool.sessions import SSHSession

extract = EchoParser.extract_echo_command


# ---------- 已知提示符循环剥离 ----------


def test_single_prompt():
    assert extract("root@host:~# ls -la") == "ls -la"


def test_multi_prompt_same_line():
    """阿里云等 readline 清屏重绘：一行内多个连续提示符"""
    assert extract("root@host:~# root@host:~# root@host:~# cd /var") == "cd /var"


def test_debian_style_prompt():
    assert extract("user@host:~$ systemctl status nginx") == "systemctl status nginx"


def test_prompt_only_empty_command():
    assert extract("root@host:~#") == ""
    assert extract("root@host:~# ") == ""


# ---------- 宽松 PS1 兜底（自定义提示符） ----------


def test_custom_ps1_fallback():
    assert extract("[user@host ~]$ git status") == "git status"
    assert extract("root@host# reboot") == "reboot"


def test_custom_ps1_with_path():
    assert extract("user@host:/var/www$ ls") == "ls"


# ---------- 普通输出行不应误记 ----------


def test_plain_output_line_not_recorded():
    assert extract("total 48") == ""
    assert extract("drwxr-xr-x 2 root root 4096") == ""
    assert extract("nginx is running") == ""


# ---------- 特殊命令 ----------


def test_python_continuation_not_recorded():
    assert extract("...") == ""
    assert extract("...     x = 1") == ""


def test_long_command_capped():
    assert extract("x" * 501) == ""


def test_blank_line():
    assert extract("") == ""
    assert extract("   ") == ""


# ---------- 提示符独立成行（自研/简易 shell，如 mini>） ----------


def test_prompt_own_line_command_next_line():
    """mini shell：提示符独立成行，命令回显在下一行（分块到达）"""
    p = EchoParser()
    assert p.feed("mini> \n") == []
    assert p.feed("CMD") == []
    assert p.feed("2\n") == ["CMD2"]  # Tab 补全增量拼在命令行内，最终命令被捕获


def test_prompt_own_line_output_after_cmd_not_misrecorded():
    """mini shell：命令记录后，命令的输出行不再误记为命令"""
    p = EchoParser()
    p.feed("mini> \n")
    assert p.feed("whoami\n") == ["whoami"]
    assert p.feed("root\n") == []


def test_prompt_own_line_empty_enter_then_cmd():
    """空回车（提示符独立成行）后输入正常命令：只记录一次"""
    p = EchoParser()
    assert p.feed("root@host:~#\n") == []
    assert p.feed("root@host:~# ls\n") == ["ls"]


def test_python_continuation_not_prompt_own_line():
    """python 续行提示符不触发"下一行命令"捕获（其后是多行输出）"""
    p = EchoParser()
    assert p.feed(">>> for i in range(2):\n") == ["for i in range(2):"]
    p.feed("...     print(i)\n")
    assert p.feed("...\n") == []  # ... 不触发"下一行命令"捕获
    assert p.feed("0\n") == []  # 多行输出不被误记为命令


# ---------- 退格擦除重放（↑ 召回/退格编辑） ----------


def test_backspace_erase_replay():
    """readline ↑ 召回/退格编辑输出 \\b \\b 擦除序列：重放后得到最终命令"""
    p = EchoParser()
    # 先敲 l，↑ 召回 cd /var（前缀不同，readline 先擦 l 再输出新内容）
    assert p.feed("root@host:~# l\b \bcd /var\n") == ["cd /var"]


def test_bell_char_cleaned():
    """多候选补全失败的 bell 残留（行尾 \\x07）：清洗后记录干净命令"""
    p = EchoParser()
    assert p.feed("root@host:~# ls /e\x07\n") == ["ls /e"]


# ---------- 行内 \r 覆盖（Tab 补全重写整行） ----------


@pytest.mark.asyncio
async def test_parse_echo_line_tab_completion_keeps_final():
    """Tab 补全会 \r 重写整行：应只保留最后一次覆盖后的命令"""
    import asyncio

    s = SSHSession("t", "10.0.0.1", 22, "root")
    s._has_shell = True
    s.process = object()  # type: ignore[assignment]  # _parse_echo_line 要求 process 非 None
    s._echo_parser.buffer = ""
    s._echo_parser.last_cmd = ""
    s._echo_parser.last_time = 0
    recorded = []

    async def fake_record(cmd):
        recorded.append(cmd)

    s._record_echo = fake_record
    # 模拟：cd /va\r 被 readline 重写为 cd /var
    s._parse_echo_line("root@host:~# cd /va\rroot@host:~# cd /var\n")
    await asyncio.sleep(0.1)
    assert recorded == ["cd /var"]
