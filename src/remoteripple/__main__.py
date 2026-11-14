from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .storage import config_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="RemoteRipplePy")
    parser.add_argument("target", nargs="?", help="启动后连接的主机，例如 192.168.1.10:5900")
    args = parser.parse_args()
    log_dir = config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(process)d | %(thread)d | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(log_dir / "remoteripple.log", encoding="utf-8"), logging.StreamHandler()],
    )
    if sys.platform != "win32":
        raise SystemExit("RemoteRipplePy 当前仅支持 Windows")
    from .app import RemoteRippleApp
    from .models import Host, parse_target

    app = RemoteRippleApp()
    if args.target:
        try:
            host, port = parse_target(args.target)
            app.root.after(100, lambda: app.open_session(Host(host, port)))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    app.run()


if __name__ == "__main__":
    main()
