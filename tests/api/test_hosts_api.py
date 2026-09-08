# -*- coding: utf-8 -*-
"""主机/分组/快捷指令 API 测试（原 test_api_smoke.py 用例的 pytest 化 + 扩充）"""


def _hosts(client):
    """GET /api/hosts 返回 {hosts, groups, host_types}，取 hosts 列表"""
    return client.get("/api/hosts").json()["hosts"]


def test_host_crud_via_api(client):
    r = client.post("/api/hosts", json={"name": "web1", "host": "10.0.0.1", "username": "root", "group": "prod"})
    assert r.status_code == 200, r.text
    hid = r.json()["id"]

    assert any(h["id"] == hid and h["name"] == "web1" for h in _hosts(client))

    r = client.delete(f"/api/hosts/{hid}")
    assert r.status_code == 200
    assert not any(h["id"] == hid for h in _hosts(client))


def test_host_duplicate_via_api(client):
    r = client.post("/api/hosts", json={"name": "web1", "host": "10.0.0.1", "username": "root"})
    hid = r.json()["id"]
    r = client.post(f"/api/hosts/{hid}/duplicate")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "web1 副本"


def test_group_duplicate_copies_hosts_via_api(client):
    client.post("/api/hosts", json={"name": "a", "host": "10.0.0.1", "username": "root", "group": "prod"})
    client.post("/api/hosts", json={"name": "b", "host": "10.0.0.2", "username": "root", "group": "prod"})
    r = client.post("/api/groups/prod/duplicate")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "prod 副本"
    assert len(r.json()["copied_hosts"]) == 2


def test_hosts_reorder_via_api(client):
    h1 = client.post("/api/hosts", json={"name": "a", "host": "10.0.0.1", "username": "root"}).json()["id"]
    h2 = client.post("/api/hosts", json={"name": "b", "host": "10.0.0.2", "username": "root"}).json()["id"]
    r = client.post("/api/hosts/reorder", json={"ids": [h2, h1]})
    assert r.status_code == 200
    order = [h["id"] for h in _hosts(client)]
    assert order[:2] == [h2, h1]


def test_host_not_found_404(client):
    assert client.delete("/api/hosts/no-such-id").status_code == 404
    assert client.post("/api/hosts/no-such-id/duplicate").status_code == 404


def test_quick_command_crud_via_api(client):
    r = client.post("/api/quick-commands", json={"name": "重启", "command": "systemctl restart nginx"})
    assert r.status_code == 200, r.text
    qid = r.json()["id"]

    cmds = client.get("/api/quick-commands").json()["commands"]
    assert any(q["id"] == qid for q in cmds)

    assert client.delete(f"/api/quick-commands/{qid}").status_code == 200
    assert not any(q["id"] == qid for q in client.get("/api/quick-commands").json()["commands"])


def test_hosts_listing_connection_fields(client):
    """主机列表需含连接信息字段（前端自动登录/预操作依赖）"""
    client.post("/api/hosts", json={"name": "web1", "host": "10.0.0.1", "username": "root", "password": "secret"})
    h = next(x for x in _hosts(client) if x["name"] == "web1")
    for field in ("host", "port", "username", "password", "group"):
        assert field in h, f"缺少字段 {field}"
