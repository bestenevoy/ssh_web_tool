"""SFTP 下载到本机：先写 .part 再原子改名（崩溃/失败不会留下"看起来完整"的截断文件）"""

import pytest

CHUNKS = [b"AAAA", b"BBBB", b"CCCC"]
CONTENT = b"".join(CHUNKS)
MTIME = 1700000000


class _FakeSession:
    """内存远端文件：read_file_stream 按块吐出内容，可注入中途失败"""

    def __init__(self, content: bytes = CONTENT, fail_after: int | None = None):
        self.is_connected = True
        self.content = content
        self.fail_after = fail_after  # 吐出第 N 块后抛错（模拟传输中断）
        self.offsets: list[int] = []  # 每次流式读的起始位置（续传断言用）

    def is_local(self):
        return False

    async def is_dir(self, path: str) -> bool:
        return False  # 单文件下载路径（目录递归另有测试）

    async def stat_file(self, path: str) -> int:
        return len(self.content)

    async def stat_info(self, path: str) -> tuple[int, int]:
        return (len(self.content), MTIME)

    async def read_file_stream(self, path: str, chunk_size: int = 4, offset: int = 0):
        self.offsets.append(offset)
        rest = self.content[offset:]
        for i in range(0, len(rest), chunk_size):
            if self.fail_after is not None and i // chunk_size >= self.fail_after:
                raise RuntimeError("远端通道中断")
            yield rest[i : i + chunk_size]


@pytest.fixture()
def fake_session(fake_sessions):
    def _install(session: _FakeSession) -> _FakeSession:
        fake_sessions.sessions["sess-dl"] = session
        return session

    return _install


def _download(client, tmp_path):
    return client.post(
        "/api/sftp/sess-dl/download-to",
        json={"remote_path": "/data/payload.bin", "local_dir": str(tmp_path)},
    )


def test_download_writes_final_file_and_leaves_no_part(client, fake_session, tmp_path):
    fake_session(_FakeSession())
    r = _download(client, tmp_path)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "downloaded"
    dest = tmp_path / "payload.bin"
    assert dest.read_bytes() == CONTENT
    assert not (tmp_path / "payload.bin.part").exists()
    assert not (tmp_path / "payload.bin.part.meta").exists()  # 成功后指纹文件随即清掉


def test_download_failure_keeps_preexisting_file_and_retains_part(client, fake_session, tmp_path):
    """中途失败：目标位置原有的同名文件不受牵连，.part + 指纹留着供下次续传"""
    dest = tmp_path / "payload.bin"
    dest.write_bytes(b"OLD-GOOD-CONTENT")
    s = fake_session(_FakeSession(fail_after=1))
    r = _download(client, tmp_path)
    assert r.status_code == 500
    assert dest.read_bytes() == b"OLD-GOOD-CONTENT"
    assert (tmp_path / "payload.bin.part").read_bytes() == b"AAAA"
    assert (tmp_path / "payload.bin.part.meta").read_text(encoding="utf-8") == f"{len(CONTENT)}:{MTIME}"
    assert s.offsets == [0]
