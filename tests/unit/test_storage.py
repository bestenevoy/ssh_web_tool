# -*- coding: utf-8 -*-
"""storage 单元测试：主机/分组/快捷指令 CRUD、原子写（回归 Bug4）、排序、复制"""
import json
from pathlib import Path

from ssh_web_tool.storage import Storage


def make_storage(tmp_path):
    """每个测试独立 storage 实例（临时 data.json）"""
    return Storage(data_file=str(tmp_path / "data.json"))


# ---------- 主机 CRUD ----------

def test_host_crud(tmp_path):
    s = make_storage(tmp_path)
    h = s.add_host({"name": "web1", "host": "10.0.0.1", "username": "root"})
    assert h["id"] and h["name"] == "web1"

    got = s.get_host(h["id"])
    assert got["host"] == "10.0.0.1"

    assert s.update_host(h["id"], {"name": "web1-updated"})
    assert s.get_host(h["id"])["name"] == "web1-updated"

    assert s.delete_host(h["id"])
    assert s.get_host(h["id"]) is None


def test_host_duplicate(tmp_path):
    s = make_storage(tmp_path)
    h = s.add_host({"name": "web1", "host": "10.0.0.1", "username": "root", "group": "prod"})
    dup = s.duplicate_host(h["id"])
    assert dup["id"] != h["id"]
    assert dup["name"] == "web1 副本"
    assert dup["host"] == "10.0.0.1"  # 内容一致
    assert len(s.list_hosts()) == 2


def test_host_reorder(tmp_path):
    s = make_storage(tmp_path)
    ids = [s.add_host({"name": f"h{i}", "host": f"10.0.0.{i}", "username": "root"})["id"] for i in range(3)]
    assert s.reorder_hosts([ids[2], ids[0], ids[1]])
    order = [h["id"] for h in s.list_hosts()]
    assert order == [ids[2], ids[0], ids[1]]


def test_group_duplicate_copies_hosts(tmp_path):
    s = make_storage(tmp_path)
    s.add_host({"name": "a", "host": "10.0.0.1", "username": "root", "group": "prod"})
    s.add_host({"name": "b", "host": "10.0.0.2", "username": "root", "group": "prod"})
    dup = s.duplicate_group("prod")
    assert dup["name"] == "prod 副本"
    assert len(dup["copied_hosts"]) == 2
    # 副本主机 id 必须全新
    origin_ids = {h["id"] for h in s.list_hosts() if h["group"] == "prod"}
    dup_ids = {h["id"] for h in dup["copied_hosts"]}
    assert not (origin_ids & dup_ids)


# ---------- 快捷指令 ----------

def test_quick_command_crud(tmp_path):
    s = make_storage(tmp_path)
    default_count = len(s.list_quick_commands())  # 预置默认指令（如系统信息等）
    q = s.add_quick_command("重启", "systemctl restart nginx")
    assert q["id"]
    assert len(s.list_quick_commands()) == default_count + 1

    s.update_quick_command(q["id"], "重启2", "systemctl restart nginx2")
    by_id = {qc["id"]: qc for qc in s.list_quick_commands()}
    assert by_id[q["id"]]["name"] == "重启2"

    assert s.delete_quick_command(q["id"])
    assert len(s.list_quick_commands()) == default_count


def test_quick_command_has_no_param_hint(tmp_path):
    """回归：前端类型已删除 param_hint 字段，存储层不应再写它（新数据）"""
    s = make_storage(tmp_path)
    q = s.add_quick_command("测试", "echo hi")
    assert "param_hint" not in q
    # 更新后同样不写
    s.update_quick_command(q["id"], "测试2", "echo hi2")
    updated = next(qc for qc in s.list_quick_commands() if qc["id"] == q["id"])
    assert "param_hint" not in updated


# ---------- 原子写（回归 Bug4） ----------

def test_save_is_atomic_no_tmp_residue(tmp_path):
    s = make_storage(tmp_path)
    s.add_host({"name": "web1", "host": "10.0.0.1", "username": "root"})
    data_file = Path(s.data_file)
    assert data_file.exists()
    # 原子写后不应残留 .tmp 文件
    assert not Path(str(data_file) + ".tmp").exists()
    # 文件必须是合法 JSON，且能完整读回
    raw = json.loads(data_file.read_text(encoding="utf-8"))
    assert raw["hosts"][0]["name"] == "web1"


def test_save_preserves_reload_consistency(tmp_path):
    s = make_storage(tmp_path)
    s.add_host({"name": "web1", "host": "10.0.0.1", "username": "root"})
    # 重新从磁盘加载，数据一致
    s2 = Storage(data_file=s.data_file)
    assert len(s2.list_hosts()) == 1
    assert s2.list_hosts()[0]["host"] == "10.0.0.1"


def test_save_handles_corrupt_file_gracefully(tmp_path):
    """data.json 损坏时加载不应崩溃（回退空数据）"""
    data_file = tmp_path / "data.json"
    data_file.write_text("{ corrupted json !!!", encoding="utf-8")
    s = Storage(data_file=str(data_file))
    assert s.list_hosts() == []
