from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, font as tkfont

# 设计令牌：摘自 assets/claude-style.css（Claude 风格 · 温暖极简）
LIGHT = {
    "bg": "#faf9f5",        # --cream 页面主背景
    "surface": "#f0eee6",   # --cream-deep 次级背景
    "card": "#ffffff",      # --card 卡片表面
    "line": "#e8e5dc",      # --line 细分隔线
    "ink": "#29261b",       # --ink 主文本
    "ink_soft": "#6b6455",  # --ink-soft 次文本
    "coral": "#d97757",     # --coral 品牌高亮
    "coral_deep": "#c15f3c",  # --coral-deep 悬停加深
}
DARK = {
    "bg": "#252525",
    "surface": "#333333",
    "card": "#2f2f2f",
    "line": "#454545",
    "ink": "#eeeeee",
    "ink_soft": "#b9b4a8",
    "coral": "#d97757",
    "coral_deep": "#e08a6a",
}
FONT_FALLBACK = "Segoe UI"
FONT_MONO = ("Consolas", 10)
_FONT_FILE = "Smiley Sans Oblique.ttf"
_FR_PRIVATE = 0x10

_theme_font: tuple[str, int] = (FONT_FALLBACK, 10)


def font_path() -> Path | None:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        candidates += [
            base / "assets" / _FONT_FILE,
            base / "remoteripple" / "assets" / _FONT_FILE,
        ]
    candidates.append(Path(__file__).with_name("assets") / _FONT_FILE)
    if not meipass:
        candidates.append(Path(__file__).resolve().parents[2] / "assets" / _FONT_FILE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_theme_family() -> str:
    path = font_path()
    if not path:
        return FONT_FALLBACK
    try:
        if not ctypes.windll.gdi32.AddFontResourceExW(str(path), _FR_PRIVATE, 0):
            return FONT_FALLBACK
    except Exception:
        return FONT_FALLBACK
    matched: str | None = None
    for family in tkfont.families():
        lowered = family.casefold()
        if "smiley sans" in lowered or "得意黑" in family:
            if not family.startswith("@"):
                return family
            matched = matched or family
    return matched or FONT_FALLBACK


def theme_font(size: int = 10, bold: bool = False) -> tuple[str, int] | tuple[str, int, str]:
    family, _ = _theme_font
    return (family, size, "bold") if bold else (family, size)


def apply_theme(root: tk.Misc, night: bool = False) -> None:
    global _theme_font
    c = DARK if night else LIGHT
    family = _load_theme_family()
    _theme_font = (family, 10)
    ui = (family, 10)
    ui_bold = (family, 10, "bold")
    ui_head = (family, 24)
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=c["bg"])

    style.configure(
        ".",
        font=ui,
        background=c["bg"],
        foreground=c["ink"],
        fieldbackground=c["card"],
        bordercolor=c["line"],
        lightcolor=c["card"],
        darkcolor=c["card"],
    )

    style.configure("TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["card"])
    style.configure("TLabel", background=c["bg"], foreground=c["ink"])
    style.configure("Soft.TLabel", background=c["bg"], foreground=c["ink_soft"])
    style.configure("Card.TLabel", background=c["card"], foreground=c["ink"])
    style.configure(
        "Status.TLabel",
        background=c["surface"],
        foreground=c["ink_soft"],
        padding=(10, 5),
        anchor="w",
    )

    style.configure(
        "TButton",
        font=ui,
        background=c["card"],
        foreground=c["ink"],
        bordercolor=c["line"],
        focuscolor=c["coral"],
        padding=(12, 5),
        relief="flat",
        borderwidth=1,
    )
    style.map(
        "TButton",
        background=[("pressed", c["surface"]), ("active", c["surface"])],
        bordercolor=[("focus", c["coral"])],
    )

    style.configure(
        "Accent.TButton",
        background=c["coral"],
        foreground="#ffffff",
        bordercolor=c["coral"],
        focuscolor=c["coral_deep"],
        font=ui_bold,
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", c["surface"]), ("pressed", c["coral_deep"]), ("active", c["coral_deep"])],
        foreground=[("disabled", c["ink_soft"])],
        bordercolor=[("focus", c["coral_deep"])],
    )

    style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(10, 8, 10, 0))
    style.configure("TNotebook.Tab", padding=(16, 7), background=c["surface"], foreground=c["ink_soft"])
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["card"])],
        foreground=[("selected", c["coral_deep"])],
        bordercolor=[("selected", c["line"])],
    )

    style.configure(
        "Treeview",
        background=c["card"],
        fieldbackground=c["card"],
        foreground=c["ink"],
        rowheight=26,
        borderwidth=0,
        font=ui,
    )
    style.map(
        "Treeview",
        background=[("selected", c["coral"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=c["surface"],
        foreground=c["ink_soft"],
        font=ui_bold,
        relief="flat",
        padding=(8, 6),
    )
    style.map("Treeview.Heading", background=[("active", c["line"])])

    style.configure(
        "TEntry",
        fieldbackground=c["card"],
        foreground=c["ink"],
        bordercolor=c["line"],
        lightcolor=c["card"],
        darkcolor=c["card"],
        insertcolor=c["ink"],
        padding=5,
    )
    style.map("TEntry", bordercolor=[("focus", c["coral"])], lightcolor=[("focus", c["coral"])])
    style.configure("TCombobox", fieldbackground=c["card"], foreground=c["ink"], padding=5, arrowcolor=c["ink_soft"])
    style.map("TCombobox", fieldbackground=[("readonly", c["card"])])

    style.configure("TCheckbutton", background=c["bg"], foreground=c["ink"], focuscolor=c["coral"])
    style.map("TCheckbutton", background=[("active", c["bg"])])

    style.configure("TLabelframe", background=c["bg"], bordercolor=c["line"], relief="flat", borderwidth=1)
    style.configure("TLabelframe.Label", background=c["bg"], foreground=c["ink_soft"], font=ui)

    style.configure("TScale", troughcolor=c["surface"], background=c["card"], bordercolor=c["line"], lightcolor=c["line"], darkcolor=c["line"])
    style.map("TScale", background=[("active", c["coral"])])

    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=c["surface"],
        background=c["coral"],
        bordercolor=c["surface"],
        lightcolor=c["coral"],
        darkcolor=c["coral"],
        thickness=6,
    )

    style.configure("TScrollbar", background=c["surface"], troughcolor=c["bg"], bordercolor=c["bg"], arrowcolor=c["ink_soft"], gripcount=0)
    style.map("TScrollbar", background=[("active", c["line"])])

    style.configure("TPanedwindow", background=c["bg"])
