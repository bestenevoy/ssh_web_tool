"""文件域路由：本地文件读取/写入/目录浏览（编辑器页后端）

- 小文件（≤ 20MB）：一次读全量，可编辑可保存
- 大文件（> 20MB）：只读查看，按行分页（has_more 提示还有后续行）
- 编码：utf-8 → gbk → latin-1 依次探测；写回使用读入时探测到的编码
"""

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ssh_web_tool.api.models import FileWriteRequest
from ssh_web_tool.config import get_app_dir

router = APIRouter(prefix="/api/files", tags=["files"])

# 可编辑文件大小上限：超过则只读分页查看（编辑器不加载全量）
MAX_EDIT_SIZE = 20 * 1024 * 1024
# 大文件只读分页单页行数上限（后端硬上限，前端分页控件不超过该值）
MAX_PAGE_LINES = 5000
# 目录浏览条目上限（防止极端目录拖垮响应）
MAX_LIST_ENTRIES = 1000
# 编码白名单（探测结果与写回参数都限定在此集合内）
_ALLOWED_ENCODINGS = ("utf-8", "gbk", "latin-1")


def _resolve_path(path: str) -> Path:
    """规范化用户输入路径：支持 ~ 展开；空路径 400"""
    p = (path or "").strip()
    if not p:
        raise HTTPException(status_code=400, detail="路径不能为空")
    return Path(os.path.expanduser(p))


def _detect_encoding(data: bytes) -> str:
    """按 utf-8 → gbk → latin-1 探测编码（探测样本不足时逐级放宽）"""
    for enc in _ALLOWED_ENCODINGS:
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"  # latin-1 任意字节序列都合法，兜底必成功


@router.get("/read")
async def api_read_file(path: str, start_line: int = 0, max_lines: int = MAX_PAGE_LINES):
    """读取本地文件（文本）

    - 返回 readonly 标记：文件 > 20MB 时只读（前端切分页查看模式）
    - 大文件按行分页：start_line 起最多返回 max_lines 行（上限 5000），has_more 表示还有更多
    - 返回探测到的 encoding，前端保存时原样传回以保持编码一致
    - 文件 IO（全量读入/逐行扫描/解码）是同步阻塞操作，放入工作线程，避免卡事件循环
    """
    p = _resolve_path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not p.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")
    size = p.stat().st_size
    readonly = size > MAX_EDIT_SIZE
    max_lines = max(1, min(int(max_lines), MAX_PAGE_LINES))
    start_line = max(0, int(start_line))
    try:
        return await asyncio.to_thread(_read_file_sync, p, size, readonly, start_line, max_lines)
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="没有读取该文件的权限")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e}")


def _read_file_sync(p: Path, size: int, readonly: bool, start_line: int, max_lines: int) -> dict:
    """同步读取文件（在工作线程内执行；HTTPException 照常向上抛出由 FastAPI 处理）"""
    with open(p, "rb") as f:
        sample = f.read(8192)
        if b"\x00" in sample:
            raise HTTPException(status_code=400, detail="二进制文件不支持在编辑器中打开")
        encoding = _detect_encoding(sample)
        f.seek(0)
        if not readonly:
            # 小文件：全量读入（universal newlines 统一 \r\n → \n）
            content = f.read().decode(encoding)
            lines = content.split("\n")
            # split 会把结尾换行多切一个空元素：去掉以保真行数
            if lines and lines[-1] == "":
                lines.pop()
            return {
                "path": str(p),
                "size": size,
                "readonly": False,
                "encoding": "utf-8" if encoding == "utf-8" else encoding,
                "content": "\n".join(lines),
                "start_line": 0,
                "lines_returned": len(lines),
                "has_more": False,
            }
        # 大文件：跳过 start_line 行后读 max_lines 行（流式，不全量加载）
        buf: list[str] = []
        pos = 0
        with open(p, encoding=encoding, errors="replace", newline=None) as tf:
            for line in tf:
                if pos >= start_line and len(buf) < max_lines:
                    buf.append(line.rstrip("\n").rstrip("\r"))
                elif pos >= start_line:
                    break
                pos += 1
        return {
            "path": str(p),
            "size": size,
            "readonly": True,
            "encoding": encoding,
            "content": "\n".join(buf),
            "start_line": start_line,
            "lines_returned": len(buf),
            "has_more": pos > start_line + len(buf),
        }


@router.post("/write")
async def api_write_file(req: FileWriteRequest):
    """写入本地文件（编辑器保存）

    - 已存在且 > 20MB 的文件拒绝写入（只读查看场景）
    - encoding 限定白名单（与读取探测一致），保持文件原编码
    - 同步写盘放入工作线程，避免大内容写阻塞事件循环
    """
    p = _resolve_path(req.path)
    if p.exists() and p.stat().st_size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="文件过大（>20MB），仅支持只读查看，不能保存")
    encoding = req.encoding if req.encoding in _ALLOWED_ENCODINGS else "utf-8"
    if len(req.content.encode(encoding, errors="replace")) > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="保存内容过大（>20MB）")
    try:
        return await asyncio.to_thread(_write_file_sync, p, req.content, encoding)
    except PermissionError:
        raise HTTPException(status_code=403, detail="没有写入该文件的权限")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e}")


def _write_file_sync(p: Path, content: str, encoding: str) -> dict:
    """同步写入文件（在工作线程内执行）"""
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding=encoding, newline="") as f:
        f.write(content)
    return {"path": str(p), "size": p.stat().st_size, "encoding": encoding}


@router.get("/list")
async def api_list_dir(dir: str):
    """浏览目录（编辑器打开文件弹窗的简易文件列表）

    返回目录下条目（目录在前、名称排序，上限 1000 项），不递归。
    """
    d = _resolve_path(dir)
    if not d.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not d.is_dir():
        raise HTTPException(status_code=400, detail="路径不是目录")
    try:
        entries_raw = list(d.iterdir())
    except PermissionError:
        raise HTTPException(status_code=403, detail="没有访问该目录的权限")
    entries = []
    for entry in entries_raw[: MAX_LIST_ENTRIES * 2]:
        if len(entries) >= MAX_LIST_ENTRIES:
            break
        try:
            entries.append(
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                    "mtime": int(entry.stat().st_mtime),
                }
            )
        except Exception:
            continue  # 单个条目 stat 失败（占用/权限）直接跳过
    dirs = sorted((e for e in entries if e["is_dir"]), key=lambda e: e["name"].lower())
    files = sorted((e for e in entries if not e["is_dir"]), key=lambda e: e["name"].lower())
    return {"dir": str(d), "parent": str(d.parent) if d.parent != d else None, "entries": dirs + files}


@router.get("/defaults")
async def api_editor_defaults():
    """编辑器默认目录：新建 .zs 默认保存到 scripts/；日志快速打开入口用 logs/"""
    app_dir = get_app_dir()
    scripts_dir = app_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = app_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return {"scripts_dir": str(scripts_dir), "logs_dir": str(logs_dir)}
