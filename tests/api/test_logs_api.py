# -*- coding: utf-8 -*-
"""/api/logs/{session_id} 接口测试：会话删除后仍可从日志文件读取历史（重连保留旧内容的关键）"""
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import main


def _make_log(tmp_path, session_id: str, content: str, ended: bool = False) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if ended:
        name = f"8.137.52.71_20260910-100000_20260910-100100_{session_id}.log"
    else:
        name = f"8.137.52.71_20260910-100000_running_{session_id}.log"
    p = log_dir / name
    p.write_text(content, encoding="utf-8")
    return p


def test_logs_by_file_reads_deleted_session(monkeypatch, tmp_path):
    """会话已删除（断开）→ 按日志文件名匹配读取"""
    sid = "abc12345"
    _make_log(tmp_path, sid, "Welcome to Ubuntu\nroot@x:~# echo HI\nHI\n", ended=True)
    monkeypatch.setattr(main.SSHSession, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(main.session_manager, "get_session", lambda sid: None)

    c = TestClient(main.app)
    r = c.get(f"/api/logs/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert "echo HI" in body["content"]
    assert "HI" in body["content"]


def test_logs_by_file_matches_running_name(monkeypatch, tmp_path):
    """运行中命名（_running_）也能匹配"""
    sid = "def56789"
    _make_log(tmp_path, sid, "root@x:~# ls\nfile1\n", ended=False)
    monkeypatch.setattr(main.SSHSession, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(main.session_manager, "get_session", lambda sid: None)

    c = TestClient(main.app)
    r = c.get(f"/api/logs/{sid}")
    assert r.status_code == 200
    assert "file1" in r.json()["content"]


def test_logs_by_file_prefers_live_session(monkeypatch, tmp_path):
    """会话还活着 → 优先走内存 get_history_logs"""
    class FakeSession:
        def get_history_logs(self, offset=0, limit=2000):
            return "LIVE-SESSION-CONTENT"

    monkeypatch.setattr(main.SSHSession, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(main.session_manager, "get_session", lambda sid: FakeSession())

    c = TestClient(main.app)
    r = c.get("/api/logs/xyz")
    assert r.status_code == 200
    assert "LIVE-SESSION-CONTENT" in r.json()["content"]


def test_logs_by_file_unknown_session(monkeypatch, tmp_path):
    """无会话且无日志文件 → 空内容（不 404，前端 catch 友好）"""
    monkeypatch.setattr(main.SSHSession, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(main.session_manager, "get_session", lambda sid: None)

    c = TestClient(main.app)
    r = c.get("/api/logs/nonexist")
    assert r.status_code == 200
    assert r.json()["content"] == ""


def test_logs_by_file_cleans_ansi(monkeypatch, tmp_path):
    """ANSI 转义被清理（复用 _format_history_logs）"""
    sid = "ansi1234"
    _make_log(tmp_path, sid, "\x1b[32mGREEN\x1b[0m\nplain\n", ended=True)
    monkeypatch.setattr(main.SSHSession, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(main.session_manager, "get_session", lambda sid: None)

    c = TestClient(main.app)
    r = c.get(f"/api/logs/{sid}")
    assert "\x1b[" not in r.json()["content"]
    assert "GREEN" in r.json()["content"]
