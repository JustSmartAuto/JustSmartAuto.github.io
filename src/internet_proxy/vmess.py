"""客户端 VMess 上行：把 SOCKS5 入口交给内嵌的 v2ray 核心。

启用后客户端不再走自研隧道，而是由本机 v2ray 生成以下链路：

    应用 / tun2socks / 局域网设备 → 本机 SOCKS5 (0.0.0.0:cfg.port)
        → VMess 出站 → v2ray 服务端 → 互联网

对上层完全透明：透明代理与局域网其它设备仍只认本机 SOCKS5 端口。
规则模式下把私网网段直连，避免访问同局域网设备时绕到服务端。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.request import ProxyHandler, build_opener

from .config import Config
from .installer import (
    CREATE_NO_WINDOW,
    V2RAY_DIR,
    is_installed,
    port_open,
)

log = logging.getLogger("internet_proxy.vmess")

CLIENT_CONF = V2RAY_DIR / "client.json"
CLIENT_ERROR_LOG = V2RAY_DIR / "client-error.log"

# 服务端发现页端口：借它的 HTTP Date 判断两端时钟偏差
DISCOVERY_PORT = 18081
# VMess 时间戳容差约 90 秒，留 30 秒余量就提前校正
CLOCK_TOLERANCE = 60
_opener = build_opener(ProxyHandler({}))  # 显式忽略系统代理，避免校时被代理劫持

# 规则模式下的直连网段：与 client.is_lan_host 的判断保持一致
PRIVATE_CIDRS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
]

Log = Callable[[str], None]


def normalize_uuid(text: str) -> str | None:
    """校验并规范化 VMess UUID；非法返回 None."""
    try:
        return str(uuid.UUID(text.strip()))
    except (ValueError, AttributeError):
        return None


def _is_admin() -> bool:
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _server_date(cfg: Config) -> datetime | None:
    """读取服务端发现页的 HTTP Date；服务端未开发现页时返回 None."""
    host = cfg.v2ray_server.strip().strip("[]")
    if not host:
        return None
    if ":" in host:  # IPv6 需要加方括号
        host = f"[{host}]"
    try:
        with _opener.open(f"http://{host}:{DISCOVERY_PORT}/", timeout=2) as resp:
            raw = resp.headers.get("Date")
    except OSError:
        return None
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _set_system_time(target: datetime) -> bool:
    """调用 PowerShell 改本机时间（需要管理员）；成功返回 True."""
    local = target.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Date -Date '{local}'"],
            capture_output=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def align_clock(cfg: Config, log_fn: Log) -> None:
    """以服务端时间为准校正本机时钟。

    VMess 会校验时间戳，两端相差超过约 90 秒时服务端报 ``invalid user``，
    表现为隧道能连上但所有请求失败。工控机常无外网、CMOS 也易漂移，因此
    启动时主动对齐；非管理员只提示，不改系统时间。
    """
    server_time = _server_date(cfg)
    if server_time is None:
        return
    skew = (datetime.now(timezone.utc) - server_time.astimezone(timezone.utc)).total_seconds()
    if abs(skew) <= CLOCK_TOLERANCE:
        log.debug("与服务端时钟偏差 %.0f 秒", skew)
        return
    log_fn(f"两端时钟相差 {skew:+.0f} 秒，超出 VMess 约 90 秒容差")
    if not _is_admin():
        log_fn("请把系统时间调整为服务端时间，否则 VMess 会报 invalid user")
        return
    if _set_system_time(server_time):
        log_fn(f"已按服务端时间校正系统时钟（偏差 {skew:+.0f} 秒）")
    else:
        log_fn("自动校时失败，请手动调整为服务端时间")


def build_config(cfg: Config, uid: str) -> dict:
    """生成 v2ray 客户端配置：SOCKS5 入站 + VMess 出站（+ 私网直连）."""
    stream: dict = {"network": cfg.v2ray_network}
    if cfg.v2ray_network == "ws":
        stream["wsSettings"] = {"path": cfg.v2ray_path}
    if cfg.v2ray_tls:
        stream["security"] = "tls"
        stream["tlsSettings"] = {"serverName": cfg.v2ray_server}

    conf: dict = {
        "log": {
            "loglevel": "warning",
            "access": "none",  # 常驻运行不写访问日志，避免长期增长
            "error": CLIENT_ERROR_LOG.as_posix(),
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "0.0.0.0",
                "port": cfg.port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True, "ip": "0.0.0.0"},
            }
        ],
        "outbounds": [
            {
                "tag": "vmess-out",
                "protocol": "vmess",
                "settings": {
                    "vnext": [
                        {
                            "address": cfg.v2ray_server,
                            "port": cfg.v2ray_port,
                            "users": [
                                {"id": uid, "alterId": 0, "security": "auto", "level": 0}
                            ],
                        }
                    ]
                },
                "streamSettings": stream,
            },
            {"tag": "direct", "protocol": "freedom", "settings": {}},
        ],
    }
    if cfg.proxy_mode == "rule":
        conf["routing"] = {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "ip": PRIVATE_CIDRS, "outboundTag": "direct"}
            ],
        }
    return conf


def write_config(cfg: Config, uid: str) -> Path:
    CLIENT_CONF.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_CONF.write_text(
        json.dumps(build_config(cfg, uid), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return CLIENT_CONF


def server_ip(cfg: Config) -> str | None:
    """解析服务端 IPv4，供透明代理加 /32 直连路由（防止隧道自身回环）."""
    host = cfg.v2ray_server.strip().strip("[]")
    if not host:
        return None
    try:
        addr = ipaddress.ip_address(host)
        return str(addr) if addr.version == 4 else None
    except ValueError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def _tail(path: Path, lines: int = 5) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    return "\n".join(data[-lines:])


class VmessUplink:
    """内嵌 v2ray 核心的生命周期（客户端角色，VMess 上行）."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None

    def start(self, log_fn: Log = print) -> bool:
        if not is_installed():
            log_fn("未安装 v2ray，无法使用 VMess 上行（可在安装器页一键安装）")
            return False
        if not self.cfg.v2ray_server.strip():
            log_fn("未填写 v2ray 服务端地址，无法使用 VMess 上行")
            return False
        uid = normalize_uuid(self.cfg.v2ray_uuid)
        if uid is None:
            log_fn("VMess UUID 无效（应形如 xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx）")
            return False
        self.cfg.v2ray_uuid = uid

        # 端口被上次残留的 v2ray 占用时，新进程会绑定失败，而就绪检查会被
        # 旧进程的监听误导成“启动成功”，这里直接快速失败并给出可操作提示。
        if port_open(self.cfg.port):
            log_fn(
                f"本机 SOCKS5 端口 {self.cfg.port} 已被占用（上次残留的 v2ray "
                "或本软件实例），请先结束该进程再启动"
            )
            return False

        align_clock(self.cfg, log_fn)

        path = write_config(self.cfg, uid)
        log_fn(
            f"VMess 上行: {self.cfg.v2ray_server}:{self.cfg.v2ray_port}"
            f"（{self.cfg.v2ray_network.upper()}"
            f"{' + TLS' if self.cfg.v2ray_tls else ''}）→ 本机 SOCKS5 0.0.0.0:{self.cfg.port}"
        )
        try:
            self._proc = subprocess.Popen(
                [str(V2RAY_DIR / "v2ray.exe"), "run", "-c", path.name],
                cwd=str(V2RAY_DIR),
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            log_fn(f"v2ray 启动失败: {exc}")
            return False

        deadline = time.time() + 5
        while time.time() < deadline:
            if self._proc.poll() is not None:
                log_fn(f"v2ray 退出（码 {self._proc.returncode}），请检查服务端配置")
                detail = _tail(CLIENT_ERROR_LOG)
                if detail:
                    log_fn(detail)
                self._proc = None
                return False
            if port_open(self.cfg.port):
                return True
            time.sleep(0.15)
        log_fn(f"v2ray 启动超时：本机 SOCKS5 {self.cfg.port} 未就绪")
        self.stop()
        return False

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
