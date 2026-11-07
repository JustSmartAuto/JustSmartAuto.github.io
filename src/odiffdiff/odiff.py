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

import os
import shutil
import subprocess
import sys
import tempfile
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


def _is_ascii(s: str) -> bool:
    """字符串能否无损编码为 ASCII（中文路径检测）。"""
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


@dataclass
class _Staged:
    """暂存后的文件（位于临时目录中的纯 ASCII 文件名）。"""
    dir: Path
    base: Path
    compare: Path
    out: Path


def _make_stage_dir() -> Path:
    """创建暂存目录，优先选纯 ASCII 的临时位置。

    ANSI 代码页（如 GBK）下的程序内部用 ANSI API 解析路径，
    纯 ASCII 的 cwd 可保证兼容；否则退回默认临时目录（配合相对路径仍可用）。
    """
    candidates: list[Path] = [Path(tempfile.gettempdir())]
    if sys.platform == "win32":
        candidates += [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Temp",
            Path.home() / "AppData" / "Local" / "Temp",
            Path(os.environ.get("SystemDrive", "C:")) / "Windows" / "Temp",
        ]
    for c in candidates:
        if not str(c) or not _is_ascii(str(c)):
            continue
        try:
            c.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix="odiff_stage_", dir=c))
        except OSError:
            continue
    # 兜底：默认临时目录（路径可能含中文用户名，但配合相对路径调用仍可行）
    return Path(tempfile.mkdtemp(prefix="odiff_stage_"))


def _bmp_needs_png(path: Path) -> bool:
    """判断 BMP 是否需要先转 PNG。

    odiff 的 BMP 解码只支持 24/32 位、BI_RGB / BI_BITFIELDS；
    工业相机常见的 8 位灰度 BMP、RLE 压缩 BMP 会报 "Could not load base image"。
    """
    if path.suffix.lower() != ".bmp":
        return False
    try:
        with path.open("rb") as f:
            head = f.read(34)
    except OSError:
        return False
    if len(head) < 34 or head[:2] != b"BM":
        return False
    bit_count = int.from_bytes(head[28:30], "little")
    compression = int.from_bytes(head[30:34], "little")
    return bit_count not in (24, 32) or compression not in (0, 3)


def _bmp_to_png(src: Path, dst: Path) -> None:
    """用 Pillow 把 odiff 不支持的 BMP 变体转成 PNG（统一转为 RGB）。"""
    from PIL import Image

    with Image.open(src) as im:
        im.convert("RGB").save(dst, "PNG")


def _stage_files(base: Path, compare: Path, diff_output: Path) -> _Staged:
    """把输入复制/转换到纯 ASCII 文件名的临时目录。

    触发条件：路径含非 ASCII 字符，或 BMP 位深/压缩方式不被 odiff 支持。
    """
    stage_dir = _make_stage_dir()

    def _temp_name(src: Path, tag: str) -> str:
        return f"{tag}{src.suffix.lower() or '.png'}"

    def _prepare(src: Path, tag: str) -> Path:
        if _bmp_needs_png(src):
            dst = stage_dir / f"{tag}.png"
            _bmp_to_png(src, dst)
            return dst
        if _is_ascii(str(src)):
            return src
        dst = stage_dir / _temp_name(src, tag)
        shutil.copy2(src, dst)
        return dst

    s_base = _prepare(base, "base")
    s_compare = _prepare(compare, "compare")
    # 输出必须落在暂存目录内：子进程以 cwd=暂存目录 + 相对文件名调用，
    # 否则会写到暂存目录里而非 diff_output，拷回时被误删（0 字节文件）。
    s_out = stage_dir / _temp_name(diff_output, "diff")

    return _Staged(dir=stage_dir, base=s_base, compare=s_compare, out=s_out)


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

    # —— 中文路径兼容 + BMP 格式兼容（Windows）——
    # odiff.exe 在 ANSI 代码页（GBK 等）系统上接收的命令行参数会被错误解码，
    # 含中文的输入/输出路径因此失效。检测到非 ASCII 路径时，
    # 自动暂存为纯 ASCII 文件名并以相对路径调用，结束后拷回结果。
    # 另外 odiff 只认 24/32 位 BMP，8 位灰度等变体需先转 PNG。
    stage: _Staged | None = None
    if sys.platform == "win32" and (
        not _is_ascii(str(base))
        or not _is_ascii(str(compare))
        or not _is_ascii(str(diff_output))
        or _bmp_needs_png(Path(base))
        or _bmp_needs_png(Path(compare))
    ):
        try:
            stage = _stage_files(Path(base), Path(compare), Path(diff_output))
        except (OSError, ValueError):
            stage = None  # 暂存失败则按原路径直接调用

    if stage is not None:
        # 用相对路径 + cwd 调用，argv 里完全不出现中文；
        # 未暂存的 ASCII 输入（如纯 ASCII 的 PNG 模板）必须用绝对路径，
        # 否则会被误当成暂存目录内的文件名。
        def _arg(p: Path) -> str:
            try:
                return p.relative_to(stage.dir).as_posix()
            except ValueError:
                return str(p)

        cmd = [
            str(exe),
            _arg(stage.base),
            _arg(stage.compare),
            _arg(stage.out),
            *opts.build_args(),
            "--parsable-stdout",
        ]
        cwd = stage.dir
    else:
        cmd = [
            str(exe),
            str(base),
            str(compare),
            str(diff_output),
            *opts.build_args(),
            "--parsable-stdout",
        ]
        cwd = None

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 子进程输出可能被系统代码页污染，容错解码
            timeout=timeout,
            cwd=cwd,
            creationflags=0,  # Windows 下避免弹窗
        )
    except FileNotFoundError as exc:
        return DiffResult(False, "file-error", stderr=str(exc))
    except subprocess.TimeoutExpired:
        return DiffResult(False, "file-error", stderr=f"超时 ({timeout}s)")
    finally:
        # 把暂存目录里生成的 diff 结果图拷回真实输出路径
        if stage is not None:
            try:
                if stage.out.is_file():
                    shutil.copyfile(stage.out, diff_output)
            except OSError:
                pass
            shutil.rmtree(stage.dir, ignore_errors=True)

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
