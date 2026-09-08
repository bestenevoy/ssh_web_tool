"""
历史命令存储模块（SQLite + aiosqlite 异步）

- 数据库文件：~/.ai4one/wstool/history.db（随统一数据目录）
- 表 command_history：command(唯一) / count(使用次数) / last_used / ignored(忽略标记)
- 提供记录、搜索、最近、忽略/恢复、JSON 迁移能力
"""
import json
import re
import time
from pathlib import Path
from typing import List, Dict

import aiosqlite

# ANSI/控制字符清洗：方向键等 ESC 序列若被拆解残留（如 [D、[C），也会被清理
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_OSC_RE = re.compile(r'\x1b\][\s\S]*?(\x07|\x1b\\)')
_CTRL_RE = re.compile(r'[\x00-\x1f\x7f]')


def clean_command(command: str) -> str:
    """清洗命令：剥离 ANSI 转义序列与残留控制字符，返回规范命令"""
    text = _ANSI_RE.sub('', command)
    text = _OSC_RE.sub('', text)
    text = _CTRL_RE.sub('', text)
    return text.strip()

from .config import get_app_dir

DB_NAME = "history.db"


def get_db_path() -> Path:
    """历史命令数据库路径（统一数据目录下）"""
    return get_app_dir() / DB_NAME


async def init_db() -> None:
    """建表（幂等）"""
    db = get_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS command_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL UNIQUE,
                count INTEGER NOT NULL DEFAULT 1,
                last_used REAL NOT NULL,
                ignored INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_last ON command_history(last_used)"
        )
        await conn.commit()
        await _purge_dirty_commands(conn)
        await conn.commit()


# 孤立方向键/功能键残留模式：如 [T、[D、[C、[A、[B、[3~ 等（旧版本输入处理把
# ESC 序列拆开后残留的字面片段，无 ESC 前缀无法用 ANSI 正则清除）
_GARBAGE_RE = re.compile(r'^\[\S{0,3}$')


async def _purge_dirty_commands(conn) -> int:
    """清理历史命令中的控制字符脏数据（旧版本方向键残留 [D/[C 等），返回清理条数"""
    cur = await conn.execute("SELECT id, command FROM command_history")
    rows = await cur.fetchall()
    removed = 0
    for rid, cmd in rows:
        if not cmd:
            continue
        clean = clean_command(cmd)
        if clean != cmd:
            if not clean:
                await conn.execute("DELETE FROM command_history WHERE id = ?", (rid,))
            else:
                try:
                    await conn.execute(
                        "UPDATE command_history SET command = ? WHERE id = ?", (clean, rid))
                except Exception:
                    # 清洗后与已有记录冲突：保留原记录，删除脏行
                    await conn.execute("DELETE FROM command_history WHERE id = ?", (rid,))
            removed += 1
        elif _GARBAGE_RE.match(clean):
            # 整条命令就是孤立控制残留（无内容可言）：直接删除
            await conn.execute("DELETE FROM command_history WHERE id = ?", (rid,))
            removed += 1
    return removed


async def record_command(command: str) -> None:
    """记录一条命令：已存在则次数 +1 并刷新使用时间，不存在则插入"""
    # 记录前清洗 ANSI/控制字符，防止方向键残留（[D/[C 等）污染历史
    cmd = clean_command(command)
    if not cmd or len(cmd) > 500:
        return
    async with aiosqlite.connect(get_db_path()) as conn:
        await conn.execute(
            "INSERT INTO command_history (command, count, last_used) VALUES (?, 1, ?) "
            "ON CONFLICT(command) DO UPDATE SET count = count + 1, last_used = excluded.last_used",
            (cmd, time.time()),
        )
        await conn.commit()


async def search_commands(keyword: str = "", limit: int = 50, include_ignored: bool = False) -> List[Dict]:
    """搜索命令，按使用频次降序（频次相同按最后使用时间降序）；默认过滤已忽略"""
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        kw = keyword.strip()
        conds: List[str] = []
        args: List = []
        if not include_ignored:
            conds.append("ignored = 0")
        if kw:
            conds.append("command LIKE ?")
            args.append(f"%{kw}%")
        sql = "SELECT command, count, last_used FROM command_history"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY count DESC, last_used DESC LIMIT ?"
        args.append(limit)
        cur = await conn.execute(sql, args)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_recent_commands(limit: int = 100) -> List[Dict]:
    """最近使用的命令（按最后使用时间降序），过滤已忽略"""
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT command, count, last_used FROM command_history "
            "WHERE ignored = 0 ORDER BY last_used DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_ignored_commands(limit: int = 200) -> List[Dict]:
    """已忽略的命令列表（供恢复管理）"""
    async with aiosqlite.connect(get_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT command, count, last_used FROM command_history "
            "WHERE ignored = 1 ORDER BY last_used DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def ignore_command(command: str) -> bool:
    """忽略一条命令，返回是否生效"""
    cmd = command.strip()
    if not cmd:
        return False
    async with aiosqlite.connect(get_db_path()) as conn:
        cur = await conn.execute(
            "UPDATE command_history SET ignored = 1 WHERE command = ?", (cmd,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def unignore_command(command: str) -> bool:
    """恢复一条被忽略的命令"""
    cmd = command.strip()
    if not cmd:
        return False
    async with aiosqlite.connect(get_db_path()) as conn:
        cur = await conn.execute(
            "UPDATE command_history SET ignored = 0 WHERE command = ?", (cmd,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def migrate_from_json(json_path: Path) -> int:
    """
    把旧版 data.json 中的 command_history 导入 SQLite（幂等）。
    - 数据库已有历史数据时不重复导入
    - 返回导入条数
    """
    if not json_path.is_file():
        return 0
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    hist = data.get("command_history") or {}
    if not hist:
        return 0
    async with aiosqlite.connect(get_db_path()) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM command_history")
        existing = (await cur.fetchone())[0]
        if existing > 0:
            return 0
        for cmd, info in hist.items():
            clean = clean_command(cmd)
            if not clean or _GARBAGE_RE.match(clean):
                continue  # 跳过控制字符残留垃圾（如 [T、[D 等）
            await conn.execute(
                "INSERT OR IGNORE INTO command_history (command, count, last_used) VALUES (?, ?, ?)",
                (clean, info.get("count", 1), info.get("last_used", time.time())),
            )
        await conn.commit()
        return len(hist)
