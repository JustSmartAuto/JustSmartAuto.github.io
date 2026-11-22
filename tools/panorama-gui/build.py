"""PyInstaller 打包脚本：uv run python build.py

产物：dist/PanoramaFusion.exe（单文件、无控制台、带 fusion.ico 图标）。
"""
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(APP_DIR, "assets", "fusion.ico")
ASSETS = os.path.join(APP_DIR, "assets")
ENTRY = os.path.join(APP_DIR, "app.py")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--name", "PanoramaFusion",
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
print("完成：dist/PanoramaFusion.exe")
