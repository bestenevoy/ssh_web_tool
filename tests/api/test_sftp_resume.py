"""SFTP 断点续传：上次失败/取消留下的 .part 接着传，而不是从头再来

指纹 = 源文件 "size:mtime"（下载侧记在本机 .part.meta，上传侧记在远端 .part.meta）。
指纹对不上（源文件被改写过）必须从头传——接着拼只会得到一个内容错乱的文件。
"""

import pytest

from ssh_web_tool.api.routers.sftp import _meta_text, _resume_point, _within
from ssh_web_tool.transfers import transfer_registry

CONTENT = b"0123456789ABCDEF"  # 16 字节，方便按半截切
MTIME = 1700000000
REMOTE = "/data/payload.bin"


class _Remote:
    """内存远端文件：固定内容，记录每次流式读的起始偏移（续传断言用）"""

    def __init__(self):
        self.is_connected = True
        self.offsets: list[int] = []

    def is_local(self):
        return False

    async def is_dir(self, path: str) -> bool:
        return False

    async def stat_file(self, path: str) -> int:
        return len(CONTENT)

    async def stat_info(self, path: str) -> tuple[int, int]:
        return (len(CONTENT), MTIME)

    async def read_file_stream(self, path: str, chunk_size: int = 4, offset: int = 0):
        self.offsets.append(offset)
        rest = CONTENT[offset:]
        for i in range(0, len(rest), chunk_size):
            yield rest[i : i + chunk_size]


class _UploadRemote:
    """内存远端文件系统（上传侧）：记录 append 模式与最终内容"""

    def __init__(self, part: bytes = b"", meta: str | None = None):
        self.is_connected = True
        self.files: dict[str, bytes] = {}
        if part:
            self.files[f"{REMOTE}.part"] = part
        if meta is not None:
            self.files[f"{REMOTE}.part.meta"] = meta.encode("utf-8")
        self.appends: list[bool] = []

    def is_local(self):
        return False

    async def write_file(self, path: str, content) -> bool:
        self.files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return True

    async def read_file(self, path: str, max_size: int = 10 * 1024 * 1024) -> str:
        return self.files[path].decode("utf-8")

    async def stat_file(self, path: str) -> int:
        return len(self.files[path]) if path in self.files else -1

    async def write_file_stream(self, path: str, chunk_iter, append: bool = False) -> int:
        self.appends.append(append)
        buf = bytearray(self.files.get(path, b"") if append else b"")
        sent = 0
        async for chunk in chunk_iter:
            buf += chunk
            sent += len(chunk)
        self.files[path] = bytes(buf)
        return sent

    async def rename_file(self, old: str, new: str) -> None:
        self.files[new] = self.files.pop(old)

    async def delete_file(self, path: str) -> bool:
        self.files.pop(path, None)
        return True


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例从空的传输任务表开始（列表断言只看本次传输）"""
    transfer_registry._tasks.clear()
    yield
    transfer_registry._tasks.clear()


@pytest.fixture()
def install(fake_sessions):
    def _install(session):
        fake_sessions.sessions["sess-res"] = session
        return session

    return _install


def _seed_part(tmp_path, have: int, meta: str | None) -> None:
    """在本机伪造上次中断留下的 .part（及可选指纹文件）"""
    (tmp_path / "payload.bin.part").write_bytes(CONTENT[:have])
    if meta is not None:
        (tmp_path / "payload.bin.part.meta").write_text(meta, encoding="utf-8")


def _download(client, tmp_path):
    return client.post(
        "/api/sftp/sess-res/download-to",
        json={"remote_path": REMOTE, "local_dir": str(tmp_path)},
    )


def _upload(client, tmp_path):
    src = tmp_path / "payload.bin"
    src.write_bytes(CONTENT)
    r = client.post(
        "/api/preop/upload",
        json={"session_id": "sess-res", "source": str(src), "source_type": "path", "remote": REMOTE},
    )
    return r, src


def test_download_resumes_from_existing_part(client, install, tmp_path):
    """本机已有半截 .part 且指纹对得上：只读远端剩余字节，拼出完整文件"""
    s = install(_Remote())
    _seed_part(tmp_path, 6, _meta_text(len(CONTENT), MTIME))
    r = _download(client, tmp_path)
    assert r.status_code == 200, r.text
    assert r.json()["size"] == len(CONTENT)
    assert s.offsets == [6]  # 前 6 字节没有重传
    assert (tmp_path / "payload.bin").read_bytes() == CONTENT
    assert not (tmp_path / "payload.bin.part").exists()
    assert not (tmp_path / "payload.bin.part.meta").exists()


def test_download_restarts_when_fingerprint_mismatch(client, install, tmp_path):
    """源文件被改写过（mtime 变了）：旧 .part 作废，从头传"""
    s = install(_Remote())
    _seed_part(tmp_path, 6, _meta_text(len(CONTENT), MTIME + 99))
    r = _download(client, tmp_path)
    assert r.status_code == 200, r.text
    assert s.offsets == [0]
    assert (tmp_path / "payload.bin").read_bytes() == CONTENT


def test_download_ignores_part_without_meta(client, install, tmp_path):
    """只有 .part 没有指纹：不敢接着拼（不知道源文件是不是同一个），从头传"""
    s = install(_Remote())
    _seed_part(tmp_path, 6, None)
    _download(client, tmp_path)
    assert s.offsets == [0]
    assert (tmp_path / "payload.bin").read_bytes() == CONTENT


def test_upload_resumes_from_remote_part(client, install, tmp_path):
    """远端已有半截 .part 且指纹对得上：以 append 模式只补传剩余部分"""
    src = tmp_path / "payload.bin"
    src.write_bytes(CONTENT)
    meta = _meta_text(len(CONTENT), int(src.stat().st_mtime))
    s = install(_UploadRemote(part=CONTENT[:6], meta=meta))
    r = client.post(
        "/api/preop/upload",
        json={"session_id": "sess-res", "source": str(src), "source_type": "path", "remote": REMOTE},
    )
    assert r.status_code == 200, r.text
    assert r.json()["size"] == len(CONTENT)
    assert s.appends == [True]
    assert s.files[REMOTE] == CONTENT
    assert f"{REMOTE}.part" not in s.files
    assert f"{REMOTE}.part.meta" not in s.files  # 成功后指纹清掉


def test_upload_restarts_when_fingerprint_mismatch(client, install, tmp_path):
    """源文件改过：远端半截 .part 作废，覆盖重传"""
    s = install(_UploadRemote(part=CONTENT[:6], meta="16:1"))
    r, _ = _upload(client, tmp_path)
    assert r.status_code == 200, r.text
    assert s.appends == [False]
    assert s.files[REMOTE] == CONTENT


def test_resumed_flag_surfaced_in_transfer_list(client, install, tmp_path):
    """传输列表能看到这次是续传（前端据此打「续传」标记）"""
    install(_Remote())
    _seed_part(tmp_path, 6, _meta_text(len(CONTENT), MTIME))
    assert _download(client, tmp_path).status_code == 200
    r = client.get("/api/sftp/transfers", params={"session_id": "sess-res"})
    assert r.status_code == 200
    tasks = r.json()["transfers"]
    assert tasks and tasks[0]["resumed"] is True
    assert tasks[0]["done"] == len(CONTENT)  # 已落地字节计入进度，进度条不从 0 开始


def test_resume_point_rules():
    """续传判定的三个条件：拿得到源大小、指纹一致、半截内容确实在 (0, size) 之间"""
    fp = _meta_text(100, 555)
    assert _resume_point(100, 555, fp, 40) == 40
    assert _resume_point(100, 555, fp, 0) == 0  # 空的 .part，没什么可续
    assert _resume_point(100, 555, fp, 100) == 0  # 已经传完（该走改名而不是续传）
    assert _resume_point(100, 555, fp, 120) == 0  # 比源文件还大，指纹不可信
    assert _resume_point(100, 556, fp, 40) == 0  # mtime 变了
    assert _resume_point(100, 555, "", 40) == 0  # 没有指纹
    assert _resume_point(-1, 0, fp, 40) == 0  # 拿不到源文件大小


def test_within_blocks_parent_escape(tmp_path):
    base = tmp_path / "dest"
    base.mkdir()
    assert _within(base / "sub" / "a.txt", base)
    assert not _within(base.parent / "outside.txt", base)
