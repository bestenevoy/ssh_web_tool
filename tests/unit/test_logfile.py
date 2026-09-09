# -*- coding: utf-8 -*-
"""会话日志：命名（ip-开始-结束-sessionid）/ 同会话复用 / 聚合写入 / \r 覆盖合并"""
import asyncio
import glob
import logging
import os
import time

import pytest

from ssh_web_tool.sessions import SSHSession


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """把 LOG_DIR 指到临时目录 + 清空 logger 缓存与全局 handlers，避免跨测试污染"""
    monkeypatch.setattr(SSHSession, "LOG_DIR", str(tmp_path))
    SSHSession._logger_cache.clear()
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("ssh_session."):
            lg = logging.getLogger(name)
            for h in lg.handlers[:]:
                lg.removeHandler(h)
    yield
    SSHSession._logger_cache.clear()


def _mk_session(session_id: str = "abc12345", host: str = "8.137.52.71"):
    return SSHSession(session_id, host, 22, "root")


# ---------- 命名 ----------

def test_log_file_naming_format():
    s = _mk_session()
    name = os.path.basename(s._log_file)
    # {host}_{start}_running_{session_id}.log
    parts = name.rsplit(".", 1)[0].split("_")
    assert parts[0] == "8.137.52.71"
    assert parts[-1] == "abc12345"
    assert parts[-2] == "running"
    # 中间是时间戳 YYYYMMDD-HHMMSS
    import re
    assert re.fullmatch(r"\d{8}-\d{6}", parts[1])


def test_log_file_sanitizes_host():
    s = SSHSession("sid12345", "2001:db8::1", 22, "u")
    assert os.path.basename(s._log_file).startswith("2001_db8__1_")


def test_finalize_log_file_replaces_running_with_end(tmp_path):
    s = _mk_session()
    assert "_running_" in s._log_file
    # 真实场景：先写入内容（FileHandler 打开句柄），再 finalize 应能成功 rename
    s._feed_log("hello finalize\r\n")
    s._flush_log_now()
    assert os.path.exists(s._log_file)
    old_path = s._log_file
    s.finalize_log_file()
    assert "_running_" not in s._log_file
    assert os.path.exists(s._log_file)
    assert not os.path.exists(old_path)
    with open(s._log_file, encoding="utf-8") as f:
        assert "hello finalize" in f.read()
    name = os.path.basename(s._log_file)
    parts = name.rsplit(".", 1)[0].split("_")
    assert re_fullmatch(r"\d{8}-\d{6}", parts[-2]), parts
    assert parts[-1] == "abc12345"


def re_fullmatch(pattern, s):
    import re
    return re.fullmatch(pattern, s)


def test_resolve_existing_log_reuses_same_session_file(tmp_path):
    """同一 session_id 恢复：沿用旧文件（同一终端 = 同一份记录）"""
    s1 = _mk_session()
    # 模拟旧文件已存在（含结束时间，命名 ip_start_end_sid.log）
    old = os.path.join(tmp_path, "8.137.52.71_20260909-100000_20260909-110000_abc12345.log")
    open(old, "w").close()
    s2 = SSHSession("abc12345", "8.137.52.71", 22, "root")
    assert s2._log_file == old


# ---------- \r 覆盖合并 ----------

def test_collapse_cr_lines_keeps_last_cr_state():
    text = "wget 10%\rwget 50%\rwget 100%\nOK\n"
    assert SSHSession._collapse_cr_lines(text) == "wget 100%\nOK\n"


def test_collapse_cr_lines_multiline():
    text = "line1\rline1b\nline2\rline2b\rline2c\n"
    assert SSHSession._collapse_cr_lines(text) == "line1b\nline2c\n"


def test_collapse_cr_lines_no_cr():
    assert SSHSession._collapse_cr_lines("a\nb\n") == "a\nb\n"


def test_collapse_cr_lines_keeps_crlf_lines():
    """CRLF 换行不得被误判为进度条覆盖（回归：曾整行内容被 \r 吞掉）"""
    assert SSHSession._collapse_cr_lines("第一行\r\n第二行\r\n") == "第一行\n第二行\n"
    assert SSHSession._collapse_cr_lines("echo ok\r\nok\r\n") == "echo ok\nok\n"


# ---------- 聚合写入 ----------

def test_feed_log_flush_writes_cleaned_single_block(tmp_path):
    """聚合 flush：ANSI 清洗 + \r 合并，日志文件只含最终显示内容"""
    s = _mk_session()

    async def _run():
        s._feed_log("\x1b[31m进度\x1b[0m 10%\r\x1b[31m进度\x1b[0m 100%\n完成\r\n")
        assert s._log_buf != ""  # 有运行中 loop → 走异步调度，缓冲未清
        # 直接同步 flush（跳过 0.6s 静默等待）
        s._flush_log_now()
        assert s._log_buf == ""

    asyncio.run(_run())
    content = open(s._log_file, encoding="utf-8").read()
    assert "10%" not in content          # \r 覆盖前的状态不落盘
    assert "100%" in content
    assert "完成" in content             # CRLF 行不得被 \r 误吞（回归）
    assert "\x1b" not in content         # ANSI 已清洗


def test_feed_log_flush_schedules_async(tmp_path):
    """异步静默 flush：等待超过 LOG_FLUSH_DELAY 后自动落盘"""
    s = _mk_session()

    async def _run():
        s._feed_log("hello world\r\n")
        await asyncio.sleep(s.LOG_FLUSH_DELAY + 0.3)
        assert s._log_buf == ""

    asyncio.run(_run())
    content = open(s._log_file, encoding="utf-8").read()
    assert "hello world" in content


def test_feed_log_flush_threshold(tmp_path):
    """超过阈值立即 flush（不等待静默期）"""
    s = _mk_session()
    big = "x" * (s.LOG_FLUSH_MAX + 100)
    s._feed_log(big)
    assert s._log_buf == ""  # 已同步 flush
    content = open(s._log_file, encoding="utf-8").read()
    assert content.count("x") == s.LOG_FLUSH_MAX + 100


def test_close_flushes_remaining_log(tmp_path):
    """关闭会话：剩余缓冲落盘 + 日志文件名补全结束时间"""
    s = _mk_session()

    async def _run():
        s._feed_log("tail data\r\n")
        await s.close()

    asyncio.run(_run())
    assert "_running_" not in s._log_file  # close 已补全 end
    # 找到最终文件
    files = glob.glob(os.path.join(tmp_path, "*.log"))
    assert len(files) == 1
    assert "tail data" in open(files[0], encoding="utf-8").read()


def test_cleanup_old_logs_removes_stale_only(tmp_path):
    old = os.path.join(tmp_path, "h_20260101-000000_20260101-010000_old12345.log")
    new = os.path.join(tmp_path, "h_20260909-000000_running_new12345.log")
    open(old, "w").close()
    open(new, "w").close()
    # 把旧文件 mtime 改到 40 天前
    old_ts = time.time() - 40 * 86400
    os.utime(old, (old_ts, old_ts))
    n = SSHSession.cleanup_old_logs(days=30)
    assert n == 1
    assert not os.path.exists(old)
    assert os.path.exists(new)
