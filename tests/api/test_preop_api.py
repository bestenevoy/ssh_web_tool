# -*- coding: utf-8 -*-
"""预操作上传 API 回归测试（Bug1：二进制文件静默损坏 + 路径穿越）"""
import pytest


def _make_fake_session():
    """内存会话：write_file 捕获收到的内容，不真实写远端"""
    class FakeSession:
        def __init__(self):
            self.is_connected = True
            self.written = []  # [(path, content)]

        async def write_file(self, path, content):
            self.written.append((path, content))
            return True

    return FakeSession()


@pytest.fixture()
def preop_session(fake_sessions):
    s = _make_fake_session()
    fake_sessions.sessions["sess-1"] = s
    return s


def test_upload_binary_is_byte_preserving(client, fake_sessions, tmp_path, preop_session):
    """回归 Bug1：含非法 UTF-8 字节的二进制文件上传必须原字节无损"""
    src = tmp_path / "payload.bin"
    payload = bytes([0x00, 0xFF, 0xFE, 0x80, 0x41]) * 100
    src.write_bytes(payload)

    r = client.post("/api/preop/upload", json={
        "session_id": "sess-1", "source": str(src),
        "source_type": "path", "remote": "/tmp/payload.bin",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["size"] == len(payload)

    # 关键断言：write_file 收到的是原始 bytes（未被 utf-8 replace 解码损坏）
    path, content = preop_session.written[-1]
    assert path == "/tmp/payload.bin"
    assert isinstance(content, bytes)
    assert content == payload


def test_upload_text_ok(client, fake_sessions, tmp_path, preop_session):
    src = tmp_path / "install.sh"
    src.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    r = client.post("/api/preop/upload", json={
        "session_id": "sess-1", "source": str(src),
        "source_type": "path", "remote": "/tmp/install.sh",
    })
    assert r.status_code == 200
    _, content = preop_session.written[-1]
    assert content == src.read_bytes()


def test_script_source_accepts_filename(client, fake_sessions, tmp_path, preop_session, monkeypatch):
    """script 类型：scripts 目录下的文件名可正常上传"""
    import main as main_module

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "deploy.sh").write_text("echo deploy", encoding="utf-8")
    # main.py 里 get_app_dir 是 import 进来的引用，需 patch main 模块
    monkeypatch.setattr(main_module, "get_app_dir", lambda: tmp_path)

    r = client.post("/api/preop/upload", json={
        "session_id": "sess-1", "source": "deploy.sh",
        "source_type": "script", "remote": "/tmp/deploy.sh",
    })
    assert r.status_code == 200, r.text
    _, content = preop_session.written[-1]
    assert content == b"echo deploy"


def test_script_source_rejects_path_traversal(client, fake_sessions, tmp_path, preop_session, monkeypatch):
    """回归：script 类型拒绝路径穿越（../data.json 读任意文件）"""
    import main as main_module

    (tmp_path / "data.json").write_text("SECRET", encoding="utf-8")
    monkeypatch.setattr(main_module, "get_app_dir", lambda: tmp_path)

    r = client.post("/api/preop/upload", json={
        "session_id": "sess-1", "source": "../data.json",
        "source_type": "script", "remote": "/tmp/x",
    })
    assert r.status_code == 400
    assert "路径" in r.json()["detail"]

    # 子目录形式同样拒绝
    r = client.post("/api/preop/upload", json={
        "session_id": "sess-1", "source": "sub/dir/file.sh",
        "source_type": "script", "remote": "/tmp/x",
    })
    assert r.status_code == 400


def test_upload_missing_source_404(client, fake_sessions, tmp_path, preop_session):
    r = client.post("/api/preop/upload", json={
        "session_id": "sess-1", "source": str(tmp_path / "nope.sh"),
        "source_type": "path", "remote": "/tmp/x",
    })
    assert r.status_code == 404


def test_upload_no_session_404(client, fake_sessions, tmp_path):
    src = tmp_path / "a.sh"
    src.write_text("x", encoding="utf-8")
    r = client.post("/api/preop/upload", json={
        "session_id": "missing", "source": str(src),
        "source_type": "path", "remote": "/tmp/x",
    })
    assert r.status_code == 404
