"""配置持久化：目录解析与读写。

目录解析顺序（不弹任何交互确认）：
    1. D:/JustSmart/InternetProxyPy —— 默认，目录不存在时自动创建
    2. %APPDATA%/InternetProxyPy    —— 默认目录不可用（分区缺失/无写权限）时回退
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PORT = 10082
DEFAULT_GATEWAY = "192.168.1.10"

_ROLES = ("server", "client")


def _default_dir() -> Path:
    """返回配置目录：优先 D:/JustSmart，创建失败或不可写时回退 %APPDATA%."""
    root = Path("D:/") / "JustSmart" / "InternetProxyPy"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write_probe"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        return root
    except OSError:
        pass
    root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    root = root / "InternetProxyPy"
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_dir() -> Path:
    """自动创建并返回配置目录；后续 config 写盘都用它."""
    return _default_dir()


CONFIG_PATH = config_dir() / "config.json"


class Config:
    """代理配置：角色、网关 IP、端口。缺省即服务端口 10082."""

    def __init__(
        self,
        role: str = "server",
        gateway: str = DEFAULT_GATEWAY,
        port: int = DEFAULT_PORT,
    ) -> None:
        if role not in _ROLES:
            role = "server"
        self.role = role
        self.gateway = gateway
        self.port = int(port)

    def to_dict(self) -> dict:
        return {"role": self.role, "gateway": self.gateway, "port": self.port}

    def save(self, path: Path = CONFIG_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        cfg = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        role = data.get("role")
        if role in _ROLES:
            cfg.role = role
        gateway = data.get("gateway")
        if isinstance(gateway, str) and gateway.strip():
            cfg.gateway = gateway.strip()
        try:
            port = int(data.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        cfg.port = port if 1 <= port <= 65535 else DEFAULT_PORT
        return cfg
