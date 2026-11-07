"""主界面：Tkinter + TkDND2，Windows 95 风格（设计令牌取自 assets/02-windows-95.css）。

三个标签页：
    - 日志：滚动文本区，客户端 / 服务端运行日志实时输出
    - 设置：角色（服务端/客户端）、网关 IP、端口（默认 10082）、VMess 上行、
      开机自启，保存写回 config.json
    - 安装器：nginx / v2ray 的安装状态与一键安装、启停、打开目录

窗口启动即沉淀（withdraw，静默最小化到托盘），仅经托盘唤出，用户无感知。
关闭按钮（X）只隐藏回托盘，退出只能走托盘菜单；最小化按钮为系统默认（最小化到任务栏）。
"""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from tkinterdnd2 import TkinterDnD

from . import autostart, installer
from .config import CONFIG_PATH, Config
from .vmess import normalize_uuid

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
        self.root.geometry("620x660")
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

        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True, pady=(6, 0))
        log_tab = tk.Frame(notebook, bg=BG)
        set_tab = tk.Frame(notebook, bg=BG)
        inst_tab = tk.Frame(notebook, bg=BG)
        notebook.add(log_tab, text="日志")
        notebook.add(set_tab, text="设置")
        notebook.add(inst_tab, text="安装器")
        notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_installer())

        self._build_log_tab(log_tab)
        self._build_settings_tab(set_tab)
        self._build_installer_tab(inst_tab)
        self._refresh_installer()

    def _build_log_tab(self, parent: tk.Frame) -> None:
        # —— 日志（Win95 内凹 log view）——
        logbox = tk.Frame(parent, bg=CHROME, bd=2, relief="solid")
        logbox.pack(fill="both", expand=True, padx=2, pady=2)
        self.log = tk.Text(
            logbox, bg=WHITE, fg=INK, font=MONO, state="disabled",
            height=12, wrap="char", bd=0,
        )
        sb = tk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

    def _build_settings_tab(self, parent: tk.Frame) -> None:
        # —— 基础设置（Win95 groupbox 风格）——
        panel = tk.Frame(parent, bg=CHROME, padx=8, pady=8)
        panel.pack(fill="x", padx=2, pady=2)
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
        self.autostart = tk.BooleanVar(value=self.cfg.autostart)
        tk.Checkbutton(
            panel, text="开机自启(含 nginx/v2ray)", variable=self.autostart,
            bg=CHROME, font=FONT, activebackground=CHROME,
        ).grid(row=4, column=1, columnspan=2, sticky="w")
        tk.Button(panel, text="保存", bg=CHROME, font=FONT, command=self._save).grid(
            row=5, column=1, columnspan=2, sticky="we", pady=(4, 0)
        )

        # —— 代理控制：运行状态指示灯 + 开始/停止按钮 ——
        control = tk.Frame(panel, bg=CHROME)
        control.grid(row=6, column=0, columnspan=3, sticky="we", pady=(8, 0))
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

        # —— VMess 上行：客户端连接 v2ray 服务端，替代内置隧道 ——
        proxy = tk.Frame(parent, bg=CHROME, padx=8, pady=8)
        proxy.pack(fill="x", padx=2, pady=2)
        for c in range(4):
            proxy.grid_columnconfigure(c, pad=4, weight=0 if c > 0 else 1)

        self.v2ray_enabled = tk.BooleanVar(value=self.cfg.v2ray_enabled)
        tk.Checkbutton(
            proxy, text="通过 v2ray 服务端（VMess）代理互联网", variable=self.v2ray_enabled,
            bg=CHROME, font=FONT, activebackground=CHROME,
        ).grid(row=0, column=0, columnspan=4, sticky="w")

        tk.Label(proxy, text="服务端", bg=CHROME, font=FONT).grid(row=1, column=0, sticky="w")
        self.v2ray_server = tk.StringVar(value=self.cfg.v2ray_server)
        tk.Entry(proxy, textvariable=self.v2ray_server, font=FONT, bg=WHITE, width=18).grid(
            row=1, column=1, columnspan=2, sticky="we"
        )
        port_box = tk.Frame(proxy, bg=CHROME)
        port_box.grid(row=1, column=3, sticky="w")
        tk.Label(port_box, text="端口", bg=CHROME, font=FONT).pack(side="left", padx=(0, 4))
        self.v2ray_port = tk.StringVar(value=str(self.cfg.v2ray_port))
        tk.Entry(
            port_box, textvariable=self.v2ray_port, font=FONT, bg=WHITE, width=7,
        ).pack(side="left")

        tk.Label(proxy, text="UUID", bg=CHROME, font=FONT).grid(row=2, column=0, sticky="w")
        self.v2ray_uuid = tk.StringVar(value=self.cfg.v2ray_uuid)
        tk.Entry(proxy, textvariable=self.v2ray_uuid, font=FONT, bg=WHITE).grid(
            row=2, column=1, columnspan=3, sticky="we"
        )

        tk.Label(proxy, text="传输", bg=CHROME, font=FONT).grid(row=3, column=0, sticky="w")
        self.v2ray_network = tk.StringVar(
            value="WS" if self.cfg.v2ray_network == "ws" else "TCP"
        )
        for i, label in enumerate(("TCP", "WS")):
            tk.Radiobutton(
                proxy, text=label, variable=self.v2ray_network, value=label,
                bg=CHROME, font=FONT,
            ).grid(row=3, column=1 + i, sticky="w")
        self.v2ray_tls = tk.BooleanVar(value=self.cfg.v2ray_tls)
        tk.Checkbutton(
            proxy, text="TLS", variable=self.v2ray_tls,
            bg=CHROME, font=FONT, activebackground=CHROME,
        ).grid(row=3, column=3, sticky="w")

        tk.Label(proxy, text="WS 路径", bg=CHROME, font=FONT).grid(row=4, column=0, sticky="w")
        self.v2ray_path = tk.StringVar(value=self.cfg.v2ray_path)
        tk.Entry(proxy, textvariable=self.v2ray_path, font=FONT, bg=WHITE).grid(
            row=4, column=1, columnspan=3, sticky="we"
        )

        tk.Label(
            proxy,
            text="服务端 UUID 见「安装器」页；启用后网关 IP 不再参与转发。",
            bg=CHROME, fg=CHROME_DARK, font=FONT, justify="left",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))

    def _build_installer_tab(self, parent: tk.Frame) -> None:
        # —— 安装器：nginx / v2ray 状态与操作 ——
        panel = tk.Frame(parent, bg=CHROME, padx=8, pady=8)
        panel.pack(fill="both", expand=True, padx=2, pady=2)

        tk.Label(panel, text="服务组件", bg=CHROME, font=FONT).pack(anchor="w")
        self.inst_status = tk.StringVar(value="检测中 ...")
        tk.Label(
            panel, textvariable=self.inst_status, justify="left", anchor="nw",
            bg=WHITE, fg=INK, font=MONO, bd=1, relief="sunken", padx=4, pady=4,
        ).pack(fill="x", pady=(2, 8))

        tk.Label(
            panel,
            text=f"v2ray：SOCKS5 0.0.0.0:{installer.V2RAY_PORT}  "
                 f"账号 {installer.PROXY_USER} / 密码 {installer.PROXY_PASS}",
            bg=CHROME, font=FONT, justify="left",
        ).pack(anchor="w")
        tk.Label(
            panel,
            text=f"nginx：静态站点 0.0.0.0:{installer.NGINX_PORT}，/status 健康检查",
            bg=CHROME, font=FONT, justify="left",
        ).pack(anchor="w")
        self.vmess_info = tk.StringVar(
            value=f"VMess：0.0.0.0:{installer.V2RAY_VMESS_PORT}  安装后生成 UUID"
        )
        tk.Label(
            panel, textvariable=self.vmess_info, bg=CHROME, font=FONT,
            justify="left", wraplength=540,
        ).pack(anchor="w")

        row = tk.Frame(panel, bg=CHROME)
        row.pack(fill="x", pady=(10, 0))
        tk.Button(
            row, text="一键安装/修复", bg=CHROME, font=FONT,
            command=lambda: self._installer_action(installer.request_install),
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            row, text="启动服务", bg=CHROME, font=FONT,
            command=lambda: self._installer_action(installer.start_services),
        ).pack(side="left", padx=4)
        tk.Button(
            row, text="停止服务", bg=CHROME, font=FONT,
            command=lambda: self._installer_action(installer.stop_services),
        ).pack(side="left", padx=4)

        row2 = tk.Frame(panel, bg=CHROME)
        row2.pack(fill="x", pady=(6, 0))
        tk.Button(
            row2, text="打开目录", bg=CHROME, font=FONT, command=self._open_install_dir,
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            row2, text="刷新状态", bg=CHROME, font=FONT, command=self._refresh_installer,
        ).pack(side="left", padx=4)
        tk.Button(
            row2, text="复制 UUID", bg=CHROME, font=FONT, command=self._copy_uuid,
        ).pack(side="left", padx=4)

        tk.Label(
            panel,
            text="首次启动会自动请求管理员权限（UAC）并静默安装，取消后可在本页重试。",
            bg=CHROME, fg=CHROME_DARK, font=FONT, justify="left",
        ).pack(anchor="w", pady=(10, 0))

    # —— 安装器操作 ——
    def _installer_action(self, action) -> None:
        """安装 / 启停放到后台线程，避免 UAC、解压和等待端口时卡住界面."""
        def work() -> None:
            try:
                action(self.append_log)
            except Exception as exc:  # 失败只记日志，不弹异常
                self.append_log(f"安装器执行失败: {exc}")
            finally:
                self.root.after(0, self._refresh_installer)

        threading.Thread(target=work, name="installer-ui", daemon=True).start()

    def _refresh_installer(self) -> None:
        def work() -> None:
            try:
                st = installer.status()
            except Exception as exc:
                text = f"状态检测失败: {exc}"
                info = None
            else:
                text = (
                    f"nginx  {'已安装' if st['nginx_installed'] else '未安装'}"
                    f"  {'运行中' if st['nginx_running'] else '未运行'}\n"
                    f"v2ray  {'已安装' if st['v2ray_installed'] else '未安装'}"
                    f"  {'运行中' if st['v2ray_running'] else '未运行'}\n"
                    f"VMess  {st['vmess_port']}"
                    f"  {'运行中' if st['vmess_running'] else '未运行'}\n"
                    f"目录   {st['base_dir']}"
                )
                info = (
                    f"VMess：0.0.0.0:{st['vmess_port']}  "
                    f"UUID {st['vmess_uuid'] or '（安装后生成）'}"
                )
            self.root.after(0, lambda: self.inst_status.set(text))
            if info is not None:
                self.root.after(0, lambda: self.vmess_info.set(info))

        threading.Thread(target=work, name="installer-status", daemon=True).start()

    def _copy_uuid(self) -> None:
        """把服务端 VMess UUID 复制到剪贴板，方便填到客户端."""
        try:
            uid = installer.status()["vmess_uuid"]
        except Exception as exc:
            self.append_log(f"读取 UUID 失败: {exc}")
            return
        if not uid:
            self.append_log("UUID 尚未生成：请先在安装器页完成安装")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(uid)
        self.append_log(f"已复制 VMess UUID: {uid}")

    def _open_install_dir(self) -> None:
        try:
            os.startfile(str(installer.BASE_DIR))
        except OSError as exc:
            self.append_log(f"打开目录失败: {exc}")

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
        try:
            v2ray_port = int(self.v2ray_port.get())
        except ValueError:
            self.append_log("保存失败: v2ray 端口必须是 1-65535 的整数")
            return
        if not (1 <= v2ray_port <= 65535):
            self.append_log("保存失败: v2ray 端口必须是 1-65535 的整数")
            return

        server = self.v2ray_server.get().strip()
        uid = self.v2ray_uuid.get().strip()
        if self.v2ray_enabled.get():
            if not server:
                self.append_log("保存失败: 启用 VMess 时需填写 v2ray 服务端地址")
                return
            normalized = normalize_uuid(uid)
            if normalized is None:
                self.append_log("保存失败: VMess UUID 无效（形如 8 位-4 位-4 位-4 位-12 位）")
                return
            uid = normalized

        path = self.v2ray_path.get().strip() or "/vmess"
        if not path.startswith("/"):
            path = "/" + path

        self.cfg.role = role
        self.cfg.gateway = self.gw.get().strip() or self.cfg.gateway
        self.cfg.port = port
        self.cfg.proxy_mode = "global" if self.mode.get() == "全局代理" else "rule"
        self.cfg.transparent = self.transparent.get()
        self.cfg.v2ray_enabled = self.v2ray_enabled.get()
        self.cfg.v2ray_server = server
        self.cfg.v2ray_port = v2ray_port
        self.cfg.v2ray_uuid = uid
        self.cfg.v2ray_network = "ws" if self.v2ray_network.get() == "WS" else "tcp"
        self.cfg.v2ray_path = path
        self.cfg.v2ray_tls = self.v2ray_tls.get()
        self.cfg.autostart = self.autostart.get()
        self.cfg.save()
        self.append_log(f"配置已写入 {CONFIG_PATH}")

        # 开机自启是系统侧状态：只在勾选状态变化时改计划任务 / 注册表
        if self.cfg.autostart != autostart.is_enabled():
            autostart.apply(self.cfg.autostart, self.append_log)

        self.controller.reload(self.cfg)

    def _on_exception(self, *exc) -> None:  # Tk 死循环兜底：打到日志即可
        logging.getLogger("internet_proxy.ui").error("UI 异常", exc_info=exc)

    def run(self) -> None:
        self.root.mainloop()
