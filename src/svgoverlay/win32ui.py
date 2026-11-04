"""Win32 细节：拖入文件夹（WM_DROPFILES）与图标文件生成。

pywebview 的 icon 参数在 winforms 后端可用（WinForms.Form.Icon），
需要提供 .ico 文件；这里由 assets/overlay_icon.png 生成多尺寸 ico。
拖放：关闭 WebView2 自带的拖放接管，改由父窗口接收 WM_DROPFILES，
这样才能拿到完整的文件系统路径（浏览器端拿不到路径）。
"""

from __future__ import annotations

import ctypes
import logging
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.CallWindowProcW.restype = LRESULT
user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]

shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragAcceptFiles.restype = None
shell32.DragQueryFileW.restype = wintypes.UINT
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragFinish.restype = None
shell32.DragFinish.argtypes = [wintypes.HANDLE]


def _read_drop(hdrop: int) -> list[str]:
    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    paths: list[str] = []
    for index in range(count):
        length = shell32.DragQueryFileW(hdrop, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
        if buffer.value:
            paths.append(buffer.value)
    return paths


class FileDropTarget:
    """在指定窗口上接收 WM_DROPFILES，回调收到绝对路径列表。"""

    def __init__(self, hwnd: int, on_paths: Callable[[list[str]], None]) -> None:
        self._hwnd = hwnd
        self._on_paths = on_paths
        self._proc = WNDPROC(self._wndproc)
        self._old_proc: int | None = None

    def install(self) -> bool:
        self._old_proc = user32.SetWindowLongPtrW(
            self._hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p)
        )
        if not self._old_proc:
            logger.warning("窗口过程挂接失败：%s", ctypes.get_last_error())
            return False
        shell32.DragAcceptFiles(self._hwnd, True)
        return True

    def uninstall(self) -> None:
        shell32.DragAcceptFiles(self._hwnd, False)
        if self._old_proc:
            user32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, self._old_proc)
            self._old_proc = None

    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001
        if msg == WM_DROPFILES:
            try:
                paths = _read_drop(wparam)
            finally:
                shell32.DragFinish(wparam)
            if paths:
                try:
                    self._on_paths(paths)
                except Exception:  # noqa: BLE001
                    logger.exception("处理拖放失败")
            return 0
        if self._old_proc:
            return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)
        return 0


def make_ico(png: Path) -> Path | None:
    """把 logo png 生成多尺寸 ico（窗口图标用）。"""
    try:
        from PIL import Image

        target = Path(tempfile.gettempdir()) / "svgoverlay_app.ico"
        image = Image.open(png).convert("RGBA")
        image.save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                  (64, 64), (128, 128), (256, 256)])
        return target
    except Exception as exc:  # noqa: BLE001
        logger.warning("生成窗口图标失败：%s", exc)
        return None


def set_app_id(app_id: str) -> None:
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:  # noqa: BLE001 - 老系统可能不支持
        logger.debug("设置 AppUserModelID 失败", exc_info=True)


def window_handle(form) -> int:  # noqa: ANN001
    """WinForms Form.Handle 可能是 pythonnet IntPtr，统一转成 int。"""
    handle = form.Handle
    to_int64 = getattr(handle, "ToInt64", None)
    return int(to_int64()) if callable(to_int64) else int(handle)


def disable_webview_drop(form) -> bool:  # noqa: ANN001
    """关闭 WebView2 自带的拖放，让 WM_DROPFILES 走父窗口。"""
    try:
        form.webview.AllowExternalDrop = False
        return True
    except Exception:  # noqa: BLE001
        logger.warning("无法关闭 WebView2 内置拖放", exc_info=True)
        return False
