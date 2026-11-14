from __future__ import annotations

import os
import queue
import string
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

from .models import Host
from .rfbftp import FtpError, FtpSession

try:
    from tkinterdnd2 import DND_FILES
except ImportError:
    DND_FILES = None


def remote_join(parent: str, name: str) -> str:
    return (parent.rstrip("/") + "/" + name).replace("//", "/")


def remote_parent(path: str) -> str:
    if path == "/":
        return "/"
    parent = path.rstrip("/").rsplit("/", 1)[0]
    return parent or "/"


class FileTransferTab(ttk.Frame):
    def __init__(self, master: tk.Misc, host: Host, status: Callable[[str], None]) -> None:
        super().__init__(master)
        self.host = host
        self.set_status = status
        self.ftp: FtpSession | None = None
        self.local_path = host.settings.local_path or "/"
        self.remote_path = host.settings.remote_path or "/"
        self._jobs: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]] | None] = queue.Queue()
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._closed = False
        self._worker = threading.Thread(target=self._run, name=f"FTP-{host.address}", daemon=True)
        self._worker.start()
        self._build()
        self.after(100, self._poll)
        self.submit(self._connect)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=5)
        top.pack(fill=tk.X)
        ttk.Label(top, text=f"{self.host.display_name} · TightVNC RFB 文件传输").pack(side=tk.LEFT)
        ttk.Button(top, text="新建目录", command=self.mkdir).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="重命名", command=self.rename).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="删除", command=self.delete).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top, text="刷新", command=self.refresh).pack(side=tk.RIGHT, padx=2)

        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        local = ttk.Frame(panes)
        remote = ttk.Frame(panes)
        panes.add(local, weight=1)
        panes.add(remote, weight=1)

        self.local_var = tk.StringVar(value=self.local_path)
        self.remote_var = tk.StringVar(value=self.remote_path)
        self.local_tree = self._side(local, "本地文件（可拖放到右侧）", self.local_var, self.open_local, self.local_up)
        self.remote_tree = self._side(remote, "远程文件", self.remote_var, self.open_remote, self.remote_up)
        self.local_tree.bind("<Double-1>", lambda _e: self.open_local())
        self.remote_tree.bind("<Double-1>", lambda _e: self.open_remote())

        buttons = ttk.Frame(self, padding=5)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="上传所选 →", style="Accent.TButton", command=self.upload_selected).pack(side=tk.LEFT)
        ttk.Button(buttons, text="← 下载所选", style="Accent.TButton", command=self.download_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="上传文件…", command=self.upload_dialog).pack(side=tk.LEFT)
        ttk.Button(buttons, text="上传目录…", command=self.upload_directory_dialog).pack(side=tk.LEFT, padx=5)
        self.progress = ttk.Progressbar(buttons, mode="determinate")
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=10)

        if DND_FILES:
            self.remote_tree.drop_target_register(DND_FILES)
            self.remote_tree.dnd_bind("<<Drop>>", self._drop)
            self.local_tree.drop_target_register(DND_FILES)
            self.local_tree.dnd_bind("<<Drop>>", self._local_drop)

    def _side(self, parent: ttk.Frame, title: str, variable: tk.StringVar, opener: Callable[[], None], up: Callable[[], None]) -> ttk.Treeview:
        ttk.Label(parent, text=title).pack(anchor=tk.W)
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="上级", width=6, command=up).pack(side=tk.LEFT)
        entry = ttk.Entry(bar, textvariable=variable)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.bind("<Return>", lambda _e: opener())
        tree = ttk.Treeview(parent, columns=("size", "modified"), selectmode="extended")
        tree.heading("#0", text="名称")
        tree.heading("size", text="大小")
        tree.heading("modified", text="修改时间")
        tree.column("#0", width=280)
        tree.column("size", width=100, anchor=tk.E)
        tree.column("modified", width=145)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            callback, args = job
            try:
                callback(*args)
            except Exception as exc:
                self._events.put(("error", str(exc)))

    def submit(self, callback: Callable[..., Any], *args: Any) -> None:
        if not self._closed:
            self._jobs.put((callback, args))

    def _poll(self) -> None:
        if self._closed:
            return
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self.set_status(f"文件传输错误：{payload}")
                messagebox.showerror("文件传输", payload, parent=self)
            elif kind == "status":
                self.set_status(payload)
            elif kind == "progress":
                current, total = payload
                self.progress.configure(maximum=max(1, total), value=current)
            elif kind == "connected":
                self._fill_remote(payload)
            elif kind == "remote":
                self._fill_remote(payload)
            elif kind == "local":
                self._fill_local(payload)
            elif kind == "refresh":
                self.refresh()
        self.after(100, self._poll)

    def _connect(self) -> None:
        self._events.put(("status", "正在建立独立 RFB 文件传输会话…"))
        self.ftp = FtpSession((self.host.address, self.host.port), self.host.password)
        self._events.put(("status", f"文件传输已连接：{self.ftp.server_info.name}"))
        self._events.put(("connected", self.ftp.list_dir(self.remote_path)))
        self._events.put(("local", None))

    def refresh(self) -> None:
        self.local_path = self.local_var.get().strip() or "/"
        self.remote_path = self.remote_var.get().strip() or "/"
        if self.ftp:
            self.submit(lambda: self._events.put(("remote", self.ftp.list_dir(self.remote_path))))
        self._events.put(("local", None))

    def _fill_remote(self, entries: list[dict[str, Any]]) -> None:
        self.remote_tree.delete(*self.remote_tree.get_children())
        for entry in sorted(entries, key=lambda e: (not e["is_dir"], e["name"].lower())):
            self.remote_tree.insert("", tk.END, iid=remote_join(self.remote_path, entry["name"]), text=("📁 " if entry["is_dir"] else "📄 ") + entry["name"], values=(self._size(entry["size"]), self._date(entry["mod_time"])))

    def _fill_local(self, _payload: Any) -> None:
        self.local_tree.delete(*self.local_tree.get_children())
        if self.local_path == "/":
            for letter in string.ascii_uppercase:
                path = Path(f"{letter}:/")
                if path.exists():
                    self.local_tree.insert("", tk.END, iid=str(path), text=f"📁 {letter}:", values=("", ""))
            return
        root = Path(self.local_path)
        if not root.is_dir():
            self._events.put(("error", f"本地目录不存在：{root}"))
            return
        for path in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            stat = path.stat()
            self.local_tree.insert("", tk.END, iid=str(path), text=("📁 " if path.is_dir() else "📄 ") + path.name, values=(self._size(0 if path.is_dir() else stat.st_size), self._date(stat.st_mtime)))

    @staticmethod
    def _size(value: int) -> str:
        if value < 1024:
            return str(value)
        for unit in ("KB", "MB", "GB", "TB"):
            value /= 1024
            if value < 1024:
                return f"{value:.1f} {unit}"
        return f"{value:.1f} PB"

    @staticmethod
    def _date(value: float) -> str:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M") if value else ""

    def open_remote(self) -> None:
        selected = self.remote_tree.selection()
        if selected and not self.remote_tree.item(selected[0], "text").startswith("📁"):
            return
        path = selected[0] if selected else self.remote_var.get().strip()
        self.remote_path = path or "/"
        self.remote_var.set(self.remote_path)
        self.host.settings.remote_path = self.remote_path
        self.submit(lambda: self._events.put(("remote", self.ftp.list_dir(self.remote_path))))

    def remote_up(self) -> None:
        self.remote_path = remote_parent(self.remote_path)
        self.remote_var.set(self.remote_path)
        self.submit(lambda: self._events.put(("remote", self.ftp.list_dir(self.remote_path))))

    def open_local(self) -> None:
        selected = self.local_tree.selection()
        if selected and not self.local_tree.item(selected[0], "text").startswith("📁"):
            return
        path = selected[0] if selected else self.local_var.get().strip()
        self.local_path = path or "/"
        self.local_var.set(self.local_path)
        self.host.settings.local_path = self.local_path
        self._events.put(("local", None))

    def local_up(self) -> None:
        path = Path(self.local_path)
        self.local_path = "/" if path.parent == path else str(path.parent)
        self.local_var.set(self.local_path)
        self._events.put(("local", None))

    def _selected_local(self) -> list[str]:
        return list(self.local_tree.selection())

    def _selected_remote(self) -> list[str]:
        return list(self.remote_tree.selection())

    def upload_selected(self) -> None:
        for path in self._selected_local():
            self._upload_path(path)

    def upload_dialog(self) -> None:
        from tkinter import filedialog
        for path in filedialog.askopenfilenames(parent=self, title="选择上传文件"):
            self._upload_path(path)

    def upload_directory_dialog(self) -> None:
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self, title="选择上传目录")
        if path:
            self._upload_path(path)

    def _upload_path(self, path: str) -> None:
        self.submit(self._upload_worker, path)

    def _upload_worker(self, path: str) -> None:
        source = Path(path)
        target = remote_join(self.remote_path, source.name)
        if source.is_dir():
            try:
                self.ftp.mkdir(target)
            except FtpError:
                pass
            children = list(source.iterdir())
            for index, child in enumerate(children, 1):
                old = self.remote_path
                self.remote_path = target
                try:
                    self._upload_worker(str(child))
                finally:
                    self.remote_path = old
                self._events.put(("progress", (index, len(children))))
        else:
            total = source.stat().st_size
            self._events.put(("status", f"正在上传 {source.name}（{total} 字节）"))
            self.ftp.upload_file(str(source), target)
            self._events.put(("progress", (total, total)))
        self._events.put(("status", f"上传完成：{source.name}"))
        self._events.put(("refresh", None))

    def download_selected(self) -> None:
        for path in self._selected_remote():
            self.submit(self._download_worker, path)

    def _download_worker(self, path: str, target: Path | None = None) -> None:
        name = path.rsplit("/", 1)[-1]
        if target is None:
            local_root = Path(self.local_path) if self.local_path != "/" else Path.home() / "Downloads"
            local_root.mkdir(parents=True, exist_ok=True)
            target = local_root / name
        entries = self.ftp.list_dir(remote_parent(path))
        entry = next((e for e in entries if e["name"] == name), None)
        if entry and entry["is_dir"]:
            target.mkdir(parents=True, exist_ok=True)
            for child in self.ftp.list_dir(path):
                self._download_worker(remote_join(path, child["name"]), target / child["name"])
        else:
            self._events.put(("status", f"正在下载 {name}"))
            self.ftp.download_file(path, str(target))
        self._events.put(("status", f"下载完成：{target}"))
        self._events.put(("local", None))

    def mkdir(self) -> None:
        name = simpledialog.askstring("新建目录", "目录名：", parent=self)
        if name and "/" not in name and "\\" not in name:
            self.submit(self._mkdir_worker, name)

    def _mkdir_worker(self, name: str) -> None:
        self.ftp.mkdir(remote_join(self.remote_path, name))
        self._events.put(("refresh", None))

    def rename(self) -> None:
        selected = self._selected_remote()
        if len(selected) != 1:
            messagebox.showinfo("重命名", "请选择一个远程文件或目录", parent=self)
            return
        old = selected[0]
        name = simpledialog.askstring("重命名", "新名称：", initialvalue=old.rsplit("/", 1)[-1], parent=self)
        if name and "/" not in name and "\\" not in name:
            self.submit(lambda: (self.ftp.rename(old, remote_join(remote_parent(old), name)), self._events.put(("refresh", None))))

    def delete(self) -> None:
        selected = self._selected_remote()
        if selected and messagebox.askyesno("删除", f"确定删除所选的 {len(selected)} 项吗？", parent=self):
            for path in selected:
                self.submit(lambda p=path: (self.ftp.remove(p), self._events.put(("refresh", None))))

    def _drop(self, event: tk.Event) -> None:
        for path in self.tk.splitlist(event.data):
            self._upload_path(path)
        self.set_status(f"已加入 {len(self.tk.splitlist(event.data))} 个上传任务")

    def _local_drop(self, event: tk.Event) -> None:
        paths = self.tk.splitlist(event.data)
        if paths:
            path = Path(paths[0])
            self.local_path = str(path if path.is_dir() else path.parent)
            self.local_var.set(self.local_path)
            self._events.put(("local", None))

    def close(self) -> None:
        self._closed = True
        self._jobs.put(None)
        self.host.settings.local_path = self.local_path
        self.host.settings.remote_path = self.remote_path
        if self.ftp:
            self.ftp.close()
