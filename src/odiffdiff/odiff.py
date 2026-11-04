"""odiff.exe 子进程封装。

CLI 用法：
    odiff <base> <compare> [diff_output] [options]

Exit codes:
    0  - Images match
    21 - Layout difference (--fail-on-layout)
    22 - Pixel differences found

parsable-stdout 格式：
    diffCount;diffPercentage   （exit 22）
    空                          （exit 0 / 21）
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# odiff.exe 查找顺序：显式指定 > 同目录 assets > 项目根 assets > PATH
def locate_odiff(explicit: str | Path | None = None) -> Path:
    """找到 odiff.exe 的绝对路径。"""
    if explicit:
        p = Path(explicit).resolve()
        if not p.exists():
            raise FileNotFoundError(f"指定的 odiff 不存在: {p}")
        return p

    # 与本文件同级的 assets（PyInstaller onefile 解包后）
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "assets" / "odiff-windows-x64.exe",  # src/odiffdiff → 项目根/assets
        here.parent.parent / "assets" / "odiff-windows-x64.exe",         # PyInstaller 解包后
        Path("odiff-windows-x64.exe"),                                    # PATH / 当前目录
        Path("odiff.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError(
        "找不到 odiff.exe — 请把 odiff-windows-x64.exe 放到 assets/ 目录，"
        "或通过 odiff_path 参数显式指定"
    )


@dataclass
class DiffOptions:
    """odiff.exe 可选参数，全部给默认值。"""
    threshold: float = 0.1                     # 0.0 ~ 1.0
    diff_color: str = "#ff0000"               # 十六进制高亮色
    antialiasing: bool = False                 # 忽略抗锯齿像素
    diff_mask: bool = False                    # 只输出变更像素（透明底）
    diff_overlay: float | None = None          # 白底叠加（0.5 常用），None=关闭
    fail_on_layout: bool = False               # 尺寸不同直接失败
    reduce_ram_usage: bool = False             # 省内存但慢
    ignore_regions: list[tuple[int, int, int, int]] = field(default_factory=list)

    def build_args(self) -> list[str]:
        args: list[str] = [
            "-t", f"{self.threshold:.4f}",
            "--diff-color", self.diff_color,
        ]
        if self.antialiasing:
            args.append("--aa")
        if self.diff_mask:
            args.append("--diff-mask")
        if self.diff_overlay is not None:
            args += ["--diff-overlay", f"{self.diff_overlay:.2f}"]
        if self.fail_on_layout:
            args.append("--fail-on-layout")
        if self.reduce_ram_usage:
            args.append("--reduce-ram-usage")
        if self.ignore_regions:
            parts = [f"{x1}:{y1}-{x2}:{y2}" for x1, y1, x2, y2 in self.ignore_regions]
            args += ["--ignore", ",".join(parts)]
        return args


@dataclass
class DiffResult:
    match: bool
    reason: str                     # "match" | "pixel-diff" | "layout-diff" | "file-error"
    diff_count: int = 0
    diff_percentage: float = 0.0
    exit_code: int = 0
    stderr: str = ""

    @property
    def summary(self) -> str:
        if self.match:
            return "✓ 完全一致"
        if self.reason == "layout-diff":
            return f"✗ 尺寸不同 (exit {self.exit_code})"
        if self.reason == "file-error":
            return f"✗ 文件错误: {self.stderr.strip()}"
        return (
            f"✗ 差异像素 {self.diff_count} ({self.diff_percentage:.2f}%)"
        )


def run_diff(
    base: str | Path,
    compare: str | Path,
    diff_output: str | Path,
    *,
    options: DiffOptions | None = None,
    odiff_path: str | Path | None = None,
    timeout: int = 30,
) -> DiffResult:
    """执行 odiff.exe 对比。

    返回 DiffResult；不抛异常（失败全映射到 reason）。
    """
    exe = locate_odiff(odiff_path)
    opts = options or DiffOptions()

    cmd = [
        str(exe),
        str(base),
        str(compare),
        str(diff_output),
        *opts.build_args(),
        "--parsable-stdout",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=0,  # Windows 下避免弹窗
        )
    except FileNotFoundError as exc:
        return DiffResult(False, "file-error", stderr=str(exc))
    except subprocess.TimeoutExpired:
        return DiffResult(False, "file-error", stderr=f"超时 ({timeout}s)")

    exit_code = proc.returncode
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if exit_code == 0:
        return DiffResult(True, "match", exit_code=exit_code, stderr=stderr)

    if exit_code == 21:
        return DiffResult(False, "layout-diff", exit_code=exit_code, stderr=stderr)

    if exit_code == 22:
        # parsable-stdout: "diffCount;diffPercentage"
        count, pct = 0, 0.0
        if stdout and ";" in stdout:
            cs, ps = stdout.split(";", 1)
            try:
                count = int(cs)
            except ValueError:
                pass
            try:
                pct = float(ps)
            except ValueError:
                pass
        return DiffResult(
            False, "pixel-diff",
            diff_count=count, diff_percentage=pct,
            exit_code=exit_code, stderr=stderr,
        )

    # 其他退出码 —— 视为错误
    return DiffResult(False, "file-error", exit_code=exit_code, stderr=stderr or stdout)
