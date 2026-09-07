# -*- coding: utf-8 -*-
"""
Windows 系统托盘（纯 ctypes Win32 实现，零第三方依赖）

常驻右下角系统托盘：
- 双击 / 左键单击：打开 Web 界面（默认浏览器）
- 右键菜单：
    「打开界面」       → 默认浏览器打开 Web UI（防止误关页面后可随时找回）
    「打开配置目录」   → 资源管理器打开程序目录（config.json / data.json 所在）
    「打开配置文件」   → 默认编辑器打开 config.json
    「打开日志窗口」   → 新开 cmd 黑窗口实时跟随 logs/server.log（请求日志）
    「退出」           → 停止服务并退出

配合 PyInstaller --noconsole 打包：双击 EXE 无黑窗口，服务常驻托盘。
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# ---------- 关键 Win32 函数显式签名（消除 ctypes 默认转换的不确定性） ----------
user32.AppendMenuW.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_uint, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HANDLE
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.UINT
user32.DestroyMenu.argtypes = [wt.HANDLE]
user32.DestroyMenu.restype = wt.BOOL
user32.GetMenuStringW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, ctypes.c_int, wt.UINT]
user32.GetMenuStringW.restype = ctypes.c_int
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.GetLastActivePopup.argtypes = [wt.HWND]
user32.GetLastActivePopup.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.DestroyWindow.restype = wt.BOOL
user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
user32.UnregisterClassW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wt.BOOL

# ---------- Win32 常量 ----------
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_BOTTOMALIGN = 0x0020
TPM_LEFTALIGN = 0x0000

CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
CW_USEDEFAULT = 0x80000000
WS_OVERLAPPED = 0x00000000
WS_EX_TOOLWINDOW = 0x00000080
COLOR_WINDOW = 5
TRANSPARENT = 1
BLACKNESS = 0x00000042

ID_OPEN_WEB = 1001
ID_OPEN_DIR = 1002
ID_OPEN_CONFIG = 1003
ID_OPEN_LOG = 1004
ID_EXIT = 1005


def RGB(r, g, b):
    return r | (g << 8) | (b << 16)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP),
    ]


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

# 托盘菜单命令回调（由 main.py 注入）
_menu_actions = {}


def _create_icon():
    """程序化生成 32x32 托盘图标：深色终端窗口 + 绿色 ">_" 提示符"""
    W = 32
    H = 32
    hdc_screen = user32.GetDC(None)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, W, H)
    old_bmp = gdi32.SelectObject(hdc, hbmp)

    # 深色背景
    bg_brush = gdi32.CreateSolidBrush(RGB(13, 18, 32))
    rc = RECT(0, 0, W, H)
    user32.FillRect(hdc, ctypes.byref(rc), bg_brush)
    gdi32.DeleteObject(bg_brush)

    # 顶部亮色条（终端窗口风格）
    bar_brush = gdi32.CreateSolidBrush(RGB(34, 44, 72))
    rc2 = RECT(0, 0, W, 6)
    user32.FillRect(hdc, ctypes.byref(rc2), bar_brush)
    gdi32.DeleteObject(bar_brush)

    # 绿色 ">_"
    gdi32.SetBkMode(hdc, TRANSPARENT)
    gdi32.SetTextColor(hdc, RGB(82, 196, 26))
    font = gdi32.CreateFontW(20, 0, 0, 0, 700, 0, 0, 0,
                             0, 0, 0, 0, 0, "Consolas")
    old_font = gdi32.SelectObject(hdc, font)
    gdi32.TextOutA(hdc, 3, 6, b">_", 2)
    gdi32.SelectObject(hdc, old_font)
    gdi32.DeleteObject(font)

    # AND mask：全 0（不透明），mono 位图用 BLACKNESS 填充
    hdc_mono = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp_mono = gdi32.CreateCompatibleBitmap(hdc_mono, W, H)
    old_mono = gdi32.SelectObject(hdc_mono, hbmp_mono)
    rc3 = RECT(0, 0, W, H)
    gdi32.PatBlt(hdc_mono, 0, 0, W, H, BLACKNESS)
    gdi32.SelectObject(hdc_mono, old_mono)
    gdi32.DeleteDC(hdc_mono)

    icon_info = ICONINFO()
    icon_info.fIcon = True
    icon_info.hbmMask = hbmp_mono
    icon_info.hbmColor = hbmp
    hicon = user32.CreateIconIndirect(ctypes.byref(icon_info))

    gdi32.SelectObject(hdc, old_bmp)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteObject(hbmp_mono)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(None, hdc_screen)
    return hicon


class TrayIcon:
    """Win32 托盘图标（在独立线程中运行消息循环）"""

    def __init__(self, base_url: str, app_dir: Path, config_path: Path, log_path: Path):
        self.base_url = base_url
        self.app_dir = str(app_dir)
        self.config_path = str(config_path)
        self.log_path = str(log_path)
        self.hwnd = None
        self._class_atom = None
        self._taskbar_created = None

    # ---------- 菜单动作 ----------
    def open_web(self):
        import webbrowser
        try:
            webbrowser.open(self.base_url)
        except Exception as e:
            print(f"[tray] 打开界面失败: {e}")

    def open_dir(self):
        try:
            os.startfile(self.app_dir)  # noqa: S606
        except Exception as e:
            print(f"[tray] 打开配置目录失败: {e}")

    def open_config(self):
        try:
            os.startfile(self.config_path)  # noqa: S606
        except Exception as e:
            print(f"[tray] 打开配置文件失败: {e}")

    def open_log(self):
        try:
            log = self.log_path
            cmd = f'cmd /k powershell -NoExit -Command "Get-Content -LiteralPath \'{log}\' -Wait -Tail 200"'
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            print(f"[tray] 打开日志窗口失败: {e}")

    def exit_app(self):
        print("[tray] 通过托盘菜单退出")
        os._exit(0)  # noqa: PLR1722

    # ---------- 窗口过程 ----------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_TRAYICON:
                if lparam == WM_LBUTTONDBLCLK or lparam == WM_LBUTTONUP:
                    self.open_web()
                    return 0
                if lparam == WM_RBUTTONUP:
                    self._show_menu()
                    return 0
            if msg == self._taskbar_created:
                # explorer 重启后重加托盘图标
                self._add_icon()
                return 0
            if msg == WM_COMMAND:
                cmd_id = wparam & 0xFFFF
                action = _menu_actions.get(cmd_id)
                if action:
                    action()
                return 0
        except Exception as e:
            print(f"[tray] 窗口过程异常: {e}")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------- 托盘 ----------
    def _add_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = "SSH Web Tool - 网页版SSH终端"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _force_foreground(self):
        """将隐藏的托盘窗口强制置为前台（标准托盘做法，参考 pystray）"""
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNA：不激活显示
        user32.SetForegroundWindow(self.hwnd)
        # SetForegroundWindow 对隐藏窗口常被系统拒绝（SetForegroundLockTimeout 限制），
        # 用 AttachThreadInput 挂接到当前前台线程后置前，是官方 workaround
        fore = user32.GetForegroundWindow()
        if fore and fore != self.hwnd:
            fore_tid = user32.GetWindowThreadProcessId(fore, None)
            cur_tid = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(cur_tid, fore_tid, True)
            user32.BringWindowToTop(self.hwnd)
            user32.SetForegroundWindow(self.hwnd)
            user32.AttachThreadInput(cur_tid, fore_tid, False)
        user32.ShowWindow(self.hwnd, 0)  # SW_HIDE

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        if not menu:
            logging.error("[tray] CreatePopupMenu 失败")
            return

        # 持久保存字符串引用：某些情况下菜单只保存指针而不拷贝文本
        labels = [
            (ID_OPEN_WEB, "打开界面 (Web)"),
            (ID_OPEN_DIR, "打开配置目录"),
            (ID_OPEN_CONFIG, "打开配置文件 config.json"),
            (ID_OPEN_LOG, "打开日志窗口"),
            (ID_EXIT, "退出"),
        ]
        self._menu_labels = []

        def _append(mid, text):
            buf = ctypes.create_unicode_buffer(text)
            self._menu_labels.append(buf)
            r = user32.AppendMenuW(menu, MF_STRING, mid, buf)
            if not r:
                logging.error(f"[tray] AppendMenuW 失败 id={mid} err={kernel32.GetLastError()}")
            # 诊断：读回菜单项文本，确认字符串是否正确写入
            if r:
                rb = ctypes.create_unicode_buffer(256)
                n = user32.GetMenuStringW(menu, user32.GetMenuItemCount(menu) - 1, rb, 256, 0x0400)
                logging.info(f"[tray] 菜单项 id={mid} 写入成功，读回[{n}]={rb.value!r}")

        _append(ID_OPEN_WEB, "打开界面 (Web)")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _append(ID_OPEN_DIR, "打开配置目录")
        _append(ID_OPEN_CONFIG, "打开配置文件 config.json")
        _append(ID_OPEN_LOG, "打开日志窗口")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _append(ID_EXIT, "退出")

        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._force_foreground()
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        # 菜单关闭后发 WM_NULL，避免菜单窗口残留不消失（标准技巧）
        user32.PostMessageW(self.hwnd, 0x0000, 0, 0)  # WM_NULL
        if cmd == ID_OPEN_WEB:
            self.open_web()
        elif cmd == ID_OPEN_DIR:
            self.open_dir()
        elif cmd == ID_OPEN_CONFIG:
            self.open_config()
        elif cmd == ID_OPEN_LOG:
            self.open_log()
        elif cmd == ID_EXIT:
            self.exit_app()

    # ---------- 主循环 ----------
    def run(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._hicon = _create_icon()
        if not self._hicon:
            self._hicon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION

        proc = WNDPROC(self._wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.hIcon = self._hicon
        wc.hCursor = None
        wc.hbrBackground = COLOR_WINDOW
        wc.lpszClassName = "SSHWebToolTrayWnd"
        wc.hIconSm = self._hicon
        self._class_atom = user32.RegisterClassExW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, "SSHWebToolTrayWnd", "SSHWebToolTray",
            WS_OVERLAPPED, CW_USEDEFAULT, CW_USEDEFAULT, 0, 0,
            None, None, hinst, None,
        )
        if not self.hwnd:
            print("[tray] 创建托盘窗口失败")
            return

        # 动作表（供 WM_COMMAND 使用）
        _menu_actions[ID_OPEN_WEB] = self.open_web
        _menu_actions[ID_OPEN_DIR] = self.open_dir
        _menu_actions[ID_OPEN_CONFIG] = self.open_config
        _menu_actions[ID_OPEN_LOG] = self.open_log
        _menu_actions[ID_EXIT] = self.exit_app

        self._add_icon()
        print("[tray] 系统托盘已就绪（右键图标打开菜单）")

        # 消息循环
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 清理
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        user32.DestroyWindow(self.hwnd)
        user32.DestroyIcon(self._hicon)
        user32.UnregisterClassW("SSHWebToolTrayWnd", hinst)


def start_tray(base_url: str, app_dir, config_path, log_path) -> threading.Thread:
    """
    在后台线程启动系统托盘（不阻塞主流程）。

    Args:
        base_url: Web UI 地址，如 http://127.0.0.1:8765
        app_dir: 程序目录（config.json/data.json 所在）
        config_path: 配置文件路径
        log_path: 服务日志文件路径（logs/server.log）
    """
    tray = TrayIcon(base_url, Path(app_dir), Path(config_path), Path(log_path))

    def _run():
        try:
            tray.run()
        except Exception as e:
            print(f"[tray] 托盘线程异常退出: {e}")

    t = threading.Thread(target=_run, name="tray", daemon=True)
    t.start()
    return t


def acquire_single_instance() -> bool:
    """
    单实例检查：已有一个实例在运行时返回 False（调用方应打开页面后退出）。

    命名 Mutex 在进程退出时由系统自动释放。
    """
    kernel32.CreateMutexW(None, False, "Local\\SSHWebTool_SingleInstance")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
