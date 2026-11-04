"""应用入口。

用法：
    svg-overlay [--folder PATH] [-v]

不带参数时启动空界面，可通过「打开文件夹」或直接把文件夹拖入窗口。

拖放方案：使用 WebView2 原生拖放（AllowExternalDrop 默认开启）。
把文件夹拖入窗口松手后，WebView2 会导航到该文件夹的 file:/// 目录列表页；
Python 侧通过 loaded 事件识别这次导航，从 URL 取出文件夹路径，
调用 handle_drop 载入扫描，然后 load_url 恢复主界面。
（Win32 WM_DROPFILES / 手动 OLE IDropTarget 方案在 WebView2 下不可靠，已废弃。）
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import webview

from svgoverlay import APP_NAME, APP_TITLE, VERSION
from svgoverlay.api import OverlayApi
from svgoverlay.resources import assets_dir, index_html
from svgoverlay.win32ui import make_ico, set_app_id

logger = logging.getLogger(__name__)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=__doc__)
    parser.add_argument("--folder", type=Path, default=None,
                        help="启动时载入的文件夹（也可拖入或从界面选择）")
    parser.add_argument("-v", "--verbose", action="store_true", help="开启调试日志与开发者工具")
    return parser.parse_args(argv)


def _path_from_file_url(url: str) -> Path | None:
    """从 file:// URL 取出文件夹路径；拖入的是文件时取其所在目录。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "file":
            return None
        path = Path(unquote(parsed.path))
    except Exception:  # noqa: BLE001
        return None
    if path.is_file():
        path = path.parent
    return path if path.is_dir() else None


def main(argv=None) -> None:
    args = parse_args(argv)
    # windowed exe 没有 stderr，打包环境下额外写文件日志便于诊断
    handlers = None
    if getattr(sys, "frozen", False):
        handlers = [logging.FileHandler(
            Path(tempfile.gettempdir()) / "SvgOverlayPy.log", mode="w", encoding="utf-8"
        )]
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )

    api = OverlayApi()
    api._initial_folder = str(args.folder) if args.folder else ""
    icon = make_ico(assets_dir() / "overlay_icon.png")
    index = index_html()
    window = webview.create_window(
        APP_TITLE,
        url=str(index),
        js_api=api,
        width=1200,
        height=780,
        min_size=(980, 640),
        background_color="#008080",
    )
    api._window = window

    def on_loaded() -> None:
        """识别“拖入文件夹/文件”造成的 file:// 导航：载入后恢复主界面。"""
        try:
            url = window.get_current_url() or ""
            if not url.startswith("file:"):
                return
            current = Path(unquote(urlparse(url).path))
            try:
                if current.samefile(index):
                    return  # 回到主界面自身
            except OSError:
                pass
            folder = _path_from_file_url(url)
            if folder is None:
                return
            logger.info("检测到拖放导航：%s", folder)
            api.handle_drop([str(folder)])
            window.load_url(str(index))  # 恢复主界面（重新触发 on_loaded，会走 samefile 分支忽略）
        except Exception:  # noqa: BLE001
            logger.exception("处理拖放导航失败")

    def on_shown() -> None:
        try:
            set_app_id(APP_NAME)
        except Exception:  # noqa: BLE001
            logger.exception("窗口初始化失败")

    window.events.shown += on_shown
    window.events.loaded += on_loaded

    logger.info("%s %s 启动", APP_NAME, VERSION)
    webview.start(icon=str(icon) if icon else None, debug=args.verbose)


if __name__ == "__main__":
    main()
