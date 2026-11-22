"""全景图融合桌面工具（Claude 风格）。

特性：
- tkinterdnd2 拖入多张图片（文件或文件夹）
- 列表内拖拽 / 上下按钮手动排序，或一键自动排序（OpenCV Stitcher）
- 导出 融合图_{timestamp}.jpeg，质量 95
- 运行：uv run python app.py；打包：uv run python build.py
"""
import os
import sys

# PyInstaller --windowed 下 sys.stdout / sys.stderr 为 None，部分库（如 cv2）在
# 导入期会写输出，若不重定向会在执行业务代码前崩溃。这里先兜底重定向到空设备。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")

import datetime
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

from tkinterdnd2 import DND_FILES, TkinterDnD

import stitch_engine
from theme import (BODY, CARD, CORAL, CORAL_DEEP, CREAM, CREAM_DEEP, FONT_CN,
                   FONT_SANS, H1, H2, INK, INK_SOFT, KICKER, LEDE, LINE,
                   PillButton, RoundedCard)


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = resource_path(os.path.join("assets", "fusion.ico"))
DEFAULT_OUTPUT = os.path.join(APP_DIR, "output")


class App:
    def __init__(self, root: TkinterDnD.Tk):
        self.root = root
        self.images = []                  # list[str] 绝对路径，顺序即手动顺序
        self.sort_mode = tk.StringVar(value="auto")
        self.output_dir = tk.StringVar(value=DEFAULT_OUTPUT)
        self._q = queue.Queue()
        self._running = False
        self._drag_idx = None
        self._closing = False
        self._after_id = None

        self._build_ui()
        self._register_drop()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_queue()

    # ---------------- UI ----------------
    def _build_ui(self):
        r = self.root
        r.title("全景图融合工具")
        try:
            r.iconbitmap(ICON)
        except Exception:
            pass
        r.configure(bg=CREAM)
        r.geometry("900x820")
        r.minsize(760, 680)

        wrap = tk.Frame(r, bg=CREAM)
        wrap.pack(fill="both", expand=True, padx=36, pady=32)

        # 顶部：kicker + 大标题（"融合" 用珊瑚色强调，对应 h1 em）+ 导语
        tk.Label(wrap, text="PANORAMA · 全景融合", font=KICKER, fg=CORAL,
                 bg=CREAM).pack(anchor="w")
        title_row = tk.Frame(wrap, bg=CREAM)
        title_row.pack(anchor="w", pady=(8, 10))
        for text, color in [("全景图", INK), ("融合", CORAL), ("工具", INK)]:
            tk.Label(title_row, text=text, font=H1, fg=color,
                     bg=CREAM).pack(side="left")
        tk.Label(wrap, text="拖入多张图片，选择排序方式，一键生成高质量全景融合图。",
                 font=LEDE, fg=INK_SOFT, bg=CREAM, justify="left").pack(anchor="w")

        self._build_image_card(wrap)
        self._build_action_card(wrap)

        tk.Label(wrap, text="OpenCV 全景拼接 · 手动 / 自动排序 · JPEG 质量 95",
                 font=(FONT_SANS, 9), fg=INK_SOFT, bg=CREAM).pack(anchor="w", pady=(20, 0))

    def _build_image_card(self, parent):
        card = RoundedCard(parent)
        card.pack(fill="x", pady=(24, 0))
        inner = card.inner

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="图片列表", font=H2, fg=INK_SOFT, bg=CARD).pack(side="left")
        self.count_lbl = tk.Label(head, text="0 张", font=(FONT_SANS, 10),
                                  fg=CORAL_DEEP, bg=CARD)
        self.count_lbl.pack(side="right")

        # 列表 + 滚动条
        lf = tk.Frame(inner, bg=CARD)
        lf.pack(fill="x", pady=(10, 12))
        self.lb = tk.Listbox(
            lf, height=8, bg=CARD, fg=INK, selectbackground=CORAL,
            selectforeground="#ffffff", font=(FONT_SANS, 11), borderwidth=0,
            highlightthickness=1, highlightbackground=LINE,
            highlightcolor=CORAL, activestyle="none", selectmode="extended")
        sb = tk.Scrollbar(lf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 第一行控件：增删
        row1 = tk.Frame(inner, bg=CARD)
        row1.pack(fill="x")
        PillButton(row1, "＋ 添加图片", self._on_add, primary=False, w=120, h=36).pack(side="left")
        PillButton(row1, "移除选中", self._on_remove, primary=False, w=104, h=36).pack(side="left", padx=8)
        PillButton(row1, "清空", self._on_clear, primary=False, w=72, h=36).pack(side="left")

        # 第二行：排序
        row2 = tk.Frame(inner, bg=CARD)
        row2.pack(fill="x", pady=(10, 0))
        tk.Label(row2, text="排序：", font=(FONT_SANS, 10, "bold"), fg=INK_SOFT,
                 bg=CARD).pack(side="left")
        self.btn_up = PillButton(row2, "↑ 上移", self._on_up, primary=False, w=92, h=36)
        self.btn_up.pack(side="left", padx=(8, 4))
        self.btn_down = PillButton(row2, "↓ 下移", self._on_down, primary=False, w=92, h=36)
        self.btn_down.pack(side="left", padx=(4, 16))
        self.btn_auto = PillButton(row2, "自动排序", self._set_auto, primary=True, w=108, h=36)
        self.btn_auto.pack(side="left")
        self.btn_manual = PillButton(row2, "手动排序", self._set_manual, primary=False, w=108, h=36)
        self.btn_manual.pack(side="left", padx=(8, 0))

        tk.Label(inner, text="提示：可将图片文件直接拖入窗口；在列表中拖拽或用上下按钮调整顺序。",
                 font=(FONT_SANS, 9), fg=INK_SOFT, bg=CARD).pack(anchor="w", pady=(12, 0))

        # 列表内部拖拽排序
        self.lb.bind("<ButtonPress-1>", self._lb_press)
        self.lb.bind("<B1-Motion>", self._lb_drag)
        self.lb.bind("<ButtonRelease-1>", self._lb_release)

    def _build_action_card(self, parent):
        card = RoundedCard(parent)
        card.pack(fill="x", pady=(16, 0))
        inner = card.inner

        tk.Label(inner, text="输出", font=H2, fg=INK_SOFT, bg=CARD).pack(anchor="w")

        out_row = tk.Frame(inner, bg=CARD)
        out_row.pack(fill="x", pady=(8, 12))
        tk.Label(out_row, text="保存到：", font=BODY, fg=INK, bg=CARD).pack(side="left")
        self.out_entry = tk.Entry(out_row, textvariable=self.output_dir, font=BODY,
                                  fg=INK, bg=CREAM_DEEP, readonlybackground=CREAM_DEEP,
                                  borderwidth=0, highlightthickness=0, state="readonly")
        self.out_entry.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=6)
        PillButton(out_row, "更改", self._on_change_dir, primary=False, w=72, h=34).pack(side="left")
        PillButton(out_row, "打开", self._on_open_dir, primary=False, w=72, h=34).pack(side="left", padx=(8, 0))

        tk.Label(inner, text="格式：JPEG · 质量 95 · 命名：融合图_{时间戳}.jpeg",
                 font=(FONT_SANS, 10), fg=INK_SOFT, bg=CARD).pack(anchor="w")

        act = tk.Frame(inner, bg=CARD)
        act.pack(fill="x", pady=(16, 0))
        self.btn_stitch = PillButton(act, "开始融合", self._on_stitch,
                                     primary=True, w=180, h=50)
        self.btn_stitch.pack(side="left")
        self.status_lbl = tk.Label(act, text="就绪", font=BODY, fg=INK_SOFT, bg=CARD,
                                   anchor="w")
        self.status_lbl.pack(side="left", padx=16, fill="x", expand=True)

    # ---------------- 拖放 ----------------
    def _register_drop(self):
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, e):
        self._add_paths(self._parse_drop(e.data))

    @staticmethod
    def _parse_drop(data):
        paths, buf, brace = [], "", 0
        for ch in data:
            if ch == "{":
                brace += 1
                continue
            if ch == "}":
                brace -= 1
                continue
            if ch == " " and brace == 0:
                if buf:
                    paths.append(buf)
                    buf = ""
                continue
            buf += ch
        if buf:
            paths.append(buf)
        return paths

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for name in sorted(os.listdir(p)):
                    full = os.path.join(p, name)
                    if os.path.isfile(full) and stitch_engine.is_image(full) \
                            and full not in self.images:
                        self.images.append(full)
                        added += 1
            elif os.path.isfile(p) and stitch_engine.is_image(p) and p not in self.images:
                self.images.append(p)
                added += 1
        if added:
            self._refresh_listbox()
            self._set_status(f"已添加 {added} 张图片")
        else:
            self._set_status("没有新增的图片文件")

    # ---------------- 列表操作 ----------------
    def _refresh_listbox(self, select=None):
        self.lb.delete(0, "end")
        for i, p in enumerate(self.images, 1):
            self.lb.insert("end", f"{i:>2}.  {os.path.basename(p)}")
        self.count_lbl.config(text=f"{len(self.images)} 张")
        if select is not None and 0 <= select < len(self.images):
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(select)
            self.lb.activate(select)

    def _selected(self):
        sel = list(self.lb.curselection())
        return sel

    def _on_add(self):
        files = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                       ("所有文件", "*.*")])
        if files:
            self._add_paths(list(files))

    def _on_remove(self):
        for i in reversed(self._selected()):
            if 0 <= i < len(self.images):
                del self.images[i]
        self._refresh_listbox()

    def _on_clear(self):
        self.images.clear()
        self._refresh_listbox()

    def _on_up(self):
        sel = self._selected()
        if not sel:
            return
        i = sel[0]
        if i > 0:
            self.images[i - 1], self.images[i] = self.images[i], self.images[i - 1]
            self._refresh_listbox(select=i - 1)

    def _on_down(self):
        sel = self._selected()
        if not sel:
            return
        i = sel[0]
        if i < len(self.images) - 1:
            self.images[i + 1], self.images[i] = self.images[i], self.images[i + 1]
            self._refresh_listbox(select=i + 1)

    def _lb_press(self, e):
        self._drag_idx = self.lb.nearest(e.y)

    def _lb_drag(self, e):
        idx = self.lb.nearest(e.y)
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(idx)
        self.lb.activate(idx)

    def _lb_release(self, e):
        if self._drag_idx is None:
            return
        end = self.lb.nearest(e.y)
        if end != self._drag_idx and 0 <= self._drag_idx < len(self.images) \
                and 0 <= end < len(self.images):
            item = self.images.pop(self._drag_idx)
            self.images.insert(end, item)
            self._refresh_listbox(select=end)
        self._drag_idx = None

    # ---------------- 排序模式 ----------------
    def _set_auto(self):
        self.sort_mode.set("auto")
        self.btn_auto.set_primary(True)
        self.btn_manual.set_primary(False)
        self._set_status("自动排序：OpenCV 将按内容匹配自动确定顺序")

    def _set_manual(self):
        self.sort_mode.set("manual")
        self.btn_manual.set_primary(True)
        self.btn_auto.set_primary(False)
        self._set_status("手动排序：按当前列表从左到右依次融合")

    # ---------------- 输出目录 ----------------
    def _on_change_dir(self):
        d = filedialog.askdirectory(title="选择输出目录", initialdir=self.output_dir.get())
        if d:
            self.output_dir.set(d)

    def _on_open_dir(self):
        d = self.output_dir.get()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as ex:
            messagebox.showerror("无法打开目录", str(ex))

    # ---------------- 融合 ----------------
    def _set_status(self, text):
        self.status_lbl.config(text=text)

    def _on_stitch(self):
        if self._running:
            return
        if len(self.images) < 2:
            messagebox.showwarning("图片不足", "至少需要 2 张图片才能融合。")
            return
        out_dir = self.output_dir.get()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as ex:
            messagebox.showerror("输出目录无效", str(ex))
            return

        self._running = True
        self.btn_stitch.set_enabled(False)
        self.btn_stitch._text = "融合中…"
        self.btn_stitch._draw()
        paths = list(self.images)
        mode = self.sort_mode.get()
        t = threading.Thread(target=self._worker, args=(paths, mode, out_dir),
                             daemon=True)
        t.start()

    def _worker(self, paths, mode, out_dir):
        try:
            self._q.put(("status", "正在加载图片…"))
            imgs = []
            for p in paths:
                im = stitch_engine.imread_unicode(p)
                if im is not None:
                    imgs.append(im)
            if len(imgs) < 2:
                self._q.put(("result", False, "有效图片不足 2 张"))
                return

            if mode == "auto":
                self._q.put(("status", "自动排序中：特征匹配与融合…"))
                status, pano = stitch_engine.stitch_auto(imgs)
                if status != 0:
                    self._q.put(("result", False, stitch_engine.status_msg(status)))
                    return
            else:
                self._q.put(("status", "按列表顺序配准与融合…"))
                pano = stitch_engine.stitch_manual(imgs)

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"融合图_{ts}.jpeg"
            out = os.path.join(out_dir, name)
            self._q.put(("status", f"正在保存 {name}（质量 95）…"))
            if not stitch_engine.imwrite_unicode(out, pano, quality=95):
                self._q.put(("result", False, "保存失败"))
                return
            self._q.put(("result", True, out))
        except Exception as ex:
            self._q.put(("result", False, f"{type(ex).__name__}: {ex}"))

    def _poll_queue(self):
        try:
            while True:
                ev = self._q.get_nowait()
                if ev[0] == "status":
                    self._set_status(ev[1])
                elif ev[0] == "result":
                    self._finish(ev[1], ev[2])
        except queue.Empty:
            pass
        if not self._closing:
            self._after_id = self.root.after(150, self._poll_queue)

    def _on_close(self):
        self._closing = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        self.root.destroy()

    def _finish(self, ok, info):
        self._running = False
        self.btn_stitch._text = "开始融合"
        self.btn_stitch.set_enabled(True)
        if ok:
            self._set_status("完成：" + os.path.basename(info))
            if messagebox.askyesno("融合完成", f"已保存：\n{info}\n\n是否打开所在文件夹？"):
                self._on_open_dir()
        else:
            self._set_status("失败：" + info)
            messagebox.showerror("融合失败", info)


def _diag(msg):
    """诊断日志：写到临时目录，便于无窗口模式下排查启动问题。"""
    try:
        path = os.path.join(os.environ.get("TEMP", "."), "panorama_gui.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


def main():
    try:
        root = TkinterDnD.Tk()
        App(root)
        root.mainloop()
    except BaseException:
        # 无窗口模式下异常不会出现在控制台，落盘到临时目录便于排查
        _diag("未捕获异常：\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
