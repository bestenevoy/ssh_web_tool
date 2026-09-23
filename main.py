"""
FastAPI 后端入口（组合根）
- 应用装配：FastAPI app / 静态页面 / API 路由挂载 / 生命周期
- 桌面壳：pywebview 窗口 + 单实例锁 + WebView2 检查 + 托盘

业务路由已按域拆分到 ssh_web_tool/api/routers（config/sessions/hosts/groups/
quick_commands/history/sftp/storage_auto/external/ws），本文件只负责组装与启动。
"""

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ssh_web_tool import history_db
from ssh_web_tool.api import router as api_router
from ssh_web_tool.config import (
    CONFIG_FILE_NAME,
    ensure_config_file,
    ensure_data_dir,
    get_app_dir,
    load_config,
    resolve_server_config,
)

# 单例绑定（组合根暴露给 deps.py）：本文件不直接使用，仅供
# `import main` 后在调用时读取绑定（路由取单例 / 测试替换注入假实现）。
from ssh_web_tool.event_bus import event_bus  # noqa: F401
from ssh_web_tool.external_sessions import external_hub  # noqa: F401
from ssh_web_tool.sessions import SSHSession, session_manager  # noqa: F401
from ssh_web_tool.storage import storage
from ssh_web_tool.version import APP_VERSION


def _register_as_main() -> None:
    """将自己注册为 sys.modules 中的 "main"，保证 `python main.py` 脚本模式下
    `import main` 复用本模块。

    deps.py 在调用时 `import main` 读取单例绑定（测试兼容：替换 main.session_manager
    等绑定生效）。若脚本以 __main__ 运行且未注册，`import main` 会把本文件
    二次执行，路由拿到的将是另一份 app/单例。
    """
    if __name__ == "__main__":
        sys.modules.setdefault("main", sys.modules[__name__])


_register_as_main()

app = FastAPI(title="SSH Web Tool", version=APP_VERSION)


@app.on_event("shutdown")
async def _shutdown_cleanup():
    """程序退出前关闭数据库单例连接。

    history_db 使用全局单例 aiosqlite 连接（非 daemon worker 线程），
    若不显式关闭，进程异常退出时可能残留 -wal/-shm 文件。
    """
    try:
        await history_db.close_db()
    except Exception:
        pass


# PyInstaller 打包兼容：静态文件从临时目录读取，数据文件保存在 EXE 所在目录
def get_resource_path(relative_path: str) -> Path:
    """获取资源文件路径（兼容 PyInstaller 打包）"""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 打包后，资源文件在临时目录
        return Path(sys._MEIPASS) / relative_path  # type: ignore[attr-defined]
    return Path(__file__).parent / relative_path


def get_data_path(relative_path: str) -> Path:
    """获取数据文件路径（统一存放在 ~/.ai4one/sshtool，不随打包丢失）"""
    return get_app_dir() / relative_path


BASE_DIR = Path(__file__).parent
STATIC_DIR = get_resource_path("static")
DATA_DIR = get_data_path(".")

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 挂载按域拆分的业务路由（HTTP + WebSocket）
app.include_router(api_router)


# ============ 页面路由 ============


@app.get("/")
async def index():
    """主页：网页 UI（禁用缓存，确保每次打开/刷新都加载最新构建，避免旧页面缓存导致功能不一致）"""
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


# ============ 启动 ============


def _acquire_single_instance() -> bool:
    """单实例检查：已有一个实例在运行时返回 False（Windows 命名 Mutex）

    注意：必须用 use_last_error=True + ctypes.get_last_error()。
    ctypes.windll.kernel32.GetLastError() 的返回值会被 ctypes 自身的
    内部调用覆盖，导致单实例检查误判（多个实例同时通过检查）。
    """
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Local\\SSHWebTool_SingleInstance")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _exit_quietly(code: int = 0) -> None:
    """启动阶段的提前退出。

    history_db.init_db() 已打开 aiosqlite 单例连接，其内部工作线程是
    非守护线程（aiosqlite/core.py Thread 无 daemon=True），main() 直接
    return 会留下一个无窗口僵尸进程（用户看到"启动了但没反应"），
    必须先关闭数据库再硬退出。
    """
    try:
        asyncio.run(history_db.close_db())
    except Exception:
        pass
    # os._exit 不刷新缓冲：管道/终端下提前 print 的提示会丢失，先手动 flush
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    os._exit(code)


def _check_webview2_runtime() -> bool:
    """检查 WebView2 Runtime 是否已安装。

    pywebview 在 Windows 上优先使用 EdgeChromium（WebView2）内核，
    但运行时缺失时会静默回退到 MSHTML（IE 内核）——现代前端页面将完全无法渲染。
    这里提前检测，缺失时给出明确提示而不是让用户面对白屏。

    注意：机器级安装的 WebView2 注册在 HKLM 的 32 位视图（WOW6432Node）下，
    64 位 Python 默认视图看不到，必须显式用 KEY_WOW64_32KEY 读取。
    """
    import winreg

    # WebView2 Runtime 稳定版的注册表 GUID（与 pywebview 内部检测一致）
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    root, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}", 0, winreg.KEY_READ | view
                ) as key:
                    pv = winreg.QueryValueEx(key, "pv")[0]
                    if pv and pv != "0.0.0.0":
                        return True
            except OSError:
                continue

    # 兜底：注册表异常时直接找安装目录下的 msedgewebview2.exe（覆盖非常规安装方式）
    import glob

    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(env)
        if base and glob.glob(os.path.join(base, "Microsoft", "EdgeWebView", "Application", "*", "msedgewebview2.exe")):
            return True
    return False


def main():
    """启动 SSH Web Tool 服务（pywebview 桌面窗口版）"""
    # PyInstaller 打包后多进程支持
    if hasattr(sys, "frozen"):
        import multiprocessing

        multiprocessing.freeze_support()

    import logging

    import uvicorn

    # 统一数据目录：~/.ai4one/sshtool
    ensure_data_dir()

    # 历史命令数据库：建表（幂等）
    try:
        asyncio.run(history_db.init_db())
    except Exception as e:
        print(f"[history] 初始化历史数据库失败: {e}")

    # 单实例：已有实例在运行则退出（windowed EXE 无控制台，print 不可见，需弹窗提示）
    if not _acquire_single_instance():
        msg = "SSH Web Tool 已在运行，请勿重复启动。\n\n（如需重新启动，请先退出已运行的实例）"
        print(msg)
        if hasattr(sys, "frozen"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, "SSH Web Tool", 0x40)  # MB_ICONINFORMATION
        _exit_quietly(0)

    # WebView2 Runtime 缺失时 pywebview 会静默回退 IE 内核，页面无法渲染；提前拦截并指引安装
    if sys.platform == "win32" and not _check_webview2_runtime():
        msg = (
            "未检测到 Microsoft WebView2 Runtime，桌面窗口无法启动。\n\n"
            "请安装后重试：\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
            "（Win10/11 通常已内置，多数情况只需下载 Evergreen Bootstrapper 一键安装）"
        )
        print(msg)
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, "SSH Web Tool", 0x30)  # MB_ICONWARNING
        _exit_quietly(1)

    # 加载配置
    ensure_config_file()
    cfg = load_config()
    try:
        server = resolve_server_config(cfg)
    except (RuntimeError, ValueError) as e:
        print(f"\n启动失败：{e}")
        print(f"提示：可编辑 {CONFIG_FILE_NAME} 修改 server.port / server.host 后重启")
        if hasattr(sys, "frozen"):
            input("\n按回车键退出...")
        return

    host, port = server["host"], server["port"]
    base_url = f"http://{host}:{port}"

    # 记录实际使用的端口
    try:
        (get_app_dir() / ".running_port").write_text(str(port), encoding="utf-8")
    except OSError:
        pass

    # ===== 请求日志：同时写入 logs/server.log 与控制台 =====
    log_dir = Path(SSHSession.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    server_log = log_dir / "server.log"
    if not server_log.is_file():
        server_log.write_text("", encoding="utf-8")
    file_handler = logging.FileHandler(str(server_log), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
        root_logger.addHandler(stream_handler)

    print("=" * 50)
    print(f"SSH Web Tool v{APP_VERSION} 启动中...")
    print(f"配置文件: {CONFIG_FILE_NAME}")
    print(f"本地服务: {base_url}")
    print(f"API 文档: {base_url}/docs")
    print("数据文件:", storage.data_file)
    print("日志目录:", SSHSession.LOG_DIR)
    print("请求日志:", server_log)
    print("=" * 50)

    # 清理超过 30 天的旧会话日志
    try:
        n = SSHSession.cleanup_old_logs(days=30)
        if n:
            print(f"[日志] 已清理 {n} 个超过 30 天的旧会话日志")
    except Exception as e:
        print(f"[日志] 清理旧日志失败: {e}")

    # 启动时后台静默检查更新（有新版才弹提示，不自动更新）
    try:
        from ssh_web_tool.updater import check_for_update_quiet

        threading.Thread(target=check_for_update_quiet, daemon=True).start()
    except Exception as e:
        print(f"[updater] 启动更新检查失败: {e}")

    # 在后台线程启动 uvicorn（FastAPI 服务）
    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": host, "port": port, "log_level": "info", "workers": 1, "log_config": None},
        daemon=True,
    )
    server_thread.start()

    # 等待服务就绪
    import urllib.request

    for _ in range(30):
        try:
            urllib.request.urlopen(f"{base_url}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)

    # 启动 pywebview 窗口
    import webview

    # 允许网页触发下载（SFTP 下载走 <a download>，pywebview 默认禁止下载）
    # 开启后 WebView2 会弹原生"另存为"对话框
    webview.settings["ALLOW_DOWNLOADS"] = True

    # 原生剪贴板桥（pywebview js_api）：前端调用 window.pywebview.api.copy_text(text)
    # WebView2 下 navigator.clipboard.writeText 常被安全策略/焦点要求拒绝而静默失败，
    # 终端复制走这里最可靠。用 Win32 SetClipboardData 直接写系统剪贴板，无第三方依赖。

    class ClipboardApi:
        def copy_text(self, text: str) -> bool:
            """把文本写入 Windows 系统剪贴板（CF_UNICODETEXT）"""
            try:
                import ctypes

                u32 = ctypes.windll.user32
                k32 = ctypes.windll.kernel32
                CF_UNICODETEXT = 13
                GMEM_MOVEABLE = 0x0002
                # 显式 64 位签名：GlobalAlloc/GlobalLock 返回句柄 (HANDLE)，
                # ctypes 默认 restype=c_int 会截断 64 位指针导致 Operation 失败/崩溃
                vt = ctypes.c_void_p
                k32.GlobalAlloc.restype = vt
                k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
                k32.GlobalLock.restype = vt
                k32.GlobalLock.argtypes = [vt]
                k32.GlobalUnlock.argtypes = [vt]
                u32.SetClipboardData.argtypes = [ctypes.c_uint, vt]
                u32.SetClipboardData.restype = vt
                data = text.encode("utf-16-le") + b"\x00\x00"  # 含结尾 NUL
                h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h_mem:
                    return False
                ok = False
                try:
                    ptr = k32.GlobalLock(h_mem)
                    if ptr:
                        ctypes.memmove(ptr, data, len(data))
                        k32.GlobalUnlock(h_mem)
                        if u32.OpenClipboard(None):
                            try:
                                u32.EmptyClipboard()
                                set_ok = u32.SetClipboardData(CF_UNICODETEXT, h_mem)
                                ok = bool(set_ok)
                                if ok:
                                    h_mem = None  # 系统已接管，交由系统释放
                            finally:
                                u32.CloseClipboard()
                finally:
                    if h_mem:  # 未成功（系统未接管）才手动释放，避免 double-free
                        k32.GlobalFree(h_mem)
                return ok
            except Exception:
                return False

        def open_console(self) -> bool:
            """打开 WebView2 开发者工具（顶栏 🐞 按钮的调试入口）。

            打包版 webview.start(debug=False) 时 AreDevToolsEnabled 与 F12 加速键
            都是关的（pywebview 按 debug 开关统一设置），这里在运行时把这两项打开
            再调 OpenDevToolsWindow；此后 F12 也可直接切换。注意 pywebview 6.x
            WinForms 后端里 WebView2 控件挂在 BrowserView.instances（BrowserForm
            .webview），不在 webview.Window 实例上；js_api 回调不在 UI 线程，
            CoreWebView2 调用须经 Control.Invoke 编组。失败原因打 stdout 便于排查。
            """
            try:
                from webview.platforms.winforms import BrowserView

                target: tuple = ()
                for form in BrowserView.instances.values():
                    ctrl = getattr(form, "webview", None)
                    core = getattr(ctrl, "CoreWebView2", None) if ctrl is not None else None
                    if core is not None:
                        target = (ctrl, core)
                        break
                if not target:
                    print(f"[devtools] 失败：BrowserView.instances={list(BrowserView.instances)} 无就绪 CoreWebView2")
                    return False
                ctrl, core = target

                def _open():
                    core.Settings.AreDevToolsEnabled = True
                    core.Settings.AreBrowserAcceleratorKeysEnabled = True
                    core.OpenDevToolsWindow()

                try:
                    if ctrl.InvokeRequired:
                        from System import Action  # pyright: ignore[reportMissingImports] — pythonnet 运行时才有

                        ctrl.Invoke(Action(_open))
                    else:
                        _open()
                except Exception as e:
                    print(f"[devtools] Invoke 编组失败，直调一次: {e!r}")
                    try:
                        _open()
                    except Exception as e2:
                        print(f"[devtools] 直调也失败: {e2!r}")
                        return False
                return True
            except Exception as e:
                print(f"[devtools] 异常: {e!r}")
                return False

    def on_closing():
        """窗口关闭确认"""
        import ctypes

        result = ctypes.windll.user32.MessageBoxW(
            0,
            "确定要退出 SSH Web Tool 吗？\n所有 SSH 连接将被断开。",
            "确认退出",
            0x04 | 0x30 | 0x00,  # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON1
        )
        if result == 6:  # IDYES
            # 关闭历史数据库
            try:
                asyncio.run(history_db.close_db())
            except Exception:
                pass
            # 硬杀进程：os._exit 走 C exit() 仍会等待全部 DLL 卸载（WebView2 等），
            # 实测点"是"后窗口要卡 ~2s 才消失；TerminateProcess 跳过卸载立即退出。
            # 必须显式声明 64 位类型：GetCurrentProcess 返回伪句柄 (HANDLE)-1，
            # ctypes 默认 restype=c_int 会截断成 0xFFFFFFFF，TerminateProcess
            # 收到错误句柄静默失败（ERROR_INVALID_HANDLE），表现为点退出无反应
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
            # 极端情况下终止异步生效，os._exit 兜底（正常不会走到）
            os._exit(0)
        return False  # 阻止默认关闭行为（只在确认后退出）

    window = webview.create_window(
        title=f"SSH Web Tool v{APP_VERSION}",
        url=base_url,
        width=1280,
        height=800,
        min_size=(800, 500),
        text_select=False,  # CSS 层面精细控制：标题/标签禁止选中，终端/输入框允许选中
        js_api=ClipboardApi(),
    )
    if window is None:  # create_window 失败（类型标注为 Optional，正常路径不会发生）
        raise RuntimeError("创建 pywebview 窗口失败")
    # pywebview 事件挂在 window.events 上；closing 处理器返回 False 可取消关闭
    window.events.closing += on_closing
    # debug 由配置文件决定：config.json 中设置 "debug": true 即开启（打包版也可用 F12 开发者工具），
    # false / 缺省则关闭，减少内存与 CPU 占用
    is_debug = bool(cfg.get("debug", False))
    # private_mode=False 关闭 InPrivate 模式：否则 WebView2 每次启动都清空 localStorage，
    # 前端的字体/主题等设置（存于 localStorage）重启后全部重置。
    # storage_path 把 WebView2 用户数据目录收敛到应用数据目录下，便于集中管理
    webview_profile = get_app_dir() / "webview"
    webview_profile.mkdir(parents=True, exist_ok=True)
    webview.start(
        debug=is_debug,
        private_mode=False,
        storage_path=str(webview_profile),
    )


if __name__ == "__main__":
    main()
