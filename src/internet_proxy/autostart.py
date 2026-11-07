"""开机自启：优先用计划任务（最高权限）注册，避免每次登录弹 UAC。

打包出的 exe 带 ``--uac-admin`` 清单，直接写 HKCU\\...\\Run 会在每次登录时
弹一次 UAC；计划任务以“最高权限”运行则静默启动，适合无人值守的工控机。
非管理员（如开发时用 ``python main.py``）无法创建最高权限任务，此时退回
注册表启动项，代价是登录时会弹 UAC。

开关状态本身持久化在 config.json 的 ``autostart``，本模块只负责把系统侧
（计划任务 / 注册表）改到与之一致。
"""

from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path
from typing import Callable

from .installer import is_admin

TASK_NAME = "InternetProxyPy"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "InternetProxyPy"
CREATE_NO_WINDOW = 0x08000000

Log = Callable[[str], None]


def _command() -> str:
    """开机要执行的命令行：打包后是 exe 本身，开发时是 python + main.py."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="mbcs", errors="ignore",
        creationflags=CREATE_NO_WINDOW,
    )


def _task_exists() -> bool:
    try:
        return _run(["schtasks", "/query", "/tn", TASK_NAME]).returncode == 0
    except OSError:
        return False


def _run_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return str(value)
    except OSError:
        return None


def is_enabled() -> bool:
    """系统侧是否已注册开机自启（计划任务或注册表启动项任一即可）."""
    return _task_exists() or _run_value() is not None


def _create_task() -> bool:
    result = _run(
        [
            "schtasks", "/create", "/f", "/tn", TASK_NAME,
            "/tr", _command(), "/sc", "onlogon", "/rl", "highest",
        ]
    )
    return result.returncode == 0


def _delete_task() -> None:
    if _task_exists():
        _run(["schtasks", "/delete", "/f", "/tn", TASK_NAME])


def _set_run_value() -> bool:
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _command())
        return True
    except OSError:
        return False


def _delete_run_value() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except OSError:
        pass


def enable(log: Log = print) -> bool:
    if is_admin() and _create_task():
        _delete_run_value()  # 计划任务已覆盖，避免登录时重复启动
        log("已启用开机自启（计划任务，最高权限，无需 UAC）")
        return True
    if _set_run_value():
        log("已启用开机自启（注册表 Run，登录时可能弹 UAC）")
        return True
    log("开机自启启用失败：需要管理员权限，请以管理员身份运行本程序")
    return False


def disable(log: Log = print) -> bool:
    if not is_admin() and _task_exists():
        log("开机自启关闭失败：计划任务需要管理员权限")
        return False
    _delete_task()
    _delete_run_value()
    log("已关闭开机自启")
    return True


def apply(enabled: bool, log: Log = print) -> bool:
    """把系统侧状态同步为 ``enabled``；返回是否成功."""
    return enable(log) if enabled else disable(log)
