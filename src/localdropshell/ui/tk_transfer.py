"""文件传输标签页 — 三栏布局 (本地 / 按钮 / 远程) + 进度 + 日志.

依赖: tkinter / tkinter.ttk / pathlib / os / logging / asyncio /
      threading / aiohttp / queue.

线程模型:
    * 所有 aiohttp 调用放到独立后台线程, 用 asyncio.new_event_loop() +
      run_until_complete 执行, 结果通过 queue.Queue 回传.
    * Tk 主线程通过 root.after 轮询 queue, 拿到结果后更新 widget.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
import tkinter.ttk as ttk

from localdropshell.settings import DEFAULT_VNC_PORT, format_target, parse_target
from localdropshell.ui.tk_drop import enable_drop_dnd


logger = logging.getLogger("localdropshell.ui.tk_transfer")


# ---------------------------------------------------------------------------
# 通用格式化 / 远程目标参数
# ---------------------------------------------------------------------------

def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / (1024 ** 2):.1f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def _format_mtime(ts: float) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return ""


def _target_fields(target: dict[str, Any] | None) -> dict[str, Any]:
    """把当前目标转换成请求里要带的 host/port；未连接时返回空 dict."""
    if not target or not target.get("connected"):
        return {}
    host = str(target.get("host") or "").strip()
    if not host:
        return {}
    try:
        port = int(target.get("port") or DEFAULT_VNC_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_VNC_PORT
    return {"host": host, "port": port}


# ---------------------------------------------------------------------------
# Win95 配色 (重复引入, 避免循环依赖 — tk_drop 不关心这些)
# ---------------------------------------------------------------------------

from localdropshell.ui.tk_main import Win95_COLORS, Win95_FONT, Win95_FONT_BOLD  # noqa: E402


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class FileItem:
    """本地或远程文件条目."""
    path: str
    size: int = 0
    mtime: float = 0.0
    status: str = "就绪"  # 就绪 / 传输中 / 完成 / 失败

    def label(self) -> str:
        return os.path.basename(self.path) or self.path


# ---------------------------------------------------------------------------
# 后台线程 HTTP 助手
# ---------------------------------------------------------------------------

@dataclass
class _HttpRequest:
    kind: str  # "upload" / "download" / "list_remote" / "probe"
    payload: dict = field(default_factory=dict)
    result_queue: queue.Queue = field(default_factory=queue.Queue)


def _http_worker(base_url: str, req: _HttpRequest) -> None:
    """在后台线程执行一次 aiohttp 请求, 把结果塞回 result_queue."""

    async def _run() -> None:
        out: dict = {"ok": False, "data": None, "error": None}
        try:
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                if req.kind == "probe":
                    async with sess.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        out["ok"] = resp.status == 200
                        out["data"] = await resp.json() if out["ok"] else None

                elif req.kind == "list_remote":
                    async with sess.post(
                        f"{base_url}/rfb/file/list",
                        json=req.payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        out["ok"] = resp.status == 200
                        out["data"] = await resp.json() if out["ok"] else await resp.text()

                elif req.kind == "upload":
                    local_path: str = req.payload["local_path"]
                    remote_dir: str = req.payload.get("remote_dir", "/")
                    file_size = os.path.getsize(local_path)
                    out["data"] = {"size": file_size}

                    with open(local_path, "rb") as fh:
                        form = aiohttp.FormData()
                        form.add_field("path", os.path.basename(local_path))
                        form.add_field("dir", remote_dir)
                        host = str(req.payload.get("host") or "")
                        if host:
                            form.add_field("host", host)
                            form.add_field("port", str(req.payload.get("port") or DEFAULT_VNC_PORT))
                        form.add_field("file", fh, filename=os.path.basename(local_path))

                        async with sess.post(
                            f"{base_url}/upload",
                            data=form,
                            timeout=aiohttp.ClientTimeout(total=300),
                        ) as resp:
                            out["ok"] = resp.status == 200
                            out["data"] = await resp.json() if out["ok"] else await resp.text()

                elif req.kind == "download":
                    remote_path: str = req.payload["remote_path"]
                    local_dir: str = req.payload.get("local_dir", str(Path.home()))
                    out["data"] = {"local_dir": local_dir}

                    body = {"path": remote_path}
                    body.update(_target_fields(req.payload))

                    async with sess.post(
                        f"{base_url}/download",
                        json=body,
                        timeout=aiohttp.ClientTimeout(total=300),
                    ) as resp:
                        if resp.status == 200:
                            fname = os.path.basename(remote_path)
                            save_to = os.path.join(local_dir, fname)
                            Path(local_dir).mkdir(parents=True, exist_ok=True)
                            with open(save_to, "wb") as fh:
                                async for chunk in resp.content.iter_chunked(64 * 1024):
                                    fh.write(chunk)
                            out["ok"] = True
                            out["data"] = {"saved_to": save_to}
                        else:
                            out["ok"] = False
                            out["data"] = await resp.text()
        except Exception as exc:
            out["error"] = str(exc)
        finally:
            req.result_queue.put(out)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def _run_http(base_url: str, kind: str, payload: dict) -> dict:
    """同步包装 — 调用方拿到 dict 结果."""
    req = _HttpRequest(kind=kind, payload=payload)
    threading.Thread(target=_http_worker, args=(base_url, req), daemon=True).start()
    # 等待最多 10 分钟
    try:
        return req.result_queue.get(timeout=600)
    except queue.Empty:
        return {"ok": False, "data": None, "error": "HTTP 请求超时"}


# ---------------------------------------------------------------------------
# 构建三栏 Tab
# ---------------------------------------------------------------------------

def build_transfer_tab(
    parent: tk.Frame,
    base_url: str,
    initial_folder: str | None = None,
) -> dict:
    """在 parent 内构造完整的文件传输 UI.

    Returns
    -------
    dict
        {"root", "add_paths", "log", "set_progress", "set_target", "get_target"}
        ``set_target(host, port, path)`` 供收藏夹快捷按钮填入远程地址并连接。
    """

    root = tk.Frame(parent, bg=Win95_COLORS["chrome"])
    root.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    # ========= 顶部: 远程地址栏 =========
    initial_host = "127.0.0.1"
    target: dict[str, Any] = {
        "host": initial_host,
        "port": DEFAULT_VNC_PORT,
        "dir": "/",
        "connected": False,  # False 表示沿用服务端本机探测到的 VNC Server
    }

    addr_bar = tk.Frame(root, bg=Win95_COLORS["chrome"])
    addr_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))

    tk.Label(addr_bar, text="远程地址:", bg=Win95_COLORS["chrome"],
             fg=Win95_COLORS["ink"], font=Win95_FONT_BOLD).pack(side=tk.LEFT, padx=(2, 4))

    addr_var = tk.StringVar(value=initial_host)
    addr_entry = tk.Entry(addr_bar, textvariable=addr_var, width=20, font=Win95_FONT,
                          bd=2, relief=tk.SUNKEN)
    addr_entry.pack(side=tk.LEFT, padx=(0, 6))

    tk.Label(addr_bar, text="远程目录:", bg=Win95_COLORS["chrome"],
             fg=Win95_COLORS["ink"], font=Win95_FONT_BOLD).pack(side=tk.LEFT, padx=(6, 4))

    dir_var = tk.StringVar(value="/")
    dir_entry = tk.Entry(addr_bar, textvariable=dir_var, width=16, font=Win95_FONT,
                         bd=2, relief=tk.SUNKEN)
    dir_entry.pack(side=tk.LEFT, padx=(0, 6))

    small_btn_style = {
        "font": Win95_FONT,
        "bg": Win95_COLORS["chrome"],
        "fg": Win95_COLORS["ink"],
        "activebackground": Win95_COLORS["chrome_light"],
        "activeforeground": Win95_COLORS["ink"],
        "bd": 2,
        "relief": tk.RAISED,
        "padx": 8,
        "pady": 2,
    }

    connect_btn = tk.Button(addr_bar, text="连接", width=8, **small_btn_style)
    connect_btn.pack(side=tk.LEFT, padx=2)

    refresh_btn = tk.Button(addr_bar, text="刷新列表", **small_btn_style)
    refresh_btn.pack(side=tk.LEFT, padx=2)

    status_lbl = tk.Label(
        addr_bar,
        text="当前目标: 服务端本机 VNC（未连接远程地址）",
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=Win95_FONT,
    )
    status_lbl.pack(side=tk.LEFT, padx=(10, 2))

    # ========= 顶部 Paned 三栏 =========
    paned = tk.PanedWindow(
        root,
        orient=tk.HORIZONTAL,
        bg=Win95_COLORS["chrome"],
        sashwidth=6,
        bd=0,
        relief=tk.FLAT,
    )
    paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # --- 左: 本地 Treeview ---
    left_frame = tk.Frame(paned, bg=Win95_COLORS["chrome"])
    paned.add(left_frame, minsize=260)

    tk.Label(left_frame, text="📁 本地文件 (支持拖放)",
             bg=Win95_COLORS["chrome"], fg=Win95_COLORS["ink"],
             font=Win95_FONT_BOLD).pack(anchor="w", padx=4, pady=(2, 0))

    local_tree = ttk.Treeview(
        left_frame,
        columns=("path", "size", "mtime", "status"),
        show="headings",
        selectmode="extended",
        style="Win95.Treeview",
    )
    local_tree.heading("path", text="文件名")
    local_tree.heading("size", text="大小")
    local_tree.heading("mtime", text="修改时间")
    local_tree.heading("status", text="状态")
    local_tree.column("path", width=180, anchor="w")
    local_tree.column("size", width=70, anchor="e")
    local_tree.column("mtime", width=120, anchor="center")
    local_tree.column("status", width=70, anchor="center")
    local_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    local_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=local_tree.yview)
    local_tree.configure(yscrollcommand=local_scroll.set)
    local_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # 启用拖放
    enable_drop_dnd(local_tree, lambda paths: _on_local_dropped(local_tree, paths, _item_store["local"]))

    # --- 中: 按钮区 ---
    mid_frame = tk.Frame(paned, bg=Win95_COLORS["chrome"], width=120)
    paned.add(mid_frame, minsize=100)

    tk.Label(mid_frame, text="传输操作",
             bg=Win95_COLORS["chrome"], fg=Win95_COLORS["ink"],
             font=Win95_FONT_BOLD).pack(anchor="w", padx=4, pady=(2, 6))

    btn_style = {
        "font": Win95_FONT,
        "bg": Win95_COLORS["chrome"],
        "fg": Win95_COLORS["ink"],
        "activebackground": Win95_COLORS["chrome_light"],
        "activeforeground": Win95_COLORS["ink"],
        "bd": 2,
        "relief": tk.RAISED,
        "padx": 6,
        "pady": 3,
    }

    tk.Button(mid_frame, text="→ 上传选中", **btn_style,
              command=lambda: _do_upload(base_url, local_tree, remote_tree, _item_store, log, set_progress, target)).pack(fill=tk.X, padx=4, pady=2)
    tk.Button(mid_frame, text="← 下载选中", **btn_style,
              command=lambda: _do_download(base_url, local_tree, remote_tree, _item_store, log, set_progress, target)).pack(fill=tk.X, padx=4, pady=2)
    tk.Button(mid_frame, text="批量生成 (递归)", **btn_style,
              command=lambda: _do_batch(base_url, local_tree, remote_tree, _item_store, log, set_progress, target)).pack(fill=tk.X, padx=4, pady=2)
    tk.Button(mid_frame, text="刷新远程列表", **btn_style,
              command=lambda: _refresh_remote(base_url, remote_tree, _item_store, log, target)).pack(fill=tk.X, padx=4, pady=2)
    tk.Button(mid_frame, text="清除本地列表", **btn_style,
              command=lambda: _clear_tree(local_tree, _item_store["local"])).pack(fill=tk.X, padx=4, pady=2)

    # --- 右: 远程 Treeview ---
    right_frame = tk.Frame(paned, bg=Win95_COLORS["chrome"])
    paned.add(right_frame, minsize=260)

    tk.Label(right_frame, text="🖥 远程文件 (双击打开 / 右键刷新)",
             bg=Win95_COLORS["chrome"], fg=Win95_COLORS["ink"],
             font=Win95_FONT_BOLD).pack(anchor="w", padx=4, pady=(2, 0))

    remote_tree = ttk.Treeview(
        right_frame,
        columns=("path", "size", "mtime", "status"),
        show="headings",
        selectmode="extended",
        style="Win95.Treeview",
    )
    for col, label, w in [("path", "文件名", 180), ("size", "大小", 70),
                          ("mtime", "修改时间", 120), ("status", "状态", 70)]:
        remote_tree.heading(col, text=label)
        remote_tree.column(col, width=w, anchor="w" if col == "path" else "center")
    remote_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    remote_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=remote_tree.yview)
    remote_tree.configure(yscrollcommand=remote_scroll.set)
    remote_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # 双击 → 在远程端打开
    remote_tree.bind(
        "<Double-1>",
        lambda _e: _open_remote_double_click(base_url, remote_tree, log),
    )
    # 右键 → 刷新
    remote_menu = tk.Menu(root, tearoff=0)
    remote_menu.add_command(label="刷新列表",
                            command=lambda: _refresh_remote(base_url, remote_tree, _item_store, log, target))
    remote_tree.bind("<Button-3>", lambda e: remote_menu.tk_popup(e.x_root, e.y_root))

    # ========= 底部: 进度条 + 日志 =========
    bottom = tk.Frame(root, bg=Win95_COLORS["chrome"])
    bottom.pack(side=tk.BOTTOM, fill=tk.X)

    progress = ttk.Progressbar(
        bottom,
        orient=tk.HORIZONTAL,
        mode="determinate",
        maximum=100,
        value=0,
        style="Win95.Horizontal.TProgressbar",
    )
    progress.pack(fill=tk.X, padx=4, pady=(4, 2))

    log_frame = tk.Frame(root, bg=Win95_COLORS["chrome"])
    log_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False)

    log_text = tk.Text(
        log_frame,
        height=6,
        bg="#ffffff",
        fg=Win95_COLORS["ink"],
        font=("Consolas", 9, "normal"),
        state=tk.DISABLED,
        wrap=tk.WORD,
    )
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
    log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=log_text.yview)
    log_text.configure(yscrollcommand=log_scroll.set)
    log_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 4))

    # ========= item_store: 本地 / 远程 两个 id→FileItem 映射 =========
    _item_store: dict[str, dict[str, FileItem]] = {
        "local": {},
        "remote": {},
    }

    # ========= 工具函数 =========
    def _add_paths(paths: list[str]) -> None:
        """把绝对路径追加到本地列表."""
        for raw in paths:
            try:
                p = Path(raw)
                if p.is_dir():
                    # 递归扫描, 只加叶子文件
                    for f in p.rglob("*"):
                        if f.is_file():
                            _add_single(str(f))
                elif p.is_file():
                    _add_single(str(p))
                else:
                    # 不存在的路径也要提示
                    log(f"⚠ 跳过 (不存在): {raw}")
            except Exception as exc:
                log(f"⚠ 添加路径失败 {raw!r}: {exc}")

    def _add_single(abs_path: str) -> None:
        try:
            if abs_path in _item_store["local"]:
                return
            stat = os.stat(abs_path)
            item = FileItem(
                path=abs_path,
                size=stat.st_size,
                mtime=stat.st_mtime,
                status="就绪",
            )
            uid = local_tree.insert(
                "",
                tk.END,
                values=(item.label(), _format_size(item.size), _format_mtime(item.mtime), item.status),
            )
            _item_store["local"][uid] = item
        except Exception as exc:
            log(f"⚠ 读取 {abs_path!r} 失败: {exc}")

    def _log(msg: str) -> None:
        def _write() -> None:
            log_text.configure(state=tk.NORMAL)
            log_text.insert(tk.END, msg + "\n")
            log_text.see(tk.END)
            log_text.configure(state=tk.DISABLED)
        # log 可能从后台线程调用 —— 都用 root.after 转回主线程
        try:
            root.after(0, _write)
        except Exception:
            _write()

    def _set_progress(value: float, max_value: float = 100.0, label: str = "") -> None:
        def _apply() -> None:
            if max_value > 0:
                progress.configure(maximum=max_value, value=value)
            else:
                progress.configure(value=0)
            if label:
                _log(f"[{value}/{max_value}] {label}")
        try:
            root.after(0, _apply)
        except Exception:
            _apply()

    log = _log
    set_progress = _set_progress
    add_paths = _add_paths

    # ========= 远程地址栏动作 =========
    def _on_connect() -> None:
        try:
            t_host, t_port = parse_target(addr_var.get())
        except ValueError as exc:
            status_lbl.configure(text=f"⚠ {exc}", fg="#c00000")
            log(f"⚠ {exc}")
            return
        target["host"] = t_host
        target["port"] = t_port
        target["dir"] = dir_var.get().strip() or "/"
        target["connected"] = True
        addr_var.set(format_target(t_host, t_port))
        status_lbl.configure(
            text=f"当前目标: {format_target(t_host, t_port)}  目录 {target['dir']}",
            fg="#000080",
        )
        log(f"ℹ 已连接远程目标 {format_target(t_host, t_port)}，目录 {target['dir']}")
        _refresh_remote(base_url, remote_tree, _item_store, log, target)

    def _on_refresh() -> None:
        target["dir"] = dir_var.get().strip() or "/"
        _refresh_remote(base_url, remote_tree, _item_store, log, target)

    def _set_target(t_host: str, t_port: int | None = None, t_path: str | None = None) -> None:
        """供收藏夹快捷按钮调用：填入地址/目录并立即连接."""
        addr_var.set(format_target(t_host, int(t_port or DEFAULT_VNC_PORT)))
        if t_path:
            dir_var.set(t_path)
        _on_connect()

    connect_btn.configure(command=_on_connect)
    refresh_btn.configure(command=_on_refresh)
    addr_entry.bind("<Return>", lambda _e: _on_connect())
    dir_entry.bind("<Return>", lambda _e: _on_refresh())

    # 初始化目录
    if initial_folder:
        log(f"ℹ 初始目录: {initial_folder}")
        Path(initial_folder).mkdir(parents=True, exist_ok=True)

    # 启动后异步探测远程
    log(f"ℹ 文件传输标签页已挂载, base_url={base_url}")
    root.after(400, lambda: _refresh_remote(base_url, remote_tree, _item_store, log, target))

    return {
        "root": root,
        "add_paths": add_paths,
        "log": log,
        "set_progress": set_progress,
        "set_target": _set_target,
        "get_target": lambda: dict(target),
    }


# ---------------------------------------------------------------------------
# 各按钮动作实现
# ---------------------------------------------------------------------------

def _on_local_dropped(tree: ttk.Treeview, paths: list[str], store: dict[str, FileItem]) -> None:
    """DnD 回调 — 在主线程调用, 直接改 tree/state."""
    import os
    from pathlib import Path

    def _fmt_size(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        elif n < 1024 ** 2:
            return f"{n / 1024:.1f} KB"
        return f"{n / (1024 ** 2):.1f} MB"

    def _fmt_mtime(ts: float) -> str:
        import time
        if not ts:
            return ""
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        except Exception:
            return ""

    for raw in paths:
        try:
            p = Path(raw)
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        _insert_one(str(f))
            elif p.is_file():
                _insert_one(str(p))
        except Exception:
            continue

    def _insert_one(abs_path: str) -> None:
        if abs_path in store:
            return
        try:
            stat = os.stat(abs_path)
            uid = tree.insert(
                "", tk.END,
                values=(os.path.basename(abs_path), _fmt_size(stat.st_size),
                        _fmt_mtime(stat.st_mtime), "就绪"),
            )
            store[uid] = FileItem(path=abs_path, size=stat.st_size,
                                  mtime=stat.st_mtime, status="就绪")
        except Exception:
            pass


def _clear_tree(tree: ttk.Treeview, store: dict[str, FileItem]) -> None:
    for uid in list(tree.get_children("")):
        tree.delete(uid)
    store.clear()


def _refresh_remote(
    base_url: str,
    tree: ttk.Treeview,
    store: dict[str, FileItem],
    log: Callable[[str], None],
    target: dict[str, Any] | None = None,
) -> None:
    remote_dir = str((target or {}).get("dir") or "/")
    payload: dict[str, Any] = {"path": remote_dir}
    payload.update(_target_fields(target))

    def _worker() -> None:
        result = _run_http(base_url, "list_remote", payload)
        root = tree.winfo_toplevel()

        def _apply() -> None:
            for uid in list(tree.get_children("")):
                tree.delete(uid)
            store.clear()
            if not result.get("ok"):
                log(f"⚠ 远程列表获取失败: {result.get('error') or result.get('data')}")
                return
            data = result.get("data") or {}
            entries = data.get("entries") or data.get("items") or []
            base_dir = data.get("path") or remote_dir
            for it in entries:
                name = it.get("name") or str(it.get("path") or "").rsplit("/", 1)[-1]
                path = it.get("path") or posixpath.join(base_dir, name)
                size = int(it.get("size") or 0)
                mtime = float(it.get("mod_time", it.get("mtime", 0)) or 0)
                status = "目录" if it.get("is_dir") else "文件"
                uid = tree.insert(
                    "", tk.END,
                    values=(name, _format_size(size), _format_mtime(mtime), status),
                )
                store[uid] = FileItem(path=path, size=size, mtime=mtime, status=status)
            target_text = (result.get("data") or {}).get("target")
            suffix = f" @ {target_text}" if target_text else ""
            log(f"ℹ 远程列表刷新成功{suffix}, {len(entries)} 项")
        try:
            root.after(0, _apply)
        except Exception:
            _apply()

    log("ℹ 正在获取远程文件列表...")
    threading.Thread(target=_worker, daemon=True).start()


def _get_selected_items(tree: ttk.Treeview, store: dict[str, FileItem]) -> list[FileItem]:
    return [store[uid] for uid in tree.selection() if uid in store]


def _do_upload(
    base_url: str,
    local_tree: ttk.Treeview,
    remote_tree: ttk.Treeview,
    store: dict[str, dict[str, FileItem]],
    log: Callable[[str], None],
    set_progress: Callable[[float, float, str], None],
    target: dict[str, Any] | None = None,
) -> None:
    items = _get_selected_items(local_tree, store["local"])
    if not items:
        log("⚠ 请先在左侧选择要上传的本地文件")
        return
    remote_dir = str((target or {}).get("dir") or "/")

    def _worker() -> None:
        total = len(items)
        for idx, item in enumerate(items, 1):
            set_progress(idx, total, f"上传 {os.path.basename(item.path)}")
            payload: dict[str, Any] = {
                "local_path": item.path,
                "remote_dir": remote_dir,
            }
            payload.update(_target_fields(target))
            result = _run_http(base_url, "upload", payload)
            log(f"{'✅' if result.get('ok') else '❌'} 上传 {item.label()}: {result.get('data') or result.get('error')}")
        set_progress(total, total, "上传完成")
        # 刷新远程列表
        _refresh_remote(base_url, remote_tree, store["remote"], log, target)

    log(f"ℹ 开始上传 {len(items)} 个文件 → {remote_dir} ...")
    threading.Thread(target=_worker, daemon=True).start()


def _do_download(
    base_url: str,
    local_tree: ttk.Treeview,
    remote_tree: ttk.Treeview,
    store: dict[str, dict[str, FileItem]],
    log: Callable[[str], None],
    set_progress: Callable[[float, float, str], None],
    target: dict[str, Any] | None = None,
) -> None:
    items = _get_selected_items(remote_tree, store["remote"])
    if not items:
        log("⚠ 请先在右侧选择要下载的远程文件")
        return
    local_dir = os.path.expanduser("~/Downloads")

    def _worker() -> None:
        total = len(items)
        for idx, item in enumerate(items, 1):
            set_progress(idx, total, f"下载 {item.label()}")
            payload: dict[str, Any] = {
                "remote_path": item.path,
                "local_dir": local_dir,
            }
            payload.update(_target_fields(target))
            result = _run_http(base_url, "download", payload)
            log(f"{'✅' if result.get('ok') else '❌'} 下载 {item.label()}: {result.get('data') or result.get('error')}")
        set_progress(total, total, "下载完成")

    log(f"ℹ 开始下载 {len(items)} 个文件 → {local_dir}")
    threading.Thread(target=_worker, daemon=True).start()


def _do_batch(
    base_url: str,
    local_tree: ttk.Treeview,
    remote_tree: ttk.Treeview,
    store: dict[str, dict[str, FileItem]],
    log: Callable[[str], None],
    set_progress: Callable[[float, float, str], None],
    target: dict[str, Any] | None = None,
) -> None:
    """批量生成 — 递归扫描选中目录并逐个上传."""
    items = _get_selected_items(local_tree, store["local"])
    if not items:
        log("⚠ 请先在左侧选择要批量上传的本地条目")
        return
    remote_dir = str((target or {}).get("dir") or "/")

    # 展开目录里所有文件
    from pathlib import Path
    flat: list[str] = []
    for it in items:
        p = Path(it.path)
        if p.is_file():
            flat.append(str(p))
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    flat.append(str(f))

    log(f"ℹ 批量上传: 展开后共 {len(flat)} 个文件")

    def _worker() -> None:
        total = len(flat)
        for idx, abs_path in enumerate(flat, 1):
            set_progress(idx, total, f"批量上传 {os.path.basename(abs_path)}")
            payload: dict[str, Any] = {
                "local_path": abs_path,
                "remote_dir": remote_dir,
            }
            payload.update(_target_fields(target))
            result = _run_http(base_url, "upload", payload)
            ok = result.get("ok")
            label = os.path.basename(abs_path)
            if ok:
                log(f"✅ {label}")
            else:
                log(f"❌ {label}: {result.get('error') or result.get('data')}")
        set_progress(total, total, "批量上传完成")
        _refresh_remote(base_url, remote_tree, store["remote"], log, target)

    threading.Thread(target=_worker, daemon=True).start()


def _open_remote_double_click(
    base_url: str,
    tree: ttk.Treeview,
    store: dict[str, FileItem],
    log: Callable[[str], None],
) -> None:
    sel = tree.selection()
    if not sel:
        return
    item = store.get(sel[0])
    if not item:
        return
    log(f"ℹ 双击远程条目, 请求远程端打开: {item.path}")
    # TODO: server 端实现 /exec open <path> 或专用 handler
    log("⚠ 远程打开 API 待 server.py 实现")
