"""PyInstaller 打包：单文件 exe，命名 LocalFileDropShell_{yyyyMMddHHmmss}。

混合 UI 架构：
    - 主界面（文件/文件夹拖放、传输列表、日志）使用 tkinter + tkinterdnd2
      （Win32 WM_DROPFILES 原生拖放）+ Windows 95 风格
    - VNC 远程桌面（noVNC + WebSocket 代理）和 Shell CLI GUI
      使用 pywebview 内嵌浏览器实现

用法：
    uv run python build_exe.py                        # 普通打包（不内嵌 VNC/noVNC，探测已有直接复用）
    uv run python build_exe.py --with-vnc-bundle      # 额外内嵌 tvnserver + noVNC 前端（无 TightVNC 环境兜底）

参考：diffimg/build_exe.py（pywebview/tkinterdnd2/pythonnet 显式 collect-all）、
      http_shell_cli_vnc（单文件 Go 二进制零依赖）。
"""

from __future__ import annotations

import argparse
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

    系统 Python 即使装了 PyInstaller，通常也没有 pywebview / aiohttp / websockets / click，
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
    """打包前确认关键依赖可导入，缺了就明确报错而不是产出坏 exe。

    tkinter 是 Python 标准库，此处仅检查 pip 安装的第三方包；stdlib 的 tkinter
    若不存在说明 Python 安装时勾掉了 Tcl/Tk（常见嵌入式发行版），运行时才会报错。
    """
    missing = [
        name
        for name in (
            "webview",
            "PIL",
            "aiohttp",
            "websockets",
            "click",
            "PyInstaller",
            "tkinterdnd2",
        )
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        sys.exit(f"缺少依赖：{', '.join(missing)}，请先运行 `uv sync`")


def main() -> None:
    reexec_in_venv()
    check_deps()

    parser = argparse.ArgumentParser(description="PyInstaller 打包 LocalFileDropShell")
    parser.add_argument(
        "--with-vnc-bundle",
        action="store_true",
        help="额外内嵌 tvnserver + noVNC 前端（默认不内嵌，探测已有 VNC Server 直接复用）",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"LocalFileDropShell_{stamp}"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--icon",
        str(ASSETS / "drop3.ico"),
        "--add-data",
        f"{ASSETS}{';'}assets",
        "--paths",
        str(ROOT / "src"),
        # —— pywebview 部分（用于 VNC 远程桌面和 Shell CLI GUI 标签页）——
        "--collect-all",
        "webview",
        "--collect-all",
        "pythonnet",
        "--collect-all",
        "clr_loader",
        "--hidden-import",
        "webview.platforms.winforms",
        "--hidden-import",
        "webview.platforms.edgechromium",
        # —— tkinterdnd2（Win32 WM_DROPFILES 原生拖放，主界面文件传输）——
        "--collect-all",
        "tkinterdnd2",
        # PyInstaller 对 tkinter 标准库的子模块收集不完整，显式补全
        "--collect-submodules",
        "tkinter",
    ]

    if args.with_vnc_bundle:
        vnc_dir = ROOT / "vnc_bundle"
        if vnc_dir.exists():
            cmd += ["--add-data", f"{vnc_dir}{';'}vnc_bundle"]
        else:
            print(
                "警告：--with-vnc-bundle 已指定但 vnc_bundle/ 目录不存在，"
                "打包将不内嵌 VNC 文件。请先从 http_shell_cli_vnc/public_fs "
                "拷贝 tvnc/ 与 novnc/ 到项目根目录 vnc_bundle/"
            )

    cmd.append(str(ROOT / "src" / "localdropshell" / "cli.py"))
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"打包完成: {ROOT / 'dist' / (name + '.exe')}")


if __name__ == "__main__":
    main()
