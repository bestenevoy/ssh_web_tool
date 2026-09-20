"""拦截/手切连接挂载已保存主机：_match_saved_host 按 host+port+username 精确匹配

本机终端输入 ssh 建立的会话原本 host_id 为空，不计入主机列表连接数/状态点；
switch_to_ssh 成功后经此匹配挂载。
"""

import pytest

import ssh_web_tool.deps as deps_mod
from ssh_web_tool.sessions import SSHSession

_HOSTS = [
    {"id": "h1", "host": "10.0.0.1", "port": 22, "username": "root"},
    {"id": "h2", "host": "10.0.0.1", "port": 2222, "username": "admin"},
    {"id": "h3", "host": "example.com", "username": "ubuntu"},  # 缺 port 字段 → 按 22
]


class _Storage:
    def list_hosts(self):
        return _HOSTS


def _patch(monkeypatch):
    monkeypatch.setattr(deps_mod, "get_storage", lambda: _Storage())


def _sess(host: str, port: int, user: str) -> SSHSession:
    return SSHSession("t1", host, port, user)


def test_match_by_host_port_user(monkeypatch):
    _patch(monkeypatch)
    assert _sess("10.0.0.1", 22, "root")._match_saved_host() == "h1"
    assert _sess("10.0.0.1", 2222, "admin")._match_saved_host() == "h2"


def test_missing_port_defaults_22(monkeypatch):
    _patch(monkeypatch)
    assert _sess("example.com", 22, "ubuntu")._match_saved_host() == "h3"


def test_mismatch_returns_empty(monkeypatch):
    _patch(monkeypatch)
    assert _sess("10.0.0.1", 22, "admin")._match_saved_host() == ""  # 用户不符
    assert _sess("10.0.0.9", 22, "root")._match_saved_host() == ""  # 主机不符


def test_storage_error_swallowed(monkeypatch):
    class _Boom:
        def list_hosts(self):
            raise RuntimeError("存储未初始化")

    monkeypatch.setattr(deps_mod, "get_storage", lambda: _Boom())
    assert _sess("10.0.0.1", 22, "root")._match_saved_host() == ""


@pytest.mark.asyncio
async def test_switch_to_ssh_mounts_host_id(monkeypatch):
    """switch_to_ssh 成功末尾执行挂载：host_id 随匹配结果更新（不匹配则清空）"""
    _patch(monkeypatch)
    s = _sess("0.0.0.0", 22, "nobody")  # 会话初始字段与目标无关，切换后应更新并重挂载

    async def _noop(**kwargs):
        return None

    s.connect = _noop  # type: ignore[method-assign]
    s.start_interactive_shell = _noop  # type: ignore[method-assign]
    await s.switch_to_ssh("10.0.0.1", 22, "root", "pw")
    assert s.host_id == "h1"

    # 切到未保存主机：host_id 清空，不会错挂在旧主机上
    await s.switch_to_ssh("192.168.1.5", 22, "guest", "pw")
    assert s.host_id == ""
