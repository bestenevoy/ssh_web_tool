"""传输列表扩展 API 测试：host 字段透传 + reveal 打开本机目录"""

import pytest

from ssh_web_tool.transfers import transfer_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """注册表是进程级全局，测试前后清空避免互相污染"""
    transfer_registry._tasks.clear()
    yield
    transfer_registry._tasks.clear()


def test_transfers_endpoint_carries_host(client):
    transfer_registry.create("s1", "upload", "a.txt", "本机上传", "/tmp/a.txt", host="root@10.0.0.1")
    r = client.get("/api/sftp/transfers")
    assert r.status_code == 200
    ts = r.json()["transfers"]
    assert len(ts) == 1 and ts[0]["host"] == "root@10.0.0.1"


def test_reveal_unknown_task_404(client):
    r = client.post("/api/sftp/transfers/nope/reveal")
    assert r.status_code == 404


def test_reveal_rejects_upload(client):
    t = transfer_registry.create("s1", "upload", "a.txt", "本机上传", "/remote/a.txt")
    r = client.post(f"/api/sftp/transfers/{t.id}/reveal")
    assert r.status_code == 400


def test_reveal_rejects_browser_download(client):
    """浏览器下载没有本机落盘路径（dst 是占位符），不可揭示"""
    t = transfer_registry.create("s1", "download", "a.txt", "/remote/a.txt", "浏览器下载")
    t.finish("done")
    r = client.post(f"/api/sftp/transfers/{t.id}/reveal")
    assert r.status_code == 400


def test_reveal_missing_file_404(client):
    t = transfer_registry.create("s1", "download", "gone.txt", "/r/gone.txt", "/no/such/gone.txt")
    t.finish("done")
    r = client.post(f"/api/sftp/transfers/{t.id}/reveal")
    assert r.status_code == 404


def test_reveal_success_uses_task_path(client, tmp_path, monkeypatch):
    """成功路径：只 reveal 任务自带 dst，且以系统文件管理器选中该文件"""
    import ssh_web_tool.api.routers.sftp as sftp_router

    calls = []
    monkeypatch.setattr(sftp_router.subprocess, "Popen", lambda args, **kw: calls.append(args))
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    t = transfer_registry.create("s1", "download", "ok.txt", "/r/ok.txt", str(f))
    t.finish("done")

    r = client.post(f"/api/sftp/transfers/{t.id}/reveal")
    assert r.status_code == 200
    assert r.json()["path"] == str(f)
    assert len(calls) == 1 and str(f) in calls[0][-1]
