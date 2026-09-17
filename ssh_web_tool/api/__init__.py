"""API 汇总：按域拆分后的路由统一挂载，main.py 只需 include 这一个 router"""

from fastapi import APIRouter

from ssh_web_tool.api.routers import (
    config,
    external,
    files,
    groups,
    history,
    hosts,
    quick_commands,
    sessions,
    sftp,
    storage_auto,
    ws,
)

router = APIRouter()

for _mod in (config, sessions, hosts, groups, quick_commands, history, sftp, storage_auto, external, ws, files):
    router.include_router(_mod.router)
