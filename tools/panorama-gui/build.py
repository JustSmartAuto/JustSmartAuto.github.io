"""PyInstaller 打包脚本：uv run python build.py

产物：dist/PanoramaFusion_YYYYMMDD_HHMMSS.exe（单文件、无控制台、带 fusion.ico 图标）。
文件名带时间戳，方便保留历史构建。
"""
import datetime
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(APP_DIR, "assets", "fusion.ico")
ASSETS = os.path.join(APP_DIR, "assets")
ENTRY = os.path.join(APP_DIR, "app.py")

STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
NAME = f"PanoramaFusion_{STAMP}"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--name", NAME,
    "--icon", ICON,
    # 打包图标资源（运行时通过 sys._MEIPASS 读取）
    "--add-data", f"{ASSETS};assets",
    # tkinterdnd2 的 tkdnd 运行库必须随包收集
    "--collect-all", "tkinterdnd2",
    "--collect-all", "cv2",
    ENTRY,
]
print(">>>", " ".join(cmd))
subprocess.check_call(cmd)
print(f"完成：dist/{NAME}.exe")
