"""SFTP 域路由：远程文件管理 + 预操作上传 + scripts 目录"""

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

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


async def _write_remote_atomic(session, remote_path: str, chunks, append: bool = False) -> int:
    """上传落盘：先写 <目标>.part 再改名转正；失败保留 .part 供下次续传

    直接写目标路径时，传输中途取消/断线会在远端留下一个"看起来完整"的截断文件，
    覆盖上传还会先毁掉原有的好文件。走 .part 后目标原封不动。
    append=True 为断点续传：接着远端已有 .part 往后写（不清空、不重传已落地部分）。
    """
    part = f"{remote_path}.part"
    total = await session.write_file_stream(part, chunks, append=append)
    await session.rename_file(part, remote_path)
    return total


async def _remote_meta(session, path: str) -> str:
    """读远端续传指纹（.part.meta），读不到就是空串（= 不能续传）"""
    try:
        return (await session.read_file(path, max_size=64)).strip()
    except Exception:
        return ""


async def _local_chunks(task, src: Path, offset: int = 0):
    """本机文件分块读生成器：4MB 一次也走线程池

    同步读会占住事件循环，期间所有会话的终端输出广播一起被饿死
    （慢盘/杀软实时扫描时尤其明显）。task.add_done 兼作取消检查点。
    offset>0 为断点续传：跳过已传部分。
    """
    with open(src, "rb") as f:
        if offset > 0:
            f.seek(offset)
        while True:
            chunk = await asyncio.to_thread(f.read, SFTP_CHUNK_SIZE)
            if not chunk:
                break
            task.add_done(len(chunk))  # 已请求取消时抛 TransferCanceled 中断
            yield chunk


async def _upload_one(session, task, src: Path, remote_path: str) -> int:
    """上传单个本机文件到远端路径（.part + 改名），返回文件总字节数

    远端已有同名 .part 且指纹（源文件 size:mtime）对得上时自动续传：已落地字节
    直接计入进度，只补传剩余部分；指纹对不上（源文件改过）就从头传。
    """
    st = await asyncio.to_thread(src.stat)
    size, mtime = st.st_size, int(st.st_mtime)
    part, meta = f"{remote_path}.part", f"{remote_path}.part.meta"
    offset = _resume_point(size, mtime, await _remote_meta(session, meta), await session.stat_file(part))
    if offset:
        task.add_done(offset)
        task.resumed = True
        print(f"[SFTP] 上传续传 {remote_path}: 已有 {offset}/{size} 字节，只补传剩余部分")
    # 指纹先落远端：中途断线后下次才知道这个 .part 是不是同一个源文件传出来的
    await session.write_file(meta, _meta_text(size, mtime))
    sent = await _write_remote_atomic(session, remote_path, _local_chunks(task, src, offset), append=bool(offset))
    # 失败/取消不删 .part 与 .meta——那正是下次续传的素材；成功后指纹文件随即清掉
    await _safe_remote_remove(session, meta)
    return offset + sent


@dataclass
class _Entry:
    """目录递归清单条目；rel 为相对根目录的路径（统一 "/" 分隔，本机侧交给 Path 转换）"""

    rel: str
    is_dir: bool
    size: int
    src: str  # 源侧绝对路径（远端 posix 路径 / 本机路径）


# 递归条目上限：误选整盘目录时不至于把清单和传输列表撑爆
_WALK_LIMIT = 20000


async def _walk_remote(session, root: str) -> list[_Entry]:
    """广度扫描远端目录树（list_directory 一次 readdir 就带回大小，不逐文件 stat）

    父目录一定排在子条目之前，落盘侧按顺序建目录即可。注意 list_directory 对
    读不了的目录返回空列表（与文件浏览器同款口径），这类子树会被静默跳过。
    """
    out: list[_Entry] = []
    queue: list[tuple[str, str]] = [(root.rstrip("/") or "/", "")]
    while queue:
        cur, rel = queue.pop(0)
        for item in await session.list_directory(cur):
            name = item.get("name") or ""
            if name in ("", ".", ".."):
                continue
            child_rel = f"{rel}/{name}" if rel else name
            child = f"{cur}/{name}" if cur != "/" else f"/{name}"
            is_dir = item.get("type") == "dir"
            out.append(_Entry(child_rel, is_dir, int(item.get("size") or 0), child))
            if is_dir:
                queue.append((child, child_rel))
            if len(out) > _WALK_LIMIT:
                raise HTTPException(status_code=400, detail=f"目录条目超过 {_WALK_LIMIT}，请分目录传输")
    return out


def _walk_local_sync(root: Path) -> list[_Entry]:
    """本机目录树清单（os.walk 自顶向下，父目录先于子条目）；同步实现，调用方走线程池"""
    out: list[_Entry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        for d in sorted(dirnames):
            out.append(_Entry(f"{rel_dir}/{d}" if rel_dir else d, True, 0, str(Path(dirpath) / d)))
        for f in sorted(filenames):
            p = Path(dirpath) / f
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append(_Entry(f"{rel_dir}/{f}" if rel_dir else f, False, size, str(p)))
        if len(out) > _WALK_LIMIT:
            raise ValueError(f"目录条目超过 {_WALK_LIMIT}，请分目录传输")
    return out


def _remote_join(base: str, rel: str) -> str:
    """拼远端路径（rel 用 "/" 分隔，posix 语义）"""
    return f"{base.rstrip('/')}/{rel}"


def _within(dest: Path, base: Path) -> bool:
    """dest 是否落在 base 之内：远端条目名里可能带 ../，必须挡住路径穿越"""
    try:
        return dest.resolve().is_relative_to(base.resolve())
    except Exception:
        return False


class _AsyncLocalWriter:
    """下载到本机的文件写入器：磁盘 I/O 全部走线程池。

    4MB 一块的同步 write/close 在杀软实时扫描、慢盘（U 盘/网络盘）上会卡几十
    毫秒甚至更久，期间事件循环被占住——所有会话的终端输出广播一起饿死。
    """

    def __init__(self, path: Path, append: bool = False):
        self.path = path
        self.append = append  # 断点续传：追加到已有 .part 末尾而不是从头覆盖
        self._fh: BinaryIO | None = None

    async def open(self) -> None:
        self._fh = await asyncio.to_thread(self.path.open, "ab" if self.append else "wb")

    async def write(self, data: bytes) -> None:
        fh = self._fh
        if fh is None:
            raise RuntimeError("写入器未打开")
        await asyncio.to_thread(fh.write, data)

    async def close(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is not None:
            await asyncio.to_thread(fh.close)

    async def close_quietly(self) -> None:
        """异常/取消路径收尾：清理失败不再抛，避免掩盖原始错误"""
        try:
            await self.close()
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
    """远程文件/目录下载到本机目录（服务器端流式下载，Xftp 双窗工作台用）

    浏览器无法写本地任意路径，由同机 Server 直接经 SFTP 流式写入目标目录。
    远程路径是目录时递归下载，在本机镜像出同样的目录结构。
    """
    session = _get_connected_session(session_id)
    filename = _basename(req.remote_path)
    local_dir = Path(req.local_dir).expanduser()
    if not local_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"本地目录不存在: {req.local_dir}")
    if await session.is_dir(req.remote_path):
        return await _download_dir(session, session_id, req.remote_path, local_dir)
    if not filename:
        raise HTTPException(status_code=400, detail="远程路径不是文件")
    dest = local_dir / filename
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
        total = await _download_one(session, task, req.remote_path, dest)
        task.finish("done")
    except TransferCanceled:
        task.finish("canceled", "已取消")
        return {"status": "canceled", "path": req.remote_path}
    except Exception as e:
        task.finish("failed", str(e))
        raise HTTPException(status_code=500, detail=f"下载失败: {e!s}")
    await _publish_transfer(session_id, "sftp_download", req.remote_path, str(dest), total)
    return {"status": "downloaded", "path": req.remote_path, "local": str(dest), "size": total}


async def _download_dir(session, session_id: str, remote_dir: str, local_root: Path) -> dict:
    """递归下载远端目录：先扫一遍拿到清单与总大小（进度条才有分母），再逐文件搬运"""
    entries = await _walk_remote(session, remote_dir)
    files = [e for e in entries if not e.is_dir]
    base = local_root / _basename(remote_dir)
    task = transfer_registry.create(
        session_id,
        "download",
        f"{base.name}/（{len(files)} 个文件）",
        remote_dir,
        str(base),
        host=_host_label(session),
        total=sum(e.size for e in files),
    )
    total, done_files = 0, 0
    try:
        # 目录骨架先建齐（含空目录），文件再逐个 .part 落盘
        await asyncio.to_thread(base.mkdir, parents=True, exist_ok=True)
        for e in entries:
            if e.is_dir:
                await asyncio.to_thread((base / e.rel).mkdir, parents=True, exist_ok=True)
        for e in files:
            dest = base / e.rel
            if not _within(dest, base):
                raise RuntimeError(f"远端条目路径越界，已拒绝: {e.rel}")
            total += await _download_one(session, task, e.src, dest)
            done_files += 1
        task.finish("done")
    except TransferCanceled:
        task.finish("canceled", "已取消")
        return {"status": "canceled", "path": remote_dir, "files": done_files, "size": total}
    except Exception as e:
        task.finish("failed", str(e))
        raise HTTPException(status_code=500, detail=f"目录下载失败（已完成 {done_files}/{len(files)} 个文件）: {e!s}")
    await _publish_transfer(session_id, "sftp_download", remote_dir, str(base), total, len(files))
    return {"status": "downloaded", "path": remote_dir, "local": str(base), "size": total, "files": len(files)}


async def _upload_dir(session, session_id: str, src_root: Path, remote_dir: str) -> dict:
    """递归上传本机目录：远端镜像出同样的目录结构，每个文件仍走 .part + 改名"""
    entries = await asyncio.to_thread(_walk_local_sync, src_root)
    files = [e for e in entries if not e.is_dir]
    remote_dir = remote_dir.rstrip("/") or "/"
    task = transfer_registry.create(
        session_id,
        "upload",
        f"{src_root.name}/（{len(files)} 个文件）",
        str(src_root),
        remote_dir,
        host=_host_label(session),
        total=sum(e.size for e in files),
    )
    total, done_files = 0, 0
    try:
        await session.make_directory(remote_dir)
        for e in entries:
            if e.is_dir:
                await session.make_directory(_remote_join(remote_dir, e.rel))
        for e in files:
            total += await _upload_one(session, task, Path(e.src), _remote_join(remote_dir, e.rel))
            done_files += 1
        task.finish("done")
    except TransferCanceled:
        task.finish("canceled", "已取消")
        return {"status": "canceled", "source": str(src_root), "path": remote_dir, "files": done_files, "size": total}
    except Exception as e:
        task.finish("failed", str(e))
        raise HTTPException(status_code=500, detail=f"目录上传失败（已完成 {done_files}/{len(files)} 个文件）: {e!s}")
    await _publish_transfer(session_id, "sftp_upload", str(src_root), remote_dir, total, len(files))
    return {"status": "uploaded", "source": str(src_root), "path": remote_dir, "size": total, "files": len(files)}


async def _download_one(session, task, remote_path: str, dest: Path) -> int:
    """单个远端文件下载到 dest：先写 .part 再原子改名，返回文件总字节数

    已有 .part 且源文件没变过（size:mtime 与 .meta 记录一致）时自动续传：从 .part
    长度处接着读远端，已落地字节直接计入进度。
    失败/取消向上抛（整体任务状态由调用方判定），.part 与 .meta 都留着——那正是
    下次续传的素材；目标位置原有的同名文件始终不碰。
    """
    part = dest.with_name(f"{dest.name}.part")
    meta = _meta_path(part)
    size, mtime = await session.stat_info(remote_path)
    offset = _resume_point(size, mtime, await _local_meta(meta), _local_size(part))
    if offset:
        task.add_done(offset)
        task.resumed = True
        print(f"[SFTP] 下载续传 {remote_path}: 已有 {offset}/{size} 字节，只补传剩余部分")
    writer = _AsyncLocalWriter(part, append=offset > 0)
    total = offset
    try:
        await writer.open()
        await _write_local_meta(meta, size, mtime)
        async for chunk in session.read_file_stream(remote_path, offset=offset):
            task.add_done(len(chunk))
            await writer.write(chunk)
            total += len(chunk)
        await writer.close()
        await asyncio.to_thread(part.replace, dest)
        await _safe_local_remove(meta)
        return total
    except BaseException:
        await writer.close_quietly()
        raise


def _meta_path(part: Path) -> Path:
    return part.with_name(f"{part.name}.meta")


def _meta_text(size: int, mtime: int) -> str:
    """续传校验用源文件指纹：大小 + 修改时间"""
    return f"{size}:{mtime}"


def _resume_point(size: int, mtime: int, meta: str, have: int) -> int:
    """能续传就返回已落地字节数，否则 0（从头传）

    三个条件缺一不可：拿得到源文件大小、.part 有内容且比源文件小、指纹与源文件
    当前 size:mtime 一致——源被改写过还接着拼，只会得到一个内容错乱的文件。
    """
    if size < 0 or meta != _meta_text(size, mtime):
        return 0
    return have if 0 < have < size else 0


def _local_size(part: Path) -> int:
    try:
        return part.stat().st_size if part.exists() else 0
    except OSError:
        return 0


async def _local_meta(meta: Path) -> str:
    try:
        return await asyncio.to_thread(meta.read_text, encoding="utf-8") if meta.exists() else ""
    except OSError:
        return ""


async def _write_local_meta(meta: Path, size: int, mtime: int) -> None:
    """落 .part.meta（源文件指纹）；写不进去不影响传输，只是下次没法续传"""
    if size < 0:
        return
    try:
        await asyncio.to_thread(meta.write_text, _meta_text(size, mtime), encoding="utf-8")
    except OSError:
        pass


async def _publish_transfer(session_id: str, event: str, src: str, dst: str, size: int, files: int = 0):
    """传输完成事件（目录传输额外带上文件数）"""
    extra = f"，{files} 个文件" if files else ""
    label = "下载到本机" if event == "sftp_download" else "上传"
    await get_event_bus().publish(
        event,
        "api",
        f"[{session_id}] SFTP {label}: {src} → {dst} ({size} bytes{extra})",
        session_id=session_id,
        path=src,
        size=size,
    )


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
        total = await _write_remote_atomic(session, remote_path, req_chunks())
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
        # .part 留在远端（下次可续传）；目标位置原有同名文件始终不动
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
    if src.is_dir():
        # 目录：递归上传，远端镜像出同样的目录结构（整目录一个传输任务）
        return await _upload_dir(session, req.session_id, src, req.remote)
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
    try:
        total = await _upload_one(session, task, src, req.remote)
        task.finish("done")
        return {"status": "uploaded", "source": str(src), "path": req.remote, "size": total}
    except TransferCanceled:
        # .part 与指纹留在远端（下次自动续传），目标位置原有文件保留
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
