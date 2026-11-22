"""Claude 风格（Warm Minimalist）设计令牌与 Tkinter 圆角组件。

令牌提取自 assets/claude-style.css，Tkinter 不支持 CSS，这里用同等配色 / 字体 /
圆角在 Canvas 上重绘关键组件：白色圆角卡片（.card）、胶囊按钮（.cta）。
"""
import tkinter as tk

# ---------- 设计令牌（与 claude-style.css 一一对应） ----------
CREAM = "#faf9f5"          # 页面主背景
CREAM_DEEP = "#f0eee6"     # 次级背景
CARD = "#ffffff"           # 卡片表面
LINE = "#e8e5dc"           # 细分隔线
INK = "#29261b"            # 主文本
INK_SOFT = "#6b6455"       # 次文本
CORAL = "#d97757"          # 品牌高亮
CORAL_DEEP = "#c15f3c"     # 悬停加深

RADIUS = 16                # 卡片圆角

FONT_SERIF = "Georgia"     # 大标题 / 长文（中文由 Windows 字体链接回退）
FONT_SANS = "Segoe UI"     # UI / kicker / 按钮
FONT_CN = "Microsoft YaHei"

H1 = (FONT_SERIF, 30)
H2 = (FONT_SANS, 12, "bold")
BODY = (FONT_SANS, 11)
LEDE = (FONT_SANS, 12)
KICKER = (FONT_SANS, 10, "bold")


def round_rect(canvas, bbox, r, fill, outline=None, width=1, tags=None):
    """在 Canvas 上绘制圆角矩形（填充 + 可选描边），Tk 8.6 无原生 round_rect。"""
    x1, y1, x2, y2 = bbox
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    kw = {} if tags is None else {"tags": tags}
    fill_kw = dict(fill=fill, outline=fill)
    # 中间十字带 + 四个扇形角，拼出实心圆角矩形；outline 设为 fill 以隐藏内部拼缝
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, **fill_kw, **kw)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, **fill_kw, **kw)
    arcs = [
        (x1, y1, x1 + 2 * r, y1 + 2 * r, 90),
        (x2 - 2 * r, y1, x2, y1 + 2 * r, 0),
        (x2 - 2 * r, y2 - 2 * r, x2, y2, 270),
        (x1, y2 - 2 * r, x1 + 2 * r, y2, 180),
    ]
    for ax1, ay1, ax2, ay2, start in arcs:
        canvas.create_arc(ax1, ay1, ax2, ay2, start=start, extent=90,
                          style="pieslice", **fill_kw, **kw)
    # 描边：四段直线 + 四段圆弧
    if outline:
        b = dict(outline=outline, width=width)
        t = tags
        canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width)
        canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width)
        canvas.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width)
        canvas.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width)
        for ax1, ay1, ax2, ay2, start in arcs:
            canvas.create_arc(ax1, ay1, ax2, ay2, start=start, extent=90,
                              style="arc", **b)


class RoundedCard(tk.Canvas):
    """白底圆角卡片，内部放一个 Frame 承载子控件。"""

    def __init__(self, master, radius=RADIUS, fill=CARD, outline=LINE, pad=20, **kw):
        super().__init__(master, highlightthickness=0, bd=0, bg=master["bg"], **kw)
        self._r = radius
        self._fill = fill
        self._outline = outline
        self._pad = pad
        self.inner = tk.Frame(self, bg=fill)
        self._win = self.create_window(pad, pad, anchor="nw", window=self.inner)
        self._after_id = None
        self.inner.bind("<Configure>", self._schedule_fit)
        self.bind("<Configure>", self._draw)

    def _schedule_fit(self, _e):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(10, self._fit)

    def _fit(self):
        self._after_id = None
        w = self.inner.winfo_reqwidth() + self._pad * 2
        h = self.inner.winfo_reqheight() + self._pad * 2
        self.configure(width=w, height=h)
        self.coords(self._win, self._pad, self._pad)
        self._draw()

    def _draw(self, _e=None):
        self.delete("card")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        round_rect(self, (0, 0, w - 1, h - 1), self._r,
                   fill=self._fill, outline=self._outline, tags="card")
        self.tag_lower("card")


class PillButton(tk.Canvas):
    """胶囊按钮：主按钮珊瑚填充，次按钮描边（对应 .cta / .cta.ghost）。"""

    def __init__(self, master, text, command, primary=True,
                 w=150, h=46, **kw):
        bg = master["bg"]
        super().__init__(master, width=w, height=h,
                         highlightthickness=0, bd=0, bg=bg, **kw)
        self._text = text
        self._cmd = command
        self._primary = primary
        self._bw, self._bh = w, h
        self._hover = False
        self._enabled = True
        self._draw()
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def set_primary(self, primary):
        self._primary = primary
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._draw()

    def _set_hover(self, v):
        if self._hover == v:
            return
        self._hover = v
        self._draw()

    def _click(self, _e):
        if self._enabled and self._cmd:
            self._cmd()

    def _draw(self):
        self.delete("btn")
        if not self._enabled:
            fill = "#d8d3c7" if self._primary else self["bg"]
            txt = "#9a9486"
            outline = "#d8d3c7"
            self._paint(fill, outline, txt)
            return
        if self._primary:
            fill = CORAL_DEEP if self._hover else CORAL
            self._paint(fill, fill, "#ffffff", bold=True)
        else:
            outline = CORAL if self._hover else INK
            txt = CORAL if self._hover else INK
            self._paint(self["bg"], outline, txt, bold=True)

    def _paint(self, fill, outline, txt, bold=False):
        x1, y1, x2, y2 = 1, 1, self._bw - 1, self._bh - 1
        r = (self._bh - 2) / 2
        round_rect(self, (x1, y1, x2, y2), r,
                   fill=fill, outline=outline, tags="btn")
        font = (FONT_SANS, 13, "bold") if bold else (FONT_SANS, 12)
        self.create_text(self._bw / 2, self._bh / 2, text=self._text,
                         fill=txt, font=font, tags="btn")
