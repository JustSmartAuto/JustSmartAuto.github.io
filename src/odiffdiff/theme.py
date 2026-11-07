"""Windows 95 界面主题。

设计 token 取自 assets/02-windows-95.css：
    --bg: teal 桌面 #008080、--chrome: #c0c0c0 窗口面板、
    --title: navy #000080 标题栏、按钮高光 #dfdfdf / #fff、暗边 #808080 / #000。
按钮/输入框/分组框以 2 重立体边框近似 Win95 凸起/凹陷效果。

（从 DataRouterPy 原样拷贝，同一项目族共享）
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# —— 02-windows-95.css 设计 token —————————————————————————
BG = "#008080"               # --bg
CHROME = "#c0c0c0"           # --chrome
CHROME_LIGHT = "#dfdfdf"     # --chrome-light
CHROME_LIGHTER = "#ffffff"   # --chrome-lighter
CHROME_DARK = "#808080"      # --chrome-dark
CHROME_DARKER = "#000000"    # --chrome-darker
TITLE = "#000080"            # --title / --select
TITLE_TEXT = "#ffffff"       # --title-text
INK = "#000000"              # --ink

FONT = ("Tahoma", 9)
FONT_BOLD = ("Tahoma", 9, "bold")


def apply(root: tk.Misc) -> None:
    """Win95 配色应用到根窗口与 ttk。"""
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=FONT, background=CHROME, foreground=INK)
    style.configure("TFrame", background=CHROME)
    style.configure("TLabel", background=CHROME, foreground=INK, font=FONT)
    style.configure("Win.TCheckbutton", background=CHROME, foreground=INK, font=FONT)

    # Combobox
    style.configure("Win.TCombobox", fieldbackground=CHROME_LIGHTER, foreground=INK,
                    arrowcolor=INK, borderwidth=2)
    style.map("Win.TCombobox", fieldbackground=[("readonly", CHROME_LIGHTER)],
              selectbackground=[("readonly", TITLE)],
              selectforeground=[("readonly", TITLE_TEXT)])

    # Spinbox
    style.configure("Win.TSpinbox", background=CHROME_LIGHTER, foreground=INK,
                    arrowcolor=INK, borderwidth=2)
    style.configure("Win.TEntry", fieldbackground=CHROME_LIGHTER, foreground=INK, borderwidth=2)


def win95_button(master: tk.Misc, text: str, command=None, width: int | None = None,
                 padx: int = 12) -> tk.Button:
    """Win95 凸起按钮：灰色底、亮/暗立体边。"""
    kw: dict = {
        "text": text, "command": command, "font": FONT,
        "bg": CHROME, "activebackground": CHROME_LIGHT,
        "fg": INK, "activeforeground": INK, "disabledforeground": CHROME_DARK,
        "relief": "raised", "bd": 2, "highlightthickness": 0,
        "padx": padx, "pady": 1,
    }
    if width is not None:
        kw["width"] = width
    return tk.Button(master, **kw)


def field(master: tk.Misc, **kw) -> tk.Entry:
    """Win95 凹陷输入框。"""
    kw.setdefault("bd", 2)
    kw.setdefault("relief", "sunken")
    kw.setdefault("bg", CHROME_LIGHTER)
    kw.setdefault("fg", INK)
    kw.setdefault("insertbackground", INK)
    kw.setdefault("font", FONT)
    return tk.Entry(master, **kw)


def group(master: tk.Misc, title: str = "", **kw) -> tk.LabelFrame:
    """Win95 蚀刻分组框。"""
    kw.setdefault("bd", 2)
    kw.setdefault("relief", "groove")
    kw.setdefault("text", title)
    kw.setdefault("bg", CHROME)
    kw.setdefault("fg", INK)
    kw.setdefault("font", FONT)
    kw.setdefault("labelanchor", "nw")
    return tk.LabelFrame(master, **kw)


def sunken(master: tk.Misc, **kw) -> tk.Frame:
    """承载内容区的凹陷面板（图像区等）。"""
    kw.setdefault("bd", 2)
    kw.setdefault("relief", "sunken")
    kw.setdefault("bg", CHROME_DARKER)
    return tk.Frame(master, **kw)


class ProgressBar(tk.Canvas):
    """Win95 风格分段进度条（凹陷边框 + 蓝色方块）。"""

    def __init__(self, master: tk.Misc, width: int = 200, height: int = 18, **kw):
        kw.setdefault("bg", CHROME)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(master, width=width, height=height, **kw)
        self._value = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())
        self._redraw()

    def set(self, value: float) -> None:
        """设置进度 0.0 ~ 1.0。"""
        value = max(0.0, min(1.0, float(value)))
        if value != self._value:
            self._value = value
            self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = max(20, self.winfo_width())
        h = max(10, self.winfo_height())

        # 凹陷边框：左上深灰、右下亮白
        self.create_rectangle(1, 1, w - 2, h - 2, fill=CHROME_LIGHTER, outline="")
        self.create_line(0, 0, w - 1, 0, fill=CHROME_DARK, width=2)
        self.create_line(0, 0, 0, h - 1, fill=CHROME_DARK, width=2)
        self.create_line(0, h - 1, w - 1, h - 1, fill=CHROME_LIGHTER, width=2)
        self.create_line(w - 1, 0, w - 1, h - 1, fill=CHROME_LIGHTER, width=2)

        # 蓝色分段块
        x0, y0, x1, y1 = 3, 3, w - 4, h - 4
        filled = (x1 - x0) * self._value
        block, gap = 9, 3
        x = x0
        while x < x0 + filled:
            right = min(x + block, x0 + filled)
            self.create_rectangle(x, y0, right, y1, fill=TITLE, outline="")
            x += block + gap


def titlebar(master: tk.Misc, title: str, end_text: str = "") -> tuple[tk.Frame, tk.Label]:
    """窗口内嵌标题栏（navy 底 + 白字），返回 (标题栏, 右侧状态 Label)。"""
    bar = tk.Frame(master, bg=TITLE)
    bar.pack(fill="x")
    tk.Label(bar, text=title, bg=TITLE, fg=TITLE_TEXT,
             font=FONT_BOLD).pack(side="left", padx=6, pady=3)
    end = tk.Label(bar, text=end_text, bg=TITLE, fg=TITLE_TEXT, font=FONT)
    end.pack(side="right", padx=6)
    return bar, end


def statusbar(master: tk.Misc) -> tuple[tk.Frame, tk.Label, tk.Label, tk.Label]:
    """Win95 状态栏（左长格 + 中/右短格），返回 (栏, 左Label, 中Label, 右Label)。"""
    bar = tk.Frame(master, bg=BG)
    bar.pack(fill="x", side="bottom")
    left = tk.Label(bar, text="", anchor="w", bg=CHROME, fg=INK, font=FONT,
                    padx=6, height=1)
    left.pack(side="left", fill="x", expand=True, ipady=2)
    mid = tk.Label(bar, text="", anchor="center", bg=CHROME, fg=INK, font=FONT,
                   padx=6, width=16)
    mid.pack(side="left", padx=(4, 0), ipady=2)
    right = tk.Label(bar, text="", anchor="e", bg=CHROME, fg=INK, font=FONT,
                     padx=6, width=16)
    right.pack(side="right", padx=(4, 0), ipady=2)
    return bar, left, mid, right
