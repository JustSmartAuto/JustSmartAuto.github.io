from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .models import Host, Session, utc_now

LOG = logging.getLogger(__name__)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[Any]]:
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def protect_password(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        return "plain:" + base64.b64encode(value.encode()).decode()
    raw = value.encode("utf-8")
    source, source_buf = _blob(raw)
    target = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "RemoteRipplePy", None, None, None, 1, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(target.pbData, target.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)
        del source_buf


def unprotect_password(value: str) -> str:
    if not value:
        return ""
    try:
        if value.startswith("plain:"):
            return base64.b64decode(value[6:]).decode()
        if not value.startswith("dpapi:"):
            return value
        raw = base64.b64decode(value[6:])
        source, source_buf = _blob(raw)
        target = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(target.pbData)
            del source_buf
    except Exception:
        LOG.exception("密码解密失败")
        return ""


def config_dir() -> Path:
    override = os.environ.get("REMOTERIPPLEPY_CONFIG_DIR")
    candidates = []
    if override:
        candidates.append(Path(override))
    else:
        candidates.append(Path("D:/JustSmart/RemoteRipplePy"))
        candidates.append(Path(os.environ.get("APPDATA", Path.home())) / "JustSmart" / "RemoteRipplePy")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            LOG.warning("配置目录不可用：%s", candidate)
            continue
        if os.access(candidate, os.W_OK):
            return candidate
    fallback = Path(os.environ.get("APPDATA", Path.home())) / "JustSmart" / "RemoteRipplePy"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "store.json"
        self.data: dict[str, Any] = {
            "version": 1,
            "hosts": [],
            "sessions": [],
            "settings": {
                "night_mode": False,
                "diagnostic_mode": False,
                "check_updates": False,
                "last_update": None,
            },
        }
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
                self.data["settings"] = {**self.data.get("settings", {}), **loaded.get("settings", {})}
        except (OSError, ValueError):
            LOG.exception("读取本地数据库失败")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".store-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    def hosts(self) -> list[Host]:
        hosts = []
        for raw in self.data.get("hosts", []):
            raw = dict(raw)
            raw["password"] = unprotect_password(raw.get("password", ""))
            hosts.append(Host.from_dict(raw))
        return hosts

    def get_host(self, address: str, port: int) -> Host | None:
        key = f"{address.casefold()}:{port}"
        return next((host for host in self.hosts() if host.key == key), None)

    def upsert_host(self, host: Host) -> None:
        raw = host.to_dict()
        raw["password"] = protect_password(host.password)
        hosts = self.data.setdefault("hosts", [])
        for index, old in enumerate(hosts):
            if f"{str(old.get('address', '')).casefold()}:{old.get('port', 5900)}" == host.key:
                hosts[index] = raw
                break
        else:
            hosts.append(raw)
        self.save()

    def remove_host(self, address: str, port: int) -> None:
        key = f"{address.casefold()}:{port}"
        self.data["hosts"] = [
            raw for raw in self.data.get("hosts", [])
            if f"{str(raw.get('address', '')).casefold()}:{raw.get('port', 5900)}" != key
        ]
        self.save()

    def sessions(self) -> list[Session]:
        sessions = []
        for raw in self.data.get("sessions", []):
            raw = dict(raw)
            raw["password"] = unprotect_password(raw.get("password", ""))
            sessions.append(Session.from_dict(raw))
        return sorted(sessions, key=lambda item: item.created, reverse=True)

    def add_session(self, session: Session) -> None:
        session.created = utc_now()
        raw = session.to_dict()
        raw["password"] = protect_password(session.password)
        sessions = [old for old in self.data.get("sessions", []) if f"{str(old.get('host', '')).casefold()}:{old.get('port', 5900)}" != session.key]
        sessions.insert(0, raw)
        self.data["sessions"] = sessions[:100]
        self.save()

    def remove_sessions(self, keys: set[str]) -> None:
        self.data["sessions"] = [
            raw for raw in self.data.get("sessions", [])
            if f"{str(raw.get('host', '')).casefold()}:{raw.get('port', 5900)}" not in keys
        ]
        self.save()
