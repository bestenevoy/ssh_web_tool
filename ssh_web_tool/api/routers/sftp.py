"""SFTP 域路由：远程文件管理 + 预操作上传 + scripts 目录"""

import subprocess
import sys
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
from ssh_web_tool.transfers import TransferCanceled, transfer_registry

router = APIRouter(prefix="/api", tags=["sftp"])


def _get_connected_session(session_id: str):
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    if not session or not session.is_connected:
        raise HTTPException(status_code=404, detail="会话不存在或未连接")
    if session.is_local():
        # 本机终端会话没有 SSH 传输层（conn=None），SFTP 无从谈起；明确拒绝而非 500
        raise HTTPException(status_code=400, detail="本机终端会话不支持 SFTP，请连接到远程主机后再使用文件功能")
    return session


def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or "download"


def _host_label(session) -> str:
    """传输列表显示用主机标识：user@host"""
    try:
        return f"{session.username}@{session.host}"
    except Exception:
        return ""


async def _safe_remote_remove(session, path: str) -> None:
    """取消上传后尽力删除远端半成品；失败忽略（可能根本没建出来）。"""
    try:
        await session.delete_file(path)
    except Exception:
        pass


async def _safe_local_remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


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
        filename = _basename(path)
        task = transfer_registry.create(
            session_id, "download", filename, path, "浏览器下载", host=_host_label(session), total=size
        )

        async def gen():
            # 流式转发：读取异常/取消时中断响应（流已开始后无法改状态码，结果记在任务上）
            try:
                async for chunk in session.read_file_stream(path):
                    task.add_done(len(chunk))
                    yield chunk
                task.finish("done")
            except TransferCanceled:
                task.finish("canceled", "已取消")
            except Exception as e:
                task.finish("failed", str(e))

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
    task = transfer_registry.create(
        session_id,
        "download",
        filename,
        req.remote_path,
        str(dest),
        host=_host_label(session),
        total=await session.stat_file(req.remote_path),
    )
    try:
        with open(dest, "wb") as f:
            # 分块流式写入：磁盘写 4MB 耗时可忽略，不整块进内存
            async for chunk in session.read_file_stream(req.remote_path):
                task.add_done(len(chunk))
                f.write(chunk)
                total += len(chunk)
        task.finish("done")
    except TransferCanceled:
        await _safe_local_remove(dest)
        task.finish("canceled", "已取消")
        return {"status": "canceled", "path": req.remote_path}
    except Exception as e:
        await _safe_local_remove(dest)
        task.finish("failed", str(e))
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
    task = transfer_registry.create(
        session_id,
        "upload",
        _basename(remote_path),
        "本机上传",
        remote_path,
        host=_host_label(session),
        total=file.size if file.size and file.size > 0 else -1,
    )

    async def req_chunks():
        # UploadFile 内部 spool 到临时文件，分块读取不会占用大量内存
        while True:
            chunk = await file.read(SFTP_CHUNK_SIZE)
            if not chunk:
                break
            task.add_done(len(chunk))  # 已请求取消时抛 TransferCanceled 中断
            yield chunk

    try:
        total = await session.write_file_stream(remote_path, req_chunks())
        task.finish("done")
        await get_event_bus().publish(
            "sftp_upload",
            "api",
            f"[{session_id}] SFTP 上传: {remote_path} ({total} bytes)",
            session_id=session_id,
            path=remote_path,
            size=total,
        )
        return {"status": "uploaded", "path": remote_path, "size": total}
    except TransferCanceled:
        await _safe_remote_remove(session, remote_path)
        task.finish("canceled", "已取消")
        return {"status": "canceled", "path": remote_path}
    except Exception as e:
        task.finish("failed", str(e))
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
    task = transfer_registry.create(
        req.session_id,
        "upload",
        src.name,
        str(src),
        req.remote,
        host=_host_label(session),
        total=src.stat().st_size,
    )

    async def local_chunks():
        # 本地磁盘分块读（4MB 一次，耗时可忽略）；源文件再大也不整块进内存
        with open(src, "rb") as f:
            while True:
                chunk = f.read(SFTP_CHUNK_SIZE)
                if not chunk:
                    break
                task.add_done(len(chunk))  # 已请求取消时抛 TransferCanceled 中断
                yield chunk

    try:
        total = await session.write_file_stream(req.remote, local_chunks())
        task.finish("done")
        return {"status": "uploaded", "source": str(src), "path": req.remote, "size": total}
    except TransferCanceled:
        await _safe_remote_remove(session, req.remote)
        task.finish("canceled", "已取消")
        return {"status": "canceled", "path": req.remote}
    except Exception as e:
        task.finish("failed", str(e))
        raise HTTPException(status_code=500, detail=f"上传失败: {e!s}")


@router.get("/sftp/transfers")
async def api_sftp_transfers(session_id: str | None = None):
    """传输任务列表（活动在前，完成按时间倒序）；前端 1s 轮询渲染传输列表"""
    return {"transfers": [t.to_dict() for t in transfer_registry.list(session_id)]}


@router.post("/sftp/transfers/{task_id}/cancel")
async def api_sftp_transfer_cancel(task_id: str):
    """请求取消传输：置标志位，分块循环下一次迭代生效并清理半成品"""
    ok, reason = transfer_registry.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404 if reason == "任务不存在" else 409, detail=reason)
    return {"status": "canceling"}


def _reveal_in_file_manager(path: Path) -> None:
    """在系统文件管理器中打开并选中该文件（Server 与本机同机运行，可直接拉起窗口）"""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


@router.post("/sftp/transfers/{task_id}/reveal")
async def api_sftp_transfer_reveal(task_id: str):
    """下载任务完成后打开本机目录并选中文件

    只揭示任务自带的 dst 路径（不接受任意路径入参，防目录注入）；
    浏览器下载（dst 是占位符）与上传任务没有本机文件，拒绝。
    """
    task = transfer_registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.direction != "download" or task.dst == "浏览器下载":
        raise HTTPException(status_code=400, detail="该任务没有本机文件")
    target = Path(task.dst)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件已不存在（可能已被删除或移动）")
    try:
        _reveal_in_file_manager(target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开目录失败: {e!s}")
    return {"status": "revealed", "path": str(target)}


@router.get("/scripts")
async def api_list_scripts():
    """列出 ~/.ai4one/sshtool/scripts/ 下的脚本文件（预操作上传下拉选择）"""
    scripts_dir = get_app_dir_fn()() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts = sorted(p.name for p in scripts_dir.iterdir() if p.is_file())
    return {"scripts": scripts, "dir": str(scripts_dir)}
