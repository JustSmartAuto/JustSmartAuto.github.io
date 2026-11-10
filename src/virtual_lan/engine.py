from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import CORE_ARGS, INSTANCE_NAME, VIRTUAL_CIDR
from .paths import config_dir
from .resources import resource, validate_binaries


@dataclass(frozen=True)
class Status:
    state: str = "已停止"
    virtual_ip: str = "等待分配"
    hostname: str = "-"
    peers: int = 0
    details: tuple[str, ...] = field(default_factory=tuple)


class EasyTierEngine:
    def __init__(self, on_log: Callable[[str], None], on_status: Callable[[Status], None]) -> None:
        self.on_log = on_log
        self.on_status = on_status
        self._process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._rpc_portal = ""
        self._api_warning_logged = False
        self.running = False

    @staticmethod
    def _available_rpc_portal() -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return f"127.0.0.1:{sock.getsockname()[1]}"

    @staticmethod
    def _stop_orphaned_cores() -> None:
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='easytier-core.exe'\" | "
            "Where-Object {$_.CommandLine -like '*_MEI*' -and "
            "$_.CommandLine -like '*--network-name justsmart*'} | "
            "ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            validate_binaries()
            self._stop_orphaned_cores()
            self._stop.clear()
            self._rpc_portal = self._available_rpc_portal()
            log_dir = config_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                self._process = subprocess.Popen(
                    [
                        str(resource("easytier-core.exe")), *CORE_ARGS,
                        "--rpc-portal", self._rpc_portal,
                        "--file-log-dir", str(log_dir),
                    ],
                    cwd=str(resource("easytier-core.exe").parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=flags,
                )
            except OSError as exc:
                self.on_log(f"启动失败：{exc}")
                self.on_status(Status(state="启动失败"))
                return
            self.running = True
            self.on_log(f"EasyTier 已启动，正在通过 DHCP 获取 {VIRTUAL_CIDR} 网段地址 ...")
            self.on_status(Status(state="正在连接"))
            threading.Thread(target=self._read_output, args=(self._process,), daemon=True, name="easytier-output").start()
            self._poll_thread = threading.Thread(target=self._poll, daemon=True, name="easytier-status")
            self._poll_thread.start()

    def _read_output(self, process: subprocess.Popen) -> None:
        if process.stdout:
            for line in process.stdout:
                if line.strip():
                    self.on_log(line.strip())
        code = process.wait()
        with self._lock:
            current = process is self._process
            if current:
                self.running = False
                self._process = None
        if current and not self._stop.is_set():
            self.on_log(f"EasyTier 意外退出，退出码：{code}")
            self.on_status(Status(state="连接中断"))

    def _cli(self, command: str) -> object:
        result = subprocess.run(
            [str(resource("easytier-cli.exe")), "-p", self._rpc_portal, "-n", INSTANCE_NAME, "-o", "json", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"cli 退出码 {result.returncode}")
        return json.loads(result.stdout)

    @staticmethod
    def _local_virtual_ip() -> str:
        result = subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32" / "ipconfig.exe"), "/all"],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout.decode("mbcs", errors="replace")
        match = re.search(r"\b10\.168\.10\.(?:[1-9]|[1-9]\d|1\d\d|2[0-4]\d|25[0-4])\b", output)
        return match.group(0) if match else "等待分配"

    def _poll(self) -> None:
        while not self._stop.wait(2):
            try:
                node = self._cli("node")
                peers = self._cli("peer")
                self.on_status(parse_status(node, peers))
                self._api_warning_logged = False
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
                if self._stop.is_set():
                    continue
                if not self._api_warning_logged:
                    self.on_log("CLI 状态 API 不可用，已切换为 Windows 网卡状态检测")
                    self._api_warning_logged = True
                try:
                    virtual_ip = self._local_virtual_ip()
                except (OSError, subprocess.TimeoutExpired):
                    virtual_ip = "等待分配"
                connected = virtual_ip != "等待分配"
                self.on_status(Status(
                    state="已连接" if connected else "正在获取虚拟 IP",
                    virtual_ip=virtual_ip,
                    details=("CLI 节点详情不可用；虚拟 IP 来自 Windows 网卡检测",),
                ))

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self.running = False
            self._stop.set()
        if process and process.poll() is None:
            try:
                process.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=5)
        if self._poll_thread and self._poll_thread is not threading.current_thread():
            self._poll_thread.join(timeout=2)
        self.on_status(Status())


def _items(value: object) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("peers", "peer_infos", "routes", "items", "nodes"):
            if isinstance(value.get(key), list):
                return _items(value[key])
    return []


def _find_values(value: object, names: set[str]):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in names:
                yield item
            yield from _find_values(item, names)
    elif isinstance(value, list):
        for item in value:
            yield from _find_values(item, names)


def parse_status(node: object, peers: object) -> Status:
    peer_items = _items(peers)
    ips: list[str] = []
    for value in _find_values(node, {"ipv4", "virtual_ipv4", "ip", "ip_addr", "virtual_ip"}):
        text = str(value.get("addr", value)) if isinstance(value, dict) else str(value)
        candidate = text.split("/")[0]
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address in ipaddress.ip_network(VIRTUAL_CIDR):
            ips.append(candidate)
    hostnames = [str(v) for v in _find_values(node, {"hostname"}) if v]
    details = []
    for item in peer_items:
        hostname = next(iter(_find_values(item, {"hostname"})), "节点")
        peer_ip = next(iter(_find_values(item, {"virtual_ip", "virtual_ipv4", "ipv4", "ip_addr"})), "未知")
        cost = next(iter(_find_values(item, {"cost", "latency"})), "-")
        details.append(f"{hostname}    IP：{peer_ip}    延迟/路径：{cost}")
    ip = ips[0] if ips else "等待分配"
    state = "已连接" if ips else "正在获取虚拟 IP"
    return Status(state=state, virtual_ip=ip, hostname=hostnames[0] if hostnames else "-", peers=len(peer_items), details=tuple(details))
