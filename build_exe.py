"""PyInstaller 打包：单文件 exe，命名 ImageDiffPy_{yyyyMMddHHmmss}。

用法：python build_exe.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"

# 打包进 exe 的 assets 白名单（逐项显式列出，排除大体积 zip / 原始源码）
ASSET_WHITELIST = [
    "odiff-windows-x64.exe",   # odiff 引擎 —— 运行必需
    "odiff-icon.png",          # 方形圆角 icon
    "app_icon.ico",            # 窗口 / exe 图标
    "02-windows-95.css",       # Win95 设计 token
]


def make_icon() -> Path | None:
    """返回 app_icon.ico；不存在时由 odiff-icon.png 生成。"""
    png = ASSETS / "odiff-icon.png"
    ico = ASSETS / "app_icon.ico"
    if ico.exists():
        return ico
    try:
        from PIL import Image

        img = Image.open(png)
        img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])
        return ico
    except Exception as exc:  # noqa: BLE001
        print(f"图标生成失败（将不设置 exe 图标）: {exc}")
        return None


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"ImageDiffPy_{stamp}"
    icon = make_icon()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--name", name,
    ]
    for fname in ASSET_WHITELIST:
        fpath = ASSETS / fname
        if fpath.exists():
            cmd += ["--add-data", f"{fpath}{';'}assets"]
        else:
            print(f"⚠ 跳过缺失的 asset: {fname}")

    cmd += [str(ROOT / "src" / "odiffdiff" / "app.py")]
    if icon is not None:
        cmd += ["--icon", str(icon)]

    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    exe = ROOT / "dist" / f"{name}.exe"
    print(f"\n打包完成: {exe}")


if __name__ == "__main__":
    main()
