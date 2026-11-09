"""开机自动启动：在 Windows 当前用户的 ``shell:startup`` 目录放置快捷方式.

用 Explorer 的启动文件夹而非注册表 Run 键，优点：用户可在「启动应用」里自行管理、
任务管理器可见/可禁用，卸载即删。快捷方式由 PowerShell 的 WScript.Shell COM 创建，
不引入 pywin32 依赖；脚本经 ``-EncodedCommand``（UTF-16LE）传入，规避中文路径转义问题。
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import sys
from pathlib import Path

from . import vnc as _vnc

logger = logging.getLogger("localdropshell.autostart")

SHORTCUT_NAME = "LocalFileDropShell.lnk"


def startup_dir() -> Path:
    """``shell:startup`` 对应的真实目录（当前用户）."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def is_enabled() -> bool:
    """快捷方式存在即视为已开启（与资源管理器里看到的状态一致）."""
    try:
        return shortcut_path().is_file()
    except OSError:
        return False


def _launch_command() -> tuple[str, str, str, str]:
    """返回 (target, arguments, working_dir, icon)，兼容打包与源码运行."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return str(exe), "", str(exe.parent), f"{exe},0"
    root = Path(__file__).resolve().parents[2]
    icon = root / "assets" / "drop3.ico"
    icon_loc = f"{icon},0" if icon.is_file() else f"{sys.executable},0"
    # 源码运行时优先 pythonw.exe，开机启动不弹控制台窗口
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = str(pythonw if pythonw.is_file() else sys.executable)
    return target, "-m localdropshell.cli", str(root), icon_loc


def _ps_quote(value: str) -> str:
    """PowerShell 单引号字符串字面量（内部单引号翻倍）."""
    return "'" + value.replace("'", "''") + "'"


def _run_powershell(script: str) -> None:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        **_vnc._hidden_console_kwargs(),
    )
    if proc.returncode != 0:
        raise OSError((proc.stderr or proc.stdout or "powershell 执行失败").strip())


def enable() -> Path:
    """创建开机启动快捷方式，返回其路径."""
    target, arguments, workdir, icon = _launch_command()
    lnk = shortcut_path()
    script = f"""$ErrorActionPreference = 'Stop'
$lnk = {_ps_quote(str(lnk))}
$parent = Split-Path -Parent $lnk
if (-not (Test-Path -LiteralPath $parent)) {{
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}}
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = {_ps_quote(target)}
$sc.Arguments = {_ps_quote(arguments)}
$sc.WorkingDirectory = {_ps_quote(workdir)}
$sc.IconLocation = {_ps_quote(icon)}
$sc.Description = {_ps_quote('LocalFileDropShell 开机启动')}
$sc.Save()
"""
    _run_powershell(script)
    if not lnk.is_file():
        raise OSError("快捷方式创建失败（文件未生成）")
    logger.info("已设置开机启动: %s", lnk)
    return lnk


def disable() -> None:
    """删除开机启动快捷方式（不存在时静默忽略）."""
    lnk = shortcut_path()
    try:
        lnk.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OSError(f"删除快捷方式失败: {exc}") from exc
    logger.info("已取消开机启动: %s", lnk)


def set_enabled(enabled: bool) -> None:
    if enabled:
        enable()
    else:
        disable()
