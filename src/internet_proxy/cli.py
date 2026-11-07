"""命令行入口：internet-proxy [server|client|gui]。

默认（无参数）= gui：托盘 + Win95 界面 + 代理引擎，启动静默到托盘。
--headless 时单独以 server/client 角色在前台跑，方便调试或 systemd/服务方式。
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import threading

from .config import Config

_SINGLETON_HANDLES: list[int] = []


def _claim_single_instance(role: str) -> bool:
    """同一角色只允许一个实例运行.

    程序点关闭只是隐藏到托盘，换版本后直接再启动会留下多个实例：它们各自
    启动一个 tun2socks、同时监听同一端口（SO_REUSEADDR 不会报错），互相
    抢虚拟网卡并持续占用套接字，最终把系统端口/non-paged pool 耗尽，
    tun2socks 连自己的 UDP socket 都建不出来（WSAENOBUFS 10055）。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, f"Local\\InternetProxyPy_{role}")
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _SINGLETON_HANDLES.append(handle)
    except OSError:
        pass
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="internet-proxy", description="工控机局域网互联网代理")
    parser.add_argument(
        "--role", choices=("server", "client", "gui"),
        help="运行角色；缺省 gui（托盘后台，静默启动）",
    )
    parser.add_argument(
        "--gateway", help="网关（可上网工控机）IP；客户端角色必填或写入配置文件"
    )
    parser.add_argument("--port", type=int, help="代理端口，默认 10082")
    parser.add_argument("--headless", action="store_true", help="无界面方式运行")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    cfg = Config.load()
    if args.gateway:
        cfg.gateway = args.gateway
    if args.port and 1 <= args.port <= 65535:
        cfg.port = args.port
    if args.role:
        cfg.role = args.role
    cfg.save()
    print(f"配置文件: {cfg.save().as_posix()}")

    role_key = "client" if cfg.role in ("gui", "client") else "server"
    if not _claim_single_instance(role_key):
        text = (
            f"InternetProxyPy 已有 {role_key} 实例在运行。\n"
            "请先在托盘图标上右键退出旧实例，再启动本程序。"
        )
        if args.headless:
            print(text, file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(0, text, "InternetProxyPy", 0x40)
        return 1

    if cfg.role == "gui" or (not args.headless and args.role is None):
        from .app import run_gui

        run_gui(cfg)
        return 0

    if args.role == "client" and not args.headless:
        from .app import run_gui

        run_gui(cfg)
        return 0

    # —— 无界面路径 ——
    from .app import Controller

    done = threading.Event()
    controller = Controller(cfg, on_log=print)
    try:
        done.wait()
    except KeyboardInterrupt:
        print("退出")
    controller.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
