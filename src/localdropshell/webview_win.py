"""独立 pywebview 窗口进程.

pywebview 5.x 强制要求 ``webview.start()`` 在 **main thread** 调用
（``webview/__init__.py`` 中 ``threading.current_thread().name != 'MainThread'``
会直接抛 ``WebViewException``）。本项目的 Tk 主界面已占用 main thread，
所以 VNC / Shell 窗口改为在独立子进程里创建：

    开发环境: python -m localdropshell.webview_win <json>
    打包环境: LocalFileDropShell.exe --webview-window <json>

PyInstaller 会把 webview / pythonnet / clr_loader 整体收集进 exe
（见 build_exe.py），子进程就是 exe 自身，因此打包后行为一致。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("localdropshell.webview_win")

# 打包（--windowed）后无 stdout，日志写文件由调用方决定
WINDOW_ENTRY_FLAG = "--webview-window"

LOG_NAME = "LocalFileDropShell-webview.log"


def _setup_logging() -> None:
    """--windowed 子进程没有控制台：把日志落到 %TEMP%，便于现场排查."""
    try:
        logging.basicConfig(
            filename=str(Path(tempfile.gettempdir()) / LOG_NAME),
            level=logging.INFO,
            encoding="utf-8",
            force=True,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    except Exception:  # pragma: no cover — 日志不可用时不影响窗口
        pass


def run_from_payload(payload: dict[str, Any]) -> int:
    """在**当前进程的 main thread** 上创建窗口并进入 pywebview 事件循环."""
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.warning("pywebview 未安装/未被 PyInstaller 收集: %s", exc)
        return 2

    window = webview.create_window(
        str(payload.get("title") or "LocalFileDropShell"),
        url=payload.get("url"),
        html=payload.get("html"),
        width=int(payload.get("width") or 1000),
        height=int(payload.get("height") or 700),
        min_size=(640, 420),
        text_select=True,
    )
    logger.info("pywebview 窗口已创建: %s", window)
    webview.start()
    return 0


def run_from_argv(argv: list[str]) -> int:
    _setup_logging()
    if not argv:
        return 2
    try:
        payload = json.loads(argv[0])
    except (TypeError, ValueError) as exc:
        logger.warning("窗口参数解析失败: %s", exc)
        return 2
    if not isinstance(payload, dict):
        return 2
    return run_from_payload(payload)


def launch_window(
    url: str | None,
    title: str,
    width: int = 1000,
    height: int = 700,
    html: str | None = None,
) -> subprocess.Popen | None:
    """从 Tk 主进程拉起一个独立 pywebview 窗口进程.

    返回 Popen（便于调用方保留引用 / 等待），失败返回 None。
    """
    payload = json.dumps(
        {
            "url": url,
            "title": title,
            "width": width,
            "height": height,
            "html": html,
        },
        ensure_ascii=False,
    )

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, WINDOW_ENTRY_FLAG, payload]
    else:
        cmd = [sys.executable, "-m", "localdropshell.webview_win", payload]

    popen_kwargs: dict[str, Any] = {"close_fds": True}
    if sys.platform == "win32":
        # 隐藏控制台子进程一闪而过的黑框（开发态 python.exe 为控制台程序）
        si = subprocess.STARTUPINFO()
        si.dwFlags |= 1  # STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        popen_kwargs["startupinfo"] = si
        popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        logger.warning("启动 pywebview 子进程失败: %s", exc)
        return None
    logger.info("pywebview 子进程已启动 pid=%s", proc.pid)
    return proc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_from_argv(sys.argv[1:]))
