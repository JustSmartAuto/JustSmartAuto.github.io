from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    primary = Path("D:/JustSmart/VirtualLanPy")
    fallback = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "VirtualLanPy"
    for path in (primary, fallback):
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    raise OSError(f"无法创建配置目录：{primary} 或 {fallback}")
