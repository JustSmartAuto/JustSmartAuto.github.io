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
DEFAULT_GATEWAY = "127.0.0.1"

_ROLES = ("server", "client")
_MODES = ("rule", "global")  # 规则代理（忽略局域网 IP）/ 全局代理


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
    """代理配置：角色、网关 IP、端口。

    缺省 = 客户端模式（双击 exe 即代理其它设备上网），网关未配置时用
    127.0.0.1；端口默认 10082。
    """

    def __init__(
        self,
        role: str = "client",
        gateway: str = DEFAULT_GATEWAY,
        port: int = DEFAULT_PORT,
        proxy_mode: str = "rule",
    ) -> None:
        if role not in _ROLES:
            role = "client"
        self.role = role
        self.gateway = gateway
        self.port = int(port)
        self.proxy_mode = proxy_mode if proxy_mode in _MODES else "rule"

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "gateway": self.gateway,
            "port": self.port,
            "proxy_mode": self.proxy_mode,
        }

    def save(self, path: Path = CONFIG_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        cfg = cls()  # 默认双击即服务：客户端模式 + 127.0.0.1 回退
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cfg
        if not isinstance(data, dict):
            return cfg
        role = data.get("role")
        if role in _ROLES:
            cfg.role = role
        else:
            cfg.role = "client"  # 配置中未写角色 → 客户端
        gateway = data.get("gateway")
        if isinstance(gateway, str) and gateway.strip():
            cfg.gateway = gateway.strip()
        else:
            cfg.gateway = DEFAULT_GATEWAY  # 未配置网关 → 127.0.0.1
        try:
            port = int(data.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        cfg.port = port if 1 <= port <= 65535 else DEFAULT_PORT
        mode = data.get("proxy_mode")
        cfg.proxy_mode = mode if mode in _MODES else "rule"  # 缺省规则代理
        return cfg
