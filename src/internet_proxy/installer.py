"""安装器：内嵌 nginx / v2ray 的静默安装、配置与启停。

安装目录与配置文件同根（默认 D:/JustSmart/InternetProxyPy）：
    nginx/   解压自 assets/nginx-1.31.8.zip
    v2ray/   解压自 assets/v2ray-windows-64.zip

服务形态（参考 reference/setup_v2ray_server_at_windows.md）：
    v2ray  SOCKS5 服务端，监听 0.0.0.0:11080，账号 justsmart / 密码 justsmart
    nginx  HTTP 静态站点，监听 0.0.0.0:18080，/status 为健康检查

端口取高位段，避开 1080 / 8080 这类常被其它代理、Web 服务占用的默认端口。

首次启动时 ``auto_install()`` 会在没有安装记录的情况下自动请求 UAC 提权并静默
安装；非管理员进程通过 ShellExecuteW("runas") 以 ``--install-extras`` 重新拉起
自身完成安装，用户只需在系统弹窗点一次“是”。
"""

from __future__ import annotations

import ctypes
import json
import shutil
import socket
import subprocess
import sys
import threading
import traceback
import zipfile
from pathlib import Path
from typing import Callable

from .config import config_dir

BASE_DIR = config_dir()
NGINX_DIR = BASE_DIR / "nginx"
V2RAY_DIR = BASE_DIR / "v2ray"
STATE_PATH = BASE_DIR / "installer.json"
LOG_PATH = BASE_DIR / "installer.log"

NGINX_PORT = 18080
V2RAY_PORT = 11080
PROXY_USER = "justsmart"
PROXY_PASS = "justsmart"

NGINX_ZIP = "nginx-1.31.8.zip"
V2RAY_ZIP = "v2ray-windows-64.zip"
CREATE_NO_WINDOW = 0x08000000

Log = Callable[[str], None]


# —— 基础工具 ——

def _asset(name: str) -> Path:
    """在源码目录 / 打包后的 _MEIPASS 中定位内嵌资源."""
    bases = [Path.cwd(), Path(__file__).resolve().parents[2]]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        bases.insert(0, Path(bundle))
    for base in bases:
        path = base / "assets" / name
        if path.is_file():
            return path
    return Path("assets") / name


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _read_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_installed() -> bool:
    return (NGINX_DIR / "nginx.exe").is_file() and (V2RAY_DIR / "v2ray.exe").is_file()


def status() -> dict:
    """安装 / 运行状态，供界面轮询."""
    return {
        "installed": is_installed(),
        "nginx_installed": (NGINX_DIR / "nginx.exe").is_file(),
        "v2ray_installed": (V2RAY_DIR / "v2ray.exe").is_file(),
        "nginx_running": _port_open(NGINX_PORT),
        "v2ray_running": _port_open(V2RAY_PORT),
        "base_dir": str(BASE_DIR),
    }


# —— 安装 ——

def _extract(zip_path: Path, dest: Path, strip_prefix: str = "") -> None:
    """解压到 dest；strip_prefix 用于去掉压缩包里的顶层目录."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if strip_prefix:
                if not name.startswith(strip_prefix):
                    continue
                name = name[len(strip_prefix):]
            if not name:
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(root):  # 防目录穿越
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _write_nginx_conf() -> None:
    (NGINX_DIR / "conf").mkdir(parents=True, exist_ok=True)
    (NGINX_DIR / "logs").mkdir(parents=True, exist_ok=True)
    conf = f"""worker_processes  1;
error_log  logs/error.log  warn;
pid        logs/nginx.pid;

events {{
    worker_connections  1024;
}}

http {{
    include       mime.types;
    default_type  application/octet-stream;
    access_log    logs/access.log;
    sendfile      on;
    keepalive_timeout  65;

    server {{
        listen       {NGINX_PORT};
        server_name  _;
        root         html;
        index        index.html;

        location /status {{
            default_type text/plain;
            return 200 "InternetProxyPy nginx ok\\n";
        }}

        location / {{
            try_files $uri $uri/ =404;
        }}
    }}
}}
"""
    (NGINX_DIR / "conf" / "nginx.conf").write_text(conf, encoding="utf-8")


def _write_v2ray_conf() -> None:
    conf = {
        "log": {
            "loglevel": "warning",
            "access": (V2RAY_DIR / "access.log").as_posix(),
            "error": (V2RAY_DIR / "error.log").as_posix(),
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "0.0.0.0",
                "port": V2RAY_PORT,
                "protocol": "socks",
                "settings": {
                    "auth": "password",
                    "accounts": [{"user": PROXY_USER, "pass": PROXY_PASS}],
                    "udp": True,
                    "ip": "0.0.0.0",
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {}}],
    }
    (V2RAY_DIR / "config.json").write_text(
        json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _allow_port(port: int, protocol: str, name: str, log: Log) -> None:
    """为服务端口加放行规则（参考文档中的防火墙步骤），失败只记日志."""
    try:
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        result = subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={name}", "dir=in", "action=allow",
                f"protocol={protocol}", f"localport={port}",
            ],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
        log(f"防火墙放行 {protocol} {port}: {'成功' if result.returncode == 0 else '失败'}")
    except OSError as exc:
        log(f"防火墙放行失败: {exc}")


def install(log: Log = print, start: bool = True) -> None:
    """解压并配置 nginx / v2ray，随后启动（start=False 供测试）."""
    log(f"安装目录: {BASE_DIR}")
    nginx_zip, v2ray_zip = _asset(NGINX_ZIP), _asset(V2RAY_ZIP)
    if not nginx_zip.is_file():
        raise FileNotFoundError(f"缺少内嵌资源 {NGINX_ZIP}")
    if not v2ray_zip.is_file():
        raise FileNotFoundError(f"缺少内嵌资源 {V2RAY_ZIP}")

    log(f"解压 nginx -> {NGINX_DIR}")
    _extract(nginx_zip, NGINX_DIR, strip_prefix="nginx-1.31.8/")
    log(f"解压 v2ray -> {V2RAY_DIR}")
    _extract(v2ray_zip, V2RAY_DIR)

    log("写入 nginx.conf")
    _write_nginx_conf()
    log("写入 v2ray config.json（SOCKS5 账号 justsmart）")
    _write_v2ray_conf()

    if is_admin():
        _allow_port(V2RAY_PORT, "TCP", "InternetProxyPy-v2ray-TCP", log)
        _allow_port(V2RAY_PORT, "UDP", "InternetProxyPy-v2ray-UDP", log)
        _allow_port(NGINX_PORT, "TCP", "InternetProxyPy-nginx-TCP", log)

    _write_state(
        {
            "installed": True,
            "nginx_port": NGINX_PORT,
            "v2ray_port": V2RAY_PORT,
            "user": PROXY_USER,
            "pass": PROXY_PASS,
        }
    )
    log("安装完成")
    if start:
        start_services(log)


def _listen_pids(port: int) -> set[int]:
    """从 netstat 找出监听指定端口的进程，避免误杀用户自己的同名程序."""
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, encoding="mbcs", errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except OSError:
        return pids
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            try:
                pids.add(int(parts[4]))
            except ValueError:
                continue
    return pids


def start_services(log: Log = print) -> None:
    if not is_installed():
        log("服务尚未安装，请先执行安装")
        return
    if not _port_open(V2RAY_PORT):
        subprocess.Popen(
            [str(V2RAY_DIR / "v2ray.exe"), "run", "-c", "config.json"],
            cwd=str(V2RAY_DIR),
            creationflags=CREATE_NO_WINDOW,
        )
        log(f"v2ray 已启动: SOCKS5 0.0.0.0:{V2RAY_PORT}（{PROXY_USER}/{PROXY_PASS}）")
    else:
        log(f"v2ray 已在运行: {V2RAY_PORT}")
    if not _port_open(NGINX_PORT):
        subprocess.Popen(
            [str(NGINX_DIR / "nginx.exe")],
            cwd=str(NGINX_DIR),
            creationflags=CREATE_NO_WINDOW,
        )
        log(f"nginx 已启动: http://本机:{NGINX_PORT}/status")
    else:
        log(f"nginx 已在运行: {NGINX_PORT}")


def stop_services(log: Log = print) -> None:
    nginx_exe = NGINX_DIR / "nginx.exe"
    if nginx_exe.is_file() and _port_open(NGINX_PORT):
        subprocess.run(
            [str(nginx_exe), "-s", "quit"],
            cwd=str(NGINX_DIR),
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
        )
        log("nginx 已停止")
    if _port_open(V2RAY_PORT):
        pids = _listen_pids(V2RAY_PORT)
        for pid in pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                creationflags=CREATE_NO_WINDOW,
                capture_output=True,
            )
        log("v2ray 已停止" if pids else "v2ray 端口已释放")


# —— 提权与首次自动安装 ——

def _spawn_elevated(flag: str) -> bool:
    """以管理员权限重新拉起自身；返回是否成功发起（用户取消 UAC 也会返回 False）."""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, flag
    else:
        script = Path(sys.argv[0]).resolve()
        exe, params = sys.executable, f'"{script}" {flag}'
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, str(BASE_DIR), 1
        )
    except (AttributeError, OSError):
        return False
    return rc > 32


def request_install(log: Log = print) -> bool:
    """管理员直接安装；否则弹 UAC 拉起 ``--install-extras``."""
    if is_admin():
        install(log)
        return True
    ok = _spawn_elevated("--install-extras")
    if ok:
        log("已请求管理员权限安装，请在 UAC 弹窗中确认")
    else:
        log("提权未完成（可能取消了 UAC），可在本页重试")
    return ok


def auto_install(log: Log = print) -> None:
    """首次启动自动安装一次：已安装或已尝试过则跳过."""
    if is_installed():
        return
    state = _read_state()
    if state.get("auto_attempted"):
        return
    state["auto_attempted"] = True
    _write_state(state)

    def worker() -> None:
        try:
            request_install(log)
        except Exception as exc:  # 自动安装失败不能影响主程序启动
            log(f"自动安装失败: {exc}")

    threading.Thread(target=worker, name="installer-auto", daemon=True).start()


def cli_install() -> int:
    """``--install-extras`` 入口：写日志后退出，供提权子进程调用."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        def log(message: str) -> None:
            print(message, file=fh, flush=True)

        try:
            log("=== 开始安装 ===")
            install(log)
            return 0
        except Exception:
            traceback.print_exc(file=fh)
            return 1
