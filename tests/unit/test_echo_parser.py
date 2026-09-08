# -*- coding: utf-8 -*-
"""命令回显解析测试：历史记录正确性的核心逻辑

覆盖：多提示符循环剥离（阿里云 readline 重绘）、宽松 PS1 兜底、
Tab 补全覆盖行、空命令、普通输出行不误记。
"""
import pytest

from ssh_web_tool.sessions import SSHSession

extract = SSHSession._extract_echo_command


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


# ---------- 行内 \r 覆盖（Tab 补全重写整行） ----------

@pytest.mark.asyncio
async def test_parse_echo_line_tab_completion_keeps_final():
    """Tab 补全会 \r 重写整行：应只保留最后一次覆盖后的命令"""
    import asyncio

    s = SSHSession("t", "10.0.0.1", 22, "root")
    s._has_shell = True
    s.process = object()  # _parse_echo_line 要求 process 非 None
    s._echo_buf = ""
    s._echo_last_cmd = ""
    s._echo_last_time = 0
    recorded = []

    async def fake_record(cmd):
        recorded.append(cmd)

    s._record_echo = fake_record
    # 模拟：cd /va\r 被 readline 重写为 cd /var
    s._parse_echo_line("root@host:~# cd /va\rroot@host:~# cd /var\n")
    await asyncio.sleep(0.1)
    assert recorded == ["cd /var"]
