"""文件夹批量分析：PatInspect 模板训练 + odiff 差异热力图。

PatInspect（图案检验）原理：
    mean(i,j)      = Σ training(i,j) / N              —— 训练图案（模板图像）
    std(i,j)       = sqrt(Σ (x - mean)² / N)          —— 标准差图像
    threshold(i,j) = A * std(i,j) + B                 —— 阈值图像
逐张比较 |image - mean| > threshold 的像素视为缺陷像素。
本模块输出模板图像（mean），并统计每张图的缺陷像素比例。

热力图：
    以模板图像为基准，逐张调用 odiff --diff-mask 得到差异像素掩码，
    累计每个像素被判定为差异的次数 / 参与比较张数，得到差异概率，
    再映射为伪彩色热力图。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .odiff import DiffOptions, run_diff

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp"}
_GRAY_MODES = {"1", "L", "I", "I;16", "I;16B", "I;16L", "F"}

# 进度回调：(phase, index, total, path)，phase = train / inspect / diff
Progress = Callable[[str, int, int, Path], None]


@dataclass
class TemplateResult:
    """模板训练结果。"""
    path: Path
    size: tuple[int, int]
    mode: str
    image_count: int
    mean_std: float
    mean_defect_ratio: float
    max_defect_ratio: float


@dataclass
class HeatmapResult:
    """差异热力图结果。"""
    path: Path
    size: tuple[int, int]
    image_count: int
    max_probability: float
    active_ratio: float       # 至少出现过一次差异的像素占比
    strong_ratio: float       # 差异概率 >= 50% 的像素占比


def collect_images(folder: str | Path) -> list[Path]:
    """收集文件夹第一层内的受支持图片（按文件名排序）。"""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=lambda p: p.name.lower(),
    )


def _target_mode(sample: Path) -> str:
    """按首张图决定统一模式：灰度图保持 L，其余转 RGB。"""
    with Image.open(sample) as im:
        return "L" if im.mode in _GRAY_MODES else "RGB"


def _load(path: Path, mode: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert(mode), dtype=np.float64)


def train_template(
    images: Sequence[str | Path],
    out_path: str | Path,
    *,
    threshold_scale: float = 2.0,
    threshold_offset: float = 10.0,
    progress: Progress | None = None,
) -> TemplateResult:
    """按 PatInspect 原理训练模板图像并逐张统计缺陷比例。

    训练图案即各像素均值图像；标准差用于计算逐像素阈值。
    所有训练图必须尺寸一致，否则抛出 ValueError。
    """
    paths = [Path(p) for p in images]
    if not paths:
        raise ValueError("文件夹内没有可分析的图片")

    mode = _target_mode(paths[0])
    with Image.open(paths[0]) as im:
        size = im.size
    shape = (size[1], size[0]) if mode == "L" else (size[1], size[0], 3)

    total = len(paths)
    sum_arr: np.ndarray | None = None
    sq_arr: np.ndarray | None = None
    for i, path in enumerate(paths):
        if progress:
            progress("train", i, total, path)
        arr = _load(path, mode)
        if arr.shape != shape:
            raise ValueError(
                f"图片尺寸不一致：{path.name} 为 {arr.shape[1]}×{arr.shape[0]}，"
                f"期望 {size[0]}×{size[1]}"
            )
        if sum_arr is None:
            sum_arr = np.zeros(shape, dtype=np.float64)
            sq_arr = np.zeros(shape, dtype=np.float64)
        sum_arr += arr
        sq_arr += arr * arr

    assert sum_arr is not None and sq_arr is not None
    mean = (sum_arr / total).astype(np.float32)
    var = sq_arr / total - mean.astype(np.float64) ** 2
    del sum_arr, sq_arr
    np.maximum(var, 0, out=var)
    std = np.sqrt(var).astype(np.float32)
    del var
    threshold = std * np.float32(threshold_scale) + np.float32(threshold_offset)
    mean_std = float(std.mean())

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    template = np.clip(np.rint(mean), 0, 255).astype(np.uint8)
    # odiff 的 PNG 解码不支持 8 位灰度，统一保存为 RGB
    template_img = Image.fromarray(template)
    if template_img.mode != "RGB":
        template_img = template_img.convert("RGB")
    template_img.save(out, "PNG")
    del template, template_img

    # 逐张按阈值图像统计缺陷像素
    defect_total = 0
    pixel_total = 0
    max_ratio = 0.0
    for i, path in enumerate(paths):
        if progress:
            progress("inspect", i, total, path)
        arr = _load(path, mode)
        diff = np.abs(arr - mean)
        bad = np.any(diff > threshold, axis=2) if mode == "RGB" else diff > threshold
        ratio = float(bad.mean())
        defect_total += int(bad.sum())
        pixel_total += bad.size
        max_ratio = max(max_ratio, ratio)

    return TemplateResult(
        path=out,
        size=size,
        mode=mode,
        image_count=total,
        mean_std=mean_std,
        mean_defect_ratio=defect_total / max(1, pixel_total),
        max_defect_ratio=max_ratio,
    )


def _heat_lut() -> np.ndarray:
    """0→黑、蓝→青→绿→黄→红的 256 级伪彩色查找表。"""
    stops = [0.00, 0.02, 0.20, 0.40, 0.60, 0.80, 1.00]
    colors = np.array([
        (0, 0, 0),
        (0, 0, 140),
        (0, 90, 255),
        (0, 255, 220),
        (80, 255, 80),
        (255, 230, 0),
        (255, 0, 0),
    ], dtype=np.float64)
    x = np.linspace(0.0, 1.0, 256)
    return np.stack(
        [np.interp(x, stops, colors[:, ch]) for ch in range(3)], axis=1
    ).astype(np.uint8)


def make_heatmap(
    images: Sequence[str | Path],
    reference: str | Path,
    out_path: str | Path,
    *,
    threshold: float = 0.1,
    antialiasing: bool = False,
    progress: Progress | None = None,
) -> HeatmapResult:
    """以模板为基准逐张 odiff，累计每像素差异概率并输出热力图。"""
    paths = [Path(p) for p in images]
    if not paths:
        raise ValueError("文件夹内没有可分析的图片")
    ref = Path(reference)
    if not ref.is_file():
        raise FileNotFoundError(f"模板图像不存在: {ref}")

    with Image.open(ref) as im:
        ref_size = im.size
    counts = np.zeros((ref_size[1], ref_size[0]), dtype=np.uint16)
    compared = 0
    total = len(paths)

    with tempfile.TemporaryDirectory(prefix="image_diff_heat_") as td:
        for i, path in enumerate(paths):
            if progress:
                progress("diff", i, total, path)
            mask_path = Path(td) / f"mask_{i:04d}.png"
            result = run_diff(
                ref, path, mask_path,
                options=DiffOptions(
                    threshold=threshold,
                    diff_color="#ff00ff",
                    antialiasing=antialiasing,
                    diff_mask=True,
                    fail_on_layout=True,
                ),
            )
            if result.reason == "file-error":
                raise RuntimeError(f"odiff 处理 {path.name} 失败：{result.stderr}")
            if result.reason == "layout-diff":
                raise ValueError(f"{path.name} 与模板图像尺寸不同")
            compared += 1
            if result.match:
                continue
            if not mask_path.is_file():
                raise RuntimeError(f"odiff 未生成差异掩码：{path.name}")
            with Image.open(mask_path) as im:
                if im.size != ref_size:
                    raise ValueError(f"{path.name} 的差异掩码尺寸异常")
                alpha = np.asarray(im.convert("RGBA"))[..., 3]
            counts += (alpha > 0).astype(np.uint16)

    if compared == 0:
        raise RuntimeError("没有可比较的图片")
    prob = counts.astype(np.float32) / compared
    del counts

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lut = _heat_lut()
    index = np.clip(np.rint(prob * 255.0), 0, 255).astype(np.uint8)
    rgb = lut[index]
    Image.fromarray(rgb).save(out, "PNG")
    del rgb, index

    return HeatmapResult(
        path=out,
        size=ref_size,
        image_count=compared,
        max_probability=float(prob.max()),
        active_ratio=float(np.count_nonzero(prob) / prob.size),
        strong_ratio=float((prob >= 0.5).mean()),
    )
