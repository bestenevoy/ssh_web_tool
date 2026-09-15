"""
配置文件加载与端口解析

支持通过 config.json 自定义服务配置（监听地址、端口、端口占用自动切换等）。

配置文件唯一位置：~/.ai4one/sshtool/config.json（读写同一处，保证"改哪生效哪"）。

历史教训：旧实现按 EXE 目录 → cwd → 项目根 → 数据目录多处查找，
多处存在 config.json 时"改 A 读 B"，表现为修改配置后重启不生效；
现只认统一数据目录一处。

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

CONFIG_FILE_NAME = "config.json"
EXAMPLE_FILE_NAME = "config.example.json"

# 统一数据目录：~/.ai4one/sshtool（配置文件、数据、日志全部存放于此）
DATA_DIR_NAME = ".ai4one"
DATA_DIR_SUB = "sshtool"
# 旧数据目录名（目录改名前的位置），首次运行时一次性迁移到新目录
OLD_DATA_DIR_SUB = "wstool"


def get_app_dir() -> Path:
    """获取数据/配置目录：~/.ai4one/sshtool

    配置文件（config.json）、数据（data.json）、历史库（history.db）、
    日志（logs/）、脚本（scripts/）、运行时端口（.running_port）
    统一存放在用户家目录，方便集中管理。

    首次调用时触发一次性旧数据迁移（惰性）：保证任何入口
    （python main.py / uvicorn 直启 / 打包 EXE）都是"先迁移后读写"，
    避免 storage 先读到空数据、history_db 先建空库挡住迁移。
    """
    global _MIGRATION_DONE
    d = Path.home() / DATA_DIR_NAME / DATA_DIR_SUB
    if not _MIGRATION_DONE:
        _MIGRATION_DONE = True
        if "pytest" not in sys.modules:  # 测试进程绝不触碰真实数据目录
            try:
                _do_migrate_legacy(d)
            except Exception as e:  # 迁移失败不阻塞启动
                print(f"[config] 旧数据迁移失败: {e}")
    return d


_MIGRATION_DONE = False


def ensure_data_dir() -> Path:
    """确保数据目录存在（幂等）"""
    d = get_app_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_example_path() -> Path | None:
    """config.example.json 模板所在位置（打包后随资源目录 _MEIPASS）"""
    if getattr(sys, "frozen", False):
        p = Path(sys._MEIPASS) / EXAMPLE_FILE_NAME  # type: ignore[attr-defined]
        return p if p.is_file() else None
    p = Path(__file__).resolve().parent.parent / EXAMPLE_FILE_NAME
    return p if p.is_file() else None


def _do_migrate_legacy(d: Path) -> None:
    """执行迁移：把旧位置的数据一次性复制到目标数据目录（仅复制缺失文件，不覆盖）

    迁移来源（按优先级）：
    1. 旧数据目录 ~/.ai4one/wstool（目录改名 wstool -> sshtool 的一次性迁移）
    2. EXE 所在目录 / 项目根目录（更早期的散落位置，打包用户的配置可能在 EXE 旁）

    注意：迁移只是复制，配置的读取永远只来自统一数据目录一处。
    """
    d.mkdir(parents=True, exist_ok=True)  # 惰性迁移可能先于 ensure_data_dir 执行
    roots: list[Path] = [Path.home() / DATA_DIR_NAME / OLD_DATA_DIR_SUB]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        if not root.is_dir() or root.resolve() == d.resolve():
            continue
        for name in (CONFIG_FILE_NAME, "data.json", "history.db"):
            src = root / name
            if src.is_file() and not (d / name).exists():
                try:
                    shutil.copy2(src, d / name)
                    print(f"[config] 已迁移 {src.name} -> {d}")
                except OSError as e:
                    print(f"[config] 迁移 {src} 失败: {e}")
        for sub in ("logs", "scripts"):
            src_dir = root / sub
            dst_dir = d / sub
            if src_dir.is_dir() and not dst_dir.exists():
                try:
                    shutil.copytree(src_dir, dst_dir)
                    print(f"[config] 已迁移 {sub} 目录 -> {dst_dir}")
                except OSError as e:
                    print(f"[config] 迁移 {src_dir} 失败: {e}")


def migrate_legacy_data() -> None:
    """显式迁移入口（main.py 启动时调用；幂等，惰性迁移的兜底）"""
    _do_migrate_legacy(get_app_dir())


# 前端 UI 设置默认值（持久化到 config.json 的 ui_settings 段；逐键校验后透出给前端）
UI_SETTINGS_DEFAULTS: dict = {
    "theme": "light",  # light / dark
    "font_family": 'Consolas, "Microsoft YaHei", monospace',  # 终端字体栈
    "font_size": 13,  # 终端字号（8-32）
    "block_bar": True,  # 命令块左侧色条标记
    "block_auto_fold": False,  # 命令输出超长时自动折叠（默认关）
    "block_max_lines": 30,  # 自动折叠保留的输出行数
    "block_split_mode": "prompt",  # 命令块切块方式：按提示符出现 / 按 Enter
    "custom_prompt_patterns": [],  # 自定义提示符正则（prompt 模式下优先于内置匹配）
}

# ui_settings 各键的合法值校验器（返回规范化后的值；非法返回 None 表示回退默认）
_BLOCK_MAX_LINES_CHOICES = (15, 30, 50, 100, 200)
# 自定义提示符正则上限：单条长度 / 总条数（与前端 promptPatterns.ts 常量保持一致）
_MAX_PATTERN_LENGTH = 200
_MAX_PATTERN_COUNT = 20


def _validate_ui_setting(key: str, value) -> object | None:
    """校验单个 ui_settings 键值，返回规范化值；非法返回 None"""
    if key == "theme":
        return value if value in ("light", "dark") else None
    if key == "font_family":
        return value if isinstance(value, str) and value.strip() else None
    if key == "font_size":
        return value if isinstance(value, int) and not isinstance(value, bool) and 8 <= value <= 32 else None
    if key in ("block_bar", "block_auto_fold"):
        return value if isinstance(value, bool) else None
    if key == "block_max_lines":
        return value if value in _BLOCK_MAX_LINES_CHOICES else None
    if key == "block_split_mode":
        return value if value in ("enter", "prompt") else None
    if key == "custom_prompt_patterns":
        # 列表逐项过滤（剔除而非整键回退）：保留用户合法条目，兜住脏数据
        if not isinstance(value, list):
            return None
        cleaned = [
            v.strip() for v in value if isinstance(v, str) and v.strip() and len(v.strip()) <= _MAX_PATTERN_LENGTH
        ]
        return cleaned[:_MAX_PATTERN_COUNT]
    return None  # 未知键一律忽略


# 默认配置（无配置文件时的兜底值）
DEFAULT_CONFIG: dict = {
    "server": {
        "host": "127.0.0.1",  # 服务监听地址
        "port": 8765,  # 服务监听端口
        "auto_find_free_port": True,  # 端口被占用时自动寻找空闲端口
    },
    "open_browser": True,  # 启动后延迟自动打开浏览器
    # 默认本机终端 shell：左侧「默认终端」条目打开的 shell + SSH 断开后自动进入的 shell
    "fallback_local_shell": "powershell",
    "debug": False,  # 是否开启 pywebview 调试模式（F12 开发者工具）；开启会略增内存/CPU
    "ui_settings": dict(UI_SETTINGS_DEFAULTS),  # 前端 UI 设置（主题/字体/命令块等）
}

# 合法本机 shell 取值
LOCAL_SHELL_CHOICES = ("cmd", "powershell", "pwsh")

# 顶层标量配置白名单：这些键会在 load_config 时从用户 config.json 合并进来
# （ui_settings 不在此列：它是子字典，由下方逐键合并逻辑独家处理，整体替换会丢默认值）
_TOP_LEVEL_KEYS = ("open_browser", "fallback_local_shell", "debug")


def get_fallback_local_shell(cfg: dict | None = None) -> str:
    """读取默认本机终端 shell 配置（默认终端条目 + SSH 断开后共用），非法取值回退 powershell"""
    c = cfg if cfg is not None else load_config()
    val = (c or {}).get("fallback_local_shell", "powershell")
    if val not in LOCAL_SHELL_CHOICES:
        return "powershell"
    return val


def get_ui_settings(cfg: dict | None = None) -> dict:
    """读取前端 UI 设置（逐键校验，非法/缺失键回退默认值）"""
    c = cfg if cfg is not None else load_config()
    user_ui = (c or {}).get("ui_settings")
    result = dict(UI_SETTINGS_DEFAULTS)
    if isinstance(user_ui, dict):
        for key in UI_SETTINGS_DEFAULTS:
            if key in user_ui:
                validated = _validate_ui_setting(key, user_ui[key])
                if validated is not None:
                    result[key] = validated
    return result


def save_config(cfg: dict) -> bool:
    """把配置写回统一数据目录 ~/.ai4one/sshtool/config.json（与读取同一处）

    历史问题：旧实现写到 find_config_file 定位到的文件（可能在 EXE 目录/项目根），
    与用户实际编辑的位置不一致，表现为"修改配置后重启不生效"。
    """
    try:
        path = get_app_dir() / CONFIG_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as e:
        print(f"[config] 保存配置失败: {e}")
        return False


def find_config_file() -> Path | None:
    """查找配置文件：只认统一数据目录 ~/.ai4one/sshtool/config.json 一处

    旧实现按 EXE 目录 → cwd → 项目根 → 数据目录顺序查找，
    多处存在 config.json 时"改 A 读 B"，表现为修改配置后重启不生效。
    """
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


def load_config() -> dict:
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
        # ui_settings 子字典逐键合并：用户只写部分键时其余键保留默认值（整体替换会丢默认）
        ui = user_cfg.get("ui_settings")
        if isinstance(ui, dict):
            for key, value in ui.items():
                if key in UI_SETTINGS_DEFAULTS and value is not None:
                    cfg["ui_settings"][key] = value
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


def find_free_port(start_port: int, host: str = "127.0.0.1", max_tries: int = 100) -> int | None:
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


def resolve_server_config(cfg: dict) -> dict:
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
                    f"端口 {port} 被占用，且向上未找到空闲端口（已尝试 100 个）。请修改 config.json 中的 server.port。"
                )
            print(f"[config] 端口 {port} 已被占用，自动切换到空闲端口 {free_port}")
            return {"host": host, "port": free_port, "changed": True}
        raise RuntimeError(
            f"端口 {port} 已被占用，无法启动。\n"
            f"请在 config.json 中修改 server.port，"
            f"或将 server.auto_find_free_port 设为 true 让程序自动选择空闲端口。"
        )
    return {"host": host, "port": port, "changed": False}
