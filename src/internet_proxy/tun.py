"""系统级透明代理：wintun 虚拟网卡 + tun2socks 核心.

原理：tun2socks 用 wintun.dll 创建虚拟网卡（InternetProxyPy，198.18.0.1），
把系统 TCP/UDP 流量在用户态协议栈还原后交给本地 SOCKS5
（127.0.0.1:cfg.port），用户无需给任何应用配置代理，打开软件即上网。

依赖（assets/bin/，打包时随 assets 内嵌）：
    - tun2socks-windows-amd64.exe  https://github.com/xjasonlyu/tun2socks
    - wintun.dll                   https://www.wintun.net

路由策略：0.0.0.0/1 + 128.0.0.0/1 两条覆盖路由指向虚拟网卡（不覆盖物理
默认路由）；网关本身加 /32 直连路由防止隧道回环。规则模式下局域网目标
由 SOCKS5 客户端直连，全局模式同理由客户端决定，路由层无需区分。

需要管理员权限；核心或驱动缺失时自动回退为纯 SOCKS5 模式并写日志。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("internet_proxy.tun")

TUN_IP = "198.18.0.1"
TUN_NAME = "InternetProxyPy"
CORE_EXE = "tun2socks-windows-amd64.exe"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _bin_dir() -> Path | None:
    """查找 bin 目录：exe 同目录 → PyInstaller 解包目录 → 源码 assets/bin."""
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [exe_dir / "bin", exe_dir / "assets" / "bin"]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "assets" / "bin")
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / "bin")
    for d in candidates:
        if (d / CORE_EXE).is_file():
            return d
    return None


def _is_admin() -> bool:
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)


def _physical_gateway() -> str | None:
    """取物理网卡默认网关（隧道流量的真实出口），用于网关 /32 直连路由."""
    try:
        r = _run([
            "powershell", "-NoProfile", "-Command",
            "Get-NetRoute -DestinationPrefix 0.0.0.0/0 "
            "| Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty NextHop",
        ])
        gw = r.stdout.decode("utf-8", "ignore").strip()
        return gw or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _physical_ipv4() -> str | None:
    """取物理网卡 IPv4：tun 接管后 127.0.0.1 可能被路由策略影响，
    tun2socks 回连本地 SOCKS5 改用物理网卡地址更稳妥."""
    try:
        r = _run([
            "powershell", "-NoProfile", "-Command",
            "$if = Get-NetRoute -DestinationPrefix 0.0.0.0/0 "
            "| Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty InterfaceAlias; "
            "Get-NetIPAddress -InterfaceAlias $if -AddressFamily IPv4 "
            "| Select-Object -First 1 -ExpandProperty IPAddress",
        ])
        ip = r.stdout.decode("utf-8", "ignore").strip()
        return ip or None
    except (OSError, subprocess.TimeoutExpired):
        return None


class TunProxy:
    """透明代理生命周期：起核心进程 → 配网卡地址与路由 → 停止时全部清理."""

    def __init__(self, socks_port: int, gateway: str) -> None:
        self.socks_port = socks_port
        self.gateway = gateway
        self.proc: subprocess.Popen | None = None
        self._routes_added = False
        self._gw_route_added = False

    @staticmethod
    def available() -> bool:
        return _bin_dir() is not None

    def start(self) -> bool:
        """启动透明代理；缺依赖/权限不足时返回 False（回退纯 SOCKS5）."""
        bin_dir = _bin_dir()
        if bin_dir is None:
            log.warning("未找到 bin/%s，回退为纯 SOCKS5 模式", CORE_EXE)
            return False
        if not _is_admin():
            log.warning("透明代理需管理员权限，回退为纯 SOCKS5 模式")
            return False

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")  # 让核心找到 wintun.dll
        socks_addr = _physical_ipv4() or "127.0.0.1"
        try:
            self.proc = subprocess.Popen(
                [
                    str(bin_dir / CORE_EXE),
                    "--device", f"tun://{TUN_NAME}",
                    "--proxy", f"socks5://{socks_addr}:{self.socks_port}",
                    "--loglevel", "warning",
                ],
                cwd=str(bin_dir), env=env, creationflags=_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            import threading

            def drain() -> None:
                assert self.proc is not None and self.proc.stderr is not None
                for line in self.proc.stderr:
                    log.warning("[tun2socks] %s", line.rstrip())

            threading.Thread(target=drain, daemon=True).start()
        except OSError as exc:
            log.warning("透明代理核心启动失败: %s，回退纯 SOCKS5", exc)
            return False

        time.sleep(1.5)  # 等虚拟网卡创建
        if self.proc.poll() is not None:
            log.warning("透明代理核心异常退出，回退纯 SOCKS5")
            self.proc = None
            return False
        try:
            self._configure()
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("透明代理配置失败: %s", exc)
            self.stop()
            return False
        log.info("透明代理已启用：虚拟网卡 %s (%s)，系统流量自动走代理", TUN_NAME, TUN_IP)
        return True

    def _configure(self) -> None:
        # wintun 网卡不支持 netsh 静态地址（报"找不到元素"），用 NetTCPIP cmdlet
        r = _run([
            "powershell", "-NoProfile", "-Command",
            f"Get-NetIPAddress -InterfaceAlias {TUN_NAME} -AddressFamily IPv4 "
            "-ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false; "
            f"New-NetIPAddress -InterfaceAlias {TUN_NAME} -IPAddress {TUN_IP} "
            "-PrefixLength 24 -ErrorAction Stop | Out-Null",
        ])
        if r.returncode != 0:
            raise OSError("配置网卡地址失败: " + r.stderr.decode("utf-8", "ignore").strip())

        for net in ("0.0.0.0", "128.0.0.0"):
            r = _run([
                "powershell", "-NoProfile", "-Command",
                f"New-NetRoute -DestinationPrefix {net}/1 -InterfaceAlias {TUN_NAME} "
                f"-NextHop {TUN_IP} -RouteMetric 5 -ErrorAction SilentlyContinue | Out-Null",
            ])
            if r.returncode != 0:
                log.warning("添加覆盖路由 %s/1 失败: %s", net, r.stderr.decode("utf-8", "ignore").strip())
        self._routes_added = True

        # 网关必须走物理出口，否则隧道流量被拉回虚拟网卡形成回环
        phys_gw = _physical_gateway()
        if phys_gw:
            r = _run([
                "powershell", "-NoProfile", "-Command",
                f"New-NetRoute -DestinationPrefix {self.gateway}/32 -NextHop {phys_gw} "
                "-RouteMetric 1 -ErrorAction SilentlyContinue | Out-Null",
            ])
            self._gw_route_added = r.returncode == 0

    def stop(self) -> None:
        if self._routes_added:
            try:
                _run([
                    "powershell", "-NoProfile", "-Command",
                    "Get-NetRoute -DestinationPrefix 0.0.0.0/1,128.0.0.0/1 "
                    f"-InterfaceAlias {TUN_NAME} -ErrorAction SilentlyContinue "
                    "| Remove-NetRoute -Confirm:$false",
                ])
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._routes_added = False
        if self._gw_route_added:
            try:
                _run([
                    "powershell", "-NoProfile", "-Command",
                    f"Remove-NetRoute -DestinationPrefix {self.gateway}/32 "
                    "-Confirm:$false -ErrorAction SilentlyContinue",
                ])
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._gw_route_added = False
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        log.info("透明代理已停止，路由已清理")
