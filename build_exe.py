from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
ENTRY = ROOT / "RemoteRipplePy.py"


def ensure_environment() -> None:
    if not VENV_PYTHON.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT, check=True)
    if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        raise SystemExit(subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT))
    packages = ["PIL", "tkinterdnd2", "pythonnet", "PyInstaller"]
    missing = [name for name in packages if importlib.util.find_spec(name) is None]
    if missing:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".", "pyinstaller>=6.0"],
            cwd=ROOT,
            check=True,
        )


def main() -> None:
    ensure_environment()
    parser = argparse.ArgumentParser(description="构建 RemoteRipplePy Windows 可执行文件")
    parser.add_argument("--onedir", action="store_true", help="生成目录版，默认生成单文件版")
    parser.add_argument("--console", action="store_true", help="保留控制台，便于排查运行错误")
    parser.add_argument("--name", help="自定义输出名称，默认 RemoteRipplePy_时间戳")
    parser.add_argument("--no-verify", action="store_true", help="跳过 exe 启动自检")
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("只能在 Windows 上构建 Windows exe")
    icon = ROOT / "assets" / "remoteripple.ico"
    if not ENTRY.is_file() or not icon.is_file():
        raise SystemExit("缺少 RemoteRipplePy.py 或 assets/remoteripple.ico")

    name = args.name or f"RemoteRipplePy_{datetime.now():%Y%m%d%H%M%S}"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir" if args.onedir else "--onefile",
        "--console" if args.console else "--windowed",
        "--name",
        name,
        "--icon",
        str(icon),
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{icon}{os.pathsep}assets",
        "--add-data",
        f"{ROOT / 'src' / 'remoteripple' / 'assets'}{os.pathsep}remoteripple/assets",
        "--collect-all",
        "tkinterdnd2",
        "--collect-all",
        "pythonnet",
        "--collect-all",
        "clr_loader",
        "--collect-submodules",
        "tkinter",
        "--hidden-import",
        "PIL.ImageTk",
        str(ENTRY),
    ]
    print("执行 PyInstaller…", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)

    executable = ROOT / "dist" / name / f"{name}.exe" if args.onedir else ROOT / "dist" / f"{name}.exe"
    if not executable.is_file():
        raise SystemExit(f"构建结束但未找到 exe：{executable}")
    if not args.no_verify:
        subprocess.run([str(executable), "--help"], cwd=ROOT, check=True, timeout=30)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    print(f"\n构建成功：{executable}")
    print(f"大小：{executable.stat().st_size / 1024 / 1024:.2f} MiB")
    print(f"SHA-256：{digest}")


if __name__ == "__main__":
    main()
