"""PyInstaller 打包：单文件 exe，命名 LocalFileDropShell_{yyyyMMddHHmmss}。

混合 UI 架构：
    - 主界面（文件/文件夹拖放、传输列表、日志）使用 tkinter + tkinterdnd2
      （Win32 WM_DROPFILES 原生拖放）+ Windows 95 风格
    - VNC 远程桌面（noVNC + WebSocket 代理）和 Shell CLI GUI
      使用 pywebview 内嵌浏览器实现

用法：
    uv run python build_exe.py                        # 默认打包：内嵌 assets/ + noVNC/tvnserver/xterm 全部前端资源
    uv run python build_exe.py --no-vnc-bundle        # 不内嵌 noVNC/tvnserver/xterm（复用机器上已装 TightVNC）
    双击 build_exe.py                                  # 自动初始化 .venv 并打包，结束后停住控制台

前端资源查找顺序：项目 ``vnc_bundle/`` 优先；缺失时回退同级
``http_shell_cli_vnc/public_fs/{novnc,tvnc,xterm,shell}``。

参考：diffimg/build_exe.py（pywebview/tkinterdnd2/pythonnet 显式 collect-all）、
      http_shell_cli_vnc（单文件 Go 二进制零依赖）。
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
VENV_PYTHON = (ROOT / ".venv" / "Scripts" / "python.exe").resolve()

# 前端/服务端资源（noVNC 页面、tvnserver、xterm 等）
BUNDLE_DIR = ROOT / "vnc_bundle"
LEGACY_BUNDLE_DIR = ROOT.parent / "http_shell_cli_vnc" / "public_fs"
BUNDLE_PARTS = ("novnc", "tvnc", "xterm", "shell")


def ensure_venv() -> None:
    """确保项目 .venv 存在，缺失时自动创建并同步依赖。

    双击运行时用的是系统 Python（py.exe 选中 3.13），此前直接 sys.exit("未找到 .venv")，
    控制台随即关闭表现为“闪退”；这里改为先用 uv 初始化 .venv，再继续重跑自身。
    """
    if VENV_PYTHON.exists():
        return
    print("未找到项目 venv（.venv），开始初始化，首次需下载依赖，请稍候 ...")
    uv = shutil.which("uv")
    if uv:
        subprocess.run([uv, "sync"], cwd=ROOT, check=True)
    else:
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
        subprocess.run(
            [str(VENV_PYTHON), "-m", "pip", "install", "-e", ".", "pyinstaller"],
            cwd=ROOT,
            check=True,
        )
    if not VENV_PYTHON.exists():
        sys.exit("初始化 .venv 失败：未生成 .venv/Scripts/python.exe")


def reexec_in_venv() -> None:
    """不在项目 venv 里时（如双击用了系统 Python），改用 .venv 解释器重跑自身。

    系统 Python 即使装了 PyInstaller，通常也没有 pywebview / aiohttp / websockets / click，
    打出来的 exe 会缺 webview 或 HTTP 服务模块，所以统一强制走项目 venv。
    """
    ensure_venv()
    if Path(sys.executable).resolve() == VENV_PYTHON:
        return
    print(f"当前解释器不含项目依赖，切换到 venv 重跑：{VENV_PYTHON}")
    env = dict(os.environ)
    # 子进程与父进程共用同一个控制台，由父进程统一负责结束时停住窗口
    env["LDS_BUILD_NO_PAUSE"] = "1"
    raise SystemExit(
        subprocess.run(
            [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], env=env
        ).returncode
    )


_TH32CS_SNAPPROCESS = 0x00000002


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _process_table() -> dict[int, tuple[int, str]]:
    """用 Toolhelp 快照取 {pid: (父 pid, 小写进程名)}，免装 psutil。"""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32)]
    kernel32.Process32First.restype = ctypes.c_int
    kernel32.Process32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32)]
    kernel32.Process32Next.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    table: dict[int, tuple[int, str]] = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return table
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("mbcs", "ignore").lower()
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, name)
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


_TERMINAL_HOSTS = {
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
    "conhost.exe",
    "windowsterminal.exe",
    "wt.exe",
    "openconsole.exe",
    "bash.exe",
    "wsl.exe",
    "wslhost.exe",
    "mintty.exe",
}


def _launched_by_double_click() -> bool:
    """判断是否为资源管理器双击启动：进程链 explorer.exe → py.exe → python.exe。

    从 IDE 终端或 Trae 终端启动时，祖先链里也可能出现 explorer.exe（IDE 本身是双击打开的），
    所以向上追溯时先遇到终端宿主（powershell/cmd/Windows Terminal 等）就判定为非双击。
    """
    if sys.platform != "win32":
        return False
    try:
        table = _process_table()
    except OSError:
        return False
    pid = os.getpid()
    for _ in range(8):
        parent = table.get(pid, (0, ""))[0]
        if not parent:
            return False
        name = table.get(parent, (0, ""))[1]
        if name == "explorer.exe":
            return True
        if name in _TERMINAL_HOSTS:
            return False
        pid = parent
    return False


def pause_if_double_clicked() -> None:
    """双击运行时停住控制台，避免成功/失败信息一闪而过。"""
    if os.environ.get("LDS_BUILD_NO_PAUSE") == "1":
        return
    if not _launched_by_double_click():
        return
    try:
        input("\n执行结束，按 Enter 关闭窗口 ...")
    except (EOFError, OSError):
        pass


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


def bundle_sources() -> list[tuple[Path, str]]:
    """返回要内嵌的前端资源：[(源目录, exe 内目标路径)].

    项目根目录有 ``vnc_bundle/`` 时整体内嵌；否则回退同级
    ``http_shell_cli_vnc/public_fs`` 下的 novnc / tvnc / xterm / shell。
    """
    if BUNDLE_DIR.is_dir():
        return [(BUNDLE_DIR, "vnc_bundle")]
    return [
        (LEGACY_BUNDLE_DIR / part, f"vnc_bundle/{part}")
        for part in BUNDLE_PARTS
        if (LEGACY_BUNDLE_DIR / part).is_dir()
    ]


def main() -> None:
    reexec_in_venv()
    check_deps()
    check_assets()

    parser = argparse.ArgumentParser(description="PyInstaller 打包 LocalFileDropShell")
    parser.add_argument(
        "--no-vnc-bundle",
        action="store_true",
        help="不内嵌 noVNC/tvnserver/xterm（默认全部内嵌，复用现场 TightVNC 时才需要）",
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
        # 独立 pywebview 子进程入口（exe 自身以 --webview-window 再启动一次）
        "--hidden-import",
        "localdropshell.webview_win",
        # —— tkinterdnd2（Win32 WM_DROPFILES 原生拖放，主界面文件传输）——
        "--collect-all",
        "tkinterdnd2",
        # PyInstaller 对 tkinter 标准库的子模块收集不完整，显式补全
        "--collect-submodules",
        "tkinter",
    ]

    if args.no_vnc_bundle:
        print("⏭ 已指定 --no-vnc-bundle：跳过 noVNC/tvnserver/xterm 内嵌")
    else:
        sources = bundle_sources()
        if not sources:
            sys.exit(
                "未找到可内嵌的前端资源：请把 noVNC/tvnserver/xterm 放到 "
                f"{BUNDLE_DIR}，或保留 {LEGACY_BUNDLE_DIR} 供回退；"
                "确实不需要内嵌时加 --no-vnc-bundle"
            )
        for src, dest in sources:
            cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
            print(f"内嵌资源: {src} -> {dest}")

    cmd.append(str(ROOT / "src" / "localdropshell" / "cli.py"))
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"打包完成: {ROOT / 'dist' / (name + '.exe')}")


def check_assets() -> None:
    """确认内嵌 TightVNC MSI 存在（assets/ 会整体嵌入 exe，供缺失时自动安装）."""
    if sorted(ASSETS.glob("tightvnc-*-setup-64bit.msi")):
        return
    print("警告：assets/ 缺少 tightvnc-*-setup-64bit.msi，打包后将无法自动安装 TightVNC")


def _run() -> int:
    """跑一次构建并返回退出码；异常先打印再返回，保证双击时错误信息可见。"""
    try:
        main()
        return 0
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            return 1
        return int(code or 0)
    except BaseException:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    code = _run()
    pause_if_double_clicked()
    raise SystemExit(code)
