"""SSH 私钥文件认证：私钥字段支持 文件路径 / PEM 内容 双形态（_load_private_key_text + 连接参数组装）"""

from pathlib import Path

import asyncssh
import pytest

from ssh_web_tool.sessions import SSHSession, _load_private_key_text


def _make_session() -> SSHSession:
    return SSHSession("t", "10.0.0.1", 22, "root")


def test_pem_content_passthrough():
    """PEM 内容形态（手工粘贴/旧配置）：原样返回，不按路径处理"""
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
    assert _load_private_key_text(pem) == pem


def test_key_file_path_read(tmp_path: Path):
    """文件路径形态：读取文件文本"""
    p = tmp_path / "id_ed25519"
    p.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nBBBB\n-----END OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
    assert "BBBB" in _load_private_key_text(str(p))


def test_missing_file_raises():
    with pytest.raises(ValueError, match="私钥文件不存在"):
        _load_private_key_text("no/such/dir/id_rsa_xx")


def test_connect_kwargs_from_key_file_path(tmp_path: Path):
    """路径形态走完整组装链路：读文件 → import → client_keys"""
    key = asyncssh.generate_private_key("ssh-ed25519")
    p = tmp_path / "id_ed25519"
    p.write_bytes(key.export_private_key())
    s = _make_session()
    s._private_key = str(p)
    kwargs = s._ssh_connect_kwargs()
    assert len(kwargs["client_keys"]) == 1


def test_connect_kwargs_encrypted_key_with_passphrase(tmp_path: Path):
    """带口令私钥：口令正确可用，错误口令报"私钥导入失败"（不抛 asyncssh 原始异常）"""
    key = asyncssh.generate_private_key("ssh-ed25519")
    p = tmp_path / "id_ed25519"
    p.write_bytes(key.export_private_key("pkcs8-pem", passphrase="secret123"))
    s = _make_session()
    s._private_key = str(p)
    s._passphrase = "secret123"
    assert len(s._ssh_connect_kwargs()["client_keys"]) == 1

    s._passphrase = "wrong"
    with pytest.raises(ValueError, match="私钥导入失败"):
        s._ssh_connect_kwargs()


def test_connect_kwargs_bad_file_content_error(tmp_path: Path):
    """选错文件（非密钥内容）：报错带字段值，便于定位"""
    p = tmp_path / "not_a_key.txt"
    p.write_text("hello world", encoding="utf-8")
    s = _make_session()
    s._private_key = str(p)
    with pytest.raises(ValueError, match="私钥导入失败"):
        s._ssh_connect_kwargs()
