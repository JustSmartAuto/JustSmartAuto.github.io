"""Shell CLI GUI 标签页 — 打开 pywebview 独立浏览器窗口.

依赖: tkinter / pywebview (webview) / pathlib / logging / os.

简化方案: 不在 Tk Frame 里真正内嵌 WebView2 (容易卡在 HWND 嵌入),
而是放一个按钮, 点击后用 pywebview.create_window 拉起独立窗口.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import tkinter as tk

from localdropshell.ui.tk_main import Win95_COLORS, Win95_FONT, Win95_FONT_BOLD


logger = logging.getLogger("localdropshell.ui.web_shell")


# ---------------------------------------------------------------------------
# pywebview 全局事件循环 — 只启动一次
# ---------------------------------------------------------------------------

_started_event_loop = False
_webview_windows: list = []  # 防止 GC


def _ensure_webview_event_loop() -> None:
    global _started_event_loop
    if _started_event_loop:
        return
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("pywebview 未安装, Shell CLI GUI 不可用")
        return

    def _run() -> None:
        try:
            webview.start()
        except Exception as exc:
            logger.warning("webview.start 异常: %s", exc)
        finally:
            _started_event_loop = False

    import threading
    t = threading.Thread(target=_run, daemon=True, name="pywebview-loop")
    t.start()
    _started_event_loop = True


def open_shell_window(base_url: str) -> None:
    """拉起独立的 Shell CLI GUI pywebview 窗口."""
    _ensure_webview_event_loop()
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("pywebview 未安装")
        return

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Shell CLI GUI</title>
<style>
body {{ margin:0; background:#000; color:#0f0; font-family: Consolas, 'Courier New', monospace; padding: 14px; }}
h3 {{ color:#fff; }} pre {{ white-space: pre-wrap; }} code {{ background:#222; padding: 1px 5px; border-radius: 3px; }}
.btn {{ display:inline-block; margin:6px 4px; padding:6px 12px; background:#000080; color:#fff; border:1px solid #0af;
       cursor:pointer; font-family: Tahoma, sans-serif; border-radius: 3px; text-decoration:none; }}
.btn:hover {{ background:#0000aa; }}
</style></head><body>
<h3>Shell CLI GUI — LocalFileDropShell</h3>
<p>base_url = <code>{base_url}</code></p>
<p>服务端静态入口: <a href="{base_url}/shell/" style="color:#0af">{base_url}/shell/</a></p>
<p>后端执行端点: <code>POST {base_url}/exec</code></p>
<hr>
<p>xterm.js 前端待集成 (可打包内嵌). 当前可通过 CLI 交互或直接调用 HTTP /exec。</p>
<p style="color:#888;font-size:11px;">— LocalFileDropShell v0.1.0 —</p>
</body></html>"""

    try:
        w = webview.create_window(
            "Shell CLI GUI — LocalFileDropShell",
            html=html,
            url=f"{base_url}/shell/",  # server.py 里有 /shell/ 静态 handler
            width=960,
            height=620,
            min_size=(640, 420),
            text_select=True,
        )
        _webview_windows.append(w)
        logger.info("Shell CLI GUI 窗口已创建: %s", w)
    except Exception as exc:
        logger.warning("pywebview.create_window 失败: %s", exc)


# ---------------------------------------------------------------------------
# 构建 Shell Tab
# ---------------------------------------------------------------------------

def build_shell_tab(parent: tk.Frame, base_url: str) -> None:
    """在 parent 内构造 Shell CLI GUI 占位标签页.

    不在 Tk 里内嵌 WebView2, 放按钮打开独立 pywebview 窗口.
    """

    frame = tk.Frame(parent, bg=Win95_COLORS["chrome"])
    frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    tk.Label(
        frame,
        text="⌨ Shell CLI GUI",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=("Tahoma", 14, "bold"),
    ).pack(anchor="w", padx=12, pady=(14, 4))

    tk.Label(
        frame,
        text=(
            "Shell CLI GUI 基于 pywebview 独立浏览器窗口,\n"
            "加载服务端 /shell/ 静态入口, 底层由 server.py 的\n"
            "POST /exec 端点代理到 localdropshell.shell.ShellExecutor."
        ),
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT,
        justify=tk.LEFT,
    ).pack(anchor="w", padx=12, pady=4)

    btn_style = {
        "font": Win95_FONT_BOLD,
        "bg": Win95_COLORS["title"],
        "fg": Win95_COLORS["title_text"],
        "activebackground": Win95_COLORS["title"],
        "activeforeground": Win95_COLORS["title_text"],
        "bd": 2,
        "relief": tk.RAISED,
        "padx": 16,
        "pady": 10,
        "cursor": "hand2",
    }

    open_btn = tk.Button(
        frame,
        text="🖥  打开 Shell CLI GUI 浏览器窗口",
        command=lambda: open_shell_window(base_url),
        **btn_style,
    )
    open_btn.pack(padx=12, pady=10)

    # base_url 提示
    url_label = tk.Label(
        frame,
        text=f"base_url = {base_url}",
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=("Tahoma", 9, "bold"),
    )
    url_label.pack(anchor="w", padx=12, pady=4)

    # 依赖状态
    def _check_deps() -> None:
        try:
            import webview  # noqa: F401
            dep_ok = True
        except ImportError:
            dep_ok = False

        msg = "✅ pywebview 已安装" if dep_ok else "⚠ pywebview 未安装 — 请 pip install pywebview"
        color = "#008000" if dep_ok else "#c00000"
        dep_lbl = tk.Label(
            frame, text=msg, bg=Win95_COLORS["chrome"], fg=color, font=Win95_FONT,
        )
        dep_lbl.pack(anchor="w", padx=12, pady=(4, 10))

    frame.after(200, _check_deps)

    # 快速命令参考
    tip_frame = tk.LabelFrame(
        frame,
        text="快速参考",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT_BOLD,
        padx=8,
        pady=6,
    )
    tip_frame.pack(anchor="w", padx=12, pady=(6, 10), fill=tk.X)

    tips = [
        "• POST /exec   — 后端执行任意 Shell 命令",
        "• /shell/      — 服务端静态 HTML 入口 (若已部署 xterm.js)",
        "• GET /health  — 检查 ShellExecutor / VNC / 环境",
    ]
    for t in tips:
        tk.Label(
            tip_frame, text=t,
            bg=Win95_COLORS["chrome"], fg=Win95_COLORS["ink"],
            font=("Consolas", 9, "normal"), anchor="w",
        ).pack(fill=tk.X)
