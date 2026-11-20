from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, theme
from .rotate import collect_images, rotate_image

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except Exception:
    DND_OK = False


class ImageRotateApp(TkinterDnD.Tk if DND_OK else tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[Path] = []
        self.running = False
        self.events: queue.Queue[tuple] = queue.Queue()
        self.title(f"ImageRotatePy — 图片旋转 v{__version__}")
        self.geometry("760x560")
        self.minsize(620, 440)
        self._set_icon()
        theme.apply(self)
        _, self.status_tag = theme.titlebar(self, "  ImageRotatePy — 图片旋转")
        self._build_controls()
        self._build_list()
        self._build_status()
        if DND_OK:
            for widget in (self, self.listbox, self.drop_hint):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)

    def _set_icon(self) -> None:
        base = Path(sys._MEIPASS) if getattr(sys, "_MEIPASS", None) else Path(__file__).resolve().parents[2]
        icon = base / "assets" / "rotate.ico"
        if icon.is_file():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _build_controls(self) -> None:
        panel = theme.group(self, " 图片与旋转角度 ")
        panel.pack(fill="x", padx=10, pady=10)
        row = tk.Frame(panel, bg=theme.CHROME)
        row.pack(fill="x", padx=8, pady=8)
        theme.button(row, "添加图片", self._pick_images).pack(side="left")
        theme.button(row, "添加文件夹", self._pick_folder).pack(side="left", padx=6)
        theme.button(row, "清空列表", self._clear).pack(side="left")
        self.count_label = tk.Label(row, text="0 张图片", bg=theme.CHROME, font=theme.FONT)
        self.count_label.pack(side="right")

        actions = tk.Frame(panel, bg=theme.CHROME)
        actions.pack(fill="x", padx=8, pady=(0, 8))
        self.buttons: list[tk.Button] = []
        for text, direction in (("逆时针 90°", "ccw"), ("顺时针 90°", "cw"), ("旋转 180°", "180")):
            button = theme.button(actions, text, lambda d=direction: self._start(d), width=16)
            button.pack(side="left", expand=True, fill="x", padx=3)
            self.buttons.append(button)

    def _build_list(self) -> None:
        panel = theme.group(self, " 待处理图片（可拖入图片或文件夹，文件夹包含子目录） ")
        panel.pack(fill="both", expand=True, padx=10)
        self.drop_hint = tk.Label(
            panel, text="将图片或文件夹拖到这里\n\n处理会直接覆盖原图，首次处理前备份为 文件名.扩展名.bak",
            bg=theme.WHITE, fg=theme.DARK, font=theme.FONT,
        )
        self.drop_hint.pack(fill="x", padx=8, pady=(8, 0))
        frame = tk.Frame(panel, bd=2, relief="sunken")
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.listbox = tk.Listbox(frame, font=theme.FONT, selectmode=tk.EXTENDED)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_status(self) -> None:
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(
            bar, text="等待添加图片", anchor="w", bg=theme.CHROME,
            font=theme.FONT, padx=6,
        )
        self.status.pack(fill="x", ipady=3, padx=10, pady=(6, 8))

    def _pick_images(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self, title="选择图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"), ("所有文件", "*.*")],
        )
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _pick_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择图片文件夹")
        if path:
            self._add_paths([Path(path)])

    def _on_drop(self, event) -> None:
        if not self.running:
            self._add_paths([Path(p) for p in self.tk.splitlist(event.data)])

    def _add_paths(self, paths: list[Path]) -> None:
        images = collect_images(paths)
        known = set(self.images)
        added = [path for path in images if path not in known]
        self.images.extend(added)
        for path in added:
            self.listbox.insert(tk.END, str(path))
        self.count_label.config(text=f"{len(self.images)} 张图片")
        self.status.config(text=f"新增 {len(added)} 张，共 {len(self.images)} 张")
        if images and not added:
            self.status.config(text="这些图片已在列表中")
        elif not images:
            messagebox.showinfo("没有找到图片", "请选择支持的图片或包含图片的文件夹。", parent=self)

    def _clear(self) -> None:
        if not self.running:
            self.images.clear()
            self.listbox.delete(0, tk.END)
            self.count_label.config(text="0 张图片")
            self.status.config(text="等待添加图片")

    def _start(self, direction: str) -> None:
        if self.running:
            return
        if not self.images:
            messagebox.showinfo("提示", "请先拖入或选择图片。", parent=self)
            return
        names = {"cw": "顺时针 90°", "ccw": "逆时针 90°", "180": "180°"}
        if not messagebox.askyesno(
            "确认旋转", f"将对 {len(self.images)} 张图片执行{names[direction]}旋转并覆盖原图。\n是否继续？", parent=self
        ):
            return
        self.running = True
        for button in self.buttons:
            button.config(state="disabled")
        self.status_tag.config(text="处理中")
        threading.Thread(target=self._worker, args=(list(self.images), direction), daemon=True).start()
        self.after(80, self._poll)

    def _worker(self, images: list[Path], direction: str) -> None:
        succeeded = 0
        failed = 0
        for index, path in enumerate(images, 1):
            try:
                rotate_image(path, direction)
                succeeded += 1
                self.events.put(("progress", index, len(images), path, None))
            except Exception as exc:
                failed += 1
                self.events.put(("progress", index, len(images), path, str(exc)))
        self.events.put(("done", succeeded, failed))

    def _poll(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "progress":
                _, index, total, path, error = event
                self.status.config(text=f"{index}/{total}  {path.name}" + (f" — 失败: {error}" if error else " — 完成"))
            else:
                _, succeeded, failed = event
                self.running = False
                for button in self.buttons:
                    button.config(state="normal")
                self.status_tag.config(text="完成" if not failed else "部分失败")
                self.status.config(text=f"处理完成：成功 {succeeded} 张，失败 {failed} 张")
                messagebox.showinfo("处理结果", f"成功：{succeeded} 张\n失败：{failed} 张", parent=self)
        if self.running:
            self.after(80, self._poll)

    def run(self) -> None:
        self.mainloop()
