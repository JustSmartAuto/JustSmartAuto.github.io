"""服务端发现页：把 VMess 参数以网页 / JSON 形式公布给局域网客户端。

服务端角色启动时在 ``0.0.0.0:18081`` 起一个极简 HTTP 服务：

    GET /            人类可读的说明页（VMess 参数、客户端操作步骤、服务状态）
    GET /v2ray.json  机器可读配置，客户端据此自动填写 UUID、端口、传输

客户端只需填服务端 IP 并勾选 v2ray 模式，点保存时本次请求会自动取回参数，
不用再手工抄 UUID。服务端与客户端同机时走 127.0.0.1 同样可用。
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from . import __version__
from .installer import (
    NGINX_PORT,
    PROXY_PASS,
    PROXY_USER,
    V2RAY_PORT,
    V2RAY_VMESS_PORT,
    _read_state,
    allow_port,
)

log = logging.getLogger("internet_proxy.discovery")

DISCOVERY_PORT = 18081
RULE_NAME = "InternetProxyPy-discovery"

Log = Callable[[str], None]


def payload() -> dict:
    """当前服务端的可公开参数；vmess.server 留空，客户端用自己填的 IP."""
    state = _read_state()
    uid = state.get("vmess_uuid")
    vmess_port = state.get("vmess_port")
    return {
        "product": "InternetProxyPy",
        "schema": 1,
        "version": __version__,
        "vmess": {
            "port": vmess_port if isinstance(vmess_port, int) else V2RAY_VMESS_PORT,
            "uuid": uid if isinstance(uid, str) else "",
            "network": "tcp",
            "path": "/vmess",
            "tls": False,
        },
        "socks": {"port": V2RAY_PORT, "user": PROXY_USER, "pass": PROXY_PASS},
        "http": {"port": NGINX_PORT, "status": "/status"},
    }


def local_ips() -> list[str]:
    try:
        return [
            ip
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]
            if not ip.startswith("127.")
        ]
    except OSError:
        return []


def page_html() -> str:
    """人类可读的说明页（无外部资源，离线可用）."""
    data = payload()
    vmess = data["vmess"]
    uid = vmess["uuid"] or "（尚未生成：请先到安装器页完成安装）"
    ips = local_ips()
    ip_text = " / ".join(ips) if ips else "本机局域网 IP"
    rows = [
        ("服务端 IP", ip_text),
        ("VMess 端口", str(vmess["port"])),
        ("UUID", uid),
        ("传输 / 路径", f"{vmess['network'].upper()}  {vmess['path']}"),
        ("TLS", "开启" if vmess["tls"] else "关闭"),
        ("SOCKS5", f"{data['socks']['port']}  账号 {data['socks']['user']} / {data['socks']['pass']}"),
        ("nginx", f"http://{ips[0] if ips else '本机'}:{data['http']['port']}/status"),
        ("软件版本", data["version"]),
    ]
    table = "\n".join(
        f"<tr><th>{key}</th><td><code>{value}</code></td></tr>" for key, value in rows
    )
    return f"""<!doctype html>
<html lang="zh-Hans"><head><meta charset="utf-8">
<title>InternetProxyPy 服务端配置</title>
<style>
body{{margin:0;padding:24px;background:#c0c0c0;font-family:"MS Sans Serif",Tahoma,sans-serif;font-size:13px;color:#000}}
.box{{max-width:640px;margin:0 auto;background:#c0c0c0;border:2px solid;border-color:#fff #404040 #404040 #fff;padding:12px}}
h1{{font-size:15px;margin:0 0 10px;background:#000080;color:#fff;padding:4px 8px}}
table{{border-collapse:collapse;width:100%;background:#fff;margin:8px 0}}
th,td{{border:1px solid #808080;padding:4px 8px;text-align:left;font-size:12px}}
th{{background:#c0c0c0;width:28%}}
code{{font-family:"Courier New",monospace}}
ol{{line-height:1.7;padding-left:22px}}
.note{{color:#404040;font-size:12px}}
</style></head><body>
<div class="box">
<h1>InternetProxyPy 服务端配置</h1>
<table>{table}</table>
<h2>客户端怎么连</h2>
<ol>
<li>客户端运行本软件，打开「设置」页；</li>
<li>「服务端」填 <code>{ips[0] if ips else '本机 IP'}</code>，勾选「通过 v2ray 服务端（VMess）代理互联网」；</li>
<li>点「保存」——程序会自动从本页取回 UUID、端口等参数并生效；</li>
<li>也可先点「从服务端获取配置」确认参数无误。</li>
</ol>
<p class="note">机器可读接口：<a href="/v2ray.json">/v2ray.json</a>（本软件客户端使用）。
启用 v2ray 模式后网关端口不再参与转发；局域网其它设备仍可用本机 <code>10082</code> SOCKS5。</p>
</div>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "InternetProxyPy"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/index.html"):
            body, ctype = page_html().encode("utf-8"), "text/html; charset=utf-8"
        elif path in ("/v2ray.json", "/config.json"):
            body = json.dumps(payload(), ensure_ascii=False, indent=2).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            self.send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)


class DiscoveryServer:
    """服务端角色的发现页 HTTP 服务（线程内 serve_forever）."""

    def __init__(self, port: int = DISCOVERY_PORT) -> None:
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None

    def start(self, log_fn: Log = print) -> bool:
        if self._httpd is not None:
            return True
        allow_port(self.port, "TCP", RULE_NAME, log_fn)
        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), _Handler)
        except OSError as exc:
            log_fn(f"发现页启动失败（端口 {self.port}）: {exc}")
            self._httpd = None
            return False
        threading.Thread(
            target=self._httpd.serve_forever, name="discovery-http", daemon=True
        ).start()
        ips = local_ips()
        log_fn(
            f"服务端发现页: http://{ips[0] if ips else '本机 IP'}:{self.port}/"
            f"（客户端填服务端 IP 即可自动获取配置）"
        )
        return True

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None


def fetch(host: str, port: int = DISCOVERY_PORT, timeout: float = 2.5) -> dict | None:
    """从服务端发现页取回配置；不可达或不是本软件服务端时返回 None."""
    host = host.strip().strip("[]")
    if not host:
        return None
    url = f"http://{host}:{port}/v2ray.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("product") != "InternetProxyPy":
        return None
    vmess = data.get("vmess")
    if not isinstance(vmess, dict) or not vmess.get("uuid"):
        return None
    return data
