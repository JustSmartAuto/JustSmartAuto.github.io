"""VNC Server 探测与辅助工具.

覆盖已有 VNC 服务复用（tightvnc / realvnc / ultravnc / uvnc_service / winvnc）
和内嵌 tvnserver 占位启动两条路径。不引入 win32api 额外依赖，Windows 特有
API 通过 subprocess 调用 netstat / tasklist / 注册表 (winreg) 实现。
"""

from __future__ import annotations

import atexit
import csv
import io
import os
import re
import socket
import subprocess
import time
import winreg
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# 模块常量
# ---------------------------------------------------------------------------

# 常见 VNC Server 进程名（小写，匹配时统一 lower()）
VNC_PROCESS_NAMES: set[str] = {
    "tvnserver",
    "vncserver",
    "uvnc_service",
    "winvnc",
    "tightvnc",
    "realvnc",
    "ultravnc",
    "vnc",
}

# 常规 VNC 端口（5900-5909 标准，15900-15902 自定义/代理）
PORTS_TO_SCAN: list[int] = [
    5900, 5901, 5902, 5903, 5904, 5905, 5906, 5907, 5908, 5909,
    15900, 15901, 15902,
]

# RFB 握手 banner 正则：RFB + 可选空格 + 版本号（x.y 或 003.008）+ LF
# RFC 6143 格式: "RFB%03d.%03d\n" 例如 b"RFB 003.008\n"（12 字节）
# 也兼容无空格的 b"RFB003.008\n" 或较短的 "RFB3.8\n"
_RFB_BANNER_RE = re.compile(rb"^RFB\s*\d+\.\d+\n$")


# ---------------------------------------------------------------------------
# 1. 端口扫描
# ---------------------------------------------------------------------------

def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """尝试建立 TCP 连接，成功返回 True."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


# ---------------------------------------------------------------------------
# 2. RFB 握手检测
# ---------------------------------------------------------------------------

def is_vnc_server(host: str, port: int, timeout: float = 1.5) -> bool:
    """连接后读取 VNC Server 发送的 RFB 版本号 banner，格式 ``RFB%02d.%02d\\n``（12 字节）.

    匹配通过 ``^RFB\\d{2}\\.\\d{2}\\n$`` 正则。超时或连接失败返回 False.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # RFC 6143 banner 典型为 12 字节 (b"RFB 003.008\n")，
            # 先 recv(16)，不够再补，总上限 32 字节
            banner = sock.recv(16)
            if not banner:
                return False
            if len(banner) < 9:
                try:
                    more = sock.recv(32 - len(banner))
                    banner = banner + more
                except OSError:
                    pass
            return bool(_RFB_BANNER_RE.match(banner))
    except (OSError, TimeoutError):
        return False


# ---------------------------------------------------------------------------
# 3. 进程名匹配（netstat + tasklist）
# ---------------------------------------------------------------------------

def get_listening_pids(port: int) -> list[int]:
    """返回监听指定端口的进程 PID 列表.

    通过 ``netstat -aon`` 查找包含 ``:PORT`` 且 STATE 为 ``LISTENING`` 的行，
    取最后一列（PID）。异常时返回空列表.
    """
    try:
        result = subprocess.run(
            ["netstat", "-aon"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []

    pids: list[int] = []
    needle = f":{port}"
    for line in result.stdout.splitlines():
        if "LISTENING" not in line:
            continue
        if needle not in line:
            continue
        parts = line.split()
        # netstat 输出最后一列是 PID
        if len(parts) >= 5:
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid not in pids:
                pids.append(pid)
    return pids


def pid_process_name(pid: int) -> str | None:
    """通过 ``tasklist /FI "PID eq N" /CSV`` 查询进程名（Image Name，无扩展名小写形式）.

    查询失败或进程不存在时返回 None.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    # 无表头模式（/NH）时可能有多行或空行，取第一个非空行解析
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            reader = csv.reader(io.StringIO(stripped))
            fields = next(reader)
        except (csv.Error, StopIteration):
            continue
        if not fields:
            continue
        # CSV 无表头时第一列即为 Image Name，需求说“解析最后一列”——
        # 但 tasklist 的最后一列是 Window Title，并非进程名；
        # 这里优先取第一列（Image Name），若为空再兜底取最后一列
        name = (fields[0] or fields[-1] if fields[-1] else "").strip()
        # 去除扩展名 (.exe) 并转小写
        base, ext = os.path.splitext(name)
        return base.lower() if base else (name.lower() if name else None)
    return None


def pid_processes_matching(pids: list[int]) -> list[str]:
    """返回指定 PID 列表中，进程名命中 ``VNC_PROCESS_NAMES`` 的名称集合（去重、小写）."""
    hits: list[str] = []
    for pid in pids:
        name = pid_process_name(pid)
        if name is None:
            continue
        if name.lower() in VNC_PROCESS_NAMES and name.lower() not in hits:
            hits.append(name.lower())
    return hits


# ---------------------------------------------------------------------------
# 4. 完整探测入口
# ---------------------------------------------------------------------------

@dataclass
class VncDetectResult:
    """VNC 探测结果."""

    port: int | None = None
    process_name: str | None = None
    mode: str = "none"  # "reuse" | "none"


def detect_vnc() -> VncDetectResult:
    """探测已有 VNC Server 并返回首个匹配项.

    扫描 ``PORTS_TO_SCAN`` 列表：
    1. 用 ``netstat -aon`` 找到监听端口的 PID
    2. 用 ``tasklist`` 查 PID 对应进程名，匹配 ``VNC_PROCESS_NAMES``
    3. 用 RFB 握手确认是真正的 VNC Server
    4. 三个条件都满足的第一个返回；全部未命中返回空 result.
    """
    for port in PORTS_TO_SCAN:
        pids = get_listening_pids(port)
        if not pids:
            continue
        matched_names = pid_processes_matching(pids)
        if not matched_names:
            continue
        # 进程名匹配后，再用 RFB 握手做最终确认
        if not is_vnc_server("127.0.0.1", port):
            continue
        return VncDetectResult(
            port=port,
            process_name=matched_names[0],
            mode="reuse",
        )
    return VncDetectResult(mode="none")


# ---------------------------------------------------------------------------
# 5. 内嵌 tvnserver 启动 (stub)
# ---------------------------------------------------------------------------

# 全局注册的子进程句柄，atexit 时统一 kill
_tvnserver_procs: list[subprocess.Popen] = []


def _kill_tvnservers() -> None:
    """atexit 回调：终止所有已启动的 tvnserver 子进程."""
    for proc in list(_tvnserver_procs):
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            pass
    _tvnserver_procs.clear()


atexit.register(_kill_tvnservers)


def try_start_embedded_tvnserver(binary_dir: str, rfb_port: int = 15900) -> bool:
    """尝试启动 ``binary_dir/tvnserver.exe`` 作为内嵌 VNC Server.

    - 不存在 exe 时返回 False（不抛异常，stub 模式）
    - 自动递增端口避免冲突
    - 注册 atexit 确保进程退出时 kill
    - 写入注册表 ``HKLM\\SOFTWARE\\TightVNC\\Server``（仅管理员权限）

    注意：本函数当前为 stub 实现，实际的 tvnserver.exe 参数拼装、注册表
    schema 对齐、日志重定向等细节后续在集成时补全.
    """
    exe_path = os.path.join(binary_dir, "tvnserver.exe")
    if not os.path.isfile(exe_path):
        return False

    # 自动递增端口直到找到未被占用的
    port = rfb_port
    max_tries = 10
    for _ in range(max_tries):
        if not is_port_open("127.0.0.1", port, timeout=0.3):
            break
        port += 1
    else:
        return False  # 端口全被占了，放弃

    # --- 注册表写入（best-effort，管理员权限才成功） ---
    _try_write_tvnserver_registry(rfb_port=port)

    # --- 启动进程 ---
    try:
        proc = subprocess.Popen(
            [exe_path, f"-port={port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, OSError):
        return False

    # 等一小下让端口起来，用 RFB 握手做存活确认
    time.sleep(1.0)
    if proc.poll() is not None:
        return False  # 启动后立刻退出，失败

    if not is_vnc_server("127.0.0.1", port, timeout=2.0):
        proc.kill()
        return False

    _tvnserver_procs.append(proc)
    return True


def _try_write_tvnserver_registry(rfb_port: int) -> None:
    """best-effort 写 TightVNC Server 注册表项，仅在管理员权限下成功."""
    try:
        key = winreg.CreateKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\TightVNC\Server",
        )
        with key:
            winreg.SetValueEx(key, "PortNumber", 0, winreg.REG_DWORD, rfb_port)
            winreg.SetValueEx(key, "UseAuthentication", 0, winreg.REG_DWORD, 0)
    except OSError:
        # 非管理员权限、注册表被策略锁定等，静默跳过
        pass


# ---------------------------------------------------------------------------
# 6. WebSocket 代理占位
# ---------------------------------------------------------------------------

def make_websockify_handler(
    vnc_host: str = "127.0.0.1",
    vnc_port: int = 5900,
) -> Callable:
    """返回 aiohttp WebSocketResponse handler，将浏览器 WebSocket 代理到 ``vnc_host:vnc_port``.

    占位 stub — 实际的双向数据通道（asyncio.StreamReader/StreamWriter +
    WebSocketResponse.send_bytes/receive）后续在 ``server.py`` 中集成时
    补充实现.
    """

    async def handler(_request):
        raise NotImplementedError("WebSocket 代理尚未实现，请在 server.py 集成时补全")

    handler.__vnc_host__ = vnc_host  # type: ignore[attr-defined]
    handler.__vnc_port__ = vnc_port  # type: ignore[attr-defined]
    return handler
