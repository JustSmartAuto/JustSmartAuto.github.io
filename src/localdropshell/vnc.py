"""VNC Server 探测与辅助工具.

覆盖已有 VNC 服务复用（tightvnc / realvnc / ultravnc / uvnc_service / winvnc）
和内嵌 tvnserver 占位启动两条路径。不引入 win32api 额外依赖，Windows 特有
API 通过 subprocess 调用 netstat / tasklist / 注册表 (winreg) 实现。
"""

from __future__ import annotations

import atexit
import csv
import ctypes
import io
import os
import re
import socket
import subprocess
import sys
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path
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

def _hidden_console_kwargs() -> dict:
    """windowed(无控制台)程序拉起控制台子进程时隐藏一闪而过的黑框."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= 1  # STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    # CREATE_NO_WINDOW = 0x08000000
    return {"startupinfo": si, "creationflags": 0x08000000}



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
            errors="replace",  # 中文系统 GBK 输出在 UTF-8 模式下防解码崩溃
            timeout=5,
            **_hidden_console_kwargs(),
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
            errors="replace",  # 同上
            timeout=5,
            **_hidden_console_kwargs(),
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


# ---------------------------------------------------------------------------
# 7. 内嵌 TightVNC MSI：缺失时静默提权安装
# ---------------------------------------------------------------------------

TIGHTVNC_MSI_GLOB = "tightvnc-*-setup-64bit.msi"

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_WAIT_TIMEOUT = 0x00000102
_ERROR_CANCELLED = 1223


class _ShellExecuteInfoW(ctypes.Structure):
    """SHELLEXECUTEINFOW，仅声明 runas 提权 + 等待退出所需字段."""

    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


@dataclass
class TightVncInstallResult:
    """TightVNC 自动安装结果."""

    ok: bool
    action: str  # already / installed / cancelled / failed / missing
    detail: str = ""


def bundle_asset_dir() -> Path | None:
    """内嵌资源目录：PyInstaller 解包目录 ``_MEIPASS/assets``，开发时为项目 ``assets/``."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets")
    candidates.append(Path(__file__).resolve().parents[2] / "assets")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def find_tightvnc_installer() -> Path | None:
    """在内嵌资源目录中查找 TightVNC MSI（``tightvnc-*-setup-64bit.msi``）."""
    asset_dir = bundle_asset_dir()
    if asset_dir is None:
        return None
    matches = sorted(asset_dir.glob(TIGHTVNC_MSI_GLOB))
    return matches[0] if matches else None


def tightvnc_install_dir() -> Path | None:
    """已安装的 TightVNC 目录（以存在 ``tvnserver.exe`` 为准）."""
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        candidate = Path(base) / "TightVNC"
        if (candidate / "tvnserver.exe").is_file():
            return candidate
    return None


def _registry_tightvnc_installed() -> bool:
    """卸载注册表项中是否有 DisplayName 含 TightVNC 的条目（64/32 位视图都查）."""
    uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, uninstall_key, 0, winreg.KEY_READ | view
            ) as root:
                for index in range(2000):
                    try:
                        subkey = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, subkey) as entry:
                            display_name, _ = winreg.QueryValueEx(entry, "DisplayName")
                    except OSError:
                        continue
                    if "tightvnc" in str(display_name).lower():
                        return True
        except OSError:
            continue
    return False


def is_tightvnc_installed() -> bool:
    """TightVNC 是否已安装（安装目录存在 tvnserver.exe 或注册表有卸载项）."""
    return tightvnc_install_dir() is not None or _registry_tightvnc_installed()


def _run_elevated(exe: str, params: str, timeout: float = 900.0) -> tuple[bool, int, str]:
    """用 ShellExecuteExW(runas) 提权运行并等待结束，返回 ``(成功, 退出码, 说明)``.

    用户取消 UAC 时退出码为 1223（ERROR_CANCELLED）；超时/启动失败返回 -1。
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfoW)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    info = _ShellExecuteInfoW()
    info.cbSize = ctypes.sizeof(_ShellExecuteInfoW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = _SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == _ERROR_CANCELLED:
            return False, _ERROR_CANCELLED, "用户取消了 UAC 提权"
        return False, error, f"ShellExecuteExW 失败（错误码 {error}）"

    handle = info.hProcess
    if not handle:  # 极少数情况下拿不到进程句柄
        return True, 0, ""

    try:
        wait_ms = min(int(timeout * 1000), 0xFFFFFFFF)
        wait_rc = kernel32.WaitForSingleObject(handle, wait_ms)
        if wait_rc == _WAIT_TIMEOUT:
            return False, -1, f"等待安装完成超时（{timeout:.0f}s）"
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False, -1, "读取安装进程退出码失败"
        code = int(exit_code.value)
        return code == 0, code, "" if code == 0 else f"msiexec 返回码 {code}"
    finally:
        kernel32.CloseHandle(handle)


def install_tightvnc_silently(
    timeout: float = 900.0,
    log: Callable[[str], None] = print,
) -> TightVncInstallResult:
    """用内嵌 MSI 静默安装 TightVNC（``msiexec /i <msi> /qn /norestart``，自动弹 UAC）."""
    msi = find_tightvnc_installer()
    if msi is None:
        log(f"[tightvnc] 未找到内嵌安装包（assets/{TIGHTVNC_MSI_GLOB}）")
        return TightVncInstallResult(False, "missing", "内嵌 MSI 缺失")

    log(f"[tightvnc] 开始静默安装 {msi.name}（请批准 UAC 提权弹窗）")
    ok, code, detail = _run_elevated("msiexec.exe", f'/i "{msi}" /qn /norestart', timeout)
    if not ok:
        action = "cancelled" if code == _ERROR_CANCELLED else "failed"
        log(f"[tightvnc] 安装未完成：{detail or f'退出码 {code}'}")
        return TightVncInstallResult(False, action, detail or f"退出码 {code}")

    # msiexec 返回 0 后，注册表/安装目录落盘可能略有延迟，最多再等 30s
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and not is_tightvnc_installed():
        time.sleep(1.0)
    if not is_tightvnc_installed():
        log("[tightvnc] msiexec 执行成功，但未检测到 TightVNC 安装结果")
        return TightVncInstallResult(False, "failed", "安装后未检测到 TightVNC")

    install_dir = tightvnc_install_dir()
    detail = str(install_dir) if install_dir else "注册表已登记"
    log(f"[tightvnc] 安装完成：{detail}")
    return TightVncInstallResult(True, "installed", detail)


def ensure_tightvnc_installed(
    log: Callable[[str], None] = print,
) -> TightVncInstallResult:
    """确保本机有 TightVNC：已安装直接复用，缺失时用内嵌 MSI 静默提权安装.

    未安装但已有其他 VNC Server 在运行（如 UltraVNC/RealVNC）时不安装，
    避免抢占 5900 端口；这种情况继续沿用已有通道。
    """
    install_dir = tightvnc_install_dir()
    if install_dir is not None:
        log(f"[tightvnc] 已安装：{install_dir}，跳过")
        return TightVncInstallResult(True, "already", str(install_dir))
    if _registry_tightvnc_installed():
        log("[tightvnc] 注册表已登记 TightVNC，跳过")
        return TightVncInstallResult(True, "already", "注册表已登记")

    if detect_vnc().port is not None:
        log("[tightvnc] 未安装 TightVNC，但已有其他 VNC Server 在运行，跳过安装")
        return TightVncInstallResult(True, "already", "已有其他 VNC Server")

    return install_tightvnc_silently(log=log)
