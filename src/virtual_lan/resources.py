from __future__ import annotations

import sys
from pathlib import Path


def bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def resource(name: str) -> Path:
    return bundle_dir() / "assets" / name


def validate_binaries() -> None:
    names = ("easytier-core.exe", "easytier-cli.exe", "wintun.dll", "Packet.dll", "WinDivert64.sys")
    missing = [name for name in names if not resource(name).is_file()]
    if missing:
        raise FileNotFoundError("缺少运行组件：" + "、".join(missing))
