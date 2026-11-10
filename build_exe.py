from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"VirtualLanPy_{stamp}"
    binaries = ("easytier-core.exe", "easytier-cli.exe", "wintun.dll", "Packet.dll", "WinDivert64.sys")
    command = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed", "--uac-admin", "--noconfirm", "--clean",
        "--name", name,
        "--icon", str(ROOT / "assets/virtual-lan.ico"),
        "--paths", str(ROOT / "src"),
        "--add-data", f"{ROOT / 'assets'};assets",
        "--collect-all", "tkinterdnd2",
        "--collect-submodules", "tkinter",
    ]
    for binary in binaries:
        path = ROOT / "assets" / binary
        if not path.is_file():
            raise FileNotFoundError(f"缺少打包组件：{path}")
        command += ["--add-binary", f"{path};assets"]
    command.append(str(ROOT / "main.py"))
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    print("正在打包单文件 EXE（已启用 UAC 管理员清单）...")
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    print(f"打包完成：{ROOT / 'dist' / (name + '.exe')}")


if __name__ == "__main__":
    main()
