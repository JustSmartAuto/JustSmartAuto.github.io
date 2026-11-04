"""资源目录定位：开发环境取项目 assets/，PyInstaller 单文件取解包目录。"""

from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    """assets 目录（02-windows-95.css、overlay_icon.png、web 界面文件）。"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "assets"
    return project_root() / "assets"


def index_html() -> Path:
    return assets_dir() / "index.html"
