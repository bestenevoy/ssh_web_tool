"""
配置文件加载与端口解析

支持通过 config.json 自定义服务配置（监听地址、端口、端口占用自动切换等）。

配置文件查找顺序（高优先级在前）：
    1. EXE 所在目录（PyInstaller 打包后，方便用户放在 EXE 旁边修改）
    2. 当前工作目录
    3. 项目根目录 / 脚本目录

若找不到 config.json，程序会在上述目录生成一份默认配置（优先复制
config.example.json 模板），用户修改后重启即可生效。

端口占用处理：
    - server.auto_find_free_port = true（默认）：指定端口被占用时，
      自动从 server.port 向上探测空闲端口并切换，程序仍能正常启动。
    - server.auto_find_free_port = false：端口被占用时直接报错退出，
      提示用户修改配置文件。
"""
import json
import socket
import sys
from pathlib import Path
from typing import Dict, Optional

CONFIG_FILE_NAME = "config.json"
EXAMPLE_FILE_NAME = "config.example.json"

# 默认配置（无配置文件时的兜底值）
DEFAULT_CONFIG: Dict = {
    "server": {
        "host": "127.0.0.1",            # 服务监听地址
        "port": 8765,                   # 服务监听端口
        "auto_find_free_port": True,    # 端口被占用时自动寻找空闲端口
    },
    "open_browser": True,               # 启动后延迟自动打开浏览器
    "show_password_plaintext": False,   # 前端是否明文展示密码（false=掩码显示，true=明文显示）
}


def get_app_dir() -> Path:
    """获取程序所在目录（EXE 同级，或项目根目录）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，配置/数据保存在 EXE 所在目录
        return Path(sys.executable).parent
    # 脚本模式：本文件位于 <项目根>/ssh_web_tool/config.py
    return Path(__file__).resolve().parent.parent


def find_config_file() -> Optional[Path]:
    """按优先级查找配置文件，找不到返回 None"""
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(Path(sys.executable).parent / CONFIG_FILE_NAME)
    candidates.append(Path.cwd() / CONFIG_FILE_NAME)
    candidates.append(get_app_dir() / CONFIG_FILE_NAME)
    for p in candidates:
        if p.is_file():
            return p
    return None


def ensure_config_file() -> Path:
    """
    若没有任何配置文件，则在程序目录生成默认配置。

    优先复制 config.example.json 模板（便于用户看到可配置项说明），
    没有模板则写入内置默认值。已有配置文件时直接返回，不重复生成。
    """
    existing = find_config_file()
    if existing is not None:
        return existing
    target = get_app_dir() / CONFIG_FILE_NAME
    if target.is_file():
        return target
    try:
        example = get_app_dir() / EXAMPLE_FILE_NAME
        if example.is_file():
            target.write_text(example.read_text(encoding="utf-8-sig"), encoding="utf-8")
        else:
            target.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"[config] 未找到配置文件，已生成默认配置: {target}")
        print("[config] 如需修改端口/地址，请编辑该文件后重启程序")
    except OSError as e:
        print(f"[config] 生成默认配置文件失败: {e}")
    return target


def load_config() -> Dict:
    """加载配置并合并默认值（深拷贝，避免污染 DEFAULT_CONFIG）"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg_file = find_config_file()
    if cfg_file is None:
        return cfg
    try:
        user_cfg = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
        if not isinstance(user_cfg, dict):
            print(f"[config] 配置文件格式错误（应为 JSON 对象），使用默认配置: {cfg_file}")
            return cfg
        server = user_cfg.get("server")
        if isinstance(server, dict):
            for key, value in server.items():
                if value is not None:
                    cfg["server"][key] = value
        if "open_browser" in user_cfg and isinstance(user_cfg["open_browser"], bool):
            cfg["open_browser"] = user_cfg["open_browser"]
        if "show_password_plaintext" in user_cfg and isinstance(user_cfg["show_password_plaintext"], bool):
            cfg["show_password_plaintext"] = user_cfg["show_password_plaintext"]
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] 配置文件解析失败，使用默认配置: {e}")
    return cfg


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检测端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def find_free_port(start_port: int, host: str = "127.0.0.1", max_tries: int = 100) -> Optional[int]:
    """从 start_port 开始向上寻找空闲端口，找不到返回 None"""
    if start_port < 1:
        start_port = 1
    end = min(start_port + max_tries, 65535)
    for port in range(start_port, end):
        if not port_in_use(port, host):
            return port
    return None


def validate_port(port: int) -> int:
    """校验端口范围，非法则抛出 ValueError"""
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError(f"端口号必须在 1-65535 之间，当前: {port}")
    return port


def resolve_server_config(cfg: Dict) -> Dict:
    """
    解析服务端监听配置，处理端口占用。

    Returns:
        {"host": str, "port": int, "changed": bool}

    Raises:
        ValueError: 端口号非法
        RuntimeError: 端口被占用且不允许自动切换，或找不到可用端口
    """
    server = cfg.get("server", {})
    host = str(server.get("host") or DEFAULT_CONFIG["server"]["host"])
    port = validate_port(server.get("port") or DEFAULT_CONFIG["server"]["port"])
    auto_find = bool(server.get("auto_find_free_port", True))

    if port_in_use(port, host):
        if auto_find:
            free_port = find_free_port(port, host)
            if free_port is None:
                raise RuntimeError(
                    f"端口 {port} 被占用，且向上未找到空闲端口（已尝试 100 个）。"
                    "请修改 config.json 中的 server.port。"
                )
            print(f"[config] 端口 {port} 已被占用，自动切换到空闲端口 {free_port}")
            return {"host": host, "port": free_port, "changed": True}
        raise RuntimeError(
            f"端口 {port} 已被占用，无法启动。\n"
            f"请在 config.json 中修改 server.port，"
            f"或将 server.auto_find_free_port 设为 true 让程序自动选择空闲端口。"
        )
    return {"host": host, "port": port, "changed": False}
