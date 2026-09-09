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
import shutil
import socket
import sys
from pathlib import Path
from typing import Dict, Optional

CONFIG_FILE_NAME = "config.json"
EXAMPLE_FILE_NAME = "config.example.json"

# 统一数据目录：~/.ai4one/wstool（配置文件、数据、日志全部存放于此）
DATA_DIR_NAME = ".ai4one"
DATA_DIR_SUB = "wstool"


def get_app_dir() -> Path:
    """获取数据/配置目录：~/.ai4one/wstool

    配置文件（config.json）、数据（data.json）、日志（logs/）、
    运行时端口（.running_port）统一存放在用户家目录，方便集中管理。
    """
    return Path.home() / DATA_DIR_NAME / DATA_DIR_SUB


def ensure_data_dir() -> Path:
    """确保数据目录存在（幂等）"""
    d = get_app_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_example_path() -> Optional[Path]:
    """config.example.json 模板所在位置（打包后随资源目录 _MEIPASS）"""
    if getattr(sys, 'frozen', False):
        p = Path(sys._MEIPASS) / EXAMPLE_FILE_NAME
        return p if p.is_file() else None
    p = Path(__file__).resolve().parent.parent / EXAMPLE_FILE_NAME
    return p if p.is_file() else None


def migrate_legacy_data() -> None:
    """首次运行：把旧位置（EXE 目录 / 项目根目录）的配置与数据迁移到新目录

    仅当新目录中不存在同名文件时复制，不覆盖已有数据。
    """
    d = get_app_dir()
    roots = []
    if getattr(sys, 'frozen', False):
        roots.append(Path(sys.executable).parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        for name in (CONFIG_FILE_NAME, "data.json"):
            src = root / name
            if src.is_file() and not (d / name).exists():
                try:
                    shutil.copy2(src, d / name)
                    print(f"[config] 已迁移 {src.name} -> {d}")
                except OSError as e:
                    print(f"[config] 迁移 {src} 失败: {e}")
        src_logs = root / "logs"
        dst_logs = d / "logs"
        if src_logs.is_dir() and not dst_logs.exists():
            try:
                shutil.copytree(src_logs, dst_logs)
                print(f"[config] 已迁移日志目录 -> {dst_logs}")
            except OSError as e:
                print(f"[config] 迁移日志目录失败: {e}")

# 默认配置（无配置文件时的兜底值）
DEFAULT_CONFIG: Dict = {
    "server": {
        "host": "127.0.0.1",            # 服务监听地址
        "port": 8765,                   # 服务监听端口
        "auto_find_free_port": True,    # 端口被占用时自动寻找空闲端口
    },
    "open_browser": True,               # 启动后延迟自动打开浏览器
    "fallback_local_shell": "cmd",      # SSH 断开自动切换本机终端时使用的 shell（cmd / powershell / pwsh）
}

# 合法本机 shell 取值
LOCAL_SHELL_CHOICES = ("cmd", "powershell", "pwsh")

# 顶层标量配置白名单：这些键会在 load_config 时从用户 config.json 合并进来
_TOP_LEVEL_KEYS = ("open_browser", "fallback_local_shell")


def get_fallback_local_shell(cfg: Optional[Dict] = None) -> str:
    """读取"SSH 断开后切换本机终端"的 shell 配置，非法取值回退 cmd"""
    c = cfg if cfg is not None else load_config()
    val = (c or {}).get("fallback_local_shell", "cmd")
    if val not in LOCAL_SHELL_CHOICES:
        return "cmd"
    return val


def save_config(cfg: Dict) -> bool:
    """把配置写回配置文件（~/.ai4one/wstool/config.json，保留注释不可行——纯 JSON 覆盖写）

    仅当配置目录下已有 config.json 时写入；无配置文件则创建。
    """
    try:
        path = get_app_dir() / CONFIG_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as e:
        print(f"[config] 保存配置失败: {e}")
        return False


def find_config_file() -> Optional[Path]:
    """查找配置文件（统一在数据目录 ~/.ai4one/wstool 中）"""
    p = get_app_dir() / CONFIG_FILE_NAME
    return p if p.is_file() else None


def ensure_config_file() -> Path:
    """
    若数据目录中没有配置文件，则生成默认配置。

    优先复制 config.example.json 模板（便于用户看到可配置项说明），
    没有模板则写入内置默认值。已有配置文件时直接返回，不重复生成。
    """
    existing = find_config_file()
    if existing is not None:
        return existing
    target = get_app_dir() / CONFIG_FILE_NAME
    try:
        example = get_example_path()
        if example is not None:
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
        # 顶层标量配置白名单（新增全局配置项时在这里登记即可被 load_config 读取）
        for key in _TOP_LEVEL_KEYS:
            if key in user_cfg:
                cfg[key] = user_cfg[key]
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
