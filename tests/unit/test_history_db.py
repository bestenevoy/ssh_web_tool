# -*- coding: utf-8 -*-
"""history_db 单元测试：记录/搜索/忽略/清理（数据库隔离在 conftest.fake_history_db）"""
import asyncio

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
