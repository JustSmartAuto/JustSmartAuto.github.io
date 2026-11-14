__version__ = "1.2.0"

import sys
import tkinter as tk
from pathlib import Path


def icon_path() -> Path | None:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        candidates += [
            base / "assets" / "remoteripple.ico",
            base / "remoteripple" / "assets" / "remoteripple.ico",
        ]
    candidates.append(Path(__file__).with_name("assets") / "remoteripple.ico")
    if not meipass:
        candidates.append(Path(__file__).resolve().parents[2] / "assets" / "remoteripple.ico")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def apply_icon(window: object) -> None:
    icon = icon_path()
    if not icon:
        return
    try:
        window.iconbitmap(str(icon))
    except tk.TclError:
        try:
            from PIL import Image, ImageTk

            with Image.open(icon) as image:
                window.iconphoto(False, ImageTk.PhotoImage(image))
        except Exception:
            pass
