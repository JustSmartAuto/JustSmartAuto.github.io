"""主界面：Tkinter + TkDND2，Windows 95 风格（设计令牌取自 assets/02-windows-95.css）。

两个区块：
    - 日志：滚动文本区，客户端 / 服务端运行日志实时输出
    - 设置：角色（服务端/客户端）、网关 IP、端口（默认 10082），保存写回 config.json

窗口启动即沉淀（withdraw，静默最小化到托盘），仅经托盘唤出，用户无感知。
关闭按钮（X）只隐藏回托盘，退出只能走托盘菜单；最小化按钮为系统默认（最小化到任务栏）。
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path

from tkinterdnd2 import TkinterDnD

from .config import CONFIG_PATH, Config

# —— Win95 设计令牌（与 assets/02-windows-95.css 保持一致）——
BG = "#008080"        # --bg
CHROME = "#C0C0C0"    # --chrome
CHROME_LIGHT = "#DFDFDF"
WHITE = "#FFFFFF"
CHROME_DARK = "#808080"  # --chrome-dark
INK = "#000000"       # --ink
TITLE = "#000080"     # --title / --select
TITLE_TEXT = "#FFFFFF"
FONT = ("Tahoma", 9)
MONO = ("Consolas", 9)
PORT_MIN, PORT_MAX = 1024, 49151  # 高端口位；默认 10082 落在其中


class Win95UI:
    """托盘联动的主窗口：日志 tail-view + 设置页."""

    def __init__(self, cfg: Config, controller) -> None:
        self.cfg = cfg
        self.controller = controller
        self.root = TkinterDnD.Tk()
        self.root.title("InternetProxyPy")
        self.root.iconbitmap(self._icon_path())
        self.root.geometry("560x420")
        self.root.report_callback_exception = self._on_exception
        # 关闭按钮（X）不退出，只隐藏到托盘；最小化按钮保持默认行为（最小化到任务栏）
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._build()
        # —— 启动静默：立即沉淀到托盘，用户无感知 ——
        self.root.withdraw()

    def _icon_path(self) -> str:
        import sys

        bases = [Path.cwd(), Path(__file__).resolve().parents[2]]
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            bases.insert(0, Path(bundle))
        for base in bases:
            p = base / "assets" / "cat.ico"
            if p.is_file():
                return str(p)
        return ""

    def _build(self) -> None:
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=6, pady=6)

        title = tk.Frame(body, bg=TITLE, padx=4, pady=3)
        title.pack(fill="x")
        tk.Label(
            title, text=f"InternetProxyPy — 配置 {CONFIG_PATH}",
            bg=TITLE, fg=TITLE_TEXT, font=FONT, anchor="w",
        ).pack(fill="x")

        # —— 日志（Win95 内凹 log view）——
        logbox = tk.Frame(body, bg=CHROME, bd=2, relief="solid")
        logbox.pack(fill="both", expand=True, pady=(6, 4))
        self.log = tk.Text(
            logbox, bg=WHITE, fg=INK, font=MONO, state="disabled",
            height=12, wrap="char", bd=0,
        )
        sb = tk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

        # —— 设置（Win95 groupbox 风格）——
        panel = tk.Frame(body, bg=CHROME, padx=8, pady=8)
        panel.pack(fill="x")
        for c in range(4):
            panel.grid_columnconfigure(c, pad=4, weight=0 if c > 0 else 1)

        tk.Label(panel, text="角色", bg=CHROME, font=FONT).grid(row=0, column=0, sticky="w")
        self.role = tk.StringVar(value="服务端" if self.cfg.role == "server" else "客户端")
        for i, label in enumerate(("服务端", "客户端")):
            tk.Radiobutton(
                panel, text=label, variable=self.role, value=label,
                bg=CHROME, font=FONT, command=self._sync_port_box,
            ).grid(row=0, column=1 + i, sticky="w")

        tk.Label(panel, text="网关 IP", bg=CHROME, font=FONT).grid(row=1, column=0, sticky="w")
        self.gw = tk.StringVar(value=self.cfg.gateway)
        tk.Entry(panel, textvariable=self.gw, font=FONT, bg=WHITE).grid(
            row=1, column=1, columnspan=2, sticky="we"
        )

        tk.Label(panel, text="端口", bg=CHROME, font=FONT).grid(row=2, column=0, sticky="w")
        self.port = tk.StringVar(value=str(self.cfg.port))
        self.port_box = tk.Entry(
            panel, textvariable=self.port, font=FONT, bg=WHITE,
        )
        self.port_box.grid(row=2, column=1, sticky="w")
        self._sync_port_box()

        tk.Label(panel, text="代理模式", bg=CHROME, font=FONT).grid(row=3, column=0, sticky="w")
        self.mode = tk.StringVar(
            value="规则代理" if self.cfg.proxy_mode != "global" else "全局代理"
        )
        for i, label in enumerate(("规则代理", "全局代理")):
            tk.Radiobutton(
                panel, text=label, variable=self.mode, value=label,
                bg=CHROME, font=FONT,
            ).grid(row=3, column=1 + i, sticky="w")

        self.transparent = tk.BooleanVar(value=self.cfg.transparent)
        tk.Checkbutton(
            panel, text="系统透明代理(虚拟网卡)", variable=self.transparent,
            bg=CHROME, font=FONT, activebackground=CHROME,
        ).grid(row=4, column=0, sticky="w")
        tk.Button(panel, text="保存", bg=CHROME, font=FONT, command=self._save).grid(
            row=4, column=1, columnspan=2, sticky="we", pady=(4, 0)
        )

        # —— 代理控制：运行状态指示灯 + 开始/停止按钮 ——
        control = tk.Frame(panel, bg=CHROME)
        control.grid(row=5, column=0, columnspan=3, sticky="we", pady=(8, 0))
        self.status_dot = tk.Label(
            control, text="●", bg=CHROME, fg="#008000", font=("Tahoma", 12),
        )
        self.status_dot.pack(side="left")
        self.status_text = tk.Label(control, text="运行中", bg=CHROME, font=FONT)
        self.status_text.pack(side="left", padx=(2, 12))
        self.start_btn = tk.Button(
            control, text="开始代理", bg=CHROME, font=FONT, command=self._start_proxy,
        )
        self.start_btn.pack(side="left", padx=2)
        self.stop_btn = tk.Button(
            control, text="停止代理", bg=CHROME, font=FONT, command=self._stop_proxy,
        )
        self.stop_btn.pack(side="left", padx=2)
        self.set_status(self.controller.running)

    def _start_proxy(self) -> None:
        self.controller.start()
        self.append_log("开始代理")

    def _stop_proxy(self) -> None:
        self.controller.stop()
        self.append_log("停止代理")

    def set_status(self, running: bool) -> None:
        """代理运行状态指示灯：绿=运行中，红=已停止；按钮可用态随动."""
        def apply() -> None:
            self.status_dot.configure(fg="#008000" if running else "#C00000")
            self.status_text.configure(text="运行中" if running else "已停止")
            self.start_btn.configure(state="disabled" if running else "normal")
            self.stop_btn.configure(state="normal" if running else "disabled")
        self.root.after(0, apply) if threading.current_thread() is not threading.main_thread() else apply()

    def _sync_port_box(self) -> None:
        if self.role.get() == "服务端":
            self.port_box.configure(state="normal")
        # 服务端端口即本机监听端口；客户端端口沿用网关的端口

    # —— 日志 & 托盘联动 ——
    def show(self) -> None:
        self.root.after(0, lambda: (self.root.deiconify(), self.root.lift()))

    def hide(self) -> None:
        self.root.withdraw()

    def append_log(self, text: str) -> None:
        def write() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text.rstrip("\n") + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, write)

    def _save(self) -> None:
        role = "server" if self.role.get() == "服务端" else "client"
        try:
            port = int(self.port.get())
        except ValueError:
            self.append_log("保存失败: 端口必须是 1-65535 的整数")
            return
        if not (PORT_MIN <= port <= PORT_MAX):
            self.append_log(f"保存失败: 端口须在 {PORT_MIN}-{PORT_MAX} 之间（高端口）")
            return
        self.cfg.role = role
        self.cfg.gateway = self.gw.get().strip() or self.cfg.gateway
        self.cfg.port = port
        self.cfg.proxy_mode = "global" if self.mode.get() == "全局代理" else "rule"
        self.cfg.transparent = self.transparent.get()
        self.cfg.save()
        self.append_log(f"配置已写入 {CONFIG_PATH}")
        self.controller.reload(self.cfg)

    def _on_exception(self, *exc) -> None:  # Tk 死循环兜底：打到日志即可
        logging.getLogger("internet_proxy.ui").error("UI 异常", exc_info=exc)

    def run(self) -> None:
        self.root.mainloop()
