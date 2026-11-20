from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
TRANSPOSES = {
    "cw": Image.Transpose.ROTATE_270,
    "ccw": Image.Transpose.ROTATE_90,
    "180": Image.Transpose.ROTATE_180,
}


def collect_images(paths: list[Path]) -> list[Path]:
    images: dict[Path, None] = {}
    for path in paths:
        if path.is_dir():
            candidates = (p for p in path.rglob("*") if p.is_file())
        else:
            candidates = (path,)
        for candidate in candidates:
            if candidate.suffix.lower() in SUPPORTED_EXTS:
                images[candidate.resolve()] = None
    return list(images)


def rotate_image(path: Path, direction: str) -> Path:
    if direction not in TRANSPOSES:
        raise ValueError(f"未知旋转方向: {direction}")

    backup = path.with_name(path.name + ".bak")
    with Image.open(path) as source:
        source.load()
        image = ImageOps.exif_transpose(source)
        rotated = image.transpose(TRANSPOSES[direction])
        exif = image.getexif()
        exif[274] = 1
        save_options: dict[str, object] = {}
        if source.info.get("icc_profile"):
            save_options["icc_profile"] = source.info["icc_profile"]
        if source.info.get("dpi"):
            save_options["dpi"] = source.info["dpi"]
        if exif:
            save_options["exif"] = exif.tobytes()
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            save_options.update(quality=95, subsampling=0)

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.stem}_", suffix=path.suffix, dir=path.parent
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            rotated.save(temp_path, format=source.format, **save_options)
            if not backup.exists():
                shutil.copy2(path, backup)
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            if hasattr(rotated, "close"):
                rotated.close()
    return backup
