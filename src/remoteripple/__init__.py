__version__ = "1.2.0"

from pathlib import Path


def icon_path() -> Path | None:
    icon = Path(__file__).with_name("assets") / "remoteripple.ico"
    if icon.exists():
        return icon
    fallback = Path(__file__).resolve().parents[2] / "assets" / "remoteripple.ico"
    return fallback if fallback.exists() else None


def apply_icon(window: object) -> None:
    icon = icon_path()
    if icon:
        window.iconbitmap(str(icon))
