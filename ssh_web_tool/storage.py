"""
持久化存储模块
- 主机配置、分组、快速指令、主机类型
- 用 JSON 文件存储，重启不丢失
"""
import json
import os
import sys
import time
import uuid
from typing import Dict, List, Optional


def get_data_dir() -> str:
    """获取数据文件目录
    - PyInstaller 打包后：保存在 EXE 所在目录
    - 其他情况：保存在当前工作目录（用户运行命令的目录）
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


# 默认主机类型（可自定义）
DEFAULT_HOST_TYPES = [
    {"key": "web", "label": "Web服务器", "color": "#4caf50"},
    {"key": "db", "label": "数据库", "color": "#2196f3"},
    {"key": "cache", "label": "缓存", "color": "#ff9800"},
    {"key": "prod", "label": "生产环境", "color": "#f44336"},
    {"key": "test", "label": "测试环境", "color": "#9c27b0"},
    {"key": "dev", "label": "开发环境", "color": "#607d8b"},
    {"key": "other", "label": "其他", "color": "#795548"},
]

# 默认快速指令
DEFAULT_QUICK_COMMANDS = [
    {"id": "qc_1", "name": "系统信息", "command": "uname -a && uptime", "type": "direct", "param_hint": "", "pre_ops": []},
    {"id": "qc_2", "name": "磁盘使用", "command": "df -h", "type": "direct", "param_hint": "", "pre_ops": []},
    {"id": "qc_3", "name": "内存使用", "command": "free -h", "type": "direct", "param_hint": "", "pre_ops": []},
    {"id": "qc_4", "name": "查看进程", "command": "ps aux --sort=-%mem | head -10", "type": "direct", "param_hint": "", "pre_ops": []},
    {"id": "qc_5", "name": "查看监听端口", "command": "netstat -tlnp 2>/dev/null || ss -tlnp", "type": "direct", "param_hint": "", "pre_ops": []},
    {"id": "qc_6", "name": "查看日志(最近20行)", "command": "tail -n 20 /var/log/messages 2>/dev/null || journalctl -n 20 --no-pager", "type": "direct", "param_hint": "", "pre_ops": []},
]


class Storage:
    """JSON 文件持久化存储"""

    def __init__(self, data_file: str = None):
        if data_file is None:
            data_file = os.path.join(get_data_dir(), "data.json")
        self.data_file = data_file
        self._data = self._load()

    def _load(self) -> dict:
        """加载数据文件"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 确保所有字段都存在
                data.setdefault("hosts", [])
                data.setdefault("groups", [])
                data.setdefault("quick_commands", DEFAULT_QUICK_COMMANDS.copy())
                data.setdefault("host_types", DEFAULT_HOST_TYPES.copy())
                data.setdefault("saved_terminals", [])  # 持久化的终端（会话ID复用）
                data.setdefault("command_history", {})  # 全局命令历史（跨终端，记录使用频次）
                return data
            except Exception as e:
                print(f"加载数据文件失败: {e}，使用默认数据")
        return {
            "hosts": [],
            "groups": [],
            "quick_commands": DEFAULT_QUICK_COMMANDS.copy(),
            "host_types": DEFAULT_HOST_TYPES.copy(),
            "saved_terminals": [],
            "command_history": {},
        }

    def _save(self):
        """保存数据到文件"""
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存数据文件失败: {e}")

    # ============ 主机管理 ============

    def list_hosts(self) -> List[dict]:
        """获取所有主机"""
        return self._data.get("hosts", [])

    def get_host(self, host_id: str) -> Optional[dict]:
        """根据ID获取主机"""
        for h in self._data.get("hosts", []):
            if h.get("id") == host_id:
                return h
        return None

    def add_host(self, host_data: dict) -> dict:
        """新增主机"""
        host = {
            "id": host_data.get("id") or str(uuid.uuid4())[:8],
            "name": host_data.get("name", ""),
            "host": host_data["host"],
            "port": host_data.get("port", 22),
            "username": host_data.get("username", "root"),
            "password": host_data.get("password", ""),
            "private_key": host_data.get("private_key", ""),
            "passphrase": host_data.get("passphrase", ""),
            "type": host_data.get("type", "other"),
            "group": host_data.get("group", ""),
            # 设备类型：linux（普通主机）/ storage（存储阵列）
            "device_type": host_data.get("device_type", "linux"),
            # 存储阵列管理页面配置
            "mgmt_port": host_data.get("mgmt_port", 8088),
            "mgmt_username": host_data.get("mgmt_username", ""),
            "mgmt_password": host_data.get("mgmt_password", ""),
            # Playwright 自动登录选择器配置（用户自行填写）
            "pw_username_selector": host_data.get("pw_username_selector", ""),
            "pw_password_selector": host_data.get("pw_password_selector", ""),
            "pw_login_btn_selector": host_data.get("pw_login_btn_selector", ""),
            "pw_old_password_selector": host_data.get("pw_old_password_selector", ""),
            "pw_new_password_selector": host_data.get("pw_new_password_selector", ""),
            "pw_confirm_password_selector": host_data.get("pw_confirm_password_selector", ""),
            "pw_confirm_btn_selector": host_data.get("pw_confirm_btn_selector", ""),
            "pw_success_selector": host_data.get("pw_success_selector", ""),
            "pw_headless": host_data.get("pw_headless", False),
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._data["hosts"].append(host)
        # 自动添加分组
        if host["group"] and host["group"] not in self._data["groups"]:
            self._data["groups"].append(host["group"])
        self._save()
        return host

    def update_host(self, host_id: str, host_data: dict) -> Optional[dict]:
        """更新主机"""
        for i, h in enumerate(self._data["hosts"]):
            if h["id"] == host_id:
                for key in ["name", "host", "port", "username", "password",
                            "private_key", "passphrase", "type", "group",
                            "device_type", "mgmt_port", "mgmt_username", "mgmt_password",
                            "pw_username_selector", "pw_password_selector", "pw_login_btn_selector",
                            "pw_old_password_selector", "pw_new_password_selector",
                            "pw_confirm_password_selector", "pw_confirm_btn_selector",
                            "pw_success_selector", "pw_headless"]:
                    if key in host_data:
                        h[key] = host_data[key]
                h["updated_at"] = time.time()
                # 自动添加分组
                if h["group"] and h["group"] not in self._data["groups"]:
                    self._data["groups"].append(h["group"])
                self._save()
                return h
        return None

    def delete_host(self, host_id: str) -> bool:
        """删除主机"""
        original_len = len(self._data["hosts"])
        self._data["hosts"] = [h for h in self._data["hosts"] if h["id"] != host_id]
        if len(self._data["hosts"]) < original_len:
            self._save()
            return True
        return False

    def duplicate_host(self, host_id: str) -> Optional[dict]:
        """复制主机（生成新 ID，名称加"副本"后缀）"""
        original = self.get_host(host_id)
        if not original:
            return None
        import copy
        new_host = copy.deepcopy(original)
        new_host["id"] = str(uuid.uuid4())[:8]
        new_host["name"] = f"{original.get('name', original['host'])} 副本"
        new_host["created_at"] = time.time()
        new_host["updated_at"] = time.time()
        self._data["hosts"].append(new_host)
        self._save()
        return new_host

    # ============ 分组管理 ============

    def list_groups(self) -> List[str]:
        """获取所有分组"""
        return self._data.get("groups", [])

    def reorder_groups(self, names: List[str]) -> bool:
        """按给定顺序重排分组（忽略不存在的名称，追加未列出的分组）"""
        existing = self._data.get("groups", [])
        seen = set()
        ordered = []
        for n in names:
            if n in existing and n not in seen:
                ordered.append(n)
                seen.add(n)
        for n in existing:
            if n not in seen:
                ordered.append(n)
        self._data["groups"] = ordered
        self._save()
        return True

    def reorder_hosts(self, ids: List[str]) -> bool:
        """按给定顺序重排主机（忽略不存在的 ID，追加未列出的主机）"""
        existing = self._data.get("hosts", [])
        by_id = {h["id"]: h for h in existing}
        seen = set()
        ordered = []
        for hid in ids:
            if hid in by_id and hid not in seen:
                ordered.append(by_id[hid])
                seen.add(hid)
        for h in existing:
            if h["id"] not in seen:
                ordered.append(h)
        self._data["hosts"] = ordered
        self._save()
        return True

    def add_group(self, name: str) -> bool:
        """新增分组"""
        if name and name not in self._data["groups"]:
            self._data["groups"].append(name)
            self._save()
            return True
        return False

    def delete_group(self, name: str) -> bool:
        """删除分组（组内主机移到未分组）"""
        if name in self._data["groups"]:
            self._data["groups"].remove(name)
            for h in self._data["hosts"]:
                if h.get("group") == name:
                    h["group"] = ""
            self._save()
            return True
        return False

    def rename_group(self, old_name: str, new_name: str) -> bool:
        """重命名分组（组内主机的分组名同步更新）"""
        if old_name not in self._data["groups"] or not new_name:
            return False
        if new_name in self._data["groups"]:
            return False
        idx = self._data["groups"].index(old_name)
        self._data["groups"][idx] = new_name
        for h in self._data["hosts"]:
            if h.get("group") == old_name:
                h["group"] = new_name
        self._save()
        return True

    def duplicate_group(self, name: str) -> Optional[dict]:
        """复制分组：复制分组名（加"副本"后缀），并把组内主机一并复制

        Returns:
            复制结果：{"name": 新分组名, "copied_hosts": 复制的主机列表}；
            原分组不存在返回 None
        """
        if name not in self._data["groups"]:
            return None
        import copy

        # 新分组名：xxx 副本（若已存在则追加序号）
        base = f"{name} 副本"
        new_name = base
        n = 2
        while new_name in self._data["groups"]:
            new_name = f"{base}{n}"
            n += 1
        self._data["groups"].append(new_name)

        # 复制组内主机（与 duplicate_host 相同的副本逻辑）
        copied_hosts = []
        for h in self._data["hosts"]:
            if h.get("group") != name:
                continue
            new_host = copy.deepcopy(h)
            new_host["id"] = str(uuid.uuid4())[:8]
            new_host["name"] = f"{h.get('name', h['host'])} 副本"
            new_host["group"] = new_name
            new_host["created_at"] = time.time()
            new_host["updated_at"] = time.time()
            self._data["hosts"].append(new_host)
            copied_hosts.append(new_host)
        self._save()
        return {"name": new_name, "copied_hosts": copied_hosts}

    # ============ 快速指令管理 ============

    def list_quick_commands(self) -> List[dict]:
        """获取所有快速指令（兼容旧数据：自动补全 type/param_hint/pre_ops 字段）"""
        commands = self._data.get("quick_commands", [])
        for qc in commands:
            qc.setdefault("type", "direct")
            qc.setdefault("param_hint", "")
            qc.setdefault("pre_ops", [])
        return commands

    def add_quick_command(self, name: str, command: str, description: str = "",
                          cmd_type: str = "direct", param_hint: str = "",
                          pre_ops: Optional[list] = None) -> dict:
        """新增快速指令

        Args:
            cmd_type: "direct" 直接执行 / "param" 带参数（执行前弹输入框，替换命令中的 {args} 占位符）
            param_hint: 带参数类型的参数说明（如"输入文件路径"）
            pre_ops: 预操作列表 [{"type": "upload"|"chmod"|"env", ...}]
        """
        qc = {
            "id": "qc_" + str(uuid.uuid4())[:8],
            "name": name,
            "command": command,
            "description": description,
            "type": cmd_type if cmd_type in ("direct", "param") else "direct",
            "param_hint": param_hint or "",
            "pre_ops": pre_ops or [],
        }
        self._data["quick_commands"].append(qc)
        self._save()
        return qc

    def update_quick_command(self, qc_id: str, name: str, command: str, description: str = "",
                             cmd_type: str = "direct", param_hint: str = "",
                             pre_ops: Optional[list] = None) -> Optional[dict]:
        """更新快速指令"""
        for qc in self._data["quick_commands"]:
            if qc["id"] == qc_id:
                qc["name"] = name
                qc["command"] = command
                qc["description"] = description
                qc["type"] = cmd_type if cmd_type in ("direct", "param") else "direct"
                qc["param_hint"] = param_hint or ""
                qc["pre_ops"] = pre_ops or []
                self._save()
                return qc
        return None

    def delete_quick_command(self, qc_id: str) -> bool:
        """删除快速指令"""
        original_len = len(self._data["quick_commands"])
        self._data["quick_commands"] = [q for q in self._data["quick_commands"] if q["id"] != qc_id]
        if len(self._data["quick_commands"]) < original_len:
            self._save()
            return True
        return False

    # ============ 主机类型管理 ============

    def list_host_types(self) -> List[dict]:
        """获取所有主机类型"""
        return self._data.get("host_types", [])

    def add_host_type(self, key: str, label: str, color: str) -> Optional[dict]:
        """新增主机类型"""
        if not key:
            return None
        # 检查是否已存在
        for t in self._data["host_types"]:
            if t["key"] == key:
                t["label"] = label
                t["color"] = color
                self._save()
                return t
        ht = {"key": key, "label": label, "color": color}
        self._data["host_types"].append(ht)
        self._save()
        return ht

    def delete_host_type(self, key: str) -> bool:
        """删除主机类型"""
        original_len = len(self._data["host_types"])
        self._data["host_types"] = [t for t in self._data["host_types"] if t["key"] != key]
        if len(self._data["host_types"]) < original_len:
            # 将使用该类型的主机改为 other
            for h in self._data["hosts"]:
                if h.get("type") == key:
                    h["type"] = "other"
            self._save()
            return True
        return False

    # ============ 持久化终端管理（会话ID复用） ============

    def list_saved_terminals(self) -> List[dict]:
        """获取所有持久化的终端"""
        return self._data.get("saved_terminals", [])

    def get_saved_terminal(self, session_id: str) -> Optional[dict]:
        """根据会话ID获取持久化终端"""
        for t in self._data.get("saved_terminals", []):
            if t.get("session_id") == session_id:
                return t
        return None

    def save_terminal(self, session_id: str, host_id: str, terminal_name: str = "") -> dict:
        """保存终端信息（会话ID复用，重启后可恢复）"""
        # 检查是否已存在
        for i, t in enumerate(self._data.get("saved_terminals", [])):
            if t.get("session_id") == session_id:
                t["host_id"] = host_id
                t["terminal_name"] = terminal_name or t.get("terminal_name", "")
                t["last_used"] = time.time()
                self._save()
                return t
        # 新增
        terminal = {
            "session_id": session_id,
            "host_id": host_id,
            "terminal_name": terminal_name or f"终端{len(self._data.get('saved_terminals', [])) + 1}",
            "created_at": time.time(),
            "last_used": time.time(),
        }
        self._data.setdefault("saved_terminals", []).append(terminal)
        self._save()
        return terminal

    def delete_saved_terminal(self, session_id: str) -> bool:
        """删除持久化终端"""
        original_len = len(self._data.get("saved_terminals", []))
        self._data["saved_terminals"] = [t for t in self._data.get("saved_terminals", []) if t.get("session_id") != session_id]
        if len(self._data["saved_terminals"]) < original_len:
            self._save()
            return True
        return False

    # ============ 全局命令历史（跨终端，记录使用频次） ============

    def record_command(self, command: str):
        """记录一条命令，增加使用次数"""
        cmd = command.strip()
        if not cmd or len(cmd) > 500:
            return  # 忽略空命令和过长的命令
        history = self._data.setdefault("command_history", {})
        if cmd in history:
            history[cmd]["count"] += 1
            history[cmd]["last_used"] = time.time()
        else:
            history[cmd] = {"count": 1, "last_used": time.time()}
        self._save()

    def search_commands(self, keyword: str = "", limit: int = 50) -> list:
        """
        搜索命令，按使用频次降序排序（频次相同按最后使用时间降序）
        - keyword: 搜索关键词（空则返回全部）
        - limit: 最多返回多少条
        """
        history = self._data.get("command_history", {})
        kw = keyword.lower().strip()
        results = []
        for cmd, info in history.items():
            if not kw or kw in cmd.lower():
                results.append({
                    "command": cmd,
                    "count": info.get("count", 1),
                    "last_used": info.get("last_used", 0),
                })
        # 按使用频次降序，频次相同按最后使用时间降序
        results.sort(key=lambda x: (-x["count"], -x["last_used"]))
        return results[:limit]

    def list_recent_commands(self, limit: int = 100) -> list:
        """获取最近使用的命令（按最后使用时间降序）"""
        history = self._data.get("command_history", {})
        results = [
            {"command": cmd, "count": info.get("count", 1), "last_used": info.get("last_used", 0)}
            for cmd, info in history.items()
        ]
        results.sort(key=lambda x: -x["last_used"])
        return results[:limit]


# 全局存储实例
storage = Storage()
