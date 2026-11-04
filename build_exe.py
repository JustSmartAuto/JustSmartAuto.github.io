"""PyInstaller 打包：单文件 exe，命名 SvgOverlayPy_{yyyyMMddHHmmss}。

用法：uv run python build_exe.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"


def make_icon() -> Path | None:
    """由 overlay_icon.png 生成多尺寸 app_icon.ico（需 Pillow，失败则不设图标）。"""
    png = ASSETS / "overlay_icon.png"
    try:
        from PIL import Image

        icon = ASSETS / "app_icon.ico"
        Image.open(png).convert("RGBA").save(
            icon,
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        return icon
    except Exception as exc:  # noqa: BLE001
        print(f"图标生成失败（将不设置 exe 图标）: {exc}")
        return None


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"SvgOverlayPy_{stamp}"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--name", name,
        "--add-data", f"{ASSETS}{';'}assets",
        "--paths", str(ROOT / "src"),
        # pywebview 通过 importlib 动态加载平台后端，且自带 js 数据文件，
        # PyInstaller 静态分析收不到，必须显式收集
        "--collect-all", "webview",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        str(ROOT / "src" / "svgoverlay" / "app.py"),
    ]
    icon = make_icon()
    if icon is not None:
        cmd += ["--icon", str(icon)]
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"打包完成: {ROOT / 'dist' / (name + '.exe')}")


if __name__ == "__main__":
    main()
