from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys

from .resources import validate_binaries

_MUTEX = None


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def elevate() -> bool:
    if is_admin():
        return True
    executable = sys.executable
    parameters = " ".join(subprocess.list2cmdline([arg]) for arg in sys.argv)
    if getattr(sys, "frozen", False):
        parameters = subprocess.list2cmdline(sys.argv[1:])
    else:
        parameters = subprocess.list2cmdline([os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, parameters, None, 1)
    if result <= 32:
        ctypes.windll.user32.MessageBoxW(0, "需要管理员权限才能创建虚拟网卡。请允许 UAC 提权后重试。", "VirtualLanPy", 0x10)
    return False


def single_instance() -> bool:
    global _MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = kernel32.CreateMutexW(None, False, r"Local\JustSmartVirtualLanPy")
    if ctypes.get_last_error() == 183:
        ctypes.windll.user32.MessageBoxW(0, "软件已经在运行。", "VirtualLanPy", 0x40)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JustSmart EasyTier 一键虚拟局域网")
    parser.add_argument("--check", action="store_true", help="只检查内嵌组件，不提权、不组网")
    args = parser.parse_args(argv)
    if args.check:
        validate_binaries()
        for name in ("easytier-core.exe", "easytier-cli.exe"):
            from .resources import resource
            subprocess.run([str(resource(name)), "--version"], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        print("组件检查通过")
        return 0
    if sys.platform != "win32":
        parser.error("本工具仅支持 Windows")
    if not elevate():
        return 0
    if not single_instance():
        return 1
    try:
        from .certificates import install_trusted_certificate
        install_trusted_certificate()
    except (OSError, ctypes.WinError) as exc:
        ctypes.windll.user32.MessageBoxW(
            0, f"签名证书自动导入失败：{exc}\n软件将继续运行，但 Windows 可能显示发布者警告。",
            "VirtualLanPy", 0x30,
        )
    from .ui import MainWindow
    MainWindow().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
