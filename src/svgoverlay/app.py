"""应用入口。

用法：
    svg-overlay [--folder PATH] [-v]

不带参数时启动空界面，可通过「打开文件夹」或直接把文件夹拖入窗口。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import webview

from svgoverlay import APP_NAME, APP_TITLE, VERSION
from svgoverlay.api import OverlayApi
from svgoverlay.resources import assets_dir, index_html
from svgoverlay.win32ui import (
    FileDropTarget,
    disable_webview_drop,
    make_ico,
    set_app_id,
    window_handle,
)

logger = logging.getLogger(__name__)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=__doc__)
    parser.add_argument("--folder", type=Path, default=None,
                        help="启动时载入的文件夹（也可拖入或从界面选择）")
    parser.add_argument("-v", "--verbose", action="store_true", help="开启调试日志与开发者工具")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api = OverlayApi()
    api._initial_folder = str(args.folder) if args.folder else ""
    icon = make_ico(assets_dir() / "overlay_icon.png")
    window = webview.create_window(
        APP_TITLE,
        url=str(index_html()),
        js_api=api,
        width=1200,
        height=780,
        min_size=(980, 640),
        background_color="#008080",
    )
    api._window = window

    drop_target: FileDropTarget | None = None

    def on_shown() -> None:
        nonlocal drop_target
        try:
            form = window.native
            set_app_id(APP_NAME)
            if disable_webview_drop(form):
                drop_target = FileDropTarget(window_handle(form), api.handle_drop)
                drop_target.install()
        except Exception:  # noqa: BLE001
            logger.exception("窗口初始化（拖放）失败")

    def on_closing() -> None:
        if drop_target is not None:
            drop_target.uninstall()

    window.events.shown += on_shown
    window.events.closing += on_closing

    logger.info("%s %s 启动", APP_NAME, VERSION)
    webview.start(icon=str(icon) if icon else None, debug=args.verbose)


if __name__ == "__main__":
    main()
