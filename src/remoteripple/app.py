from __future__ import annotations

import base64
import io
import json
import logging
import os
import queue
import threading
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import tkinter as tk
from tkinter import ttk

from tkinterdnd2 import TkinterDnD

from . import __version__, apply_icon
from .filetransfer import FileTransferTab
from .models import Host, Session, format_target, parse_target
from .storage import Store, config_dir
from .theme import apply_theme
from .viewer import ViewerTab

LOG = logging.getLogger(__name__)


class RemoteRippleApp:
    def __init__(self) -> None:
        self.store = Store()
        self.root = TkinterDnD.Tk()
        self.root.title(f"Remote Ripple Python {__version__}")
        self.root.geometry("1180x760")
        apply_icon(self.root)
        self.root.minsize(850, 560)
        self.events: queue.Queue[tuple[ViewerTab, str, object]] = queue.Queue()
        self.sessions: dict[str, tuple[ttk.Frame, ViewerTab, FileTransferTab, Host]] = {}
        self.status = tk.StringVar(value="就绪")
        self.search = tk.StringVar()
        self._style()
        self._menu()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.home = ttk.Frame(self.notebook)
        self.notebook.add(self.home, text="主页")
        self._build_home()
        ttk.Label(self.root, textvariable=self.status, style="Status.TLabel", anchor=tk.W).pack(fill=tk.X)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)
        self.root.after(1000, self._startup_update)

    def _style(self) -> None:
        apply_theme(self.root, bool(self.store.settings.get("night_mode")))

    def _menu(self) -> None:
        menu = tk.Menu(self.root)
        self.root.configure(menu=menu)
        file_menu = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="新建连接", command=self.new_connection)
        file_menu.add_command(label="关闭当前会话", command=self.close_current)
        file_menu.add_separator()
        file_menu.add_command(label="导出主机与会话", command=self.export_data)
        file_menu.add_command(label="打开日志目录", command=lambda: os.startfile(config_dir() / "logs"))
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        view = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="视图", menu=view)
        view.add_command(label="刷新最近会话", command=self.refresh_recent)
        view.add_command(label="设置", command=self.show_settings)
        help_menu = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="诊断日志", command=self.show_logs)
        help_menu.add_command(label="检查更新", command=lambda: self.check_updates(True))
        help_menu.add_command(label="Remote Ripple 网站", command=lambda: webbrowser.open("https://remoteripple.com"))
        help_menu.add_command(label="关于", command=lambda: messagebox.showinfo("关于", f"Remote Ripple Python {__version__}\nPython 3.13 + Tkinter + tkinterdnd2\n纯 Python RFB / TightVNC 文件传输", parent=self.root))

    def _build_home(self) -> None:
        hero = ttk.Frame(self.home, padding=16)
        hero.pack(fill=tk.X)
        ttk.Label(hero, text="Remote Ripple", font=("Segoe UI", 22, "bold")).pack(side=tk.LEFT)
        ttk.Button(hero, text="新建连接", style="Accent.TButton", command=self.new_connection).pack(side=tk.RIGHT)
        ttk.Button(hero, text="设置", command=self.show_settings).pack(side=tk.RIGHT, padx=6)

        connect = ttk.LabelFrame(self.home, text="快速连接（host、host:5900、host::5900 或 [IPv6]:5900）", padding=10)
        connect.pack(fill=tk.X, padx=16)
        self.quick = ttk.Entry(connect, font=("Segoe UI", 11))
        self.quick.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.quick.bind("<Return>", lambda _e: self.quick_connect())
        ttk.Button(connect, text="连接", style="Accent.TButton", command=self.quick_connect).pack(side=tk.LEFT, padx=6)

        search_bar = ttk.Frame(self.home, padding=(16, 12, 16, 4))
        search_bar.pack(fill=tk.X)
        ttk.Label(search_bar, text="搜索最近会话").pack(side=tk.LEFT)
        entry = ttk.Entry(search_bar, textvariable=self.search)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.search.trace_add("write", lambda *_: self.refresh_recent())
        ttk.Button(search_bar, text="删除所选", command=self.delete_selected).pack(side=tk.LEFT)

        columns = ("address", "created")
        self.recent = ttk.Treeview(self.home, columns=columns, selectmode="extended")
        self.recent.heading("#0", text="名称")
        self.recent.heading("address", text="地址")
        self.recent.heading("created", text="最近连接")
        self.recent.column("#0", width=300)
        self.recent.column("address", width=280)
        self.recent.column("created", width=220)
        self.recent.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        self.recent.bind("<Double-1>", lambda _e: self.connect_selected())
        self.recent.bind("<Return>", lambda _e: self.connect_selected())
        self.recent.bind("<Delete>", lambda _e: self.delete_selected())
        self.recent_menu = tk.Menu(self.recent, tearoff=False)
        self.recent_menu.add_command(label="连接", command=self.connect_selected)
        self.recent_menu.add_command(label="重命名主机", command=self.rename_selected)
        self.recent_menu.add_command(label="修改密码", command=self.password_selected)
        self.recent_menu.add_command(label="删除记录", command=self.delete_selected)
        self.recent.bind("<Button-3>", self._recent_context)
        self.refresh_recent()

    def _recent_context(self, event: tk.Event) -> None:
        item = self.recent.identify_row(event.y)
        if item and item not in self.recent.selection():
            self.recent.selection_set(item)
        self.recent_menu.tk_popup(event.x_root, event.y_root)

    def refresh_recent(self) -> None:
        self.recent.delete(*self.recent.get_children())
        hosts = {host.key: host for host in self.store.hosts()}
        query = self.search.get().casefold().strip()
        for session in self.store.sessions():
            host = hosts.get(session.key)
            name = host.display_name if host else session.host
            address = format_target(session.host, session.port)
            if query and query not in name.casefold() and query not in address.casefold():
                continue
            self.recent.insert("", tk.END, iid=session.key, text=name, values=(address, session.created.replace("T", " ")[:19]))

    def quick_connect(self) -> None:
        try:
            address, port = parse_target(self.quick.get())
        except ValueError as exc:
            messagebox.showerror("地址错误", str(exc), parent=self.root)
            return
        old = self.store.get_host(address, port)
        host = old or Host(address, port)
        self.open_session(host)

    def new_connection(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("新建远程连接")
        apply_icon(dialog)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        variables = {name: tk.StringVar() for name in ("address", "port", "alias", "password")}
        variables["port"].set("5900")
        encoding = tk.StringVar(value="自动")
        for row, (label, key, show) in enumerate((
            ("主机地址", "address", ""), ("端口", "port", ""),
            ("显示名称（可选）", "alias", ""), ("VNC 密码（可选）", "password", "*"),
        )):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=5)
            ttk.Entry(frame, width=36, textvariable=variables[key], show=show).grid(row=row, column=1, pady=5)
        ttk.Label(frame, text="画面编码").grid(row=4, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(frame, textvariable=encoding, values=("自动", "ZRLE", "Hextile", "原始"), state="readonly").grid(row=4, column=1, sticky=tk.EW)

        def connect() -> None:
            try:
                address, port = parse_target(f"{variables['address'].get()}:{variables['port'].get()}")
            except ValueError as exc:
                messagebox.showerror("地址错误", str(exc), parent=dialog)
                return
            host = self.store.get_host(address, port) or Host(address, port)
            host.alias = variables["alias"].get().strip()
            host.password = variables["password"].get()
            host.settings.encoding = encoding.get()
            dialog.destroy()
            self.open_session(host)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(buttons, text="连接", style="Accent.TButton", command=connect).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.LEFT)
        dialog.bind("<Return>", lambda _e: connect())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

    def connect_selected(self) -> None:
        for key in self.recent.selection():
            session = next((item for item in self.store.sessions() if item.key == key), None)
            if session:
                host = self.store.get_host(session.host, session.port) or Host(session.host, session.port, password=session.password)
                self.open_session(host)

    def delete_selected(self) -> None:
        keys = set(self.recent.selection())
        if keys and messagebox.askyesno("删除记录", f"删除 {len(keys)} 条最近会话？", parent=self.root):
            self.store.remove_sessions(keys)
            self.refresh_recent()

    def rename_selected(self) -> None:
        selected = self.recent.selection()
        if len(selected) != 1:
            return
        key = selected[0]
        session = next((s for s in self.store.sessions() if s.key == key), None)
        if not session:
            return
        host = self.store.get_host(session.host, session.port) or Host(session.host, session.port)
        name = simpledialog.askstring("主机别名", "显示名称：", initialvalue=host.alias, parent=self.root)
        if name is not None:
            host.alias = name.strip()
            self.store.upsert_host(host)
            self.refresh_recent()

    def password_selected(self) -> None:
        selected = self.recent.selection()
        if len(selected) != 1:
            return
        session = next((s for s in self.store.sessions() if s.key == selected[0]), None)
        if not session:
            return
        host = self.store.get_host(session.host, session.port) or Host(session.host, session.port)
        password = simpledialog.askstring("VNC 密码", "新密码（留空则清除）：", show="*", parent=self.root)
        if password is not None:
            host.password = password
            self.store.upsert_host(host)

    def open_session(self, host: Host) -> None:
        if host.key in self.sessions:
            frame = self.sessions[host.key][0]
            self.notebook.select(frame)
            return
        self.store.upsert_host(host)
        self.store.add_session(Session(host.address, host.port, host.password))
        self.refresh_recent()
        frame = ttk.Frame(self.notebook)
        tabs = ttk.Notebook(frame)
        tabs.pack(fill=tk.BOTH, expand=True)
        viewer = ViewerTab(tabs, host, self._viewer_event)
        transfer = FileTransferTab(tabs, host, self.status.set)
        tabs.add(viewer, text="远程桌面")
        tabs.add(transfer, text="文件传输")
        self.sessions[host.key] = frame, viewer, transfer, host
        self.notebook.add(frame, text=host.display_name)
        self.notebook.select(frame)
        self.quick.delete(0, tk.END)

    def _viewer_event(self, viewer: ViewerTab, kind: str, payload: object) -> None:
        self.events.put((viewer, kind, payload))

    def _poll(self) -> None:
        while True:
            try:
                viewer, kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "close":
                self._destroy_session(viewer)
                continue
            viewer.handle_event(kind, payload)
            if kind == "error":
                self.status.set(f"连接错误：{payload}")
            elif kind == "connected":
                self.status.set(f"已连接 {viewer.host.display_name}")
        self.root.after(100, self._poll)

    def _destroy_session(self, viewer: ViewerTab) -> None:
        item = next((item for item in self.sessions.values() if item[1] is viewer), None)
        if not item:
            return
        frame, viewer, transfer, host = item
        transfer.close()
        image = viewer.client.screenshot()
        if image:
            image.thumbnail((250, 150))
            output = io.BytesIO()
            image.save(output, "PNG")
            host.thumbnail = base64.b64encode(output.getvalue()).decode()
        self.store.upsert_host(host)
        self.notebook.forget(frame)
        del self.sessions[host.key]

    def close_current(self) -> None:
        selected = self.notebook.select()
        item = next((item for item in self.sessions.values() if str(item[0]) == selected), None)
        if item:
            item[1].close()

    def show_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("设置")
        apply_icon(win)
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        night = tk.BooleanVar(value=self.store.settings.get("night_mode"))
        diagnostic = tk.BooleanVar(value=self.store.settings.get("diagnostic_mode"))
        updates = tk.BooleanVar(value=self.store.settings.get("check_updates", True))
        for text, variable in (("深色主题（重启后完全生效）", night), ("诊断模式（详细日志）", diagnostic), ("启动时检查更新", updates)):
            ttk.Checkbutton(frame, text=text, variable=variable).pack(anchor=tk.W, pady=5)

        def save() -> None:
            self.store.settings.update(night_mode=night.get(), diagnostic_mode=diagnostic.get(), check_updates=updates.get())
            self.store.save()
            logging.getLogger().setLevel(logging.DEBUG if diagnostic.get() else logging.INFO)
            win.destroy()

        ttk.Button(frame, text="保存", command=save).pack(side=tk.RIGHT, pady=10)
        ttk.Button(frame, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=5)

    def export_data(self) -> None:
        name = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", filetypes=[("JSON", "*.json")], title="导出（不含密码）")
        if not name:
            return
        hosts = []
        for host in self.store.hosts():
            raw = host.to_dict()
            raw["password"] = ""
            raw["thumbnail"] = ""
            hosts.append(raw)
        sessions = []
        for session in self.store.sessions():
            raw = session.to_dict()
            raw["password"] = ""
            sessions.append(raw)
        Path(name).write_text(json.dumps({"hosts": hosts, "sessions": sessions}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.set(f"已导出：{name}")

    def _startup_update(self) -> None:
        if self.store.settings.get("check_updates", True):
            self.check_updates(False)

    def check_updates(self, forced: bool) -> None:
        def worker() -> None:
            try:
                request = urllib.request.Request("https://updates.remoteripple.com/current-version", data=json.dumps({"VersionNumber": 10200, "Product": "Remote Ripple Python", "Forced": forced}).encode(), headers={"Content-Type": "application/json", "User-Agent": "RemoteRipplePy/1.2"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    result = response.read().decode()
                self.root.after(0, lambda: self.status.set(f"更新检查完成：{result[:160]}"))
                if forced:
                    self.root.after(0, lambda: messagebox.showinfo("检查更新", result, parent=self.root))
            except Exception as exc:
                LOG.info("更新检查失败: %s", exc)
                if forced:
                    self.root.after(0, lambda: messagebox.showerror("检查更新", str(exc), parent=self.root))
        threading.Thread(target=worker, daemon=True).start()

    def show_help(self) -> None:
        messagebox.showinfo("使用说明", "1. 输入主机地址和端口，必要时在“新建连接”中填写密码。\n2. 远程桌面页支持键鼠、只读、缩放、全屏、截图、剪贴板和 Ctrl+Alt+Del。\n3. 文件传输页使用独立 TightVNC RFB 会话，支持目录浏览、上传、下载、新建、重命名和删除。\n4. 可将本地文件或目录直接拖到右侧远程文件列表。\n5. 主机密码使用当前 Windows 用户的 DPAPI 加密保存。", parent=self.root)

    def show_logs(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("诊断日志")
        apply_icon(win)
        win.geometry("850x500")
        text = tk.Text(win, wrap=tk.NONE)
        text.pack(fill=tk.BOTH, expand=True)
        log_path = config_dir() / "logs" / "remoteripple.log"
        try:
            text.insert(tk.END, log_path.read_text(encoding="utf-8")[-500000:])
        except OSError as exc:
            text.insert(tk.END, str(exc))
        text.configure(state=tk.DISABLED)

    def close(self) -> None:
        for item in list(self.sessions.values()):
            item[1].client.close()
            item[3].settings = item[1].host.settings
            item[2].close()
            self.store.upsert_host(item[3])
        self.store.save()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
