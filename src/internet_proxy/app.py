"""进程装配：配置 → 代理线程（服务端或客户端）→ 托盘 + Win95 界面。

启动即静默：托盘先行的同时窗口全程 withdraw，用户无感知；代理在后台线程
常驻运行，退出仅能通过托盘菜单完成。
"""

from __future__ import annotations

import logging
import threading

from .client import Client
from .config import Config
from .server import serve

log = logging.getLogger("internet_proxy.app")


class Controller:
    """代理线程生命周期管理：按角色启动 / 停止 / 热切换."""

    def __init__(self, cfg: Config, on_log=None, on_state=None) -> None:
        self.cfg = cfg
        self.on_log = on_log or (lambda text: None)
        self.on_state = on_state or (lambda running: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._client: Client | None = None
        self.running = False
        self.start()

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.on_state(running)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        cfg, stop = self.cfg, threading.Event()
        self._stop = stop

        def run() -> None:
            self._set_running(True)
            try:
                if cfg.role == "server":
                    self.on_log(f"[server] listening TCP/UDP 0.0.0.0:{cfg.port}")
                    try:
                        serve(port=cfg.port, stop=stop)
                    except OSError as exc:
                        self.on_log(f"[server] 启动失败: {exc}")
                else:
                    client = Client(cfg)
                    self._client = client
                    self.on_log(f"[client] SOCKS5 0.0.0.0:{cfg.port} → {cfg.gateway}:{cfg.port}")
                    tun = None
                    if cfg.transparent:
                        from .tun import TunProxy

                        tun = TunProxy(cfg.port, cfg.gateway)

                        def start_tun() -> None:
                            import time

                            time.sleep(0.8)  # 等本地 SOCKS5 就绪
                            if stop.is_set():
                                return
                            if tun.start():
                                self.on_log("[tun] 系统透明代理已接管（虚拟网卡）")
                            else:
                                self.on_log("[tun] 启动失败，已回退纯 SOCKS5（原因见上方日志）")

                        threading.Thread(target=start_tun, daemon=True).start()
                    try:
                        client.start()
                    except OSError as exc:
                        self.on_log(f"[client] 启动失败: {exc}")
                    finally:
                        if tun:
                            tun.stop()
                        self._client = None
            finally:
                self._set_running(False)

        self._thread = threading.Thread(target=run, name="proxy-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止代理：置停止标志并关闭监听 socket，使阻塞的 accept 立即退出."""
        self._stop.set()
        if self._client:
            self._client.stop()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._set_running(False)

    def reload(self, cfg: Config) -> None:
        """保存配置后热切换：重建代理引擎线程."""
        self.stop()
        self.cfg = cfg
        self.start()


def run_gui(cfg: Config) -> None:  # pragma: no cover - GUI 入口
    """托盘 + 界面 + 代理引擎 三件套；阻塞到 Tk mainloop 结束."""
    from .config import CONFIG_PATH
    from .tray import TrayIcon
    from .ui import Win95UI

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    log.info("配置文件: %s", CONFIG_PATH)

    ui_holder: dict[str, Win95UI] = {}
    tray_holder: dict[str, TrayIcon] = {}

    def exit_app() -> None:
        controller.stop()
        tray = tray_holder.get("tray")
        if tray:
            tray.stop()
        ui = ui_holder.get("ui")
        if ui:
            ui.root.withdraw()
            ui.root.quit()

    def show() -> None:
        ui_holder["ui"].show()

    def build() -> None:
        ui = Win95UI(cfg, controller)
        ui_holder["ui"] = ui

        class UILogHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                ui.append_log(self.format(record))

        handler = UILogHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
        controller.on_log = ui.append_log
        controller.on_state = ui.set_status
        tray = TrayIcon(
            tk=ui.root,
            cfg=cfg,
            on_show=show,
            on_exit=exit_app,
        )
        tray_holder["tray"] = tray
        tray.start()

    controller = Controller(cfg, on_log=lambda text: log.info(text))
    build()
    ui_holder["ui"].run()

    # mainloop 结束（托盘退出）后的收尾
    controller.stop()
