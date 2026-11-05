"""PyInstaller 打包：单文件 exe，命名 LocalFileDropShell_{yyyyMMddHHmmss}。

用法：uv run python build_exe.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
VENV_PYTHON = (ROOT / ".venv" / "Scripts" / "python.exe").resolve()


def reexec_in_venv() -> None:
    """不在项目 venv 里时（如双击用了系统 Python），改用 .venv 解释器重跑自身。

    系统 Python 即使装了 PyInstaller，通常也没有 pywebview / aiohttp / tkinterdnd2，
    打出来的 exe 会缺 webview 或 HTTP 服务模块，所以统一强制走项目 venv。
    """
    if Path(sys.executable).resolve() == VENV_PYTHON:
        return
    if not VENV_PYTHON.exists():
        sys.exit("未找到 .venv，请先运行 `uv sync` 安装依赖")
    print(f"当前解释器不含项目依赖，切换到 venv 重跑：{VENV_PYTHON}")
    raise SystemExit(
        subprocess.run([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode
    )


def check_deps() -> None:
    """打包前确认关键依赖可导入，缺了就明确报错而不是产出坏 exe。"""
    missing = [
        name
        for name in ("webview", "PIL", "aiohttp", "PyInstaller")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        sys.exit(f"缺少依赖：{', '.join(missing)}，请先运行 `uv sync`")


def main() -> None:
    reexec_in_venv()
    check_deps()
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"LocalFileDropShell_{stamp}"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--name", name,
        "--icon", str(ASSETS / "drop3.ico"),
        "--add-data", f"{ASSETS}{';'}assets",
        "--paths", str(ROOT / "src"),
        # pywebview 通过 importlib 动态加载平台后端，且自带 js 数据文件，
        # PyInstaller 静态分析收不到，必须显式收集
        "--collect-all", "webview",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        # tkinterdnd2 自带 tkdnd 的 tcl 脚本与原生 dll，也必须整体收集
        "--collect-all", "tkinterdnd2",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        str(ROOT / "src" / "localdropshell" / "app.py"),
    ]
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"打包完成: {ROOT / 'dist' / (name + '.exe')}")


if __name__ == "__main__":
    main()
