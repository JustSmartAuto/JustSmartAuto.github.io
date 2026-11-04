"""文件夹扫描：把「图片 + 同名 SVG」识别为一对有效数据。

- 图片格式不限：以 Pillow 支持的扩展名为准（bmp/jpg/png/tif/webp/gif…）。
- 同名 SVG：扩展名 .svg 且文件主名（不区分大小写）与图片主名一致。
- 已生成的 {文件名}_叠加图.jpg 不参与扫描，避免重复叠加。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

OUTPUT_SUFFIX = "_叠加图.jpg"


@dataclass(frozen=True)
class Pair:
    """一对有效数据。"""

    stem: str
    image: Path
    svg: Path
    width: int
    height: int
    fmt: str

    @property
    def output(self) -> Path:
        """输出文件：与原图同目录，{文件名}_叠加图.jpg。"""
        return self.image.with_name(f"{self.image.stem}{OUTPUT_SUFFIX}")

    @property
    def output_exists(self) -> bool:
        return self.output.exists()


def upright_size(im: Image.Image) -> tuple[int, int]:
    """按 EXIF 方向返回「视觉上」的宽高（5~8 为旋转 90°，宽高互换）。"""
    try:
        orientation = im.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001 - 某些格式无 EXIF
        orientation = 1
    width, height = im.size
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


def image_extensions() -> set[str]:
    return {ext.lower() for ext in Image.registered_extensions()}


def scan_folder(folder: Path) -> tuple[list[Pair], dict]:
    """扫描文件夹，返回 (有效数据对, 统计信息)。"""
    files = [p for p in folder.iterdir() if p.is_file()]
    exts = image_extensions()

    svg_by_stem: dict[str, Path] = {}
    for path in files:
        if path.suffix.lower() == ".svg" and path.stem:
            svg_by_stem.setdefault(path.stem.casefold(), path)

    images = [
        p for p in files
        if p.suffix.lower() in exts and not p.name.endswith(OUTPUT_SUFFIX)
    ]

    pairs: list[Pair] = []
    missing_svg = 0
    unreadable = 0
    for image in images:
        svg = svg_by_stem.get(image.stem.casefold())
        if svg is None:
            missing_svg += 1
            continue
        try:
            with Image.open(image) as im:
                width, height = upright_size(im)
                fmt = (im.format or image.suffix.lstrip(".")).upper()
        except (UnidentifiedImageError, OSError, ValueError):
            unreadable += 1
            continue
        pairs.append(Pair(image.stem, image, svg, width, height, fmt))

    pairs.sort(key=lambda p: p.stem.casefold())
    stats = {
        "images": len(images),
        "pairs": len(pairs),
        "missing_svg": missing_svg,
        "unreadable": unreadable,
    }
    return pairs, stats
