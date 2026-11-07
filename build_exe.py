"""PyInstaller 打包：单文件 exe，命名 InternetProxy_{yyyyMMddHHmmss}。

架构：
    - 主界面（托盘图标、后台日志、设置）使用 tkinter + tkinterdnd2
      （托盘常驻、启动时静默最小化，用户无感知）+ Windows 95 风格
      （assets/02-windows-95.css 设计令牌）
    - 服务端（可上网工控机）监听 TCP/UDP 代理端口（默认 10082）
    - 客户端/服务端同在 main.py 入口，按配置文件中的角色启动（默认客户端）

用法：
    uv run python build_exe.py                            # 默认打包：内嵌 assets/（图标、Win95 css）
    uv run python build_exe.py --name InternetProxy       # 自定义产出文件名
    双击 build_exe.py                                      # 自动初始化 .venv 并打包，结束后停住控制台
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
SRC = ROOT / "src"
VENV_PYTHON = (ROOT / ".venv" / "Scripts" / "python.exe").resolve()


def ensure_venv() -> None:
    """确保项目 .venv 存在，缺失时自动创建并同步依赖。

    双击运行时用的是系统 Python，此前直接 sys.exit("未找到 .venv")，
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

    系统 Python 即使装了 PyInstaller，通常也没有 tkinterdnd2，
    打出来的 exe 会缺托盘模块，所以统一强制走项目 venv。
    """
    ensure_venv()
    if Path(sys.executable).resolve() == VENV_PYTHON:
        return
    print(f"当前解释器不含项目依赖，切换到 venv 重跑：{VENV_PYTHON}")
    env = dict(os.environ)
    # 子进程与父进程共用同一个控制台，由父进程统一负责结束时停住窗口
    env["IPROXY_BUILD_NO_PAUSE"] = "1"
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
        ("th32ModuleID", ctypes.c_size_t),
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

    从 IDE 终端启动时，祖先链里也可能出现 explorer.exe（IDE 本身是双击打开的），
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
    if os.environ.get("IPROXY_BUILD_NO_PAUSE") == "1":
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
            "tkinterdnd2",
            "PyInstaller",
        )
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        sys.exit(f"缺少依赖：{', '.join(missing)}，请先运行 `uv sync`")


def check_assets() -> None:
    """确认托盘/窗口图标与 Win95 设计令牌存在（assets/ 会整体嵌入 exe）."""
    missing = [
        f"assets/{name}"
        for name in (
            "cat.ico",
            "02-windows-95.css",
        )
        if not (ASSETS / name).is_file()
    ]
    if missing:
        sys.exit(f"缺少内嵌资源：{', '.join(missing)}")


def main() -> None:
    reexec_in_venv()
    check_deps()
    check_assets()
    entry = SRC / "internet_proxy" / "cli.py"
    if not entry.is_file():
        sys.exit(f"缺少入口脚本：{entry}，请先编写 src/internet_proxy 应用代码")

    parser = argparse.ArgumentParser(description="PyInstaller 打包 InternetProxyPy")
    parser.add_argument(
        "--name",
        default=None,
        help="产出 exe 名称（默认 InternetProxy_{yyyyMMddHHmmss}）",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = args.name or f"InternetProxy_{stamp}"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--uac-admin",  # 透明代理需管理员权限创建 wintun 网卡与路由
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--icon",
        str(ASSETS / "cat.ico"),
        "--add-data",
        f"{ASSETS}{';'}assets",
        "--paths",
        str(SRC),
        # —— tkinterdnd2（托盘图标 + TkDND2 扩展，界面与拖放支持）——
        "--collect-all",
        "tkinterdnd2",
        # PyInstaller 对 tkinter 标准库的子模块收集不完整，显式补全
        "--collect-submodules",
        "tkinter",
        # 托盘在子线程里跑，显式把托盘入口作为 hidden-import 兜底
        "--hidden-import",
        "internet_proxy.tray",
    ]

    if SRC.is_dir():
        cmd += [
            "--hidden-import",
            "internet_proxy",
            "--hidden-import",
            "internet_proxy.cli",
            "--hidden-import",
            "internet_proxy.app",
            "--hidden-import",
            "internet_proxy.server",
            "--hidden-import",
            "internet_proxy.client",
            "--hidden-import",
            "internet_proxy.config",
            "--hidden-import",
            "internet_proxy.installer",
            "--hidden-import",
            "internet_proxy.vmess",
            "--hidden-import",
            "internet_proxy.autostart",
            "--hidden-import",
            "internet_proxy.discovery",
        ]
    cmd.append(str(ROOT / "main.py"))
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"打包完成: {ROOT / 'dist' / (name + '.exe')}")


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
