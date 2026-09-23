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


# ---------- zsh % 提示符（macOS 默认 / 纯 zsh / 带路径） ----------


def test_zsh_percent_prompt_macos():
    assert extract("user@host ~ % systemctl status nginx") == "systemctl status nginx"


def test_zsh_percent_prompt_plain_and_path():
    assert extract("myserver% ls -la") == "ls -la"
    assert extract("~/repo % make build") == "make build"


def test_zsh_percent_feed_tab_completion():
    """zsh % 提示符下 Tab 补全 \\r 重写整行：保留补全后的最终命令（核心回归）"""
    p = EchoParser()
    assert p.feed("user@host ~ % cd /va\ruser@host ~ % cd /var\n") == ["cd /var"]


def test_zsh_percent_prompt_only_empty_enter():
    p = EchoParser()
    assert p.feed("user@host ~ %\r\n") == []
    assert p.feed("user@host ~ % ls\r\n") == ["ls"]


def test_zsh_percent_prompt_only_detection():
    assert EchoParser.is_prompt_only("user@host ~ %")
    assert EchoParser.is_prompt_only("~/repo %")
    # 裸 hostname% 不算"仅提示符"行（防误报防线）
    assert not EchoParser.is_prompt_only("myserver%")


@pytest.mark.parametrize(
    "line",
    [
        "50% done",
        "100% packet loss",
        "/dev/sda 45% used",
        "[ 50%] Building C object foo.o",
        "wget 10% |====     | 12.3M",
        "Progress 99%of100%",
    ],
)
def test_zsh_percent_negatives(line):
    """含百分比的输出行不得被误记为命令"""
    assert extract(line) == ""


# ---------- oh-my-zsh / starship / 箭头提示符 ----------


def test_oh_my_zsh_prompt():
    assert extract("➜  repo git:(main) ✗ cd /var") == "cd /var"
    assert extract("➜  repo git:(main) cd /var") == "cd /var"
    assert extract("➜  ~ ls") == "ls"
    assert extract("➜  repo git:(main) ✘ git status") == "git status"


def test_oh_my_zsh_prompt_only():
    """omz 提示符独占行（空回车）：不产生命令，也不误记后续输出"""
    p = EchoParser()
    assert p.feed("➜  repo\r\n") == []
    assert p.feed("➜  repo git:(main) ✗\r\n") == []
    assert p.feed("background output\r\n") == []


def test_oh_my_zsh_feed_tab_completion():
    p = EchoParser()
    assert p.feed("➜  repo git:(main) ✗ cd /va\r➜  repo git:(main) ✗ cd /var\n") == ["cd /var"]


def test_starship_prompt():
    assert extract("❯ docker ps") == "docker ps"
    assert EchoParser.is_prompt_only("❯")
    # 两行式第一行（路径 + ❯）
    assert EchoParser.is_prompt_only("~/repo ❯")
    assert EchoParser.is_prompt_only("user@host ❯")
    assert extract("~/repo ❯ pytest") == "pytest"


def test_arrow_glyph_output_not_recorded():
    """输出行含箭头字形但不构成提示符：不记录（含 vite/nuxi 输出）"""
    assert extract("error ❯ fail") == ""
    assert extract("✗ important notice") == ""
    assert extract("➜  Local:   http://localhost:5173/") == ""
    assert extract("➜  Network: use --host to expose") == ""


# ---------- fish ----------


def test_fish_prompt():
    assert extract("user@host ~/repo> ls") == "ls"
    assert extract("user@host /etc/nginx> cat nginx.conf") == "cat nginx.conf"


# ---------- Windows PowerShell / cmd ----------


def test_powershell_prompt():
    assert extract("PS C:\\Users\\admin> Get-ChildItem") == "Get-ChildItem"
    assert extract("PS C:\\> dir") == "dir"


def test_powershell_continuation_prompt():
    assert extract('PS C:\\Users\\a>> "text"') == '"text"'


def test_cmd_prompt():
    assert extract("C:\\Users\\admin> dir /w") == "dir /w"


# ---------- 提示符独占行的"下一行命令"捕获收紧 ----------


def test_standard_prompt_own_line_does_not_await():
    """标准 shell 空回车（提示符独占行）后跟输出行：不误记为命令"""
    p = EchoParser()
    assert p.feed("root@host:~#\r\n") == []
    assert p.feed("some random output\r\n") == []
    assert p.feed("more output\r\n") == []


def test_mini_style_prompt_still_awaits():
    """自研 shell（宽松 PS1）提示符独占行：下一行命令仍被捕获"""
    p = EchoParser()
    assert p.feed("[myshell]$\r\n") == []
    assert p.feed("run_task\r\n") == ["run_task"]


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


# ---------- 字符集转义残留（ESC ( B 等）不得混进命令 ----------


def test_charset_escape_not_recorded_with_command():
    """提示符/颜色重置 \x1b(B\x1b[m（tput sgr0）：ESC 先随序列整体剥离，
    不得残留 "(B" 字面量混入记录的命令（旧清洗只删 ESC 控制符所致）"""
    p = EchoParser()
    assert p.feed("root@host:~$ \x1b(B\x1b[mls -la\r\n") == ["ls -la"]
    p2 = EchoParser()
    assert p2.feed("mini> \n") == []
    assert p2.feed("\x1b(B\x1b[mCMD\r\n") == ["CMD"]


def test_prompt_own_line_aligned_output_not_recorded():
    """下一行捕获输出形态过滤：列对齐（free/ps 表格特征）不收，真实命令照常"""
    p = EchoParser()
    p.feed("mini> \n")
    assert p.feed("Mem:   15Gi   1.2Gi   9.5Gi\n") == []  # 表格行被拒（并消耗本次武装）
    p.feed("mini> \n")
    assert p.feed("free -h\n") == ["free -h"]  # 下一次输入的普通命令不受影响
