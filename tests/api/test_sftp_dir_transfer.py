"""SFTP 目录递归传输：远端目录树下载到本机镜像 / 本机目录树上传到远端镜像

不发起真实 SSH：假会话在内存里维护一棵远端目录树。
"""

import pytest

TREE = {
    "/data/proj": [
        {"name": "a.txt", "type": "file", "size": 4},
        {"name": "sub", "type": "dir", "size": 0},
        {"name": "empty", "type": "dir", "size": 0},
    ],
    "/data/proj/sub": [
        {"name": "b.bin", "type": "file", "size": 8},
        {"name": "deep", "type": "dir", "size": 0},
    ],
    "/data/proj/sub/deep": [{"name": "c.log", "type": "file", "size": 4}],
}
CONTENTS = {
    "/data/proj/a.txt": b"AAAA",
    "/data/proj/sub/b.bin": b"BBBBBBBB",
    "/data/proj/sub/deep/c.log": b"CCCC",
}


class _FakeSession:
    """内存远端：list_directory 按 TREE 返回，读写按 CONTENTS/files 字典"""

    def __init__(self, tree=None, contents=None):
        self.is_connected = True
        self.tree = TREE if tree is None else tree
        self.files: dict[str, bytes] = dict(CONTENTS if contents is None else contents)
        self.dirs: set[str] = set(self.tree)
        self.renames: list[tuple[str, str]] = []

    def is_local(self):
        return False

    async def is_dir(self, path: str) -> bool:
        return path in self.dirs

    async def list_directory(self, path: str = "/"):
        return list(self.tree.get(path, []))

    async def stat_file(self, path: str) -> int:
        return len(self.files[path]) if path in self.files else -1

    async def stat_info(self, path: str) -> tuple[int, int]:
        return (len(self.files[path]), 1700000000) if path in self.files else (-1, 0)

    async def write_file(self, path: str, content) -> bool:
        self.files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return True

    async def read_file(self, path: str, max_size: int = 10 * 1024 * 1024) -> str:
        return self.files[path].decode("utf-8")

    async def read_file_stream(self, path: str, chunk_size: int = 4096, offset: int = 0):
        data = self.files[path][offset:]
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def write_file_stream(self, path: str, chunk_iter, append: bool = False):
        new = b"".join([c async for c in chunk_iter])
        self.files[path] = (self.files.get(path, b"") if append else b"") + new
        return len(new)

    async def rename_file(self, old: str, new: str) -> None:
        self.renames.append((old, new))
        self.files[new] = self.files.pop(old)

    async def make_directory(self, path: str) -> None:
        self.dirs.add(path)
        self.tree.setdefault(path, [])

    async def delete_file(self, path: str) -> bool:
        self.files.pop(path, None)
        return True


@pytest.fixture()
def fake_session(fake_sessions):
    def _install(session: _FakeSession) -> _FakeSession:
        fake_sessions.sessions["sess-dir"] = session
        return session

    return _install


def test_download_dir_mirrors_tree(client, fake_session, tmp_path):
    fake_session(_FakeSession())
    r = client.post(
        "/api/sftp/sess-dir/download-to",
        json={"remote_path": "/data/proj", "local_dir": str(tmp_path)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "downloaded" and body["files"] == 3 and body["size"] == 16
    base = tmp_path / "proj"
    assert (base / "a.txt").read_bytes() == b"AAAA"
    assert (base / "sub" / "b.bin").read_bytes() == b"BBBBBBBB"
    assert (base / "sub" / "deep" / "c.log").read_bytes() == b"CCCC"
    assert (base / "empty").is_dir()  # 空目录也要镜像出来
    assert list(base.rglob("*.part")) == []  # 临时文件全部转正


def test_download_dir_skips_dot_entries(client, fake_session, tmp_path):
    """readdir 回吐的 . / .. 直接跳过，不当成条目搬运"""
    tree = {"/evil": [{"name": "..", "type": "dir", "size": 0}], "/evil/..": [{"name": "x", "type": "file", "size": 1}]}
    s = fake_session(_FakeSession(tree=tree, contents={}))
    s.files["/evil/x"] = b"Z"
    r = client.post(
        "/api/sftp/sess-dir/download-to",
        json={"remote_path": "/evil", "local_dir": str(tmp_path)},
    )
    assert r.status_code == 200, r.text
    assert not (tmp_path / "x").exists()
    assert (tmp_path / "evil").is_dir()


def test_download_dir_rejects_parent_escape(client, fake_session, tmp_path):
    """恶意服务端回吐带 ../ 的条目名也不能写到目标目录之外"""
    tree = {"/evil": [{"name": "../escaped.txt", "type": "file", "size": 1}]}
    s = fake_session(_FakeSession(tree=tree, contents={}))
    s.files["/evil/../escaped.txt"] = b"Z"
    r = client.post(
        "/api/sftp/sess-dir/download-to",
        json={"remote_path": "/evil", "local_dir": str(tmp_path)},
    )
    assert r.status_code == 500
    assert "越界" in r.json()["detail"]
    assert not (tmp_path / "escaped.txt").exists()


def test_upload_dir_mirrors_tree(client, fake_session, tmp_path):
    s = fake_session(_FakeSession(tree={}, contents={}))
    src = tmp_path / "local"
    (src / "sub" / "deep").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "a.txt").write_bytes(b"AAAA")
    (src / "sub" / "b.bin").write_bytes(b"BBBBBBBB")
    (src / "sub" / "deep" / "c.log").write_bytes(b"CCCC")
    r = client.post(
        "/api/preop/upload",
        json={"session_id": "sess-dir", "source": str(src), "source_type": "path", "remote": "/up/local"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "uploaded" and body["files"] == 3 and body["size"] == 16
    assert s.files["/up/local/a.txt"] == b"AAAA"
    assert s.files["/up/local/sub/b.bin"] == b"BBBBBBBB"
    assert s.files["/up/local/sub/deep/c.log"] == b"CCCC"
    assert {"/up/local", "/up/local/sub", "/up/local/sub/deep", "/up/local/empty"} <= s.dirs
    # 每个文件都是 .part 转正，远端不留临时文件
    assert sorted(new for _, new in s.renames) == ["/up/local/a.txt", "/up/local/sub/b.bin", "/up/local/sub/deep/c.log"]
    assert all(not p.endswith(".part") for p in s.files)


def test_upload_dir_failure_reports_progress(client, fake_session, tmp_path, monkeypatch):
    """中途失败：错误信息带上已完成/总数，已建目录与已传文件保留"""
    s = fake_session(_FakeSession(tree={}, contents={}))
    src = tmp_path / "local"
    src.mkdir()
    (src / "a.txt").write_bytes(b"AAAA")
    (src / "b.txt").write_bytes(b"BBBB")

    calls = {"n": 0}
    real_write = s.write_file_stream

    async def flaky(path, chunk_iter, append=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("远端磁盘满")
        return await real_write(path, chunk_iter, append=append)

    monkeypatch.setattr(s, "write_file_stream", flaky)
    r = client.post(
        "/api/preop/upload",
        json={"session_id": "sess-dir", "source": str(src), "source_type": "path", "remote": "/up/local"},
    )
    assert r.status_code == 500
    assert "1/2" in r.json()["detail"]
    assert not any(p.endswith(".part") for p in s.files)  # 第二个文件在写入前就失败，没落下半成品
