"""Tk 根窗口 — Win95 风格主界面.

依赖: tkinter / tkinterdnd2 / pathlib / os / logging / threading / time /
      aiohttp / asyncio / sys.

这是 UI 层的顶层文件, 导入其他 4 个 UI 模块.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
import tkinter.ttk as ttk

# tkinterdnd2 — 注意 DND_FILES / TkinterDnD 的导入方式
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # pragma: no cover — 降级处理
    DND_FILES = None  # type: ignore[assignment]
    TkinterDnD = None  # type: ignore[assignment]


logger = logging.getLogger("localdropshell.ui.tk_main")


# ---------------------------------------------------------------------------
# Windows 95 配色常量 — 来自 assets/02-windows-95.css
# ---------------------------------------------------------------------------

Win95_COLORS: dict[str, str] = {
    "bg": "#008080",
    "chrome": "#c0c0c0",
    "chrome_light": "#dfdfdf",
    "chrome_darker": "#000000",
    "title": "#000080",
    "title_text": "#ffffff",
    "ink": "#000000",
}

# MS Sans Serif 在 Windows 11 上不可用, 用 Tahoma 模拟
Win95_FONT = ("Tahoma", 9, "normal")
Win95_FONT_BOLD = ("Tahoma", 9, "bold")


# ---------------------------------------------------------------------------
# Win95 样式应用
# ---------------------------------------------------------------------------

def apply_win95_style(root: tk.Tk) -> None:
    """把 Teal 底色 + 全局字体应用到 Tk 根窗口."""
    root.configure(bg=Win95_COLORS["bg"])

    # ttk.Style — 虽然 Notebook 被我们手动 Tab 按钮替代,
    # 但 Treeview / Progressbar / Entry 仍走 ttk
    try:
        style = ttk.Style(root)
        # Windows 上有 clam / vista / xpnative 等主题
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(
            "Win95.Treeview",
            background="#ffffff",
            foreground=Win95_COLORS["ink"],
            fieldbackground="#ffffff",
            rowheight=22,
            font=Win95_FONT,
        )
        style.configure(
            "Win95.Treeview.Heading",
            background=Win95_COLORS["chrome"],
            foreground=Win95_COLORS["ink"],
            font=Win95_FONT_BOLD,
        )
        style.map(
            "Win95.Treeview",
            background=[("selected", Win95_COLORS["title"])],
            foreground=[("selected", Win95_COLORS["title_text"])],
        )

        style.configure(
            "Win95.Horizontal.TProgressbar",
            troughcolor=Win95_COLORS["chrome"],
            background=Win95_COLORS["title"],
            bordercolor=Win95_COLORS["chrome_darker"],
            thickness=16,
        )
    except tk.TclError as exc:
        logger.debug("ttk 样式配置失败 (可忽略): %s", exc)


# ---------------------------------------------------------------------------
# 手动 Tab 按钮栏 — Win95 没有 Tab 条, 自定义按钮
# ---------------------------------------------------------------------------

def make_tab_button_bar(
    root: tk.Tk,
    on_tab_change: callable | None = None,
) -> tuple[tk.Frame, dict[str, tk.Frame]]:
    """创建 Win95 风格的手动 Tab 按钮栏 + 对应内容 Frame.

    Returns
    -------
    (bar_frame, {"transfer": frame, "shell": frame, "vnc": frame})
    """

    bar_frame = tk.Frame(root, bg=Win95_COLORS["chrome"])
    bar_frame.pack(side=tk.TOP, fill=tk.X)

    # 3 个内容 Frame — 先全部挂好, 通过 raise/lower 切换可见性
    content_frame = tk.Frame(root, bg=Win95_COLORS["chrome"])
    content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    tab_frames: dict[str, tk.Frame] = {
        "transfer": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
        "shell": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
        "vnc": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
    }
    for f in tab_frames.values():
        f.place(relx=0, rely=0, relwidth=1, relheight=1)

    # Tab 按钮
    tab_defs: list[tuple[str, str]] = [
        ("transfer", "📁 文件传输"),
        ("shell", "⌨ Shell CLI GUI"),
        ("vnc", "🖥 VNC 远程桌面"),
    ]

    active_key = {"value": "transfer"}  # 闭包可变性

    def _refresh_buttons() -> None:
        for key, btn in _buttons:
            if key == active_key["value"]:
                btn.configure(
                    bg=Win95_COLORS["title"],
                    fg=Win95_COLORS["title_text"],
                    activebackground=Win95_COLORS["title"],
                    activeforeground=Win95_COLORS["title_text"],
                    relief=tk.RIDGE,
                )
            else:
                btn.configure(
                    bg=Win95_COLORS["chrome"],
                    fg=Win95_COLORS["ink"],
                    activebackground=Win95_COLORS["chrome_light"],
                    activeforeground=Win95_COLORS["ink"],
                    relief=tk.RAISED,
                )

    def _switch_tab(key: str) -> None:
        active_key["value"] = key
        tab_frames[key].tkraise()
        _refresh_buttons()
        if on_tab_change is not None:
            try:
                on_tab_change(key)
            except Exception as exc:  # pragma: no cover
                logger.warning("on_tab_change 回调异常: %s", exc)

    _buttons: list[tuple[str, tk.Button]] = []
    for key, label in tab_defs:
        btn = tk.Button(
            bar_frame,
            text=label,
            font=Win95_FONT_BOLD,
            padx=10,
            pady=4,
            bd=2,
            relief=tk.RAISED,
            command=lambda k=key: _switch_tab(k),
        )
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        _buttons.append((key, btn))

    _refresh_buttons()
    tab_frames["transfer"].tkraise()

    return bar_frame, tab_frames


# ---------------------------------------------------------------------------
# 菜单构造
# ---------------------------------------------------------------------------

def _build_menus(root: tk.Tk, base_url: str, tab_frames: dict[str, tk.Frame]) -> None:
    menubar = tk.Menu(root, tearoff=0)
    root.config(menu=menubar)

    # ---- 文件 ----
    m_file = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="文件", menu=m_file)

    def _open_folder() -> None:
        target = os.environ.get("LOCAL_DROP_FOLDER") or str(Path.home())
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}"')
        except Exception as exc:  # pragma: no cover
            logger.warning("打开文件夹失败: %s", exc)

    m_file.add_command(label="打开默认文件夹", command=_open_folder)
    m_file.add_separator()
    m_file.add_command(label="退出", command=root.destroy)

    # ---- 传输 ----
    m_xfer = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="传输", menu=m_xfer)
    m_xfer.add_command(label="开始传输 (需在文件传输页操作)", state=tk.DISABLED)
    m_xfer.add_command(label="取消全部 (需在文件传输页操作)", state=tk.DISABLED)

    # ---- Shell ----
    m_shell = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="Shell", menu=m_shell)
    m_shell.add_command(
        label="打开 Shell CLI GUI 浏览器窗口",
        command=lambda: None,  # 真正的打开逻辑由 web_shell 模块负责
    )

    # ---- VNC ----
    m_vnc = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="VNC", menu=m_vnc)
    m_vnc.add_command(label="打开 VNC 远程桌面", command=lambda: None)
    m_vnc.add_command(label="探测已有 VNC Server", command=lambda: None)

    # ---- 帮助 ----
    m_help = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="帮助", menu=m_help)
    m_help.add_command(label="使用说明", command=lambda: None)
    m_help.add_command(
        label="关于",
        command=lambda: tk.messagebox.showinfo(
            "关于", "LocalFileDropShell — 工控机一体化远程运维\nv0.1.0"
        ) if hasattr(tk, "messagebox") else None,
    )


# ---------------------------------------------------------------------------
# detect_vnc — 异步探测, 放到后台线程
# ---------------------------------------------------------------------------

def _detect_vnc_background(base_url: str, on_result: callable) -> None:
    """调用 GET {base_url}/health, 取 vnc_detected 字段."""

    def _run() -> None:
        try:
            import aiohttp

            async def _probe() -> str:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status != 200:
                            return "health 返回非 200"
                        data = await resp.json()
                        vnc = data.get("vnc_detected") or data.get("vnc") or "unknown"
                        return str(vnc)

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_probe())
            finally:
                loop.close()
            # 切回主线程回调
            try:
                from tkinter import Misc  # type: ignore[attr-defined]

                # 最简方式 — 让回调自己 root.after
                on_result(result)
            except Exception as exc:
                logger.warning("detect_vnc 回调异常: %s", exc)
        except Exception as exc:
            logger.debug("VNC 探测失败: %s", exc)
            on_result(f"探测失败: {exc}")

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# 入口函数
# ---------------------------------------------------------------------------

def run_tk_gui(
    base_url: str = "http://127.0.0.1:10022",
    host: str | None = None,
    folder: str | None = None,
) -> None:
    """启动 LocalFileDropShell 的 Win95 风格 Tk 主窗口."""

    # --- 构造 root ---
    if TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:  # pragma: no cover — 降级
        logger.warning("tkinterdnd2 未安装, 使用普通 tk.Tk (无拖拽)")
        root = tk.Tk()

    root.title("LocalFileDropShell — 工控机一体化远程运维")
    root.geometry("1040x680")
    root.minsize(820, 520)
    apply_win95_style(root)

    # --- Tab 按钮栏 ---
    bar_frame, tab_frames = make_tab_button_bar(root)

    # --- 构造菜单 ---
    _build_menus(root, base_url, tab_frames)

    # --- 填充三个 Tab ---
    # 延迟 import, 只在需要时加载 —— 各 UI 模块自己管理依赖
    from localdropshell.ui.tk_transfer import build_transfer_tab
    from localdropshell.ui.web_shell import build_shell_tab
    from localdropshell.ui.web_vnc import build_vnc_tab

    build_transfer_tab(tab_frames["transfer"], base_url, host=host, initial_folder=folder)
    build_shell_tab(tab_frames["shell"], base_url)
    build_vnc_tab(tab_frames["vnc"], base_url)

    # --- 状态栏 ---
    status_frame = tk.Frame(root, bg=Win95_COLORS["chrome"], bd=1, relief=tk.SUNKEN)
    status_frame.pack(side=tk.BOTTOM, fill=tk.X)

    status_left = tk.Label(
        status_frame,
        text="就绪",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT,
        anchor="w",
        padx=8,
        pady=2,
    )
    status_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

    status_right = tk.Label(
        status_frame,
        text=f"base_url={base_url}  |  VNC=探测中...",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT,
        anchor="e",
        padx=8,
        pady=2,
    )
    status_right.pack(side=tk.RIGHT)

    def _update_vnc_status(msg: str) -> None:
        root.after(0, lambda: status_right.configure(
            text=f"base_url={base_url}  |  VNC={msg}"
        ))

    # 异步探测
    root.after(300, lambda: _detect_vnc_background(base_url, _update_vnc_status))

    # --- 启动 ---
    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("Tk mainloop 被中断")
