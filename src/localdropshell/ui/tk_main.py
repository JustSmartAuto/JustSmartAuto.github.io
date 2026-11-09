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
) -> tuple[tk.Frame, dict[str, tk.Frame], callable]:
    """创建 Win95 风格的手动 Tab 按钮栏 + 对应内容 Frame.

    Returns
    -------
    (bar_frame, {"transfer": frame, "shell": frame, "vnc": frame, "favorites": frame}, switch_tab)
        第三个返回值可直接调用 ``switch_tab("transfer")`` 切页。
    """

    bar_frame = tk.Frame(root, bg=Win95_COLORS["chrome"])
    bar_frame.pack(side=tk.TOP, fill=tk.X)

    # 4 个内容 Frame — 先全部挂好, 通过 raise/lower 切换可见性
    content_frame = tk.Frame(root, bg=Win95_COLORS["chrome"])
    content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    tab_frames: dict[str, tk.Frame] = {
        "transfer": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
        "shell": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
        "vnc": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
        "favorites": tk.Frame(content_frame, bg=Win95_COLORS["bg"]),
    }
    for f in tab_frames.values():
        f.place(relx=0, rely=0, relwidth=1, relheight=1)

    # Tab 按钮
    tab_defs: list[tuple[str, str]] = [
        ("transfer", "📁 文件传输"),
        ("shell", "⌨ Shell CLI GUI"),
        ("vnc", "🖥 VNC 远程桌面"),
        ("favorites", "⭐ 收藏夹"),
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

    return bar_frame, tab_frames, _switch_tab


# ---------------------------------------------------------------------------
# 使用说明 / 关于 对话框
# ---------------------------------------------------------------------------

def _app_version() -> str:
    """优先取安装元数据版本；打包环境读不到 dist-info 时回退到包内常量."""
    try:
        from importlib.metadata import version

        return version("LocalFileDropShellPy")
    except Exception:
        try:
            from localdropshell import __version__

            return __version__
        except Exception:
            return "unknown"


def _center_on_parent(win: tk.Toplevel, root: tk.Tk, width: int, height: int) -> None:
    """把对话框居中偏上放在主窗口附近."""
    root.update_idletasks()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 3)
    win.geometry(f"{width}x{height}+{x}+{y}")


def _show_doc_window(
    root: tk.Tk,
    title: str,
    sections: list[tuple[str, str]],
    width: int = 780,
    height: int = 640,
) -> None:
    """Win95 风格只读文档窗口：可滚动、可选中复制、Esc 关闭."""
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=Win95_COLORS["chrome"])
    win.transient(root)
    win.minsize(600, 420)
    _center_on_parent(win, root, width, height)

    header = tk.Label(
        win,
        text=title,
        font=Win95_FONT_BOLD,
        anchor="w",
        padx=8,
        pady=4,
        bg=Win95_COLORS["title"],
        fg=Win95_COLORS["title_text"],
    )
    header.pack(side=tk.TOP, fill=tk.X)

    body = tk.Frame(win, bg=Win95_COLORS["chrome"], bd=2, relief=tk.SUNKEN)
    body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=6)

    scroll = tk.Scrollbar(body, orient=tk.VERTICAL, bg=Win95_COLORS["chrome"])
    text = tk.Text(
        body,
        wrap=tk.WORD,
        bd=0,
        padx=10,
        pady=8,
        bg="#ffffff",
        fg=Win95_COLORS["ink"],
        font=Win95_FONT,
        yscrollcommand=scroll.set,
    )
    scroll.configure(command=text.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    text.tag_configure(
        "h",
        font=Win95_FONT_BOLD,
        foreground="#000080",
        spacing1=10,
        spacing3=3,
    )
    for heading, content in sections:
        text.insert(tk.END, f"{heading}\n", "h")
        text.insert(tk.END, f"{content}\n")
    text.configure(state=tk.DISABLED)

    bar = tk.Frame(win, bg=Win95_COLORS["chrome"])
    bar.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 6))

    def _copy_all() -> None:
        root.clipboard_clear()
        root.clipboard_append("\n".join(f"{h}\n{c}" for h, c in sections))

    tk.Button(
        bar,
        text="复制内容",
        width=10,
        font=Win95_FONT,
        bd=2,
        relief=tk.RAISED,
        command=_copy_all,
    ).pack(side=tk.LEFT)
    tk.Button(
        bar,
        text="确定",
        width=10,
        font=Win95_FONT,
        bd=2,
        relief=tk.RAISED,
        default=tk.ACTIVE,
        command=win.destroy,
    ).pack(side=tk.RIGHT)

    win.bind("<Escape>", lambda _event: win.destroy())
    win.update_idletasks()
    win.wait_visibility()
    win.grab_set()
    win.focus_set()


def _usage_sections(base_url: str) -> list[tuple[str, str]]:
    """使用说明正文（与 README 的“CLI 三角色/文件传输/VNC 探测策略”保持一致）."""
    return [
        (
            "一、启动方式",
            "图形界面（默认角色 gui）：双击 LocalFileDropShell_*.exe，或运行 ldrop / local-drop-shell\n"
            "纯服务端（工控机后台常驻）：LocalFileDropShell.exe --role server --port 10022\n"
            "交互式客户端：LocalFileDropShell.exe --role client --url http://<工控机IP>:10022\n"
            "可选参数：--host / --port / --folder（gui 启动时载入本地目录）\n"
            "环境变量：HTTP_SHELL_HOST（默认 0.0.0.0）、HTTP_SHELL_PORT（默认 10022）",
        ),
        (
            "二、文件传输（文件传输页）",
            "1. 顶部“远程地址”填 ip[:端口]（端口省略按 5900），点“连接”或回车 → 切换到该远程 VNC 目标\n"
            "2. 顶部“远程目录”决定远程列表的起始路径（默认 /），回车或点“刷新列表”重新列举\n"
            "3. “协议”下拉显式选择传输协议（默认 RFB File；LocalSend / FTP / HTTP / RSYNC 为占位项），选择结果保存到 settings.json\n"
            "4. 把文件或文件夹拖到左侧“本地文件”列表（支持多选、递归目录），拖入后自动在资源管理器打开文件所属文件夹（文件夹则打开其本身）\n"
            "5. 两侧标题栏的“路径”输入框显示当前已打开路径，输入新路径后回车即可打开（本地用资源管理器打开，远程切换到该目录并刷新列表）\n"
            "6. 右侧“远程文件”双击目录进入；右键菜单“打开”同样可进入目录（仅目录可用）\n"
            "7. 远程列表上方“⬆ 上级”回退到父目录，面包屑显示当前路径并可点击任意层级直接跳转\n"
            "8. 在右侧“远程文件”选择目标文件后下载（右键菜单 / 中栏按钮）\n"
            "9. 中栏按钮：→ 上传选中 / ← 下载选中 / 批量生成 (递归) / 刷新远程列表 / 清除本地列表\n"
            "10. 上传走 RFB FTP 直写远程目标目录；未填远程地址时沿用服务端本机 VNC\n"
            "11. 大目录可用 rsync 增量同步（Adler-32 + MD5 双阶段块级 delta，失败自动回退全量）",
        ),
        (
            "三、Shell CLI GUI（Shell 页）",
            "点“打开 Shell CLI GUI 浏览器窗口”加载服务端 /shell/ 页面（xterm.js）\n"
            "命令通过 POST /exec 执行；解释器自动选择 pwsh → powershell → cmd\n"
            "输出编码自适应（UTF-8 优先，失败按 GBK/ACP 转码），默认超时 120 秒\n"
            f"也可直接在浏览器打开：{base_url}/shell/",
        ),
        (
            "四、VNC 远程桌面（VNC 页）",
            "点“打开 VNC 远程桌面 (noVNC)”加载 /vnc/ 页面；“重新探测 VNC Server”可刷新探测结果\n"
            "已探测到 TightVNC / RealVNC / UltraVNC（TCP 5900 等）→ 直接复用，零权限\n"
            "未探测到但已安装 TightVNC → 内嵌启动 tvnserver（需要管理员权限）\n"
            "未安装 TightVNC → 启动时用内嵌 MSI 静默安装（会弹一次 UAC 提权确认）\n"
            "已有其他 VNC Server 在运行时跳过安装，避免抢占 5900 端口",
        ),
        (
            "五、命令行客户端内置命令（--role client）",
            "exit | quit | q              退出\n"
            "clear | cls                  清屏\n"
            "help | ?                     显示帮助\n"
            "cd <dir>                     切换工作目录（本地或远程）\n"
            "pwd                          打印当前工作目录\n"
            "!<cmd>                       在本地执行 <cmd>\n"
            "upload <local> [remote]      上传文件到远程\n"
            "download <remote> [local]    从远程下载文件\n"
            "delete <path>                删除远程文件/目录\n"
            "rsync <local> [remote]       增量同步（push）\n"
            "rsync-pull <remote> [local]  增量同步（pull）\n"
            "rfb-list [dir]               列出远程目录（TightVNC 优先）\n"
            "rfb-upload <local> [remote]  RFB FTP 上传\n"
            "rfb-download <remote> [local] RFB FTP 下载\n"
            "其它任意输入                  作为远程 shell 命令执行",
        ),
        (
            "六、收藏夹（收藏夹页）",
            "1. 收藏项 = 备注名 + 远程地址（ip:端口，端口省略按 5900）+ 传输协议 + 文件传输默认路径 + 多个快捷路径\n"
            "2. 持久化到 D:/JustSmart/LocalFileDropShell/favorites.json（自动创建目录；不可写时回退 %APPDATA%/LocalFileDropShell）\n"
            "3. “📁 文件传输”快捷按钮 → 切到文件传输页，自动填入地址/协议并连接、列举默认路径\n"
            "4. 每条收藏下方的“📂 路径”按钮 → 直接切页并连接到该快捷路径（方便常用目录一键切换）\n"
            "5. “🖥 远程桌面”快捷按钮 → 切到 VNC 页并回填地址（点“打开 VNC 远程桌面”即连接）\n"
            "6. “✎ 载入”回填到上方表单；“📝 编辑”弹窗修改备注/地址/协议/默认路径/快捷路径；“🗑 删除”移除该条收藏",
        ),
        (
            "七、常见问题",
            "端口被占用：加 --port 8080，或设置 HTTP_SHELL_PORT\n"
            "连接失败：确认工控机以 server 角色启动，并在防火墙放行 10022\n"
            "UAC 提示：安装 TightVNC / 内嵌 VNC 需要提权；取消后仍可使用 HTTP 传输通道\n"
            "传输中断：重新选择同一文件即可按 offset 断点续传\n"
            "目标机无需安装本软件：探测到 TightVNC Server 时直接复用其 RFB FTP 通道",
        ),
    ]


def _about_sections(base_url: str, runtime: dict[str, str]) -> list[tuple[str, str]]:
    """关于页正文：版本、功能、运行环境、许可与参考项目."""
    import platform

    version = _app_version()
    frozen = bool(getattr(sys, "frozen", False))

    tightvnc_text = "未检测到"
    msi_text = "缺失（打包时 assets/ 需包含 MSI）"
    try:
        from localdropshell.vnc import (
            find_tightvnc_installer,
            is_tightvnc_installed,
            tightvnc_install_dir,
        )

        install_dir = tightvnc_install_dir()
        if install_dir is not None:
            tightvnc_text = str(install_dir)
        elif is_tightvnc_installed():
            tightvnc_text = "已安装（注册表已登记）"
        msi = find_tightvnc_installer()
        if msi is not None:
            msi_text = f"{msi.name}（{msi.stat().st_size // 1024} KB，已内嵌）"
    except Exception as exc:  # pragma: no cover — 仅防御
        tightvnc_text = f"检测失败：{exc}"

    return [
        (
            "LocalFileDropShell",
            f"版本        v{version}\n"
            "定位        面向工控机 / 调试机的一体化远程运维工具\n"
            "官网文档    仓库根目录 README.md（含协议与接口细节）\n"
            "开源许可    MIT License © 2026 JustSmartAuto",
        ),
        (
            "功能一览",
            "· 文件传送：拖放上传、文件夹递归、RFB 断点续传、rsync 增量同步\n"
            "· HTTP Shell：pwsh → powershell → cmd 自动检测，GBK/UTF-8 自适应\n"
            "· VNC 远程桌面：复用已有 VNC Server / 内嵌 tvnserver + noVNC\n"
            "· CLI 三角色：gui（默认）/ server / client，单文件 exe 即可部署\n"
            "· 弱网单工：DCF2 容器 + decimen v3 喷泉码，丢包环境下仍可完整还原",
        ),
        (
            "运行环境",
            f"Python      {platform.python_version()}\n"
            f"平台        {platform.platform()}\n"
            f"运行形态    {'打包 exe（单文件）' if frozen else '源码 / 虚拟环境'}\n"
            f"HTTP 服务   {base_url}\n"
            f"VNC 探测    {runtime.get('vnc', '未知')}\n"
            f"TightVNC    {tightvnc_text}\n"
            f"内嵌安装包  {msi_text}",
        ),
        (
            "参考项目",
            "TightVNC 2.8.87 / 2.8.90   RFB 文件传输扩展（FC000102~FC000119）\n"
            "AirFountainZulu           喷泉码 decimen v3 Wire 帧 + DCF2 容器\n"
            "diffimg                   打包脚本 / venv 重执行 / 混合 UI 结构\n"
            "http_shell_cli_vnc        HTTP Shell / rsync / RFB FTP / VNC 探测\n"
            "mosquitto_installer       内嵌安装包 + 提权静默安装的组织方式",
        ),
    ]



def _build_menus(
    root: tk.Tk,
    base_url: str,
    tab_frames: dict[str, tk.Frame],
    runtime: dict[str, str],
) -> None:
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

    def _open_drop_page() -> None:
        import webbrowser
        webbrowser.open(f"{base_url}/drop/")

    m_file.add_command(label="打开 Web 拖传页面", command=_open_drop_page)
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
    m_help.add_command(
        label="使用说明",
        command=lambda: _show_doc_window(
            root, "LocalFileDropShell 使用说明", _usage_sections(base_url)
        ),
    )
    m_help.add_command(
        label="关于 LocalFileDropShell",
        command=lambda: _show_doc_window(
            root, "关于 LocalFileDropShell", _about_sections(base_url, runtime)
        ),
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

    # 窗口/任务栏图标（打包后经 _MEIPASS/assets 解析，缺失时忽略）
    try:
        from localdropshell.vnc import bundle_asset_dir
        assets_dir = bundle_asset_dir()
        if assets_dir is not None:
            ico = assets_dir / "drop3.ico"
            if ico.is_file():
                root.iconbitmap(str(ico))
    except Exception:  # pragma: no cover — 图标失败不影响主流程
        logger.debug("设置窗口图标失败", exc_info=True)

    root.geometry("1040x680")
    root.minsize(820, 520)
    apply_win95_style(root)

    # --- Tab 按钮栏 ---
    bar_frame, tab_frames, switch_tab = make_tab_button_bar(root)

    # --- 构造菜单 ---
    runtime: dict[str, str] = {"vnc": "探测中..."}
    _build_menus(root, base_url, tab_frames, runtime)

    # --- 填充四个 Tab ---
    # 延迟 import, 只在需要时加载 —— 各 UI 模块自己管理依赖
    from localdropshell.ui.tk_transfer import build_transfer_tab
    from localdropshell.ui.web_shell import build_shell_tab
    from localdropshell.ui.web_vnc import build_vnc_tab
    from localdropshell.ui.tk_favorites import build_favorites_tab

    transfer_tab = build_transfer_tab(
        tab_frames["transfer"], base_url, initial_folder=folder
    )
    build_shell_tab(tab_frames["shell"], base_url)
    vnc_tab = build_vnc_tab(tab_frames["vnc"], base_url)

    def _open_transfer(fav, path: str | None = None) -> None:
        """收藏夹 → 文件传输：切页 + 填入地址/端口/协议/默认路径（或指定快捷路径）."""
        switch_tab("transfer")
        transfer_tab["set_target"](fav.host, fav.port, path or fav.path, fav.protocol)

    def _open_vnc(fav) -> None:
        """收藏夹 → 远程桌面：切页 + 填入地址/端口."""
        switch_tab("vnc")
        vnc_tab["set_target"](fav.host, fav.port)

    build_favorites_tab(tab_frames["favorites"], _open_transfer, _open_vnc)

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
        runtime["vnc"] = msg
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
