# -*- coding: utf-8 -*-
"""端到端测试：真实 SSH 连接（默认跳过，需 --e2e 参数）

测试机来源优先级：
1. 环境变量 WSTOOL_E2E_HOST / WSTOOL_E2E_USER / WSTOOL_E2E_PASSWORD / WSTOOL_E2E_PORT
2. 读取 data.json 中第一个启用了密码的主机

注意：这些测试会真实连接远端，运行前确认目标主机可访问。
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.e2e

# 放置在这里，便于 conftest 按标记 skip


def _e2e_target():
    """返回 (host, port, user, password) 或 None"""
    host = os.environ.get("WSTOOL_E2E_HOST")
    if host:
        return (
            host,
            int(os.environ.get("WSTOOL_E2E_PORT", "22")),
            os.environ.get("WSTOOL_E2E_USER", "root"),
            os.environ.get("WSTOOL_E2E_PASSWORD", ""),
        )
    # 从 data.json 找第一台有密码的主机
    try:
        from ssh_web_tool.storage import storage
        for h in storage.list_hosts():
            if h.get("password"):
                return (h["host"], h.get("port", 22), h.get("username", "root"), h["password"])
    except Exception:
        pass
    return None


@pytest.fixture(scope="module")
def ssh_target():
    t = _e2e_target()
    if not t:
        pytest.skip("未配置测试机：设置 WSTOOL_E2E_HOST 或在 data.json 中保存带密码的主机")
    return t


@pytest.mark.asyncio
async def test_connect_and_run_command(ssh_target):
    """真实连接 + 独立进程命令执行 + 退出码"""
    from ssh_web_tool.sessions import SSHSession

    host, port, user, password = ssh_target
    s = SSHSession("e2e-1", host, port, user)
    await s.connect(password=password)
    try:
        assert s.is_connected
        code, out, err = await s.run_command("echo e2e_hello")
        assert code == 0, err
        assert "e2e_hello" in out
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_interactive_shell_and_inject(ssh_target):
    """交互式终端 + 注入命令 + 输出捕获"""
    from ssh_web_tool.sessions import SSHSession

    host, port, user, password = ssh_target
    s = SSHSession("e2e-2", host, port, user)
    await s.connect(password=password)
    try:
        await s.start_interactive_shell()
        await asyncio.sleep(1.0)
        code, out, _ = await s.inject_and_capture("echo marker_42", capture_exit_code=True)
        assert "marker_42" in out
        assert code == 0
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_binary_upload_preserves_bytes(ssh_target):
    """回归 Bug1 的端到端版：二进制文件上传远端后字节一致"""
    import hashlib

    from ssh_web_tool.sessions import SSHSession

    host, port, user, password = ssh_target
    s = SSHSession("e2e-3", host, port, user)
    await s.connect(password=password)
    try:
        payload = bytes([0x00, 0xFF, 0xFE, 0x80, 0x41]) * 100
        await s.write_file("/tmp/wstool_e2e_payload.bin", payload)
        got = await s.read_file_bytes("/tmp/wstool_e2e_payload.bin")
        assert hashlib.md5(got).hexdigest() == hashlib.md5(payload).hexdigest()
        assert got == payload
        await s.delete_file("/tmp/wstool_e2e_payload.bin")
    finally:
        await s.close()
