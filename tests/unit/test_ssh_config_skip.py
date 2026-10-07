"""连接参数跳过 ~/.ssh/config 解析（GBK 解码崩溃修复回归）

背景：asyncssh 默认（config=() 哨兵）会读 ~/.ssh/config，且用系统默认编码
（中文 Windows = GBK）打开文件——用户 config 里的 UTF-8 中文注释会抛
UnicodeDecodeError，所有 SSH 连接在建连前就崩。修复：显式传空列表，
asyncssh 的 load 里 `if config_paths:` 为假，完全不解析任何文件。
"""

from ssh_web_tool.sessions import SSHSession


def test_connect_kwargs_has_empty_config_list():
    """config 必须是空列表（falsy 且非 () 哨兵）：() 会触发默认 ~/.ssh/config 回退"""
    s = SSHSession("t", "10.0.0.1", 22, "root")
    kwargs = s._ssh_connect_kwargs()
    assert kwargs["config"] == []
    assert kwargs["config"] != ()  # 回归防护：改回 () 即恢复 GBK 崩溃路径
