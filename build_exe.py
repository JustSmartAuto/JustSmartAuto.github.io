from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CERT_SUBJECT = "CN=JustSmart VirtualLanPy Code Signing"


def sign_exe(path: Path) -> None:
    candidates = list((Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Windows Kits/10/bin").glob("*/x64/signtool.exe"))
    if not candidates:
        raise FileNotFoundError("未找到 signtool.exe，无法签名最终 EXE")
    signtool = max(candidates, key=lambda item: item.stat().st_mtime)
    thumbprint = subprocess.check_output([
        "powershell.exe", "-NoProfile", "-Command",
        f"(Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | Where-Object {{$_.Subject -eq '{CERT_SUBJECT}'}} | Sort-Object NotAfter -Descending | Select-Object -First 1).Thumbprint",
    ], text=True).strip()
    if not thumbprint:
        raise RuntimeError(f"当前用户证书库中未找到 {CERT_SUBJECT}")
    subprocess.run([
        str(signtool), "sign", "/fd", "SHA256", "/td", "SHA256",
        "/tr", "http://timestamp.digicert.com", "/sha1", thumbprint, str(path),
    ], check=True)
    subprocess.run([str(signtool), "verify", "/pa", str(path)], check=True)


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
    output = ROOT / "dist" / f"{name}.exe"
    sign_exe(output)
    print(f"打包并签名完成：{output}")


if __name__ == "__main__":
    main()
