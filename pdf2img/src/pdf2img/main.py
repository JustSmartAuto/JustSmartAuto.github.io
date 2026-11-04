import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import pymupdf as fitz
from tkinterdnd2 import DND_FILES, TkinterDnD

DPI = 600


def convert_pdf(pdf_path, log):
    pdf_path = os.path.abspath(pdf_path)
    out_dir = os.path.dirname(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    doc = fitz.open(pdf_path)
    zoom = DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)
    total = len(doc)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out = os.path.join(out_dir, f"{base}_page{i + 1}.jpg")
        pix.save(out, jpg_quality=95)
        log(f"[{i + 1}/{total}] {os.path.basename(out)}")
    doc.close()
    return total


class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF转图片工具")
        self.geometry("480x320")
        self.label = tk.Label(
            self,
            text="将 PDF 文件拖入窗口\n或在同目录生成 600ppi JPEG 图片",
            font=("微软雅黑", 12),
            justify="center",
        )
        self.label.pack(expand=True, fill="both", padx=20, pady=(20, 5))
        self.btn = tk.Button(self, text="选择 PDF 文件…", command=self.pick)
        self.btn.pack(pady=5)
        self.logbox = tk.Text(self, height=8, state="disabled")
        self.logbox.pack(fill="both", padx=20, pady=(5, 20))
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self.on_drop)

    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        self.update_idletasks()

    def run_convert(self, paths):
        self.btn.configure(state="disabled")
        for p in paths:
            self.log(f"开始转换：{os.path.basename(p)}")
            try:
                n = convert_pdf(p, self.log)
                self.log(f"完成，共 {n} 页")
            except Exception as e:
                self.log(f"失败：{e}")
        self.btn.configure(state="normal")
        messagebox.showinfo("PDF转图片工具", "转换完成")

    def on_drop(self, event):
        paths = self.tk.splitlist(event.data)
        paths = [p for p in paths if p.lower().endswith(".pdf")]
        if not paths:
            return
        threading.Thread(target=self.run_convert, args=(paths,), daemon=True).start()

    def pick(self):
        paths = filedialog.askopenfilenames(filetypes=[("PDF 文件", "*.pdf")])
        if paths:
            threading.Thread(target=self.run_convert, args=(list(paths),), daemon=True).start()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
