"""终端镜像转录测试：日志与终端显示一致性的核心逻辑

覆盖：进度条 \\r 覆盖、退格重放、bell/ANSI 不进文本、长输出滚动捕获、
clear 捕获与去重、readline 重绘、vim alt-screen 排除（含跨 chunk）、
宽字符、resize 快照对齐、SessionLog 集成（含默认不记录/暂停恢复）。
"""

import glob
import logging
import os

import pytest

from ssh_web_tool.sessions import SSHSession
from ssh_web_tool.terminal_mirror import TerminalMirror

# ---------- TerminalMirror 单元测试 ----------


def test_plain_lines():
    m = TerminalMirror()
    m.feed("hello\r\nworld\r\n")
    assert m.drain() == ["hello", "world"]


def test_progress_bar_keeps_final():
    """\\r 行内覆盖（进度条）：只保留最终显示状态"""
    m = TerminalMirror()
    m.feed("wget 10%\rwget 50%\rwget 100%\r\n")
    assert m.drain() == ["wget 100%"]


def test_backspace_erases():
    """退格 \\x08：擦除后的最终内容"""
    m = TerminalMirror()
    m.feed("abc\x08\x08\x08xyz\r\n")
    assert m.drain() == ["xyz"]


def test_bell_and_ansi_not_in_text():
    """bell \\x07 与 ANSI 颜色序列不进入转录文本"""
    m = TerminalMirror()
    m.feed("\x1b[31mred\x1b[0m\x07 text\r\n")
    assert m.drain() == ["red text"]


def test_readline_rewrite_keeps_final():
    """readline \\r + EL 重绘（Tab 补全/编辑重写整行）：保留最终行"""
    m = TerminalMirror()
    m.feed("user@host:~$ cd /va\r\x1b[Kuser@host:~$ cd /var\r\n")
    assert m.drain() == ["user@host:~$ cd /var"]


def test_scroll_captures_all_lines():
    """超过一屏的输出：滚出顶部的行全部被捕获（长输出不丢）"""
    m = TerminalMirror(columns=20, rows=3)
    m.feed("".join(f"line{i}\r\n" for i in range(1, 8)))
    assert m.drain() == [f"line{i}" for i in range(1, 8)]


def test_scroll_two_windows_no_duplicate():
    """两次 drain 窗口之间发生滚动：已转录的行不得重复出现"""
    m = TerminalMirror(columns=20, rows=3)
    m.feed("a1\r\na2\r\na3\r\na4\r\n")
    assert m.drain() == ["a1", "a2", "a3", "a4"]
    m.feed("a5\r\na6\r\n")
    assert m.drain() == ["a5", "a6"]


def test_clear_keeps_content_no_duplicate():
    """clear：已转录内容不重复；清屏后新内容正常"""
    m = TerminalMirror(columns=20, rows=3)
    m.feed("first\r\nsecond\r\n")
    assert m.drain() == ["first", "second"]
    m.feed("\x1b[2J\x1b[Hafter-clear\r\n")
    assert m.drain() == ["after-clear"]


def test_clear_unflushed_content_captured():
    """clear 前的内容尚未 flush：擦除前捕获，不丢失"""
    m = TerminalMirror(columns=20, rows=3)
    m.feed("only-on-screen\r\n")
    m.feed("\x1b[2J\x1b[Hnew-stuff\r\n")
    lines = m.drain()
    assert "only-on-screen" in lines
    assert "new-stuff" in lines


def test_alt_screen_excluded():
    """vim 等全屏应用（\\x1b[?1049h）内容不混入转录；退出后恢复采集"""
    m = TerminalMirror()
    m.feed("before\r\n")
    m.feed("\x1b[?1049h vim TUI content \x1b[?1049l")
    m.feed("after\r\n")
    lines = m.drain()
    assert "before" in lines
    assert "after" in lines
    assert all("TUI" not in ln for ln in lines)


def test_alt_screen_split_across_chunks():
    """1049 序列跨 chunk 到达：alt 状态跨 feed 保持，内容仍被排除"""
    m = TerminalMirror()
    m.feed("before\r\n")
    m.feed("\x1b[?1049h")
    m.feed("TUI-CONTENT")
    m.feed("\x1b[?1049l")
    m.feed("after\r\n")
    lines = m.drain()
    assert "before" in lines and "after" in lines
    assert all("TUI" not in ln for ln in lines)


def test_other_private_modes_not_mistaken_as_alt():
    """?2004h（括号粘贴）、?25l（光标隐藏）等不触发 alt 排除"""
    m = TerminalMirror()
    m.feed("\x1b[?2004h\x1b[?25lnormal output\x1b[?25h\x1b[?2004l\r\n")
    assert m.drain() == ["normal output"]


def test_wide_chars_rendered_once():
    """CJK 宽字符：占位单元不重复输出"""
    m = TerminalMirror()
    m.feed("中文测试\r\n")
    assert m.drain() == ["中文测试"]


def test_resize_keeps_snapshot_alignment():
    """缩小屏幕：快照随行数对齐，已转录行（仍在屏上）不得重复"""
    m = TerminalMirror(columns=20, rows=4)
    m.feed("l1\r\nl2\r\nl3\r\nl4\r\n")
    assert m.drain() == ["l1", "l2", "l3", "l4"]
    m.resize(20, 2)  # 顶部两行被丢弃
    m.feed("l5\r\n")
    assert m.drain() == ["l5"]


def test_reset_starts_fresh():
    """reset 后从空白屏幕重新转录（完全重建场景）"""
    m = TerminalMirror()
    m.feed("old\r\n")
    m.drain()
    m.reset()
    m.feed("new\r\n")
    assert m.drain() == ["new"]


def test_resize_keeps_untranscribed_scrolled():
    """resize 保留未转录的滚出行（未开启记录长期累积的连接历史不丢）"""
    m = TerminalMirror(columns=20, rows=3)
    m.feed("".join(f"line{i}\r\n" for i in range(1, 8)))  # 4 行滚出，未 drain
    m.resize(20, 4)  # 不同尺寸触发重建，滚出行必须保留
    got = m.drain()
    assert got[:4] == ["line1", "line2", "line3", "line4"]  # 滚出行在前
    for i in range(5, 8):  # 屏幕行（resize 时补转录）
        assert f"line{i}" in got
    m.feed("after\r\n")
    assert "after" in m.drain()  # 重建后继续正常转录


def test_scrolled_off_bounded():
    """未 drain 的滚出行有上限（长期不开启记录的会话内存有界）"""
    from ssh_web_tool.terminal_mirror import _MAX_SCROLLED_LINES

    m = TerminalMirror(columns=20, rows=3)
    m.feed("".join(f"row{i}\r\n" for i in range(_MAX_SCROLLED_LINES + 500)))
    assert len(m.screen.scrolled_off) == _MAX_SCROLLED_LINES  # 最老的行被丢弃
    got = m.drain()
    # drain = 滚出行（上限内）+ 屏幕上未转录的脏行，滚出行占前 _MAX_SCROLLED_LINES 行
    assert len(got) >= _MAX_SCROLLED_LINES
    assert got[0].startswith("row498")  # 最老保留的是 row498（row0..row497 已丢弃）
    assert "row0" not in got
    assert "row20499" in got  # 最后一行仍在（屏幕脏行）


# ---------- SessionLog 集成 ----------


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


def _mk():
    return SSHSession("mirr12345", "10.0.0.9", 22, "root")


def _read_log(s) -> str:
    with open(s._log_file, encoding="utf-8") as f:
        return f.read()


def test_session_log_progress_bar_and_bell(tmp_path):
    """日志与显示一致：进度条只留终态、bell/ANSI 不入文本"""
    s = _mk()
    s.set_logging(True)
    s._feed_log("\x1b[31m下载\x1b[0m 10%\r\x1b[31m下载\x1b[0m 100%\x07\r\n完成\r\n")
    s._flush_log_now()
    content = _read_log(s)
    assert "10%" not in content
    assert "下载 100%" in content
    assert "完成" in content
    assert "\x07" not in content and "\x1b" not in content


def test_session_log_alt_screen_and_clear(tmp_path):
    """alt-screen 内容不混入；clear 后先前内容保留"""
    s = _mk()
    s.set_logging(True)
    s._feed_log("first\r\n")
    s._feed_log("\x1b[?1049hvim content\x1b[?1049l")
    s._feed_log("second\r\n")
    s._flush_log_now()
    content = _read_log(s)
    assert "first" in content and "second" in content
    assert "vim" not in content
    s._feed_log("\x1b[2J\x1b[Hthird\r\n")
    s._flush_log_now()
    content = _read_log(s)
    assert "first" in content and "second" in content and "third" in content


def test_session_log_long_output_scrolled(tmp_path):
    """超过一屏的长输出：滚出的行也全部入日志"""
    s = _mk()
    s.set_logging(True)
    s._feed_log("".join(f"row{i}\r\n" for i in range(1, 61)))
    s._flush_log_now()
    content = _read_log(s)
    for i in range(1, 61):
        assert f"row{i}" in content


def test_session_log_disabled_not_recorded(tmp_path):
    """默认不记录：不创建日志文件；开启记录后连接以来的早期输出（banner 语义）也落盘"""
    s = _mk()
    s._feed_log("before enable\r\n")
    s._flush_log_now()
    assert glob.glob(os.path.join(tmp_path, "*.log")) == []  # 未开启时从不创建文件
    s.set_logging(True)  # 开启时立即转录：之前的输出（连接信息）一并落盘
    s._feed_log("after enable\r\n")
    s._flush_log_now()
    content = _read_log(s)
    assert "after enable" in content
    assert "before enable" in content  # 镜像自会话创建起持续 feed，早期输出保留


def test_session_log_pause_flushes_tail(tmp_path):
    """暂停记录：先落盘暂停前的尾部内容"""
    s = _mk()
    s.set_logging(True)
    s._feed_log("tail\r\n")
    s.set_logging(False)
    assert "tail" in _read_log(s)
