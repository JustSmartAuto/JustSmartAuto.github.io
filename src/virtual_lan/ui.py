from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from .config import NETWORK_NAME, PUBLIC_SERVER, VIRTUAL_CIDR
from .engine import EasyTierEngine, Status
from .resources import resource

BG = "#F3F6FA"
CARD = "#FFFFFF"
PRIMARY = "#2563EB"
TEXT = "#172033"
MUTED = "#64748B"
GREEN = "#059669"
FONT = ("Microsoft YaHei UI", 10)


class MainWindow:
    def __init__(self) -> None:
        self.root = TkinterDnD.Tk()
        self.root.title("VirtualLanPy")
        self.root.geometry("720x600")
        self.root.minsize(640, 540)
        self.root.configure(bg=BG)
        self.root.iconbitmap(str(resource("virtual-lan.ico")))
        self.queue: queue.Queue = queue.Queue()
        self._closing = False
        self.engine = EasyTierEngine(self.log, self.set_status)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._pump)
        self.root.after(100, self._auto_start)

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=PRIMARY, height=82)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="VirtualLanPy", bg=PRIMARY, fg="white", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=24, pady=(16, 0))
        tk.Label(header, text="EasyTier 一键组网 · 调试专用", bg=PRIMARY, fg="#DBEAFE", font=FONT).pack(anchor="w", padx=25)

        info = tk.Frame(self.root, bg=CARD, padx=20, pady=16)
        info.pack(fill="x", padx=20, pady=16)
        self.status_var = tk.StringVar(value="准备启动")
        self.ip_var = tk.StringVar(value="等待分配")
        self.peer_var = tk.StringVar(value="0")
        for col, (title, var) in enumerate((("连接状态", self.status_var), ("本机虚拟 IP", self.ip_var), ("已连接节点", self.peer_var))):
            info.grid_columnconfigure(col, weight=1)
            tk.Label(info, text=title, bg=CARD, fg=MUTED, font=FONT).grid(row=0, column=col, sticky="w", padx=8)
            tk.Label(info, textvariable=var, bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 13, "bold")).grid(row=1, column=col, sticky="w", padx=8, pady=(4, 0))
        tk.Label(info, text=f"网络：{NETWORK_NAME}    网段：{VIRTUAL_CIDR} (DHCP)    公共节点：{PUBLIC_SERVER}", bg=CARD, fg=MUTED, font=FONT).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(14, 0))

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=24)
        self.start_button = self._button(controls, "重新连接", self.reconnect, primary=True)
        self.start_button.pack(side="left")
        self._button(controls, "复制虚拟 IP", self.copy_ip).pack(side="left", padx=8)
        self._button(controls, "断开连接", self.disconnect).pack(side="left")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=20, pady=16)
        log_tab = tk.Frame(notebook, bg=CARD)
        peer_tab = tk.Frame(notebook, bg=CARD)
        notebook.add(log_tab, text="运行日志")
        notebook.add(peer_tab, text="节点连接")
        self.log_text = self._text(log_tab)
        self.peer_text = self._text(peer_tab)
        for widget in (self.log_text, self.root):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._drop)
        tk.Label(self.root, text="可将日志文件拖入窗口打开", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24, pady=(0, 8))

    @staticmethod
    def _button(parent: tk.Frame, text: str, command, primary: bool = False) -> tk.Button:
        normal = PRIMARY if primary else CARD
        hover = "#1D4ED8" if primary else "#E2E8F0"
        foreground = "white" if primary else TEXT
        button = tk.Button(
            parent, text=text, command=command, bg=normal, fg=foreground,
            activebackground=hover, activeforeground=foreground,
            relief="flat" if primary else "solid", bd=0 if primary else 1,
            font=FONT, padx=18, pady=6, cursor="hand2",
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=hover) if button["state"] != "disabled" else None)
        button.bind("<Leave>", lambda _event: button.configure(bg=normal))
        return button

    @staticmethod
    def _text(parent: tk.Frame) -> tk.Text:
        frame = tk.Frame(parent, bg=CARD)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=("Consolas", 10), bg=CARD, fg=TEXT, relief="flat", padx=12, pady=10, state="disabled")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        return text

    def _auto_start(self) -> None:
        self.log("已获得管理员权限，自动加入虚拟局域网")
        threading.Thread(target=self.engine.start, daemon=True, name="easytier-start").start()

    def reconnect(self) -> None:
        if self._closing:
            return
        self.start_button.configure(state="disabled")

        def work() -> None:
            self.engine.stop()
            self.engine.start()
            self.queue.put(("button", None))

        threading.Thread(target=work, daemon=True, name="easytier-reconnect").start()

    def disconnect(self) -> None:
        threading.Thread(target=self.engine.stop, daemon=True, name="easytier-stop").start()
        self.log("正在断开连接 ...")

    def copy_ip(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.ip_var.get())
        self.log("虚拟 IP 已复制到剪贴板")

    def log(self, message: str) -> None:
        self.queue.put(("log", f"[{datetime.now():%H:%M:%S}] {message}"))

    def set_status(self, status: Status) -> None:
        self.queue.put(("status", status))

    def _pump(self) -> None:
        if self._closing:
            return
        try:
            for _ in range(200):
                kind, value = self.queue.get_nowait()
                if kind == "log":
                    self._append(self.log_text, value)
                elif kind == "status":
                    self.status_var.set(value.state)
                    self.ip_var.set(value.virtual_ip)
                    self.peer_var.set(str(value.peers))
                    self.peer_text.configure(state="normal")
                    self.peer_text.delete("1.0", "end")
                    self.peer_text.insert("end", "\n".join(value.details) or "暂无已连接节点")
                    self.peer_text.configure(state="disabled")
                elif kind == "button":
                    self.start_button.configure(state="normal")
                elif kind == "file":
                    self.log_text.configure(state="normal")
                    self.log_text.delete("1.0", "end")
                    self.log_text.insert("end", value)
                    self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    @staticmethod
    def _append(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _drop(self, event) -> None:
        paths = self.root.tk.splitlist(event.data)
        if not paths:
            return

        def load() -> None:
            try:
                with open(paths[0], encoding="utf-8", errors="replace") as file:
                    content = file.read()
                self.queue.put(("file", content))
            except OSError as exc:
                self.log(f"无法打开拖入的文件：{exc}")

        threading.Thread(target=load, daemon=True, name="log-file-loader").start()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.start_button.configure(state="disabled")
        self.status_var.set("正在退出 ...")

        def work() -> None:
            self.engine.stop()
            self.root.after(0, self.root.destroy)

        threading.Thread(target=work, daemon=True, name="application-shutdown").start()

    def run(self) -> None:
        self.root.mainloop()
