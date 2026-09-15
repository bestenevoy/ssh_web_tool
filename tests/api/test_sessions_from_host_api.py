"""from-host 建连 API：重连密码覆盖回归测试

需求：统一重连流程中弹窗输入的密码（req.password）优先于已保存密码；
不传/传空时回退已保存密码。FakeSession 捕获 connect 收到的凭据，不发起真实 SSH。
"""

import pytest


@pytest.fixture()
def host_id(client):
    r = client.post("/api/hosts", json={"name": "h1", "host": "10.0.0.1", "username": "root", "password": "saved-pass"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _patch_ssh(monkeypatch, connect_calls):
    from ssh_web_tool.sessions import SSHSession

    async def fake_connect(self, password=None, private_key=None, passphrase=None):
        connect_calls.append({"password": password, "private_key": private_key, "passphrase": passphrase})
        self._connected = True  # is_connected 是只读 property，直接设底层字段

    async def fake_start_shell(self, cols=120, rows=40, term_type="xterm-256color"):
        self._has_shell = True

    monkeypatch.setattr(SSHSession, "connect", fake_connect)
    monkeypatch.setattr(SSHSession, "start_interactive_shell", fake_start_shell)


def test_from_host_password_override(client, fake_sessions, monkeypatch, host_id):
    """req.password 非空时覆盖已保存密码（重连弹窗输入优先）"""
    connect_calls: list[dict] = []
    _patch_ssh(monkeypatch, connect_calls)
    r = client.post("/api/sessions/from-host", json={"host_id": host_id, "password": "typed-pass"})
    assert r.status_code == 200, r.text
    assert connect_calls[-1]["password"] == "typed-pass"


def test_from_host_falls_back_to_saved_password(client, fake_sessions, monkeypatch, host_id):
    """未传 password 时使用已保存密码"""
    connect_calls: list[dict] = []
    _patch_ssh(monkeypatch, connect_calls)
    r = client.post("/api/sessions/from-host", json={"host_id": host_id})
    assert r.status_code == 200, r.text
    assert connect_calls[-1]["password"] == "saved-pass"


def test_from_host_empty_password_falls_back(client, fake_sessions, monkeypatch, host_id):
    """password 传空串视为未提供，回退已保存密码"""
    connect_calls: list[dict] = []
    _patch_ssh(monkeypatch, connect_calls)
    r = client.post("/api/sessions/from-host", json={"host_id": host_id, "password": ""})
    assert r.status_code == 200, r.text
    assert connect_calls[-1]["password"] == "saved-pass"
