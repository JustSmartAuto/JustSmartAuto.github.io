"""把图片 / SVG 转成浏览器可直接消费的 data URL，并生成渲染载荷。

合成在 WebView2 的 canvas 中完成：
    1. 底图按原图 3 倍尺寸绘制（3× DPI）；
    2. 同名 SVG 按同样尺寸叠加；
    3. canvas.toDataURL('image/jpeg', 0.95) 回传，落盘为 {文件名}_叠加图.jpg。
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from PIL import Image, ImageOps

SCALE = 3
JPEG_QUALITY = 0.95
PREVIEW_MAX_SIDE = 1000

# 浏览器原生支持的图片格式，原样传字节可避免二次压缩
_BROWSER_MIME = {
    ".bmp": "image/bmp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
}

_XML_DECL_RE = re.compile(r"<\?xml[^>]*\?>\s*", re.I)
_IMAGE_TAG_RE = re.compile(r"<image\b[^>]*?(?:/>|>\s*</image>)", re.I | re.S)
_HREF_RE = re.compile(r"""(?:xlink:href|href)\s*=\s*["']([^"']*)["']""", re.I)
_SVG_ROOT_RE = re.compile(r"<svg\b([^>]*?)>", re.I | re.S)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _data_url(mime: str, payload: str) -> str:
    return f"data:{mime};base64,{payload}"


def _attr_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"""\b{name}\s*=\s*["']([^"']*)["']""", re.I)


def _flatten(im: Image.Image) -> Image.Image:
    """去掉透明通道（JPEG 无 alpha），透明区域铺白。"""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return im if im.mode in ("RGB", "L") else im.convert("RGB")


def upright_image(path: Path) -> Image.Image:
    """读入并按 EXIF 方向摆正的图像副本。"""
    with Image.open(path) as im:
        im.load()
        transposed = ImageOps.exif_transpose(im)
        return transposed.copy() if transposed is im else transposed


def image_data_url(
    path: Path,
    *,
    max_side: int | None = None,
    quality: int | None = None,
) -> tuple[str, int, int]:
    """返回 (dataURL, 视觉宽, 视觉高)。"""
    suffix = path.suffix.lower()
    with Image.open(path) as probe:
        orientation = probe.getexif().get(0x0112, 1)
        has_alpha = probe.mode in ("RGBA", "LA", "PA") or (
            probe.mode == "P" and "transparency" in probe.info
        )

    # 原始字节直传：浏览器支持该格式、无 EXIF 旋转、无透明通道
    if max_side is None and quality is None and suffix in _BROWSER_MIME \
            and orientation in (1, None) and not has_alpha:
        raw = path.read_bytes()
        with Image.open(path) as probe:
            width, height = probe.size
        return _data_url(_BROWSER_MIME[suffix], _b64(raw)), width, height

    im = upright_image(path)
    width, height = im.size
    if max_side is not None and max(width, height) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        width, height = im.size

    im = _flatten(im)
    buffer = io.BytesIO()
    if quality is None:
        im.save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    else:
        im.save(buffer, format="JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"
    return _data_url(mime, _b64(buffer.getvalue())), width, height


def svg_data_url(
    svg_path: Path,
    *,
    base_stem: str,
    out_width: int,
    out_height: int,
    image_width: int,
    image_height: int,
) -> str:
    """重写 SVG 后返回 data URL。

    - 移除引用同名底图的 <image>（底图由 canvas 单独绘制，避免重复压缩）；
    - 把 svg 的 width/height 放大到输出尺寸，让浏览器按 3 倍分辨率栅格化（文字更清晰）；
    - 缺少 viewBox 时按原图尺寸补一个，保证坐标映射与底图一致。
    """
    text = _XML_DECL_RE.sub("", svg_path.read_text(encoding="utf-8", errors="replace"), count=1)

    base = base_stem.casefold()

    def drop_base_image(match: re.Match[str]) -> str:
        tag = match.group(0)
        href = _HREF_RE.search(tag)
        if href and Path(href.group(1)).stem.casefold() == base:
            return ""
        return tag

    text = _IMAGE_TAG_RE.sub(drop_base_image, text)

    root = _SVG_ROOT_RE.search(text)
    if root:
        attrs = root.group(1)
        viewbox = _attr_re("viewBox").search(attrs)
        has_viewbox = False
        if viewbox:
            numbers = re.findall(r"-?\d+(?:\.\d+)?", viewbox.group(1))
            has_viewbox = len(numbers) == 4 and float(numbers[2]) > 0 and float(numbers[3]) > 0

        def set_attr(source: str, name: str, value: int) -> str:
            pattern = _attr_re(name)
            if pattern.search(source):
                return pattern.sub(f'{name}="{value}"', source, count=1)
            return f'{source} {name}="{value}"'

        attrs = set_attr(attrs, "width", out_width)
        attrs = set_attr(attrs, "height", out_height)
        if not has_viewbox:
            attrs += f' viewBox="0 0 {image_width} {image_height}"'
        text = text[: root.start(1)] + attrs + text[root.end(1) :]

    return _data_url("image/svg+xml", _b64(text.encode("utf-8")))


def output_size(pair_width: int, pair_height: int) -> tuple[int, int]:
    return pair_width * SCALE, pair_height * SCALE


def build_export_payload(pair) -> dict:
    """3 倍分辨率的合成载荷（底图为全分辨率）。"""
    out_width, out_height = output_size(pair.width, pair.height)
    image_url, width, height = image_data_url(pair.image)
    svg_url = svg_data_url(
        pair.svg,
        base_stem=pair.image.stem,
        out_width=out_width,
        out_height=out_height,
        image_width=width,
        image_height=height,
    )
    return {
        "stem": pair.stem,
        "imageUrl": image_url,
        "svgUrl": svg_url,
        "outWidth": out_width,
        "outHeight": out_height,
        "quality": JPEG_QUALITY,
    }


def build_preview_payload(pair) -> dict:
    """界面预览载荷（底图降采样，叠加层沿用 3 倍尺寸以保证清晰度）。"""
    out_width, out_height = output_size(pair.width, pair.height)
    image_url, width, height = image_data_url(
        pair.image, max_side=PREVIEW_MAX_SIDE, quality=85
    )
    svg_url = svg_data_url(
        pair.svg,
        base_stem=pair.image.stem,
        out_width=out_width,
        out_height=out_height,
        image_width=width,
        image_height=height,
    )
    output = pair.output
    return {
        "stem": pair.stem,
        "imageUrl": image_url,
        "svgUrl": svg_url,
        "width": width,
        "height": height,
        "outWidth": out_width,
        "outHeight": out_height,
        "imageName": pair.image.name,
        "svgName": pair.svg.name,
        "outputName": output.name,
        "outputExists": output.exists(),
        "scale": SCALE,
    }
