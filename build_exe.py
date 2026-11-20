from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"ImageRotatePy_{stamp}"
    icon = ASSETS / "rotate.ico"
    if not icon.is_file():
        raise FileNotFoundError(f"缺少应用图标: {icon}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--name", name,
        "--icon", str(icon),
        "--add-data", f"{icon};assets",
        str(ROOT / "src" / "imagerotatepy" / "app.py"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"\n打包完成: {ROOT / 'dist' / f'{name}.exe'}")


if __name__ == "__main__":
    main()
