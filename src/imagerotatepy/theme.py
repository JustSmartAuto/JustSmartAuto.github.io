from __future__ import annotations

import tkinter as tk

BG = "#008080"
CHROME = "#c0c0c0"
LIGHT = "#dfdfdf"
WHITE = "#ffffff"
DARK = "#808080"
BLACK = "#000000"
TITLE = "#000080"
FONT = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")


def apply(root: tk.Misc) -> None:
    root.configure(bg=BG)


def button(master: tk.Misc, text: str, command=None, width: int | None = None) -> tk.Button:
    options = {
        "text": text, "command": command, "font": FONT, "bg": CHROME,
        "activebackground": LIGHT, "relief": "raised", "bd": 2,
        "highlightthickness": 0, "padx": 12, "pady": 4,
    }
    if width is not None:
        options["width"] = width
    return tk.Button(master, **options)


def group(master: tk.Misc, title: str = "") -> tk.LabelFrame:
    return tk.LabelFrame(
        master, text=title, bg=CHROME, fg=BLACK, font=FONT,
        bd=2, relief="groove", labelanchor="nw",
    )


def titlebar(master: tk.Misc, title: str) -> tuple[tk.Frame, tk.Label]:
    bar = tk.Frame(master, bg=TITLE)
    bar.pack(fill="x")
    tk.Label(bar, text=title, bg=TITLE, fg=WHITE, font=FONT_BOLD).pack(
        side="left", padx=8, pady=4
    )
    status = tk.Label(bar, text="就绪", bg=TITLE, fg=WHITE, font=FONT)
    status.pack(side="right", padx=8)
    return bar, status
