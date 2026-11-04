"""Win32 辅助：AppUserModelID 与图标文件生成。

拖放不再依赖 Win32 消息/OLE 目标注册（在 WebView2 下不可靠），
改走 WebView2 原生拖放导航，由 app.py 的 loaded 事件识别文件夹路径。
"""

from __future__ import annotations

import ctypes
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

shell32 = ctypes.WinDLL("shell32", use_last_error=True)


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
    """设置 AppUserModelID，任务栏按应用名分组。"""
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:  # noqa: BLE001 - 老系统可能不支持
        logger.debug("设置 AppUserModelID 失败", exc_info=True)
