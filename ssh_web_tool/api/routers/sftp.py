"""SFTP 域路由：远程文件管理 + 预操作上传 + scripts 目录"""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ssh_web_tool.api.models import (
    PreopUploadRequest,
    SftpDeleteRequest,
    SftpDownloadToRequest,
    SftpListRequest,
    SftpWriteRequest,
)
from ssh_web_tool.deps import get_app_dir_fn, get_event_bus, get_session_manager
from ssh_web_tool.sessions import SFTP_CHUNK_SIZE

router = APIRouter(prefix="/api", tags=["sftp"])


def _get_connected_session(session_id: str):
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    return session


@router.post("/sftp/{session_id}/list")
async def api_sftp_list(session_id: str, req: SftpListRequest):
    """列出远程目录内容"""
    session = _get_connected_session(session_id)
    try:
        items = await session.list_directory(req.path)
        await get_event_bus().publish(
            "sftp_list",
            "api",
            f"[{session_id}] SFTP 列目录: {req.path} ({len(items)}项)",
            session_id=session_id,
            path=req.path,
            count=len(items),
        )
        return {"path": req.path, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"列出目录失败: {e!s}")


@router.get("/sftp/{session_id}/read")
async def api_sftp_read(session_id: str, path: str):
    """读取远程文件内容（文本）"""
    session = _get_connected_session(session_id)
    try:
        content = await session.read_file(path)
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e!s}")


@router.get("/sftp/{session_id}/download")
async def api_sftp_download(session_id: str, path: str):
    """下载远程文件（流式：分块读取转发，大文件不再整块进内存）"""
    session = _get_connected_session(session_id)
    try:
        size = await session.stat_file(path)
        filename = path.split("/")[-1] or "download"

        async def gen():
            # 流式转发：读取异常时中断响应（流已开始后无法改状态码）
            async for chunk in session.read_file_stream(path):
                yield chunk

        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if size >= 0:
            headers["Content-Length"] = str(size)
        await get_event_bus().publish(
            "sftp_download",
            "api",
            f"[{session_id}] SFTP 下载: {path} ({size} bytes)",
            session_id=session_id,
            path=path,
            size=size,
        )
        return StreamingResponse(gen(), media_type="application/octet-stream", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {e!s}")


@router.post("/sftp/{session_id}/write")
async def api_sftp_write(session_id: str, req: SftpWriteRequest):
    """写入远程文件内容"""
    session = _get_connected_session(session_id)
    try:
        await session.write_file(req.path, req.content)
        await get_event_bus().publish(
            "sftp_upload",
            "api",
            f"[{session_id}] SFTP 上传/写入: {req.path} ({len(req.content)} bytes)",
            session_id=session_id,
            path=req.path,
            size=len(req.content),
        )
        return {"status": "written", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e!s}")


@router.post("/sftp/{session_id}/download-to")
async def api_sftp_download_to(session_id: str, req: SftpDownloadToRequest):
    """远程文件下载到本机目录（服务器端流式下载，Xftp 双窗工作台用）

    浏览器无法写本地任意路径，由同机 Server 直接经 SFTP 流式写入目标目录。
    仅支持文件（目录递归暂不支持）。
    """
    session = _get_connected_session(session_id)
    filename = req.remote_path.rstrip("/").rsplit("/", 1)[-1]
    if not filename:
        raise HTTPException(status_code=400, detail="远程路径不是文件")
    local_dir = Path(req.local_dir).expanduser()
    if not local_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"本地目录不存在: {req.local_dir}")
    dest = local_dir / filename
    total = 0
    try:
        with open(dest, "wb") as f:
            # 分块流式写入：磁盘写 4MB 耗时可忽略，不整块进内存
            async for chunk in session.read_file_stream(req.remote_path):
                f.write(chunk)
                total += len(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {e!s}")
    await get_event_bus().publish(
        "sftp_download",
        "api",
        f"[{session_id}] SFTP 下载到本机: {req.remote_path} → {dest} ({total} bytes)",
        session_id=session_id,
        path=req.remote_path,
        size=total,
    )
    return {"status": "downloaded", "path": req.remote_path, "local": str(dest), "size": total}


@router.post("/sftp/{session_id}/delete")
async def api_sftp_delete(session_id: str, req: SftpDeleteRequest):
    """删除远程文件或目录"""
    session = _get_connected_session(session_id)
    try:
        await session.delete_file(req.path)
        return {"status": "deleted", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e!s}")


@router.post("/sftp/{session_id}/upload")
async def api_sftp_upload(session_id: str, remote_path: str, file: UploadFile = File(...)):
    """上传文件到远程服务器（multipart，流式分块写入 SFTP，支持任意大小）

    优化：原实现 `await file.read()` 将整个文件读入内存后一次性 write，
    大文件（几百 MB+）内存暴涨导致上传失败。改为 4MB 分块流式写入。
    """
    session = _get_connected_session(session_id)

    async def req_chunks():
        # UploadFile 内部 spool 到临时文件，分块读取不会占用大量内存
        while True:
            chunk = await file.read(SFTP_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    try:
        total = await session.write_file_stream(remote_path, req_chunks())
        await get_event_bus().publish(
            "sftp_upload",
            "api",
            f"[{session_id}] SFTP 上传: {remote_path} ({total} bytes)",
            session_id=session_id,
            path=remote_path,
            size=total,
        )
        return {"status": "uploaded", "path": remote_path, "size": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")
    finally:
        await file.close()


@router.post("/preop/upload")
async def api_preop_upload(req: PreopUploadRequest):
    """预操作上传：后端直读本地源文件（浏览器拿不到本地路径，由同机 Server 读取）
    - source_type=path：source 为本机绝对路径（如 D:/scripts/deploy.sh）
    - source_type=script：source 为 ~/.ai4one/sshtool/scripts/ 下的文件名"""
    # get_app_dir 走 deps 动态读取：test_preop_api 通过替换 main.get_app_dir 注入临时目录
    get_app_dir_fn_ = get_app_dir_fn()
    session = _get_connected_session(req.session_id)
    source = (req.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="未指定源文件")
    if req.source_type == "script":
        # 只接受 scripts 目录内的文件名：拒绝路径穿越（../ 等读任意文件）
        name = Path(source).name
        if name != source:
            raise HTTPException(status_code=400, detail="script 类型只接受文件名，不能包含路径")
        src = get_app_dir_fn_() / "scripts" / name
    else:
        src = Path(source).expanduser()
        if not src.is_absolute():
            src = get_app_dir_fn_() / "scripts" / src
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"源文件不存在: {source}")

    async def local_chunks():
        # 本地磁盘分块读（4MB 一次，耗时可忽略）；源文件再大也不整块进内存
        with open(src, "rb") as f:
            while True:
                chunk = f.read(SFTP_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    try:
        total = await session.write_file_stream(req.remote, local_chunks())
        return {"status": "uploaded", "source": str(src), "path": req.remote, "size": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")


@router.get("/scripts")
async def api_list_scripts():
    """列出 ~/.ai4one/sshtool/scripts/ 下的脚本文件（预操作上传下拉选择）"""
    scripts_dir = get_app_dir_fn()() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
    return {"scripts": scripts, "dir": str(scripts_dir)}
