"""命令历史域路由：全局历史（记录/搜索/忽略/恢复）+ 统一搜索"""

import asyncio

from fastapi import APIRouter

from ssh_web_tool.api.models import IgnoreCommandRequest, RecordCommandRequest
from ssh_web_tool.deps import get_history_db, get_session_manager, get_storage

router = APIRouter(prefix="/api", tags=["history"])


@router.post("/history/record")
async def api_record_command(req: RecordCommandRequest):
    """记录一条命令到全局历史（增加使用频次）

    Tab 补全延迟兜底模式（session_id + defer_sec>0）：补全文本从输出侧回来，
    前端键盘缓冲对含 Tab 的行不可信（可能残缺，补全后再输入还会交错成非前缀
    乱串，DB 前缀/超串去重拦不住）——先延迟等待回显解析（权威）落库，窗口内
    该会话已有回显记录则跳过；没等到才兜底记键盘版，保证任何 shell 下至少一条
    """
    if req.defer_sec > 0 and req.session_id:
        await asyncio.sleep(req.defer_sec)
        sess = get_session_manager().get_session(req.session_id)
        # +0.8s 容差：回显多在前端 HTTP 到达前几十毫秒内已记录，纯比较请求时刻会误兜底
        try:
            recently = sess.echo_recently_recorded(req.defer_sec + 0.8) if sess is not None else False
        except Exception:
            recently = False  # 测试替身/旧对象无此方法：视作未记录，走兜底
        if recently:
            print(f"[history] 延迟兜底·回显已记→跳过: {req.command!r}")
            return {"status": "skipped_echo"}
        print(f"[history] 延迟兜底·回显未记→落键盘版: {req.command!r}")
        await get_history_db().record_command(req.command, via="延迟兜底")
        return {"status": "ok"}
    await get_history_db().record_command(req.command)
    return {"status": "ok"}


@router.get("/history/search")
async def api_search_commands(keyword: str = "", limit: int = 50, include_ignored: bool = False):
    """搜索全局命令历史（SQLite），按使用频次降序（频次相同按最后使用时间降序）；默认过滤已忽略"""
    results = await get_history_db().search_commands(keyword, limit, include_ignored=include_ignored)
    return {"keyword": keyword, "commands": results}


@router.get("/history/recent")
async def api_recent_commands(limit: int = 100):
    """获取最近使用的命令（按最后使用时间降序），已忽略的不返回"""
    results = await get_history_db().list_recent_commands(limit)
    return {"commands": results}


@router.get("/history/ignored")
async def api_ignored_commands(limit: int = 200):
    """获取已忽略的命令列表（用于恢复管理）"""
    results = await get_history_db().list_ignored_commands(limit)
    return {"commands": results}


@router.post("/history/ignore")
async def api_ignore_command(req: IgnoreCommandRequest):
    """忽略一条命令（搜索/最近命令不再显示）"""
    ok = await get_history_db().ignore_command(req.command)
    return {"status": "ok" if ok else "not_found"}


@router.post("/history/unignore")
async def api_unignore_command(req: IgnoreCommandRequest):
    """恢复一条被忽略的命令"""
    ok = await get_history_db().unignore_command(req.command)
    return {"status": "ok" if ok else "not_found"}


@router.post("/history/clear")
async def api_clear_history():
    """清空全部命令历史，返回删除条数"""
    cleared = await get_history_db().clear_history()
    return {"status": "ok", "cleared": cleared}


@router.get("/search")
async def api_unified_search(keyword: str = "", limit: int = 50):
    """
    统一搜索：同时搜索快捷命令和历史命令
    - 快捷命令：按名称和命令内容匹配，排在前面
    - 历史命令：按命令内容匹配，按使用频次排序
    返回合并后的结果，每条结果带 type 标识（quick/history）
    """
    storage = get_storage()
    history_db = get_history_db()
    kw = keyword.lower().strip()
    results = []

    # 1. 搜索快捷命令（排在前面）
    quick_commands = storage.list_quick_commands()
    for qc in quick_commands:
        if not kw or kw in qc.get("name", "").lower() or kw in qc.get("command", "").lower():
            results.append(
                {
                    "type": "quick",
                    "id": qc.get("id", ""),
                    "name": qc.get("name", ""),
                    "command": qc.get("command", ""),
                    "cmd_type": qc.get("type", "direct"),  # direct=直接执行 / param=输入到终端后编辑
                    "pre_ops": qc.get("pre_ops", []),  # 预操作：搜索执行与点击快捷指令行为一致
                }
            )

    # 2. 搜索历史命令（按使用频次排序，SQLite，已忽略的不返回）
    history_commands = await history_db.search_commands(keyword, limit)
    for hc in history_commands:
        # 避免和快捷命令重复（相同命令只显示一次，优先显示快捷命令）
        if not any(r["type"] == "quick" and r["command"] == hc["command"] for r in results):
            results.append(
                {
                    "type": "history",
                    "command": hc["command"],
                    "count": hc.get("count", 1),
                    "last_used": hc.get("last_used", 0),
                }
            )

    return {"keyword": keyword, "results": results[:limit]}
