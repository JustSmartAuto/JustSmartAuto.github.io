"""VNC 远程桌面标签页 — 打开 pywebview 独立浏览器窗口加载 noVNC.

依赖: tkinter / pywebview (webview) / pathlib / logging / aiohttp (探测).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import tkinter as tk

from localdropshell.settings import DEFAULT_VNC_PORT, format_target, parse_target
from localdropshell.ui.tk_main import Win95_COLORS, Win95_FONT, Win95_FONT_BOLD
from localdropshell.webview_win import launch_window


logger = logging.getLogger("localdropshell.ui.web_vnc")


def open_vnc_window(
    base_url: str,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """拉起独立的 VNC 远程桌面窗口（独立子进程，pywebview 在 main thread 启动）.

    host/port 为空时使用服务端本机探测结果。
    """
    if host:
        target_text = format_target(host, int(port or DEFAULT_VNC_PORT))
        target_url = f"{base_url}/vnc/vnc.html?host={host}&port={int(port or DEFAULT_VNC_PORT)}"
    else:
        target_text = "服务端本机 VNC"
        target_url = f"{base_url}/vnc/"

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>VNC 远程桌面</title>
<style>
body {{ margin:0; background:#000; color:#fff; font-family: Tahoma, sans-serif; padding: 14px; }}
h3 {{ color:#fff; font-size: 15px; }}
code {{ background:#222; color:#0f0; padding:1px 5px; border-radius:3px; font-family:Consolas,monospace; }}
a {{ color:#0f0; }}
.btn {{ display:inline-block; margin:6px 4px; padding:8px 14px; background:#000080; color:#fff;
       border:1px solid #0af; cursor:pointer; border-radius:3px; font-family:Tahoma,sans-serif;
       text-decoration:none; }}
.btn:hover {{ background:#0000aa; }}
</style></head><body>
<h3>🖥 VNC 远程桌面 — LocalFileDropShell</h3>
<p>目标 VNC = <code>{target_text}</code></p>
<p>base_url = <code>{base_url}</code></p>
<p>noVNC 前端入口: <a href="{target_url}">{target_url}</a></p>
<p>WebSocket 代理: <code>{base_url}/websockify</code></p>
<hr>
<p>noVNC.js 前端待集成 (打包时可通过 --with-vnc-bundle 内嵌 tvnserver + noVNC)。</p>
<p style="color:#888;font-size:11px;">— LocalFileDropShell v0.1.0 —</p>
</body></html>"""

    try:
        proc = launch_window(
            url=target_url,
            title=f"VNC 远程桌面 — {target_text}",
            width=1040,
            height=720,
            html=html,
        )
        if proc is None:
            logger.warning("VNC 远程桌面窗口启动失败 (pywebview 不可用)")
        else:
            logger.info("VNC 远程桌面子进程已启动 pid=%s -> %s", proc.pid, target_text)
    except Exception as exc:
        logger.warning("pywebview 窗口启动失败: %s", exc)


# ---------------------------------------------------------------------------
# 后台探测: GET /health → vnc_detected
# ---------------------------------------------------------------------------

def _probe_vnc(base_url: str, on_result) -> None:
    def _run() -> None:
        async def _probe() -> str:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(
                        f"{base_url}/health",
                        timeout=aiohttp.ClientTimeout(total=4),
                    ) as resp:
                        if resp.status != 200:
                            return f"health={resp.status}"
                        data = await resp.json()
                        vnc = data.get("vnc_detected") or data.get("vnc") or data.get("vnc_server") or "未检测到"
                        return str(vnc)
            except Exception as exc:
                return f"探测失败: {exc}"

        loop = asyncio.new_event_loop()
        try:
            msg = loop.run_until_complete(_probe())
        finally:
            loop.close()
        on_result(msg)

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# 构建 VNC Tab
# ---------------------------------------------------------------------------

def build_vnc_tab(parent: tk.Frame, base_url: str) -> None:
    """在 parent 内构造 VNC 远程桌面标签页."""

    frame = tk.Frame(parent, bg=Win95_COLORS["chrome"])
    frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    tk.Label(
        frame,
        text="🖥 VNC 远程桌面",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=("Tahoma", 14, "bold"),
    ).pack(anchor="w", padx=12, pady=(14, 4))

    tk.Label(
        frame,
        text=(
            "VNC 远程桌面基于 noVNC (HTML5 VNC 客户端), 通过服务端\n"
            "/websockify WebSocket 代理转发到工控机本地的 VNC Server\n"
            "(如 TightVNC / TigerVNC / RealVNC), 再由 pywebview 独立窗口加载。"
        ),
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT,
        justify=tk.LEFT,
    ).pack(anchor="w", padx=12, pady=4)

    # 远程地址输入（收藏夹“远程桌面”快捷按钮会回填到这里）
    addr_row = tk.Frame(frame, bg=Win95_COLORS["chrome"])
    addr_row.pack(anchor="w", padx=12, pady=(6, 0))

    tk.Label(
        addr_row,
        text="远程地址:",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT_BOLD,
    ).pack(side=tk.LEFT, padx=(0, 4))

    addr_var = tk.StringVar(value="")
    addr_entry = tk.Entry(
        addr_row,
        textvariable=addr_var,
        width=24,
        font=Win95_FONT,
        bd=2,
        relief=tk.SUNKEN,
    )
    addr_entry.pack(side=tk.LEFT)
    tk.Label(
        addr_row,
        text="（留空 = 服务端本机 VNC；端口省略按 5900）",
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=Win95_FONT,
    ).pack(side=tk.LEFT, padx=(8, 0))

    # 探测结果 Label — 延迟从 health 端点取 vnc_detected
    probe_lbl = tk.Label(
        frame,
        text="🔍 已探测 VNC Server: 探测中...",
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=("Tahoma", 9, "bold"),
    )
    probe_lbl.pack(anchor="w", padx=12, pady=(6, 4))

    def _apply_probe(msg: str) -> None:
        def _update() -> None:
            probe_lbl.configure(text=f"🔍 已探测 VNC Server: {msg}")
        try:
            frame.after(0, _update)
        except Exception:
            _update()

    # Tab 显示后 300ms 异步探测
    frame.after(300, lambda: _probe_vnc(base_url, _apply_probe))

    btn_style = {
        "font": Win95_FONT_BOLD,
        "bg": Win95_COLORS["title"],
        "fg": Win95_COLORS["title_text"],
        "activebackground": Win95_COLORS["title"],
        "activeforeground": Win95_COLORS["title_text"],
        "bd": 2,
        "relief": tk.RAISED,
        "padx": 18,
        "pady": 10,
        "cursor": "hand2",
    }

    def _open_current() -> None:
        raw = addr_var.get().strip()
        target_host: str | None = None
        target_port: int | None = None
        if raw:
            try:
                target_host, target_port = parse_target(raw)
            except ValueError as exc:
                probe_lbl.configure(text=f"⚠ 地址无效: {exc}", fg="#c00000")
                return
            addr_var.set(format_target(target_host, target_port))
        open_vnc_window(base_url, target_host, target_port)

    open_btn = tk.Button(
        frame,
        text="🖥  打开 VNC 远程桌面 (noVNC)",
        command=_open_current,
        **btn_style,
    )
    open_btn.pack(padx=12, pady=(10, 6))

    addr_entry.bind("<Return>", lambda _e: _open_current())

    def _set_target(target_host: str, target_port: int | None = None) -> None:
        """收藏夹“远程桌面”快捷按钮：回填地址（不自动打开窗口）."""
        addr_var.set(format_target(target_host, int(target_port or DEFAULT_VNC_PORT)))
        probe_lbl.configure(text=f"🖥 当前目标 VNC: {addr_var.get()}", fg="#000080")

    refresh_btn = tk.Button(
        frame,
        text="🔄 重新探测 VNC Server",
        command=lambda: _probe_vnc(base_url, _apply_probe),
        font=Win95_FONT,
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        activebackground=Win95_COLORS["chrome_light"],
        activeforeground=Win95_COLORS["ink"],
        bd=2, relief=tk.RAISED,
        padx=10, pady=4,
    )
    refresh_btn.pack(anchor="w", padx=12, pady=(0, 6))

    # base_url / 端点提示
    tk.Label(
        frame,
        text=f"base_url = {base_url}",
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=("Tahoma", 9, "bold"),
    ).pack(anchor="w", padx=12, pady=(4, 0))

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

    # 快速参考
    tip_frame = tk.LabelFrame(
        frame, text="快速参考",
        bg=Win95_COLORS["chrome"], fg=Win95_COLORS["ink"],
        font=Win95_FONT_BOLD, padx=8, pady=6,
    )
    tip_frame.pack(anchor="w", padx=12, pady=(6, 10), fill=tk.X)

    tips = [
        "• /vnc/         — 服务端静态 noVNC 入口",
        "• /websockify    — WebSocket → VNC 代理",
        "• GET /health    — 含 vnc_detected 字段",
        "• 典型 VNC 端口: 5900 (TightVNC) / 5901 (TigerVNC)",
    ]
    for t in tips:
        tk.Label(
            tip_frame, text=t,
            bg=Win95_COLORS["chrome"], fg=Win95_COLORS["ink"],
            font=("Consolas", 9, "normal"), anchor="w",
        ).pack(fill=tk.X)

    return {
        "root": frame,
        "set_target": _set_target,
        "open": _open_current,
    }
