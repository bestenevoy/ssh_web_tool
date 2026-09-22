"""history_db 单元测试：记录/搜索/忽略/清理（数据库隔离在 conftest.fake_history_db）"""

import pytest


@pytest.mark.asyncio
async def test_record_and_search(fake_history_db):
    db = fake_history_db
    await db.record_command("ls -la")
    await db.record_command("systemctl restart nginx")
    await db.record_command("top")

    found = await db.search_commands(keyword="systemctl")
    assert len(found) == 1
    assert found[0]["command"] == "systemctl restart nginx"


@pytest.mark.asyncio
async def test_record_command_skips_when_echo_superstring_recent(fake_history_db):
    """回显已记录更完整的命令（3 秒内）：前端补全前残留晚到时不再入库"""
    db = fake_history_db
    await db.record_echo_command("cd /var/log")  # 回显权威版（Tab 补全后）
    await db.record_command("cd /var")  # 前端键入版（HTTP 晚于回显到达）

    found = await db.search_commands(keyword="cd /var")
    assert [f["command"] for f in found] == ["cd /var/log"]


@pytest.mark.asyncio
async def test_record_command_unrelated_not_blocked_by_recent(fake_history_db):
    """超串保护只拦"本条是近 3 秒某记录的严格前缀"，无关命令正常入库"""
    db = fake_history_db
    await db.record_echo_command("cd /var/log")
    await db.record_command("ls -la")

    found = await db.search_commands(keyword="ls -la")
    assert len(found) == 1


@pytest.mark.asyncio
async def test_record_echo_skips_when_superstring_recent(fake_history_db):
    """折行截断防护：前端已记录完整命令（3 秒内），回显只解析到首行截断片段——
    片段作为严格前缀不再单独入库（与 record_command 的超串检查对称）"""
    db = fake_history_db
    await db.record_command("docker run -d --name app nginx:latest extra args")
    await db.record_echo_command("docker run -d --name app nginx:la")  # 折行首行片段

    found = await db.search_commands(keyword="docker run")
    assert [f["command"] for f in found] == ["docker run -d --name app nginx:latest extra args"]


@pytest.mark.asyncio
async def test_record_echo_superstring_guard_not_block_unrelated(fake_history_db):
    """反向防护只拦"本条是近 3 秒某记录的严格前缀"：无关回显命令正常入库"""
    db = fake_history_db
    await db.record_command("docker run -d nginx")
    await db.record_echo_command("git status")

    found = await db.search_commands(keyword="git")
    assert len(found) == 1


@pytest.mark.asyncio
async def test_recent_ordering(fake_history_db):
    db = fake_history_db
    await db.record_command("cmd-1")
    await db.record_command("cmd-2")
    await db.record_command("cmd-3")

    recent = await db.list_recent_commands(limit=10)
    # 最新的在最前
    assert recent[0]["command"] == "cmd-3"
    assert recent[-1]["command"] == "cmd-1"


@pytest.mark.asyncio
async def test_ignore_hides_from_search(fake_history_db):
    db = fake_history_db
    await db.record_command("rm -rf /tmp/x")
    await db.record_command("rm -rf /tmp/y")

    assert await db.ignore_command("rm -rf /tmp/x")

    # 默认搜索不返回被忽略的
    found = await db.search_commands(keyword="rm -rf")
    assert len(found) == 1
    assert found[0]["command"] == "rm -rf /tmp/y"

    # 显式包含忽略的能看到
    found_all = await db.search_commands(keyword="rm -rf", include_ignored=True)
    assert len(found_all) == 2

    # 忽略列表能看到
    ignored = await db.list_ignored_commands()
    assert "rm -rf /tmp/x" in [i["command"] for i in ignored]

    # 取消忽略后恢复可见
    assert await db.unignore_command("rm -rf /tmp/x")
    found = await db.search_commands(keyword="rm -rf")
    assert len(found) == 2


@pytest.mark.asyncio
async def test_dedup_same_command(fake_history_db):
    db = fake_history_db
    await db.record_command("pwd")
    await db.record_command("pwd")
    recent = await db.list_recent_commands(limit=10)
    # 同一条命令只保留一条（去重），且只有一次记录
    assert len([r for r in recent if r["command"] == "pwd"]) == 1


def test_clean_command():
    from ssh_web_tool.history_db import clean_command

    assert clean_command("  ls -la  ") == "ls -la"
    assert clean_command("") == ""
    assert clean_command("sudo apt update") == "sudo apt update"


def test_clean_command_charset_escape():
    """ESC ( B / ESC ) 0 字符集指定序列（tput sgr0 等）须整体剥离，
    不得残留 "(B" 字面量当成命令开头"""
    from ssh_web_tool.history_db import clean_command

    assert clean_command("\x1b(B\x1b[mls -la") == "ls -la"
    assert clean_command("\x1b)0draw box") == "draw box"
    assert clean_command("\x1b7cd /var\x1b8") == "cd /var"


@pytest.mark.asyncio
async def test_record_echo_strips_charset_escape(fake_history_db):
    db = fake_history_db
    await db.record_echo_command("\x1b(Bsystemctl restart nginx")
    found = await db.search_commands(keyword="systemctl")
    assert [f["command"] for f in found] == ["systemctl restart nginx"]


@pytest.mark.asyncio
async def test_init_db_prunes_legacy_charset_junk(fake_history_db):
    """旧版清洗入库的 "(B…" 垃圾行：init_db 一次性清掉，[[ 开头的合法命令不受影响"""
    db = fake_history_db
    conn = await db.get_db()
    for cmd in ["(Bls -la", "(0draw", "(Bcd /var"]:
        await conn.execute("INSERT INTO command_history (command, count, last_used) VALUES (?, 1, 1)", (cmd,))
    await conn.execute("INSERT INTO command_history (command, count, last_used) VALUES ('[[ -f x ]] && ls', 1, 1)")
    await conn.commit()
    await db.init_db()  # 重跑建表即触发清理（幂等）
    found = await db.search_commands(keyword="", limit=50)
    cmds = [f["command"] for f in found]
    assert "(Bls -la" not in cmds and "(0draw" not in cmds and "(Bcd /var" not in cmds
    assert "[[ -f x ]] && ls" in cmds


def test_clean_command_private_csi_and_dcs():
    """私有/kitty 参数区 CSI（[?2004h、[>1u）与 DCS（\\x1bP…\\x1b\\\\）整体剥离不留残片"""
    from ssh_web_tool.history_db import clean_command

    assert clean_command("\x1b[?2004l\x1b[>1uvim") == "vim"
    assert clean_command("\x1bP1;2$rr\x1b\\ls") == "ls"
    assert clean_command("\x1b[1;31mrm -rf /tmp/a\x1b[0m") == "rm -rf /tmp/a"


def test_residue_guard_rejects_truncated_fragments():
    """跨 chunk 截断残片（清洗无从配对的 "[1;31m" 等）：入库防线直接拒绝"""
    from ssh_web_tool.history_db import _looks_like_escape_residue

    assert _looks_like_escape_residue("[1;31m")
    assert _looks_like_escape_residue("[D")
    assert _looks_like_escape_residue("(Bls")
    assert not _looks_like_escape_residue("[ -f x ]")  # 合法 test 写法
    assert not _looks_like_escape_residue("[[ -n $x ]]")  # bash [[
    assert not _looks_like_escape_residue("(cd /var && pwd)")  # 合法子 shell
    assert not _looks_like_escape_residue("((1+2))")


@pytest.mark.asyncio
async def test_record_skips_residue(fake_history_db):
    db = fake_history_db
    await db.record_command("[1;31m")  # 残片直接入库也应被拒
    await db.record_echo_command("[?2004h")
    found = await db.search_commands(keyword="", limit=50)
    assert [f["command"] for f in found] == []


@pytest.mark.asyncio
async def test_init_db_prunes_csi_residue(fake_history_db):
    """存量清理扩到截断 CSI 残片：[1;31m/[D 删，[ 与 [[ 开头合法命令保留"""
    db = fake_history_db
    conn = await db.get_db()
    for cmd in ["[1;31m", "[D", "[?2004l"]:
        await conn.execute("INSERT INTO command_history (command, count, last_used) VALUES (?, 1, 1)", (cmd,))
    await conn.execute("INSERT INTO command_history (command, count, last_used) VALUES ('[ -f x ] && ls', 1, 1)")
    await conn.commit()
    await db.init_db()
    cmds = [f["command"] for f in await db.search_commands(keyword="", limit=50)]
    assert "[1;31m" not in cmds and "[D" not in cmds and "[?2004l" not in cmds
    assert "[ -f x ] && ls" in cmds
