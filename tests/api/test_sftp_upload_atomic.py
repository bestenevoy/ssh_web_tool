"""SFTP 上传：远端先写 .part 再改名转正（取消/失败不毁目标位置原有的同名文件）"""

import pytest

CHUNKS = [b"AAAA", b"BBBB", b"CCCC"]
CONTENT = b"".join(CHUNKS)


class _FakeSession:
    """内存远端文件系统：可注入中途失败，记录 append 模式与改名轨迹"""

    def __init__(self, fail_after: int | None = None):
        self.is_connected = True
        self.fail_after = fail_after
        self.files: dict[str, bytes] = {}
        self.renames: list[tuple[str, str]] = []
        self.appends: list[bool] = []

    def is_local(self):
        return False

    async def write_file(self, path: str, content) -> bool:
        self.files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return True

    async def read_file(self, path: str, max_size: int = 10 * 1024 * 1024) -> str:
        # 文件不存在直接抛 KeyError：_remote_meta 捕获后当"没有指纹"处理
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
            if self.fail_after is not None and sent >= self.fail_after:
                self.files[path] = bytes(buf)[: self.fail_after]  # 只有前 N 字节真的落到远端
                raise RuntimeError("远端通道中断")
        self.files[path] = bytes(buf)
        return sent

    async def rename_file(self, old: str, new: str) -> None:
        self.renames.append((old, new))
        self.files[new] = self.files.pop(old)

    async def delete_file(self, path: str) -> bool:
        self.files.pop(path, None)
        return True


@pytest.fixture()
def fake_session(fake_sessions):
    def _install(session: _FakeSession) -> _FakeSession:
        fake_sessions.sessions["sess-up"] = session
        return session

    return _install


def _upload(client, tmp_path, payload: bytes = b"", remote: str = "/data/out.bin"):
    src = tmp_path / "src.bin"
    src.write_bytes(payload or CONTENT)
    return client.post(
        "/api/preop/upload",
        json={"session_id": "sess-up", "source": str(src), "source_type": "path", "remote": remote},
    )


def test_upload_writes_part_then_renames(client, fake_session, tmp_path):
    s = fake_session(_FakeSession())
    r = _upload(client, tmp_path)
    assert r.status_code == 200, r.text
    assert s.renames == [("/data/out.bin.part", "/data/out.bin")]
    assert s.files["/data/out.bin"] == CONTENT
    assert "/data/out.bin.part" not in s.files  # 临时文件已转正，不留残余
    assert "/data/out.bin.part.meta" not in s.files  # 续传指纹用完即删
    assert s.appends == [False]


def test_upload_failure_keeps_preexisting_and_retains_part(client, fake_session, tmp_path):
    """中途失败：目标位置原有的同名文件不受牵连，.part + 指纹留着供下次续传"""
    s = fake_session(_FakeSession(fail_after=5))
    s.files["/data/out.bin"] = b"OLD-GOOD-CONTENT"
    r = _upload(client, tmp_path)
    assert r.status_code == 500
    assert s.files["/data/out.bin"] == b"OLD-GOOD-CONTENT"
    assert s.files["/data/out.bin.part"] == b"AAAABBBB"[:5]
    assert "/data/out.bin.part.meta" in s.files


def test_multipart_upload_also_atomic(client, fake_session, tmp_path):
    """/sftp/{id}/upload（浏览器 multipart 入口）同样走 .part + 改名"""
    s = fake_session(_FakeSession())
    src = tmp_path / "m.bin"
    src.write_bytes(CONTENT)
    with src.open("rb") as fh:
        r = client.post(
            "/api/sftp/sess-up/upload",
            params={"remote_path": "/data/m.bin"},
            files={"file": ("m.bin", fh, "application/octet-stream")},
        )
    assert r.status_code == 200, r.text
    assert s.renames == [("/data/m.bin.part", "/data/m.bin")]
    assert s.files["/data/m.bin"] == CONTENT


@pytest.mark.asyncio()
async def test_append_stream_opens_binary_mode():
    """回归：续传必须以 "ab" 打开远端文件

    asyncssh 的 "a" 是文本模式（encoding 非 None），写 bytes 会在 encode 上抛
    AttributeError——实机上传续传踩过，这里锁死打开模式。
    """
    from ssh_web_tool.sessions import SSHSession

    seen: list[str] = []

    class _FakeFile:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def write(self, data):
            seen.append(type(data).__name__)

    class _FakeSftp:
        def open(self, path, mode):
            seen.append(mode)
            return _FakeFile()

    s = SSHSession("s-a", "10.0.0.1", 22, "root")

    async def _get_sftp():
        return _FakeSftp()

    async def _chunks():
        yield b"xy"

    s.get_sftp = _get_sftp  # type: ignore[method-assign]
    sent = await s.write_file_stream("/a.part", _chunks(), append=True)
    assert seen[0] == "ab"
    assert sent == 2


@pytest.mark.asyncio()
async def test_rename_falls_back_when_posix_rename_unsupported():
    """老服务端没有 posix-rename 扩展：退回先删目标再普通 rename"""
    from ssh_web_tool.sessions import SSHSession

    calls: list[str] = []

    class _FakeSftp:
        async def posix_rename(self, old, new):
            calls.append("posix")
            raise OSError("SSH_FX_OP_UNSUPPORTED")

        async def remove(self, path):
            calls.append(f"remove:{path}")

        async def rename(self, old, new):
            calls.append(f"rename:{old}->{new}")

    s = SSHSession("s-r", "10.0.0.1", 22, "root")

    async def _get_sftp():
        return _FakeSftp()

    s.get_sftp = _get_sftp  # type: ignore[method-assign]
    await s.rename_file("/a.part", "/a")
    assert calls == ["posix", "remove:/a", "rename:/a.part->/a"]
