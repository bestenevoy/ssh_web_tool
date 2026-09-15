"""SFTP 域路由：远程文件管理 + 预操作上传 + scripts 目录"""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from ssh_web_tool.api.models import PreopUploadRequest, SftpDeleteRequest, SftpListRequest, SftpWriteRequest
from ssh_web_tool.deps import get_app_dir_fn, get_event_bus, get_session_manager

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
    """下载远程文件（二进制）"""
    session = _get_connected_session(session_id)
    try:
        data = await session.read_file_bytes(path)
        filename = path.split("/")[-1] or "download"
        await get_event_bus().publish(
            "sftp_download",
            "api",
            f"[{session_id}] SFTP 下载: {path} ({len(data)} bytes)",
            session_id=session_id,
            path=path,
            size=len(data),
        )
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
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
    """上传文件到远程服务器（multipart，支持二进制文件）

    优化：原代码将 bytes decode 为 utf-8 字符串后写入，
    二进制文件（图片/压缩包/可执行文件）会被损坏。
    改为直接写入 bytes，通过 write_file_bytes 方法。
    """
    session = _get_connected_session(session_id)
    try:
        content = await file.read()
        await session.write_file(remote_path, content)
        await get_event_bus().publish(
            "sftp_upload",
            "api",
            f"[{session_id}] SFTP 上传: {remote_path} ({len(content)} bytes)",
            session_id=session_id,
            path=remote_path,
            size=len(content),
        )
        return {"status": "uploaded", "path": remote_path, "size": len(content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")


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
    try:
        # 以 bytes 读取并二进制写入：文本/二进制文件均无损（不再 utf-8 替换损坏）
        data = src.read_bytes()
        await session.write_file(req.remote, data)
        return {"status": "uploaded", "source": str(src), "path": req.remote, "size": len(data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")


@router.get("/scripts")
async def api_list_scripts():
    """列出 ~/.ai4one/sshtool/scripts/ 下的脚本文件（预操作上传下拉选择）"""
    scripts_dir = get_app_dir_fn()() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
    return {"scripts": scripts, "dir": str(scripts_dir)}
