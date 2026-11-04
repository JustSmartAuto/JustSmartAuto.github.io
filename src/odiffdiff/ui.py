"""OdiffDiff 主界面 — 三栏并排图像差分对比。

布局：
    ┌─ 标题栏 ─────────────────────────────────────────────────┐
    │  工具栏：选择 Base / Compare 图像  [参数]  [开始 Diff]  │
    ├──────────────┬──────────────┬──────────────────────────┤
    │   Base 图    │  Compare 图  │        Diff 结果         │
    │  (可缩放)    │   (可缩放)   │       (可缩放)           │
    ├──────────────┴──────────────┴──────────────────────────┤
    │  状态栏：路径 / 结果摘要 / 版本                         │
    └────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import __version__, theme
from .odiff import DiffOptions, DiffResult, run_diff

SUPPORTED_FORMATS = [
    ("图片文件", "*.png *.jpg *.jpeg *.webp *.tiff *.tif *.bmp"),
    ("PNG", "*.png"),
    ("JPEG", "*.jpg *.jpeg"),
    ("WebP", "*.webp"),
    ("TIFF", "*.tiff *.tif"),
    ("BMP", "*.bmp"),
    ("所有文件", "*.*"),
]


class OdiffDiffApp:
    """OdiffDiff 主窗口。"""

    def __init__(self) -> None:
        self.base_path: Path | None = None
        self.compare_path: Path | None = None
        self.diff_result_path: Path | None = None
        self._base_img: Image.Image | None = None
        self._compare_img: Image.Image | None = None
        self._diff_img: Image.Image | None = None
        self._base_tk: ImageTk.PhotoImage | None = None
        self._compare_tk: ImageTk.PhotoImage | None = None
        self._diff_tk: ImageTk.PhotoImage | None = None
        self._base_scale = 1.0
        self._compare_scale = 1.0
        self._diff_scale = 1.0

        self.root = tk.Tk()
        self.root.title(f"OdiffDiff — 图像差分 v{__version__}")
        self.root.geometry("1180x720")
        self.root.minsize(900, 560)
        self._set_icon()
        theme.apply(self.root)

        self._build_menu()
        self._build_titlebar()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

    # —— 窗口骨架 ————————————————————————————————————————————

    def _set_icon(self) -> None:
        icon = Path(__file__).resolve().parents[2] / "assets" / "odiff-icon.png"
        if icon.exists():
            try:
                img = Image.open(icon).resize((32, 32), Image.LANCZOS)
                self._icon_img = ImageTk.PhotoImage(img)
                self.root.iconphoto(True, self._icon_img)
            except Exception:  # noqa: BLE001
                pass

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="选择 Base 图像...", command=self._on_pick_base, accelerator="Ctrl+1")
        file_menu.add_command(label="选择 Compare 图像...", command=self._on_pick_compare, accelerator="Ctrl+2")
        file_menu.add_separator()
        file_menu.add_command(label="保存 Diff 结果为...", command=self._on_save_diff)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-1>", lambda _e: self._on_pick_base())
        self.root.bind("<Control-2>", lambda _e: self._on_pick_compare())

    def _build_titlebar(self) -> None:
        bar, self.status_tag = theme.titlebar(
            self.root, "  OdiffDiff — 图像差分", end_text="就绪")

    def _build_statusbar(self) -> None:
        bar, self.sb_left, self.sb_mid, self.sb_right = theme.statusbar(self.root)
        self.sb_right.config(text=f"v{__version__}")

    # —— 工具栏 ——————————————————————————————————————————————

    def _build_toolbar(self) -> None:
        tb = theme.group(self.root, " 图像与参数")
        tb.pack(fill="x", padx=10, pady=(10, 6))

        # 第一行：选择 Base / Compare 图像
        row1 = tk.Frame(tb, bg=theme.CHROME)
        row1.pack(fill="x", padx=8, pady=(8, 4))

        theme.win95_button(row1, "选择 Base 图像...", width=18,
                           command=self._on_pick_base).pack(side="left")
        self.ent_base = theme.field(row1)
        self.ent_base.pack(side="left", fill="x", expand=True, padx=(6, 16), ipady=2)
        self.ent_base.config(state="readonly")

        theme.win95_button(row1, "选择 Compare 图像...", width=20,
                           command=self._on_pick_compare).pack(side="left")
        self.ent_compare = theme.field(row1)
        self.ent_compare.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=2)
        self.ent_compare.config(state="readonly")

        # 第二行：参数
        row2 = tk.Frame(tb, bg=theme.CHROME)
        row2.pack(fill="x", padx=8, pady=(4, 8))

        tk.Label(row2, text="阈值", bg=theme.CHROME).pack(side="left")
        self.var_threshold = tk.DoubleVar(value=0.1)
        self.spn_threshold = ttk.Spinbox(
            row2, style="Win.TSpinbox", from_=0.0, to=1.0, increment=0.01,
            textvariable=self.var_threshold, width=6)
        self.spn_threshold.pack(side="left", padx=(4, 20))

        self.var_aa = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="忽略抗锯齿 (--aa)", variable=self.var_aa,
                       bg=theme.CHROME, activebackground=theme.CHROME,
                       font=theme.FONT).pack(side="left", padx=(0, 20))

        self.var_mask = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="Diff Mask 透明底", variable=self.var_mask,
                       bg=theme.CHROME, activebackground=theme.CHROME,
                       font=theme.FONT).pack(side="left", padx=(0, 20))

        self.var_overlay = tk.BooleanVar(value=True)
        tk.Checkbutton(row2, text="白底叠加", variable=self.var_overlay,
                       bg=theme.CHROME, activebackground=theme.CHROME,
                       font=theme.FONT).pack(side="left", padx=(0, 20))

        tk.Label(row2, text="差异色", bg=theme.CHROME).pack(side="left")
        self.var_diff_color = tk.StringVar(value="#ff0000")
        self.btn_color = tk.Button(
            row2, text="   ", bg=self.var_diff_color.get(), width=4,
            relief="sunken", bd=2, command=self._on_pick_color)
        self.btn_color.pack(side="left", padx=(4, 16))

        self.btn_run = theme.win95_button(row2, "▶ 开始 Diff", width=14,
                                          command=self._on_run_diff)
        self.btn_run.pack(side="right")

    # —— 三栏图像区 ——————————————————————————————————————————

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=theme.CHROME)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self._build_image_panel(body, "Base", col=0)
        self._build_image_panel(body, "Compare", col=1)
        self._build_image_panel(body, "Diff 结果", col=2)

    def _build_image_panel(self, parent: tk.Frame, title: str, col: int) -> None:
        panel = theme.group(parent, f" {title} ")
        panel.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 4, 0))
        parent.grid_columnconfigure(col, weight=1, uniform="col")
        parent.grid_rowconfigure(0, weight=1)

        # 尺寸 / 缩放条
        bar = tk.Frame(panel, bg=theme.CHROME)
        bar.pack(fill="x", padx=6, pady=(4, 2))
        lbl = tk.Label(bar, text=f"{title}: —", bg=theme.CHROME, font=theme.FONT)
        lbl.pack(side="left")

        # 图像容器
        canvas_frame = theme.sunken(panel)
        canvas_frame.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        canvas = tk.Canvas(canvas_frame, bg=theme.CHROME_DARKER,
                           highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        hsb = tk.Scrollbar(canvas_frame, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        setattr(self, f"_canvas_{col}", canvas)
        setattr(self, f"_label_{col}", lbl)

        # 鼠标滚轮缩放（中键） + 拖拽平移
        canvas.bind("<MouseWheel>", lambda _e, c=col: self._on_wheel(c, _e))
        canvas.bind("<Button-4>", lambda _e, c=col: self._on_wheel(c, _e))
        canvas.bind("<Button-5>", lambda _e, c=col: self._on_wheel(c, _e))
        canvas.bind("<ButtonPress-1>", lambda _e, c=col: self._start_pan(c, _e))
        canvas.bind("<B1-Motion>", lambda _e, c=col: self._do_pan(c, _e))

    # —— 图像渲染 ————————————————————————————————————————————

    def _show_image(self, col: int, image: Image.Image | None, scale: float) -> None:
        """把 PIL 图像放到 Canvas 上，scale=1.0 为 100%。"""
        canvas: tk.Canvas = getattr(self, f"_canvas_{col}")
        label: tk.Label = getattr(self, f"_label_{col}")

        canvas.delete("all")
        if image is None:
            label.config(text=f"{['Base', 'Compare', 'Diff 结果'][col]}: —")
            return

        w, h = image.size
        label.config(text=f"{['Base', 'Compare', 'Diff'][col]}: {w}×{h}  @ {scale*100:.0f}%")

        if scale != 1.0:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            disp = image.resize((nw, nh), Image.LANCZOS if scale < 1 else Image.NEAREST)
        else:
            disp = image

        tk_img = ImageTk.PhotoImage(disp)
        setattr(self, f"_tk_{col}", tk_img)  # 防 GC

        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.configure(scrollregion=(0, 0, disp.width, disp.height))

    def _on_wheel(self, col: int, event) -> None:
        """Ctrl+滚轮缩放，普通滚轮滚动。"""
        canvas: tk.Canvas = getattr(self, f"_canvas_{col}")
        state = getattr(event, "state", 0)
        if state & 0x0004:  # Ctrl
            delta = 1.1 if event.delta > 0 or event.num == 4 else 0.9
            self._zoom(col, delta)
        else:
            canvas.yview_scroll(int(-event.delta / 120), "units")

    def _zoom(self, col: int, factor: float) -> None:
        img_attr = f"_img_{col}"
        img: Image.Image | None = getattr(self, img_attr, None)
        if img is None:
            return
        scale_attr = f"_scale_{col}"
        cur: float = getattr(self, scale_attr)
        new_scale = max(0.05, min(10.0, cur * factor))
        setattr(self, scale_attr, new_scale)
        self._show_image(col, img, new_scale)

    def _start_pan(self, col: int, event) -> None:
        canvas: tk.Canvas = getattr(self, f"_canvas_{col}")
        canvas.scan_mark(event.x, event.y)

    def _do_pan(self, col: int, event) -> None:
        canvas: tk.Canvas = getattr(self, f"_canvas_{col}")
        canvas.scan_dragto(event.x, event.y, gain=1)

    # —— 图像选择 ————————————————————————————————————————————

    def _on_pick_base(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="选择 Base 图像", filetypes=SUPPORTED_FORMATS)
        if path:
            self._set_image(0, Path(path))
            self.ent_base.config(state="normal")
            self.ent_base.delete(0, tk.END)
            self.ent_base.insert(0, path)
            self.ent_base.config(state="readonly")

    def _on_pick_compare(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="选择 Compare 图像", filetypes=SUPPORTED_FORMATS)
        if path:
            self._set_image(1, Path(path))
            self.ent_compare.config(state="normal")
            self.ent_compare.delete(0, tk.END)
            self.ent_compare.insert(0, path)
            self.ent_compare.config(state="readonly")

    def _set_image(self, col: int, path: Path) -> None:
        try:
            img = Image.open(path).convert("RGB")  # odiff 统一 RGB 输入
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", f"无法打开 {path.name}:\n{exc}", parent=self.root)
            return
        setattr(self, f"_img_{col}", img)
        setattr(self, f"_scale_{col}", self._fit_scale(col, img))
        self._show_image(col, img, getattr(self, f"_scale_{col}"))
        if col == 0:
            self.base_path = path
        else:
            self.compare_path = path

    def _fit_scale(self, col: int, img: Image.Image) -> float:
        """让图像首次自适应面板大小（取 85%）。"""
        canvas: tk.Canvas = getattr(self, f"_canvas_{col}")
        self.root.update_idletasks()
        cw = max(100, canvas.winfo_width() - 20)
        ch = max(100, canvas.winfo_height() - 20)
        iw, ih = img.size
        return min(cw / iw, ch / ih) * 0.85

    # —— 参数交互 ————————————————————————————————————————————

    def _on_pick_color(self) -> None:
        color = colorchooser.askcolor(
            color=self.var_diff_color.get(), title="选择差异高亮色",
            parent=self.root)
        if color and color[1]:
            self.var_diff_color.set(color[1])
            self.btn_color.config(bg=color[1])

    # —— Diff 执行 ———————————————————————————————————————————

    def _on_run_diff(self) -> None:
        if self.base_path is None or self.compare_path is None:
            messagebox.showinfo("提示", "请先选择 Base 和 Compare 两张图像", parent=self.root)
            return
        if not self.base_path.exists():
            messagebox.showerror("错误", f"Base 图像不存在: {self.base_path}", parent=self.root)
            return
        if not self.compare_path.exists():
            messagebox.showerror("错误", f"Compare 图像不存在: {self.compare_path}", parent=self.root)
            return

        # 临时输出文件
        tmp_fd, tmp_path = tempfile.mkstemp(suffix="_odiff.png", prefix="diff_")
        os.close(tmp_fd)

        options = DiffOptions(
            threshold=float(self.var_threshold.get()),
            diff_color=self.var_diff_color.get(),
            antialiasing=bool(self.var_aa.get()),
            diff_mask=bool(self.var_mask.get()),
            diff_overlay=0.5 if self.var_overlay.get() and not self.var_mask.get() else None,
        )

        self.btn_run.config(state="disabled", text="⏳ Diff 中...")
        self.status_tag.config(text="● 运行中")
        self.sb_mid.config(text="计算中...")
        self.root.update_idletasks()

        try:
            result = run_diff(
                self.base_path, self.compare_path, tmp_path, options=options)
        except Exception as exc:  # noqa: BLE001
            self.btn_run.config(state="normal", text="▶ 开始 Diff")
            self.status_tag.config(text="出错")
            self.sb_mid.config(text="失败")
            messagebox.showerror("Diff 失败", str(exc), parent=self.root)
            return

        self.btn_run.config(state="normal", text="▶ 开始 Diff")
        self.status_tag.config(text="完成")

        self._apply_result(result, Path(tmp_path))

    def _apply_result(self, result: DiffResult, diff_path: Path) -> None:
        self.sb_left.config(
            text=f"Base: {self.base_path.name}  |  Compare: {self.compare_path.name}")

        if result.match:
            self.sb_mid.config(text="✓ 完全一致")
            self._clear_image(2)
            return

        if result.reason == "layout-diff":
            self.sb_mid.config(text="✗ 尺寸不同")
            self._clear_image(2)
            messagebox.showwarning(
                "Layout Diff",
                f"两张图片尺寸不同，无法精确对比（{self.base_path.name} vs {self.compare_path.name}）",
                parent=self.root)
            return

        if result.reason == "file-error":
            self.sb_mid.config(text="✗ Diff 失败")
            self._clear_image(2)
            messagebox.showerror("Diff 失败", result.stderr or "未知错误", parent=self.root)
            return

        # pixel-diff —— 加载 diff 图
        self.diff_result_path = diff_path
        try:
            img = Image.open(diff_path)
            self._img_2 = img
            self._scale_2 = self._fit_scale(2, img)
            self._show_image(2, img, self._scale_2)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("读取 Diff 图失败", str(exc), parent=self.root)
            return

        self.sb_mid.config(text=result.summary)

    def _clear_image(self, col: int) -> None:
        setattr(self, f"_img_{col}", None)
        setattr(self, f"_tk_{col}", None)
        setattr(self, f"_scale_{col}", 1.0)
        self._show_image(col, None, 1.0)

    # —— Diff 保存 ———————————————————————————————————————————

    def _on_save_diff(self) -> None:
        if self.diff_result_path is None or not self.diff_result_path.exists():
            messagebox.showinfo("提示", "还没有 Diff 结果可保存", parent=self.root)
            return
        save_path = filedialog.asksaveasfilename(
            parent=self.root, title="保存 Diff 结果",
            defaultextension=".png",
            filetypes=[("PNG", "*.png")])
        if save_path:
            try:
                import shutil
                shutil.copy2(self.diff_result_path, save_path)
                self.sb_mid.config(text=f"已保存: {save_path}")
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("保存失败", str(exc), parent=self.root)

    # —— 帮助 ————————————————————————————————————————————————

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于 OdiffDiff",
            f"OdiffDiff v{__version__}\n\n"
            "Python Tk GUI + odiff.exe (Zig SIMD)\n"
            "图像像素级差分对比器\n\n"
            "快捷键:\n"
            "  Ctrl+1  选 Base 图像\n"
            "  Ctrl+2  选 Compare 图像\n"
            "  Ctrl+滚轮  缩放当前面板\n"
            "  左键拖拽  平移图像",
            parent=self.root)

    # —— 运行 ————————————————————————————————————————————————

    def _on_close(self) -> None:
        self.root.destroy()

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()
