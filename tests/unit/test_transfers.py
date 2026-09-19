"""TransferRegistry：进度上报 / 取消标志 / 历史裁剪"""

from ssh_web_tool.transfers import MAX_HISTORY, TransferCanceled, TransferRegistry


def _make(reg: TransferRegistry):
    return reg.create("s1", "upload", "a.txt", "本机上传", "/tmp/a.txt", total=100)


def test_create_and_progress():
    reg = TransferRegistry()
    t = reg.create("s1", "upload", "a.txt", "本机上传", "/tmp/a.txt", host="root@10.0.0.1", total=100)
    t.add_done(40)
    assert t.done == 40 and t.status == "running"
    t.finish("done")
    d = t.to_dict()
    assert d["status"] == "done" and d["total"] == 100 and d["finished_at"] > 0
    assert d["host"] == "root@10.0.0.1"  # 传输列表显示目标/来源主机


def test_cancel_sets_flag_and_raises_on_next_chunk():
    reg = TransferRegistry()
    t = _make(reg)
    ok, _ = reg.cancel(t.id)
    assert ok and t.canceled
    try:
        t.add_done(10)
        raise AssertionError("应抛 TransferCanceled")
    except TransferCanceled:
        pass
    t.finish("canceled", "已取消")
    # 已结束任务不可再取消
    ok2, reason = reg.cancel(t.id)
    assert not ok2 and reason == "任务已结束"


def test_cancel_unknown():
    reg = TransferRegistry()
    ok, reason = reg.cancel("nope")
    assert not ok and reason == "任务不存在"


def test_list_order_and_prune():
    reg = TransferRegistry()
    running = _make(reg)
    for i in range(MAX_HISTORY + 5):
        t = reg.create("s1", "download", f"f{i}", "/r", "l")
        t.finish("done")
    tasks = reg.list("s1")
    assert tasks[0] is running  # 活动在前
    finished = [t for t in tasks if t.status != "running"]
    assert len(finished) == MAX_HISTORY  # 历史只留最近 MAX_HISTORY 条


def test_list_filter_by_session():
    reg = TransferRegistry()
    _make(reg)
    reg.create("s2", "upload", "b", "x", "y")
    assert len(reg.list("s1")) == 1
    assert len(reg.list()) == 2
