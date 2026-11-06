"""主界面：Tkinter + TkDND2，Windows 95 风格（设计令牌取自 assets/02-windows-95.css）。

两个区块：
    - 日志：滚动文本区，客户端 / 服务端运行日志实时输出
    - 设置：角色（服务端/客户端）、网关 IP、端口（默认 10082），保存写回 config.json

窗口启动即沉淀（withdraw，静默最小化到托盘），仅经托盘唤出，用户无感知。
"""

from __future__ import annotations

import logging
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
        self._build()
        # —— 启动静默：立即沉淀到托盘，用户无感知 ——
        self.root.withdraw()

    def _icon_path(self) -> str:
        for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
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
            height=12, wrap="none", bd=0,
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

        tk.Button(panel, text="保存", bg=CHROME, font=FONT, command=self._save).grid(
            row=3, column=1, columnspan=2, sticky="we", pady=(4, 0)
        )

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
        self.cfg.save()
        self.append_log(f"配置已写入 {CONFIG_PATH}")
        self.controller.reload(self.cfg)

    def _on_exception(self, *exc) -> None:  # Tk 死循环兜底：打到日志即可
        logging.getLogger("internet_proxy.ui").error("UI 异常", exc_info=exc)

    def run(self) -> None:
        self.root.mainloop()
