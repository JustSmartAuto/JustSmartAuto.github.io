"""命令行入口：internet-proxy [server|client|gui]。

默认（无参数）= gui：托盘 + Win95 界面 + 代理引擎，启动静默到托盘。
--headless 时单独以 server/client 角色在前台跑，方便调试或 systemd/服务方式。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from .config import Config


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
