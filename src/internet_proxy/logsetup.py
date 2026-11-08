"""日志初始化：控制台 + 落盘.

打包后的 exe 是 windowed 构建，没有控制台，``--headless`` 也看不到输出；
现场排障只能依赖日志文件，因此统一写到配置目录下的 ``logs/app.log``。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import config_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """配置根日志；重复调用不会叠加 handler（GUI 与 CLI 共用）."""
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_FORMAT)

    # windowed 构建下 sys.stderr 为 None，加控制台 handler 会写失败
    if sys.stderr is not None and not any(
        type(h) is logging.StreamHandler for h in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    try:
        log_dir = config_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            file_handler = RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
    except OSError:
        pass
