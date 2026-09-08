# -*- coding: utf-8 -*-
"""close_db 幂等与单例清理测试（Bug A 回归）

背景：history_db 单例 aiosqlite 连接若不关闭，非 daemon worker 线程会导致
进程正常退出（Ctrl+C/uvicorn 停止）时挂住。close_db 应幂等可重复调用。
"""
import pytest


@pytest.mark.asyncio
async def test_close_db_idempotent(fake_history_db):
    db = fake_history_db
    await db.record_command("test-close-db")
    # 首次关闭
    await db.close_db()
    assert db._db_conn is None
    # 再次关闭不报错（幂等）
    await db.close_db()
    assert db._db_conn is None
    # 关闭后可重新初始化（新连接）
    await db.init_db()
    assert db._db_conn is not None
    found = await db.search_commands(keyword="test-close-db")
    # 关闭前已提交的数据仍在（持久化在磁盘，不是内存）
    assert len(found) == 1
