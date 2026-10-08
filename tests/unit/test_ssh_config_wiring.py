"""~/.ssh/config 接线测试：别名/HostName/Port/User/IdentityFile 覆盖、ProxyCommand 报错、ProxyJump 隧道

不发真实连接：asyncssh.connect 被替换为记录参数的假实现。
config 文件走 tmp_path + monkeypatch sessions._SSH_CONFIG_PATH，绝不读开发机真实 ~/.ssh/config。
"""

import asyncio
from typing import cast

import pytest

from ssh_web_tool import sessions as sessions_mod
from ssh_web_tool.sessions import SSHSession, _parse_jump_spec

CONFIG_ALIAS = """
# 中文注释必须能解析（asyncssh 按 GBK 打开会崩，这里是自解析器）
Host myalias
    HostName 10.9.8.7
    Port 2222
    User bob
    IdentityFile {key}

Host proxy-cmd
    HostName 10.0.0.9
    ProxyCommand ssh -W %h:%p bastion

Host jump-target
    HostName 192.168.1.1
    User carol
    ProxyJump jumpuser@jumphost:2200

Host multi-target
    ProxyJump hop1,hop2

Host hop1
    HostName 1.1.1.1
    User u1

Host hop2
    HostName 2.2.2.2
    Port 2022
"""


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """写入临时 ssh config 并把解析路径指过去；返回 (config_path, key_path)"""
    key = tmp_path / "id_test"
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n", encoding="utf-8")
    p = tmp_path / "config"
    p.write_text(CONFIG_ALIAS.format(key=str(key).replace("\\", "/")), encoding="utf-8")
    monkeypatch.setattr(sessions_mod, "_SSH_CONFIG_PATH", p)
    monkeypatch.setattr(sessions_mod, "get_use_ssh_config", lambda cfg=None: True)
    return p, key


class FakeTransport:
    def __init__(self):
        self.closing = False

    def is_closing(self):
        return self.closing


class FakeConn:
    def __init__(self, host=""):
        self.host = host
        self._transport = FakeTransport()
        self.closed = False

    def close(self):
        self.closed = True
        self._transport.closing = True

    async def wait_closed(self):
        pass


@pytest.fixture()
def fake_connect(monkeypatch):
    """替换 asyncssh.connect：记录每次调用的参数并返回 FakeConn；返回调用记录列表"""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        conn = FakeConn(kwargs.get("host", ""))
        conn.tunnel = kwargs.get("tunnel")  # type: ignore[attr-defined]
        return conn

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", _fake)
    return calls


def make_session(host, port=22, username="root"):
    return SSHSession("s-1", host, port, username)


# ---------- 目标/凭据覆盖 ----------


def test_alias_overrides_host_port_user(cfg_file):
    s = make_session("myalias")
    kwargs = s._ssh_connect_kwargs()
    assert (kwargs["host"], kwargs["port"], kwargs["username"]) == ("10.9.8.7", 2222, "bob")
    assert kwargs["client_keys"] == [str(cfg_file[1]).replace("\\", "/")]


def test_no_alias_match_uses_session_values(cfg_file):
    s = make_session("10.1.2.3", 2200, "alice")
    kwargs = s._ssh_connect_kwargs()
    assert (kwargs["host"], kwargs["port"], kwargs["username"]) == ("10.1.2.3", 2200, "alice")
    assert "client_keys" not in kwargs  # 不传 = 保留 asyncssh 默认密钥/agent 行为


def test_disabled_switch_skips_config(cfg_file, monkeypatch):
    monkeypatch.setattr(sessions_mod, "get_use_ssh_config", lambda cfg=None: False)
    s = make_session("myalias", 22, "root")
    kwargs = s._ssh_connect_kwargs()
    assert (kwargs["host"], kwargs["port"], kwargs["username"]) == ("myalias", 22, "root")


def test_explicit_private_key_wins_over_identity_file(cfg_file, monkeypatch):
    """主机配置里显式选了私钥时，config 的 IdentityFile 让位（不混进候选列表）"""
    monkeypatch.setattr(sessions_mod.asyncssh, "import_private_key", lambda *a, **k: "IMPORTED")
    s = make_session("myalias")
    s._private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\ninline\n"
    s._passphrase = None
    keys = s._client_keys(s._resolve_ssh_config())
    assert keys == ["IMPORTED"]


def test_missing_identity_file_is_skipped(cfg_file, monkeypatch):
    monkeypatch.setattr(sessions_mod.os.path, "isfile", lambda p: False)
    s = make_session("myalias")
    kwargs = s._ssh_connect_kwargs()
    assert "client_keys" not in kwargs


def test_proxy_command_raises(cfg_file):
    s = make_session("proxy-cmd")
    with pytest.raises(ValueError, match="ProxyCommand"):
        s._ssh_connect_kwargs()


def test_config_still_empty_list(cfg_file):
    """回归：asyncssh 自带解析必须继续关掉（() 哨兵会退回 GBK 崩溃路径）"""
    s = make_session("myalias")
    kwargs = s._ssh_connect_kwargs()
    assert kwargs["config"] == [] and kwargs["config"] != ()


# ---------- ProxyJump 隧道 ----------


@pytest.mark.asyncio()
async def test_proxy_jump_builds_tunnel_then_target(cfg_file, fake_connect):
    s = make_session("jump-target")
    await s._connect_ssh()
    assert len(fake_connect) == 2
    jump, target = fake_connect
    assert (jump["host"], jump["port"], jump["username"]) == ("jumphost", 2200, "jumpuser")
    assert jump["tunnel"] is None
    assert (target["host"], target["username"]) == ("192.168.1.1", "carol")
    assert target["tunnel"] is s._jump_conns[-1]
    assert len(s._jump_conns) == 1


@pytest.mark.asyncio()
async def test_multi_hop_chain(cfg_file, fake_connect):
    s = make_session("multi-target")
    await s._connect_ssh()
    assert [c["host"] for c in fake_connect] == ["1.1.1.1", "2.2.2.2", "multi-target"]
    assert fake_connect[0]["tunnel"] is None  # 首跳直连
    assert fake_connect[1]["tunnel"] is not None  # 第二跳穿过首跳
    assert fake_connect[2]["tunnel"] is not None  # 目标穿过末跳
    assert len(s._jump_conns) == 2


@pytest.mark.asyncio()
async def test_tunnel_reused_by_second_connection(cfg_file, fake_connect):
    """终端连接与 SFTP 传输独立连接共享同一条隧道，不重复打穿跳板机"""
    s = make_session("jump-target")
    await s._connect_ssh()
    await s._connect_ssh()
    assert len(fake_connect) == 3  # 1 跳板 + 2 目标
    assert len(s._jump_conns) == 1


@pytest.mark.asyncio()
async def test_partial_chain_failure_closes_built_hops(cfg_file, monkeypatch):
    built: list[FakeConn] = []

    async def _fake(**kwargs):
        if kwargs.get("host") == "2.2.2.2":  # 第二跳（hop2 的 HostName）
            raise OSError("第二跳连不上")
        c = FakeConn(kwargs.get("host", ""))
        built.append(c)
        return c

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", _fake)
    s = make_session("multi-target")
    with pytest.raises(OSError):
        await s._connect_ssh()
    assert built and all(c.closed for c in built)
    assert s._jump_conns == []


@pytest.mark.asyncio()
async def test_reused_chain_survives_target_failure(cfg_file, fake_connect, monkeypatch):
    """复用中的隧道还承载着已建连接：目标连接失败时不能顺手关掉整条链"""
    s = make_session("jump-target")
    await s._connect_ssh()
    jump = cast(FakeConn, s._jump_conns[-1])

    async def _boom(**kwargs):
        raise OSError("目标拒绝")

    monkeypatch.setattr(sessions_mod.asyncssh, "connect", _boom)
    with pytest.raises(OSError):
        await s._connect_ssh()
    assert jump.closed is False
    assert s._jump_conns == [jump]


@pytest.mark.asyncio()
async def test_close_jump_releases_chain(cfg_file, fake_connect):
    s = make_session("jump-target")
    await s._connect_ssh()
    jump = cast(FakeConn, s._jump_conns[-1])
    await s.close_jump()
    assert jump.closed and s._jump_conns == []


def test_parse_jump_spec():
    assert _parse_jump_spec("user@host:2222") == ("user", "host", 2222)
    assert _parse_jump_spec("host") == (None, "host", 22)
    assert _parse_jump_spec(" host:2200 ") == (None, "host", 2200)
    assert _parse_jump_spec("[fe80::1]:2200") == (None, "fe80::1", 2200)
    assert _parse_jump_spec("a@b@[::1]") == ("a@b", "::1", 22)  # 方括号剥掉


def test_parse_jump_spec_too_many_hops():
    s = make_session("multi-target")
    with pytest.raises(ValueError, match="跳数过多"):
        asyncio.run(s._open_jump_chain(",".join(f"h{i}" for i in range(10))))
