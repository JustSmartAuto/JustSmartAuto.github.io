"""Everything 文件名搜索集成（voidtools Everything 1.4.1 HTTP Server JSON API）.

内嵌 ``assets/Everything-1.4.1.1032.x64-Setup.exe``（NSIS 安装包，``/S`` 静默开关）。
三种状态：

- running   ：HTTP Server（127.0.0.1:21123）可达，直接搜索；
- installed ：Everything.exe 已安装但 HTTP 未开 → 写 ``%APPDATA%/Everything/Everything.ini``
              后 ``Everything.exe -minimized`` 以用户会话启动；
- missing   ：未安装 → 页面确认后用内嵌安装包 ``/S`` 静默提权安装（弹一次 UAC）。

搜索语法限定当前目录子树：``"D:\\dir\\" 关键词``（带尾反斜杠的双引号路径前缀）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import vnc as _vnc

logger = logging.getLogger("localdropshell.everything")

HTTP_PORT = 21123
INSTALLER_NAME = "Everything-1.4.1.1032.x64-Setup.exe"
STARTUP_TIMEOUT = 30.0
SEARCH_LIMIT = 200

# FILETIME（1601-01-01 起 100ns 计数）与 Unix 纪元的差值
_FILETIME_EPOCH_OFFSET = 116444736000000000


def find_installer() -> Path | None:
    """内嵌 Everything 安装包路径."""
    asset_dir = _vnc.bundle_asset_dir()
    if asset_dir is None:
        return None
    candidate = asset_dir / INSTALLER_NAME
    return candidate if candidate.is_file() else None


def find_everything_exe() -> Path | None:
    """已安装的 Everything.exe（标准 Program Files 路径）."""
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        candidate = Path(base) / "Everything" / "Everything.exe"
        if candidate.is_file():
            return candidate
    return None


def http_alive(timeout: float = 1.5) -> bool:
    """Everything HTTP Server（127.0.0.1:21123）是否已就绪."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{HTTP_PORT}/", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def detect() -> str:
    """返回 ``running`` / ``installed`` / ``missing``."""
    if http_alive():
        return "running"
    if find_everything_exe() is not None:
        return "installed"
    return "missing"


# ---------------------------------------------------------------------------
# 配置（Everything.ini 为扁平 key=value，仅启动时读取，退出时写回）
# ---------------------------------------------------------------------------

def _ini_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(appdata) / "Everything" / "Everything.ini"


def _write_ini(log: Callable[[str], None] = print) -> None:
    """写入/更新 http_server_* 配置；保留文件其余内容."""
    ini = _ini_path()
    ini.parent.mkdir(parents=True, exist_ok=True)
    want = {
        "http_server_enabled": "1",
        "http_server_port": str(HTTP_PORT),
        # 仅绑回环，避免局域网直连；1.4 不认识该键时会忽略，无害
        "http_server_listen_address": "127.0.0.1",
    }
    lines: list[str] = []
    if ini.is_file():
        try:
            lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
    remaining = dict(want)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")
    log(f"[everything] 已写入配置 {ini}")


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------

def _is_running() -> bool:
    """当前用户会话是否有 Everything.exe GUI 客户端在运行.

    会话 0（Services）里的是 Everything 索引服务（SYSTEM），不属于用户实例：
    它既收不到 ``-exit`` 窗口消息，也不应被关闭；用户客户端启动后会复用其索引。
    tasklist CSV 列：映像名称, PID, 会话名, 会话#, 内存使用。
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Everything.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    for line in result.stdout.splitlines():
        parts = [p.strip('"') for p in line.strip().split('","')]
        if len(parts) >= 3 and parts[0].lower() == "everything.exe":
            if parts[2].lower() != "services":
                return True
    return False


def _request_exit(exe: Path) -> None:
    """``Everything.exe -exit`` 关闭运行中的实例（退出时写回 ini，故须先于写配置）."""
    try:
        subprocess.run([str(exe), "-exit"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _is_running():
        time.sleep(0.5)


def ensure_running(log: Callable[[str], None] = print) -> bool:
    """确保 Everything HTTP Server 就绪：写 ini →（必要时重启实例）→ 轮询就绪."""
    if http_alive():
        return True
    exe = find_everything_exe()
    if exe is None:
        return False
    if _is_running():
        log("[everything] 实例已在运行，先关闭使其重读配置")
        _request_exit(exe)
    _write_ini(log)
    subprocess.Popen(  # noqa: S603 — 路径来自 Program Files 固定位置
        [str(exe), "-minimized"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if http_alive():
            log("[everything] HTTP Server 已就绪")
            return True
        time.sleep(0.5)
    log("[everything] 等待 HTTP Server 就绪超时")
    return False


# ---------------------------------------------------------------------------
# 静默安装（NSIS /S，复用 vnc._run_elevated 提权）
# ---------------------------------------------------------------------------

def install_and_start(log: Callable[[str], None] = print) -> tuple[bool, str]:
    """确保可用：未安装则内嵌安装包静默提权安装，再启动 HTTP Server."""
    if http_alive():
        return True, "already"
    exe = find_everything_exe()
    if exe is None:
        installer = find_installer()
        if installer is None:
            return False, f"内嵌安装包缺失（assets/{INSTALLER_NAME}）"
        log(f"[everything] 开始静默安装 {installer.name}（请批准 UAC 提权弹窗）")
        ok, code, detail = _vnc._run_elevated(str(installer), "/S", timeout=600.0)
        if not ok:
            return False, detail or f"安装退出码 {code}"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and exe is None:
            exe = find_everything_exe()
            if exe is None:
                time.sleep(1.0)
        if exe is None:
            return False, "安装后未检测到 Everything.exe"
        log(f"[everything] 安装完成：{exe}")
    if ensure_running(log):
        return True, "started"
    return False, "Everything HTTP 服务未能启动"


# ---------------------------------------------------------------------------
# 搜索（HTTP JSON API）
# ---------------------------------------------------------------------------

def _syntax(query: str, base: str | None) -> str:
    """拼 Everything 搜索语法；base 非空时限定该目录子树."""
    if not base:
        return query
    prefix = base.replace("/", "\\").rstrip("\\") + "\\"
    return f'"{prefix}" {query}'


def _filetime_to_unix(value: object) -> int:
    """Everything dateModified（FILETIME 数值/字符串）转 Unix 秒；异常返回 0."""
    try:
        ft = int(str(value))
    except (TypeError, ValueError):
        return 0
    unix = (ft - _FILETIME_EPOCH_OFFSET) // 10_000_000
    return unix if unix > 0 else 0


def search(query: str, base: str | None, limit: int = SEARCH_LIMIT) -> list[dict]:
    """经 HTTP JSON API 搜索，返回与列目录同构的 entry 列表（多一个 ``full`` 绝对路径）.

    1.4.1 的 JSON 默认只给 ``type``/``name``；``path``/``size``/``date_modified``
    （下划线命名，FILETIME 字符串）需显式开启对应 ``*_column=1``。
    """
    params = urllib.parse.urlencode({
        "search": _syntax(query, base),
        "json": "1",
        "count": str(limit),
        "offset": "0",
        "path_column": "1",
        "size_column": "1",
        "date_modified_column": "1",
    })
    url = f"http://127.0.0.1:{HTTP_PORT}/?{params}"
    with urllib.request.urlopen(url, timeout=10.0) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))

    entries: list[dict] = []
    for item in data.get("results") or []:
        name = str(item.get("name") or "")
        if not name:
            continue
        folder_path = str(item.get("path") or "")  # 所在目录，不含 name
        full = (folder_path + "\\" + name) if folder_path else name
        is_dir = item.get("type") == "folder"
        raw_size = item.get("size")
        try:
            size = int(str(raw_size)) if raw_size is not None else 0
        except (TypeError, ValueError):
            size = 0
        entries.append({
            "name": name,
            "dir": is_dir,
            "size": 0 if is_dir else size,
            "mtime": _filetime_to_unix(item.get("date_modified") or item.get("dateModified")),
            "full": full.replace("\\", "/"),
        })
    return entries
