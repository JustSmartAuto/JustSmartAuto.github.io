"""配置目录 & 收藏夹持久化.

配置目录优先级:
    1. 环境变量 ``LOCAL_DROP_CONFIG_DIR``（调试 / 便携部署用）
    2. ``D:/JustSmart/LocalFileDropShell``（首选，自动创建）
    3. ``%APPDATA%/LocalFileDropShell``（首选目录不可写时回退）

收藏夹文件为 ``<配置目录>/favorites.json``，UTF-8 + 原子写。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("localdropshell.settings")


APP_NAME = "LocalFileDropShell"
PRIMARY_CONFIG_DIR = Path("D:/JustSmart/LocalFileDropShell")
FAVORITES_FILE = "favorites.json"
SETTINGS_FILE = "settings.json"
DEFAULT_VNC_PORT = 5900

# 文件传输协议（当前仅 RFB File 可用，其余为占位项）
TRANSFER_PROTOCOLS = ("RFB File", "LocalSend", "FTP", "HTTP", "RSYNC")
DEFAULT_TRANSFER_PROTOCOL = "RFB File"

_cached_dir: Path | None = None


# ---------------------------------------------------------------------------
# 配置目录
# ---------------------------------------------------------------------------

def _fallback_dir() -> Path:
    """%APPDATA%/LocalFileDropShell（无 APPDATA 时退到用户目录）."""
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / APP_NAME


def config_dir() -> Path:
    """返回（并尽力创建）配置目录；首选目录不可写时回退到 APPDATA."""
    global _cached_dir
    if _cached_dir is not None:
        return _cached_dir

    candidates: list[Path] = []
    override = os.environ.get("LOCAL_DROP_CONFIG_DIR", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(PRIMARY_CONFIG_DIR)
    candidates.append(_fallback_dir())

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.info("配置目录不可用 %s (%s)", path, exc)
            continue
        _cached_dir = path
        logger.info("配置目录: %s", path)
        return path

    # 全部失败：返回回退路径，后续读写会以异常形式暴露
    _cached_dir = candidates[-1]
    logger.warning("所有配置目录均不可用，回退到 %s", _cached_dir)
    return _cached_dir


def favorites_path() -> Path:
    return config_dir() / FAVORITES_FILE


# ---------------------------------------------------------------------------
# 界面设置（协议等）
# ---------------------------------------------------------------------------

def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE


def load_settings() -> dict:
    """读取 ``settings.json``；缺失 / 损坏时返回空 dict."""
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("读取配置文件失败 %s: %s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def save_settings(data: dict) -> Path:
    """原子写入 ``settings.json``，返回写入路径."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def load_transfer_protocol() -> str:
    """读取已保存的传输协议；非法值回退到默认 RFB File."""
    value = str(load_settings().get("transfer_protocol") or "").strip()
    return value if value in TRANSFER_PROTOCOLS else DEFAULT_TRANSFER_PROTOCOL


def save_transfer_protocol(protocol: str) -> Path:
    value = protocol if protocol in TRANSFER_PROTOCOLS else DEFAULT_TRANSFER_PROTOCOL
    data = load_settings()
    data["transfer_protocol"] = value
    return save_settings(data)


def load_minimize_to_tray() -> bool:
    """启动时最小化到托盘开关；默认关闭."""
    return bool(load_settings().get("minimize_to_tray_on_start", False))


def save_minimize_to_tray(enabled: bool) -> Path:
    data = load_settings()
    data["minimize_to_tray_on_start"] = bool(enabled)
    return save_settings(data)


# ---------------------------------------------------------------------------
# 地址解析
# ---------------------------------------------------------------------------

def parse_target(text: str, default_port: int = DEFAULT_VNC_PORT) -> tuple[str, int]:
    """解析 ``ip[:端口]``，端口省略时用 default_port（TightVNC 默认 5900）."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("请输入远程地址，例如 192.168.1.10 或 192.168.1.10:5900")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]

    port_text = ""
    if raw.startswith("["):  # [::1]:5900
        end = raw.find("]")
        if end < 0:
            raise ValueError(f"IPv6 地址格式错误: {raw}")
        host = raw[1:end]
        rest = raw[end + 1:]
        if rest.startswith(":"):
            port_text = rest[1:]
    elif raw.count(":") == 1:
        host, port_text = raw.split(":", 1)
    else:
        host = raw

    host = host.strip()
    port_text = port_text.strip()
    if not host:
        raise ValueError("远程地址缺少主机名 / IP")
    if " " in host:
        raise ValueError(f"远程地址含空格: {host!r}")

    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError(f"端口必须是数字: {port_text!r}") from None
        if not 1 <= port <= 65535:
            raise ValueError(f"端口超出范围 (1-65535): {port}")
    else:
        port = default_port
    return host, port


def format_target(host: str, port: int) -> str:
    return f"{host}:{port}"


# ---------------------------------------------------------------------------
# 收藏夹数据模型
# ---------------------------------------------------------------------------

def _normalize_paths(raw: object) -> list[str]:
    """把配置里的快捷路径规范成去重、去空白的字符串列表."""
    if isinstance(raw, str):
        raw = raw.split(";")
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths


@dataclass
class Favorite:
    """一条收藏：备注名 + 远程主机/端口 + 默认路径 + 多个快捷路径 + 传输协议."""

    name: str
    host: str
    port: int = DEFAULT_VNC_PORT
    path: str = "/"
    paths: list[str] = field(default_factory=list)
    protocol: str = DEFAULT_TRANSFER_PROTOCOL

    def address(self) -> str:
        return format_target(self.host, self.port)

    def shortcut_paths(self) -> list[str]:
        """快捷路径（不含默认路径，保持配置顺序）."""
        return [p for p in self.paths if p and p != self.path]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "paths": list(self.paths),
            "protocol": self.protocol,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Favorite":
        host = str(data.get("host") or "").strip()
        name = str(data.get("name") or "").strip() or host
        try:
            port = int(data.get("port") or DEFAULT_VNC_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_VNC_PORT
        path = str(data.get("path") or data.get("dir") or "/").strip() or "/"
        protocol = str(data.get("protocol") or "").strip()
        if protocol not in TRANSFER_PROTOCOLS:
            protocol = DEFAULT_TRANSFER_PROTOCOL
        return cls(
            name=name,
            host=host,
            port=port,
            path=path,
            paths=_normalize_paths(data.get("paths")),
            protocol=protocol,
        )


def load_favorites() -> list[Favorite]:
    """读取收藏夹；文件缺失 / 损坏时返回空列表."""
    path = favorites_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("读取收藏夹失败 %s: %s", path, exc)
        return []

    items = raw.get("favorites") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []

    favorites: list[Favorite] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fav = Favorite.from_dict(item)
        if fav.host:
            favorites.append(fav)
    return favorites


def save_favorites(favorites: list[Favorite]) -> Path:
    """原子写入收藏夹，返回写入的路径."""
    path = favorites_path()
    payload = {
        "version": 1,
        "favorites": [fav.to_dict() for fav in favorites],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".favorites-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
