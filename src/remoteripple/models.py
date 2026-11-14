from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_PORT = 5900


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HostSettings:
    ignore_input: bool = False
    auto_scale: bool = True
    scale: int = 100
    local_path: str = "/"
    remote_path: str = "/"
    encoding: str = "自动"
    compression: int = 6
    jpeg_quality: int = 6
    clipboard: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostSettings":
        defaults = cls()
        return cls(
            ignore_input=bool(data.get("ignore_input", defaults.ignore_input)),
            auto_scale=bool(data.get("auto_scale", defaults.auto_scale)),
            scale=max(1, min(400, int(data.get("scale", defaults.scale)))),
            local_path=str(data.get("local_path", defaults.local_path)),
            remote_path=str(data.get("remote_path", defaults.remote_path)),
            encoding=str(data.get("encoding", defaults.encoding)),
            compression=max(0, min(9, int(data.get("compression", defaults.compression)))),
            jpeg_quality=max(0, min(9, int(data.get("jpeg_quality", defaults.jpeg_quality)))),
            clipboard=bool(data.get("clipboard", defaults.clipboard)),
        )


@dataclass
class Host:
    address: str
    port: int = DEFAULT_PORT
    alias: str = ""
    password: str = ""
    thumbnail: str = ""
    settings: HostSettings = field(default_factory=HostSettings)

    @property
    def key(self) -> str:
        return f"{self.address.casefold()}:{self.port}"

    @property
    def display_name(self) -> str:
        return self.alias or f"{self.address}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["settings"] = asdict(self.settings)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Host":
        return cls(
            address=str(data.get("address", "")),
            port=int(data.get("port", DEFAULT_PORT)),
            alias=str(data.get("alias", "")),
            password=str(data.get("password", "")),
            thumbnail=str(data.get("thumbnail", "")),
            settings=HostSettings.from_dict(data.get("settings", {})),
        )


@dataclass
class Session:
    host: str
    port: int = DEFAULT_PORT
    password: str = ""
    created: str = field(default_factory=utc_now)

    @property
    def key(self) -> str:
        return f"{self.host.casefold()}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            host=str(data.get("host", "")),
            port=int(data.get("port", DEFAULT_PORT)),
            password=str(data.get("password", "")),
            created=str(data.get("created", "")) or utc_now(),
        )


def parse_target(text: str) -> tuple[str, int]:
    raw = text.strip()
    if not raw:
        raise ValueError("请输入主机地址")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    if raw.startswith("["):
        end = raw.find("]")
        if end < 0:
            raise ValueError("IPv6 地址缺少右方括号")
        host = raw[1:end]
        suffix = raw[end + 1:]
        port_text = suffix[1:] if suffix.startswith(":") else ""
    elif "::" in raw and raw.count("::") == 1:
        host, port_text = raw.split("::", 1)
    elif raw.count(":") == 1:
        host, port_text = raw.split(":", 1)
    else:
        host, port_text = raw, ""
    host = host.strip()
    if not host or any(ch.isspace() for ch in host):
        raise ValueError("主机地址无效")
    try:
        port = int(port_text) if port_text else DEFAULT_PORT
    except ValueError:
        raise ValueError("端口必须是数字") from None
    if not 1 <= port <= 65535:
        raise ValueError("端口范围为 1–65535")
    return host, port


def format_target(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
