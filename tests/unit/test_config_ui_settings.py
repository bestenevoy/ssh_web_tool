"""前端 UI 设置持久化（config.json 的 ui_settings 段）单测

覆盖：默认值、逐键校验回退、部分合并（缺失键保留默认）、save/load roundtrip。
"""

import json

import pytest

from ssh_web_tool import config as cfg_mod


@pytest.fixture
def tmp_app_dir(monkeypatch, tmp_path):
    """把配置目录隔离到临时目录，避免污染 ~/.ai4one/sshtool/config.json"""
    monkeypatch.setattr(cfg_mod, "get_app_dir", lambda: tmp_path)
    return tmp_path


def test_default_ui_settings(tmp_app_dir):
    """无配置文件时 ui_settings 为全默认值"""
    got = cfg_mod.get_ui_settings()
    assert got == cfg_mod.UI_SETTINGS_DEFAULTS
    assert got["theme"] == "light"
    assert got["block_auto_fold"] is False
    assert got["block_split_mode"] == "prompt"
    assert got["custom_prompt_patterns"] == []


def test_validate_ui_setting_each_key():
    """逐键校验：合法值原样返回，非法值返回 None"""
    v = cfg_mod._validate_ui_setting
    assert v("theme", "dark") == "dark"
    assert v("theme", "blue") is None
    assert v("font_family", "JetBrains Mono") == "JetBrains Mono"
    assert v("font_family", "   ") is None
    assert v("font_size", 16) == 16
    assert v("font_size", 99) is None
    assert v("font_size", True) is None  # bool 不是合法 int
    assert v("block_bar", False) is False
    assert v("block_bar", "yes") is None
    assert v("block_auto_fold", True) is True
    assert v("block_max_lines", 50) == 50
    assert v("block_max_lines", 33) is None  # 不在可选档位
    assert v("unknown_key", 1) is None  # 未知键一律忽略


def test_validate_block_split_mode():
    """切块方式：只认 enter / prompt"""
    v = cfg_mod._validate_ui_setting
    assert v("block_split_mode", "prompt") == "prompt"
    assert v("block_split_mode", "enter") == "enter"
    assert v("block_split_mode", "auto") is None
    assert v("block_split_mode", 123) is None


def test_validate_ui_font_scale():
    """界面字号倍率：有限数字 0.8-1.6，保留两位小数；非法回退 None"""
    v = cfg_mod._validate_ui_setting
    assert v("ui_font_scale", 1.0) == 1.0
    assert v("ui_font_scale", 1.15) == 1.15
    assert v("ui_font_scale", 1.2) == 1.2
    assert v("ui_font_scale", 0.8) == 0.8  # 下界
    assert v("ui_font_scale", 1.6) == 1.6  # 上界
    assert v("ui_font_scale", 0.79) is None
    assert v("ui_font_scale", 1.61) is None
    assert v("ui_font_scale", True) is None  # bool 不是合法数字
    assert v("ui_font_scale", "1.2") is None
    assert v("ui_font_scale", None) is None


def test_validate_custom_prompt_patterns_filters_items():
    """自定义正则列表：逐项过滤非法条目（非整键回退），截断到上限"""
    v = cfg_mod._validate_ui_setting
    # 合法条目保留，空串/非字符串剔除
    got = v("custom_prompt_patterns", ["mini>", "   ", 42, "db \\d+ =>"])
    assert got == ["mini>", "db \\d+ =>"]
    # 单条超长剔除
    long_item = "a" * 201
    assert v("custom_prompt_patterns", [long_item, "ok>"]) == ["ok>"]
    # 超过 20 条截断
    many = [f"p{i}>" for i in range(25)]
    filtered = v("custom_prompt_patterns", many)
    assert isinstance(filtered, list) and len(filtered) == 20
    # 整体不是列表 → 回退默认（None）
    assert v("custom_prompt_patterns", "mini>") is None
    assert v("custom_prompt_patterns", {"a": 1}) is None


def test_load_config_merges_ui_settings_per_key(tmp_app_dir):
    """ui_settings 只写部分键时其余键保留默认值（子字典逐键合并，不整体替换）"""
    (tmp_app_dir / "config.json").write_text(
        json.dumps({"ui_settings": {"theme": "dark", "font_size": 18}}), encoding="utf-8"
    )
    cfg = cfg_mod.load_config()
    assert cfg["ui_settings"]["theme"] == "dark"
    assert cfg["ui_settings"]["font_size"] == 18
    # 未写的键回退默认
    assert cfg["ui_settings"]["block_bar"] is True
    assert cfg["ui_settings"]["block_auto_fold"] is False
    assert cfg["ui_settings"]["block_max_lines"] == 30


def test_get_ui_settings_invalid_values_fall_back(tmp_app_dir):
    """非法值在校验层回退默认（load_config 保留原始值，get_ui_settings 负责校验）"""
    (tmp_app_dir / "config.json").write_text(
        json.dumps({"ui_settings": {"theme": "solarized", "font_size": 999}}), encoding="utf-8"
    )
    got = cfg_mod.get_ui_settings()
    assert got["theme"] == "light"
    assert got["font_size"] == 13


def test_ui_settings_roundtrip(tmp_app_dir):
    """save_config 写入后 load_config + get_ui_settings 能读到同一值"""
    cfg = cfg_mod.load_config()
    cfg["ui_settings"]["theme"] = "dark"
    cfg["ui_settings"]["font_size"] = 20
    assert cfg_mod.save_config(cfg) is True
    again = cfg_mod.get_ui_settings()
    assert again["theme"] == "dark"
    assert again["font_size"] == 20


def test_ui_settings_non_dict_ignored(tmp_app_dir):
    """ui_settings 不是对象时整体忽略（回退全默认）"""
    (tmp_app_dir / "config.json").write_text(json.dumps({"ui_settings": "dark"}), encoding="utf-8")
    assert cfg_mod.get_ui_settings() == cfg_mod.UI_SETTINGS_DEFAULTS


def test_new_block_keys_partial_config(tmp_app_dir):
    """旧配置无 block_split_mode/custom_prompt_patterns 键时回退默认；写了部分键时逐键合并"""
    (tmp_app_dir / "config.json").write_text(json.dumps({"ui_settings": {"theme": "dark"}}), encoding="utf-8")
    got = cfg_mod.get_ui_settings()
    assert got["theme"] == "dark"
    assert got["block_split_mode"] == "prompt"  # 回退默认
    assert got["custom_prompt_patterns"] == []
    # 写入部分新键
    (tmp_app_dir / "config.json").write_text(
        json.dumps({"ui_settings": {"block_split_mode": "enter", "custom_prompt_patterns": ["mini>"]}}),
        encoding="utf-8",
    )
    got = cfg_mod.get_ui_settings()
    assert got["block_split_mode"] == "enter"
    assert got["custom_prompt_patterns"] == ["mini>"]


# ============ 关键字高亮规则（highlight_rules，rssh 同款） ============


def _rule(keyword="ERROR", name="错误", color="#FF6B6B", enabled: object = True, case: object = False):
    # enabled/case 用 object：测试会故意喂非法值（非 bool）
    return {"keyword": keyword, "name": name, "color": color, "enabled": enabled, "is_case_sensitive": case}


def test_default_highlight_rules(tmp_app_dir):
    """默认 9 条规则（rssh 同款），字段齐全"""
    got = cfg_mod.get_ui_settings()
    rules = got["highlight_rules"]
    assert isinstance(rules, list) and len(rules) == 9
    assert all(set(r) == {"keyword", "name", "color", "enabled", "is_case_sensitive"} for r in rules)


def test_validate_highlight_rules_filters_and_dedups():
    """逐项过滤非法条目 + keyword 去重（keyword 是规则身份键）"""
    v = cfg_mod._validate_ui_setting
    got = v(
        "highlight_rules",
        [_rule(), _rule(), _rule(keyword="", name="空"), _rule(keyword="X", name=""), _rule(color="red"), "junk", 42],
    )
    assert got == [_rule()]
    # 相同 keyword 只保留首条
    assert v("highlight_rules", [_rule(name="a"), _rule(name="b")]) == [_rule(name="a")]


def test_validate_highlight_rules_limits():
    """条数/keyword 长度/名称长度上限与颜色格式"""
    v = cfg_mod._validate_ui_setting
    many = [_rule(keyword=f"k{i}", name=f"n{i}") for i in range(60)]
    filtered = v("highlight_rules", many)
    assert isinstance(filtered, list) and len(filtered) == 50
    assert v("highlight_rules", [_rule(keyword="a" * 201)]) == []
    assert v("highlight_rules", [_rule(name="x" * 101)]) == []
    assert v("highlight_rules", [_rule(color="#12345")]) == []  # 5 位 hex 非法
    assert v("highlight_rules", [_rule(color="red")]) == []  # 非 hex 非法
    assert v("highlight_rules", [_rule(color="#abcdEf")]) == [_rule(color="#ABCDEF")]  # 规范化为大写


def test_validate_highlight_rules_bool_fields_required():
    """enabled / is_case_sensitive 必须是 bool"""
    v = cfg_mod._validate_ui_setting
    assert v("highlight_rules", [_rule(enabled="yes")]) == []
    assert v("highlight_rules", [_rule(case=1)]) == []


# ============ 日志记录（log_record_dir / log_record_no_ask） ============


def test_log_record_keys_defaults(tmp_app_dir):
    """旧配置无日志记录键时回退默认：目录空串（= 程序默认 logs 目录）、不再询问关闭"""
    (tmp_app_dir / "config.json").write_text(json.dumps({"ui_settings": {"theme": "dark"}}), encoding="utf-8")
    got = cfg_mod.get_ui_settings()
    assert got["log_record_dir"] == ""
    assert got["log_record_no_ask"] is False


def test_validate_log_record_dir():
    """目录字符串：trim 规范化；空串合法（= 默认目录）；非字符串回退 None"""
    v = cfg_mod._validate_ui_setting
    assert v("log_record_dir", "  D:\\logs  ") == "D:\\logs"
    assert v("log_record_dir", "") == ""
    assert v("log_record_dir", 42) is None
    assert v("log_record_dir", None) is None


def test_validate_log_record_no_ask():
    """不再询问必须是 bool"""
    v = cfg_mod._validate_ui_setting
    assert v("log_record_no_ask", True) is True
    assert v("log_record_no_ask", False) is False
    assert v("log_record_no_ask", 1) is None
    assert v("log_record_no_ask", "yes") is None


def test_validate_highlight_rules_not_list_falls_back():
    """整体不是列表 → 回退默认（None），与 custom_prompt_patterns 同语义"""
    assert cfg_mod._validate_ui_setting("highlight_rules", "ERROR") is None
    assert cfg_mod._validate_ui_setting("highlight_rules", {"keyword": "x"}) is None


def test_highlight_rules_roundtrip(tmp_app_dir):
    """save_config 写入后 get_ui_settings 能读到同一值"""
    rules = [_rule(keyword="WARN", color="#FFD060")]
    cfg = cfg_mod.load_config()
    cfg["ui_settings"]["highlight_rules"] = rules
    assert cfg_mod.save_config(cfg) is True
    assert cfg_mod.get_ui_settings()["highlight_rules"] == rules


def test_highlight_rules_partial_config_falls_back_default(tmp_app_dir):
    """旧配置无 highlight_rules 键时回退默认 9 条"""
    (tmp_app_dir / "config.json").write_text(json.dumps({"ui_settings": {"theme": "dark"}}), encoding="utf-8")
    got = cfg_mod.get_ui_settings()
    assert got["theme"] == "dark"
    assert len(got["highlight_rules"]) == 9
