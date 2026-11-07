"""系统托盘：ctypes + Shell_NotifyIconW 的独立消息窗口线程实现。

启动静默：Tk 主窗口全程 withdraw，用户无感知；所有交互经由托盘完成：
    - 左键单击：唤出主窗口（Win95 风格：日志 + 设置）
    - 右键菜单：显示窗口 / 退出

不引入 pystray/pywin32：托盘线程自建窗口类 (CreateWindowExW + GetMessageW)，
回调经 Tk.after(0, ...) 派回 Tk 主线程，天然线程安全。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger("internet_proxy.tray")

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

# 显式声明原型：默认 restype=int 会在 64 位下截断 HWND/HICON/HMENU 等句柄，
# 导致打包后的 exe 托盘窗口创建 / Shell_NotifyIconW 静默失败。
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.GetMessageW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.restype = ctypes.c_longlong
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.LoadImageW.argtypes = [
    wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT,
]
user32.LoadImageW.restype = wt.HICON
user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.LoadIconW.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.DestroyIcon.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.c_void_p]
user32.GetCursorPos.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wt.BOOL
ctypes.windll.kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
ctypes.windll.kernel32.GetModuleHandleW.restype = wt.HMODULE

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_APP_TRAY = 0x8000 + 1987
NIM_ADD, NIM_DELETE = 0x00000000, 0x00000002
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x0001, 0x0002, 0x0004
TRAY_ID = 1987

IDI_APPLICATION = 32512


class _GUID(ctypes.Structure):
    """ctypes.wintypes.GUID 在部分 Python/PyInstaller 组合里缺失，自带一份."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    """ctypes.wintypes 没有 WNDCLASSW/WNDPROC，自带一份（缺失会导致托盘线程直接崩溃）."""

    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeout", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wt.HICON),
    ]


MENU_SHOW, MENU_EXIT = 4001, 4004
_TIPS = {
    "server": "InternetProxyPy — 代理服务端（网关）· TCP/UDP {port}",
    "client": "InternetProxyPy — 代理客户端 · 网关 {gw}:{port}",
}


def _asset_bases() -> list[Path]:
    """资源查找目录：PyInstaller 解包目录（_MEIPASS）→ 当前目录 → 源码根."""
    bases = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        bases.append(Path(bundle))
    bases += [Path.cwd(), Path(__file__).resolve().parents[2]]
    return bases


def _load_ico() -> wt.HICON:
    """加载 assets/cat.ico；找不到则用系统默认."""
    for base in _asset_bases():
        path = base / "assets" / "cat.ico"
        if path.is_file():
            hicon = user32.LoadImageW(
                None, str(path), 1, 64, 64, 0x00000010  # IMAGE_ICON | LR_LOADFROMFILE
            )
            if hicon:
                return hicon
    return user32.LoadIconW(None, IDI_APPLICATION)


class TrayIcon:
    """托盘宿主：独立窗口线程 + 消息泵，回调经 tk.after 派回主线程."""

    def __init__(self, tk, cfg, on_show, on_exit) -> None:
        self.tk = tk
        self.cfg = cfg
        self.on_show = on_show
        self.on_exit = on_exit
        self._hwnd = None
        self._hicon = None
        self._running = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._hicon = _load_ico()
        self._thread.start()
        self._running.wait(5)

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._running.clear()

    # ---- 后台线程：消息窗口 ----

    def _loop(self) -> None:
        cls_name = "InternetProxyTrayWnd"
        wnd_cls = WNDCLASSW()
        wnd_cls.lpszClassName = cls_name
        wnd_cls.lpfnWndProc = WNDPROC(self._wndproc)
        wnd_cls.hInstance = kernel32_kernel()
        atom = user32.RegisterClassW(ctypes.byref(wnd_cls))
        if not atom:
            log.warning("托盘窗口类注册失败 err=%s", ctypes.GetLastError())
            return
        self._hwnd = user32.CreateWindowExW(
            0, cls_name, "InternetProxyPy Tray", 0, 0, 0, 0, 0, None, None,
            wnd_cls.hInstance, None,
        )
        nid = self._get_nid()
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            log.warning("托盘图标注册失败 err=%s", ctypes.GetLastError())
        self._running.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if self._hicon:
            user32.DestroyIcon(self._hicon)

    def _get_nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self._hwnd
        nid.uID = TRAY_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._hicon
        try:
            if self.cfg.role == "client" and self.cfg.v2ray_enabled:
                nid.szTip = (
                    f"InternetProxyPy — 代理客户端 · VMess "
                    f"{self.cfg.v2ray_server}:{self.cfg.v2ray_port}"
                )
            else:
                nid.szTip = _TIPS.get(self.cfg.role, "InternetProxyPy").format(
                    gw=self.cfg.gateway, port=self.cfg.port
                )
        except Exception:
            nid.szTip = "InternetProxyPy"
        return nid

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_TRAY:
            if lparam == 0x0202:  # WM_LBUTTONUP：主窗口唤出
                self.tk.after(0, self.on_show)
            elif lparam == 0x0205:  # WM_RBUTTONUP：右键菜单
                self._popup_menu(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, MENU_SHOW, "显示窗口")
        user32.AppendMenuW(menu, 0, MENU_EXIT, "退出")
        user32.SetForegroundWindow(hwnd)
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        chosen = user32.TrackPopupMenu(
            menu, 0x0180,  # TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON
            pt.x, pt.y, 0, hwnd, None,
        )
        user32.DestroyMenu(menu)
        if chosen == MENU_SHOW:
            self.tk.after(0, self.on_show)
        elif chosen == MENU_EXIT:
            self.tk.after(0, self.on_exit)


def kernel32_kernel() -> int:
    return ctypes.windll.kernel32.GetModuleHandleW(None)
