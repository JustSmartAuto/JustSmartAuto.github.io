"""Windows ShellExecutor — pwsh / powershell / cmd 自动检测 + 编码自适应 + 超时 Kill.

仅写这个文件. Python 3.10+ 语法.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 模块级常量 & 缓存
# ---------------------------------------------------------------------------

# 搜索 shell 的常见位置 (绝对路径前缀列表)
_WINDOWS_SYSTEM32 = Path(os.environ.get("WINDIR", r"C:\WINDOWS")) / "System32"
_COMMON_DIRS: list[Path] = [
    _WINDOWS_SYSTEM32,
    _WINDOWS_SYSTEM32 / "WindowsPowerShell" / "v1.0",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "PowerShell"
    / "7",
]

# shell 候选优先级
_SHELL_CANDIDATES: list[str] = ["pwsh.exe", "powershell.exe", "cmd.exe"]

# 模块级缓存: 成功检测到的可执行文件绝对路径; None 表示无可用 shell
_SHELL_CACHE: str | None = None


# ---------------------------------------------------------------------------
# Shell 自动检测
# ---------------------------------------------------------------------------

def _find_shell(candidates: list[str] | None = None) -> str | None:
    """按优先级在 PATH 和常见目录查找可用 shell. 返回绝对路径或 None."""
    global _SHELL_CACHE
    if _SHELL_CACHE is not None:
        return _SHELL_CACHE
    candidates = candidates or _SHELL_CANDIDATES
    for name in candidates:
        # 1) 先走 PATH
        found = shutil.which(name)
        if found:
            _SHELL_CACHE = found
            return found
        # 2) 再扫常见绝对路径
        for d in _COMMON_DIRS:
            p = d / name
            if p.is_file():
                _SHELL_CACHE = str(p)
                return str(p)
    return None


def _reset_shell_cache() -> None:
    """单元测试用. 清除模块级 shell 缓存."""
    global _SHELL_CACHE
    _SHELL_CACHE = None


# ---------------------------------------------------------------------------
# Shell 类型识别
# ---------------------------------------------------------------------------

_POWERSHELL_BASENAMES = {"pwsh.exe", "pwsh", "powershell.exe", "powershell"}


def _is_powershell(shell_path: str) -> bool:
    """判断给定 shell 是 PowerShell 系列 (pwsh / powershell) 还是 cmd."""
    return Path(shell_path).name.lower() in _POWERSHELL_BASENAMES


def _build_cmd(shell_path: str, command: str) -> list[str]:
    """组装子进程 argv 列表, 避免字符串拼接导致的 shell 注入."""
    name = Path(shell_path).name.lower()
    if name == "cmd.exe":
        # cmd /c <command>
        return [shell_path, "/c", command]
    # pwsh / powershell 都走 -NoProfile -Command
    return [shell_path, "-NoProfile", "-Command", command]


# ---------------------------------------------------------------------------
# 编码自适应
# ---------------------------------------------------------------------------

_REPLACEMENT_CHAR = "\ufffd"
_REPLACEMENT_RATIO_THRESHOLD = 0.01  # 1 %


def _decode_output(
    raw: bytes, *, shell_path: str, label: str = "stdout"
) -> str:
    """对子进程输出 bytes 做解码.

    - PowerShell / pwsh 输出直接按 UTF-8 解码 (它们默认 UTF-16LE 但 -Command
      管道通常给 UTF-8; 先尝试 UTF-8 再回退 cp936 也兼容 cmd 情况).
    - cmd 默认 GBK (cp936). 先 UTF-8 尝试, 若成功且替换字符占比 < 1% 则信任;
      否则回退 cp936.
    """
    if not raw:
        return ""

    # 先尝试 UTF-8 (PowerShell 原生期望; 也兼容 cmd 恰好输出纯 ASCII 的情况)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        decoded = None

    if decoded is not None and not _is_powershell(shell_path):
        # cmd 场景: 启发式判断是否真的是 UTF-8
        repl_count = decoded.count(_REPLACEMENT_CHAR)
        if repl_count > 0:
            ratio = repl_count / max(1, len(decoded))
            if ratio >= _REPLACEMENT_RATIO_THRESHOLD:
                decoded = None  # 丢弃, 回退 GBK

    if decoded is None:
        # 硬回退 GBK (cp936), errors='replace' 兜住极端情况
        decoded = raw.decode("gbk", errors="replace")

    return decoded


# ---------------------------------------------------------------------------
# Windows 子进程创建辅助
# ---------------------------------------------------------------------------

def _creation_flags() -> int:
    """Windows 上隐藏子进程窗口的 creationflags."""
    if sys.platform != "win32":
        return 0
    # CREATE_NO_WINDOW = 0x08000000
    return 0x08000000


def _startupinfo() -> subprocess.STARTUPINFO | None:
    """Windows 上 STARTUPINFO (dwFlags + wShowWindow) 隐藏控制台."""
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    # STARTF_USESHOWWINDOW = 0x00000001, SW_HIDE = 0
    si.dwFlags |= 1
    si.wShowWindow = 0
    return si


# ---------------------------------------------------------------------------
# ShellExecutor
# ---------------------------------------------------------------------------

class ShellExecutor:
    """Windows Shell 命令执行器.

    - 自动检测 pwsh.exe → powershell.exe → cmd.exe.
    - cmd 输出编码自适应 UTF-8 / GBK.
    - 超时后 Kill 子进程.
    """

    def __init__(
        self,
        shell_path: str | None = None,
        default_timeout: float | None = None,
    ) -> None:
        # shell 路径: 显式覆盖 > 自动检测
        self.shell_path: str = shell_path or self._resolve_shell()

        # 默认超时: 参数 > env > 硬编码 30s
        if default_timeout is None:
            env_val = os.environ.get("HTTP_SHELL_TIMEOUT")
            if env_val:
                try:
                    default_timeout = float(env_val)
                except ValueError:
                    default_timeout = 30.0
            else:
                default_timeout = 30.0
        self.default_timeout: float = default_timeout

    # ------------------------------------------------------------------
    # 解析 shell
    # ------------------------------------------------------------------

    def _resolve_shell(self) -> str:
        found = _find_shell()
        if found is None:
            # 最后兜底: 让 subprocess 自己找; 若都找不到 exec 时会抛
            raise RuntimeError(
                "No usable shell found (tried pwsh.exe, powershell.exe, cmd.exe). "
                "Please install PowerShell or ensure cmd.exe is on PATH."
            )
        return found

    # ------------------------------------------------------------------
    # 异步执行
    # ------------------------------------------------------------------

    async def exec(
        self,
        command: str,
        timeout: float | None = None,
        work_dir: str | None = None,
    ) -> dict[str, Any]:
        """异步执行一条 shell 命令.

        返回 dict:
            status    : "success" | "error"
            stdout    : str
            stderr    : str
            exit_code : int (超时则为 -1)
            timeout   : bool
            command   : str (原始入参)
            shell     : str (实际使用的 shell 路径)
            work_dir  : str | None
        """
        shell_path = self.shell_path
        argv = _build_cmd(shell_path, command)
        cwd = work_dir or os.getcwd()
        use_timeout = timeout if timeout is not None else self.default_timeout

        proc: asyncio.subprocess.Process | None = None
        timed_out = False
        exit_code = 0
        stdout_bytes = b""
        stderr_bytes = b""

        # Windows 专属参数
        create_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            create_kwargs["creationflags"] = _creation_flags()
            create_kwargs["startupinfo"] = _startupinfo()

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                encoding=None,  # 让 communicate 返回 bytes, 我们自己解码
                **create_kwargs,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=use_timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                # Kill 进程 (Windows 上 Process.Kill 会终止进程树里的该进程)
                try:
                    proc.kill()
                except Exception:
                    pass
                # 等它收尾, 避免资源泄露
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except Exception:
                    pass
                exit_code = -1
            else:
                exit_code = proc.returncode if proc.returncode is not None else 0
        except FileNotFoundError as e:
            # shell 路径失效 / 不存在
            return {
                "status": "error",
                "stdout": "",
                "stderr": f"Shell not found: {shell_path!r} ({e})",
                "exit_code": -1,
                "timeout": False,
                "command": command,
                "shell": shell_path,
                "work_dir": cwd,
            }
        except Exception as e:
            return {
                "status": "error",
                "stdout": "",
                "stderr": f"Failed to spawn shell: {e!r}",
                "exit_code": -1,
                "timeout": False,
                "command": command,
                "shell": shell_path,
                "work_dir": cwd,
            }

        stdout = _decode_output(stdout_bytes or b"", shell_path=shell_path, label="stdout")
        stderr = _decode_output(stderr_bytes or b"", shell_path=shell_path, label="stderr")

        status = "success" if (not timed_out and exit_code == 0) else "error"

        return {
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timeout": timed_out,
            "command": command,
            "shell": shell_path,
            "work_dir": cwd,
        }

    # ------------------------------------------------------------------
    # 同步辅助
    # ------------------------------------------------------------------

    def exec_sync(
        self,
        command: str,
        timeout: float | None = None,
        work_dir: str | None = None,
    ) -> dict[str, Any]:
        """同步执行 (在已有 event loop 外调用时可用)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            return asyncio.run(self.exec(command, timeout=timeout, work_dir=work_dir))

        # 已在 event loop 内 — 用 run_coroutine_threadsafe 丢到原 loop 执行
        future = asyncio.run_coroutine_threadsafe(
            self.exec(command, timeout=timeout, work_dir=work_dir),
            loop,
        )
        return future.result()


# ---------------------------------------------------------------------------
# 便捷单例 & 直接运行
# ---------------------------------------------------------------------------

_default_executor: ShellExecutor | None = None


def get_default_executor() -> ShellExecutor:
    """模块级单例 — 懒加载. 方便其它模块直接 import 后调用."""
    global _default_executor
    if _default_executor is None:
        _default_executor = ShellExecutor()
    return _default_executor


if __name__ == "__main__":  # pragma: no cover — 仅开发测试用
    import json

    exe = ShellExecutor()
    print(f"Using shell: {exe.shell_path}")

    result = asyncio.run(exe.exec("echo hello & whoami"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
