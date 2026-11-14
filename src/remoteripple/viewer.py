from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from PIL import Image, ImageTk

from .models import Host
from .vnc import VncClient


class ViewerTab(ttk.Frame):
    def __init__(self, master: tk.Misc, host: Host, on_event: Any) -> None:
        super().__init__(master)
        self.host = host
        self.on_event = on_event
        self.client = VncClient(
            host.address, host.port, host.password, self._event, host.settings.encoding
        )
        self.client.ignore_input = host.settings.ignore_input
        self.client.clipboard_enabled = host.settings.clipboard
        self.auto_scale = tk.BooleanVar(value=host.settings.auto_scale)
        self.ignore_input = tk.BooleanVar(value=host.settings.ignore_input)
        self.clipboard = tk.BooleanVar(value=host.settings.clipboard)
        self.scale = tk.IntVar(value=host.settings.scale)
        self.state = tk.StringVar(value="正在连接")
        self.source_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.display_scale = 1.0
        self.pointer_mask = 0
        self.fullscreen_window: tk.Toplevel | None = None
        self._build()
        self.client.start()

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=(5, 4))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="断开", command=self.close).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Ctrl+Alt+Del", command=self.client.send_cad).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="截图", command=self.save_screenshot).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="全屏", command=self.toggle_fullscreen).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(toolbar, text="自动缩放", variable=self.auto_scale, command=self._settings_changed).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(toolbar, text="只读", variable=self.ignore_input, command=self._settings_changed).pack(side=tk.LEFT)
        ttk.Checkbutton(toolbar, text="剪贴板", variable=self.clipboard, command=self._settings_changed).pack(side=tk.LEFT, padx=5)
        ttk.Label(toolbar, text="缩放").pack(side=tk.LEFT)
        scale = ttk.Scale(toolbar, from_=10, to=200, variable=self.scale, command=lambda _v: self._scale_changed())
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(toolbar, text="100%", command=lambda: (self.scale.set(100), self.auto_scale.set(False), self._settings_changed())).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="发送剪贴板", command=self.send_clipboard).pack(side=tk.LEFT, padx=3)

        self.canvas = tk.Canvas(self, background="#111111", highlightthickness=0, takefocus=1)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor=tk.NW)
        self.status = ttk.Label(self, textvariable=self.state, anchor=tk.W, padding=5)
        self.status.pack(fill=tk.X)
        self.canvas.bind("<Configure>", lambda _e: self._render())
        self.canvas.bind("<Motion>", self._pointer)
        self.canvas.bind("<ButtonPress>", self._button)
        self.canvas.bind("<ButtonRelease>", self._button)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", self._wheel)
        self.canvas.bind("<Button-5>", self._wheel)
        self.canvas.bind("<KeyPress>", lambda e: self._key(e, True))
        self.canvas.bind("<KeyRelease>", lambda e: self._key(e, False))
        self.canvas.bind("<FocusIn>", lambda _e: self.state.set("已连接 · 键盘输入已激活"))
        self.canvas.focus_set()

    def _event(self, kind: str, payload: Any) -> None:
        self.on_event(self, kind, payload)

    def handle_event(self, kind: str, payload: Any) -> None:
        if kind == "frame":
            self.source_image = payload
            self._render()
        elif kind == "connected":
            self.state.set(f"已连接 · {payload['name']} · {payload['width']}×{payload['height']}")
            self._render()
        elif kind == "state":
            self.state.set(payload)
        elif kind == "error":
            self.state.set(f"错误：{payload}")
        elif kind == "disconnected":
            self.state.set("已断开")
        elif kind == "clipboard":
            self.clipboard_clear()
            self.clipboard_append(payload)
        elif kind == "bell":
            self.bell()
        elif kind == "resized":
            self.state.set(f"远程桌面尺寸：{payload[0]}×{payload[1]}")

    def _render(self) -> None:
        if not self.source_image:
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if self.auto_scale.get():
            self.display_scale = min(width / self.source_image.width, height / self.source_image.height)
        else:
            self.display_scale = max(0.01, self.scale.get() / 100)
        target = (
            max(1, round(self.source_image.width * self.display_scale)),
            max(1, round(self.source_image.height * self.display_scale)),
        )
        shown = self.source_image.resize(target, Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(shown)
        self.canvas.itemconfigure(self.image_item, image=self.photo)
        x = max(0, (width - target[0]) // 2)
        y = max(0, (height - target[1]) // 2)
        self.canvas.coords(self.image_item, x, y)
        self.canvas.configure(scrollregion=(0, 0, max(width, target[0]), max(height, target[1])))

    def _remote_position(self, event: tk.Event) -> tuple[int, int]:
        if not self.source_image:
            return 0, 0
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        coords = self.canvas.coords(self.image_item)
        return int((x - coords[0]) / self.display_scale), int((y - coords[1]) / self.display_scale)

    def _pointer(self, event: tk.Event) -> None:
        x, y = self._remote_position(event)
        self.client.pointer(x, y, self.pointer_mask)

    def _button(self, event: tk.Event) -> None:
        bit = {1: 1, 2: 2, 3: 4}.get(event.num)
        if bit is None:
            return
        if event.type == tk.EventType.ButtonPress:
            self.pointer_mask |= bit
            self.canvas.focus_set()
        else:
            self.pointer_mask &= ~bit
        x, y = self._remote_position(event)
        self.client.pointer(x, y, self.pointer_mask)

    def _wheel(self, event: tk.Event) -> None:
        if getattr(event, "num", None) == 4 or event.delta > 0:
            bit = 8
        else:
            bit = 16
        x, y = self._remote_position(event)
        self.client.pointer(x, y, self.pointer_mask | bit)
        self.client.pointer(x, y, self.pointer_mask)

    def _key(self, event: tk.Event, down: bool) -> str:
        self.client.key_event(event.keysym, event.char, down)
        return "break"

    def _settings_changed(self) -> None:
        self.client.ignore_input = self.ignore_input.get()
        self.client.clipboard_enabled = self.clipboard.get()
        self.host.settings.ignore_input = self.ignore_input.get()
        self.host.settings.auto_scale = self.auto_scale.get()
        self.host.settings.clipboard = self.clipboard.get()
        self.host.settings.scale = self.scale.get()
        self._render()

    def _scale_changed(self) -> None:
        self.auto_scale.set(False)
        self._settings_changed()

    def send_clipboard(self) -> None:
        try:
            self.client.send_clipboard(self.clipboard_get())
        except tk.TclError:
            pass

    def save_screenshot(self) -> None:
        image = self.client.screenshot()
        if not image:
            return
        name = filedialog.asksaveasfilename(
            parent=self, title="保存远程桌面截图", defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")], initialfile=f"{self.host.address}.png",
        )
        if name:
            image.save(Path(name))
            self.state.set(f"截图已保存：{name}")

    def toggle_fullscreen(self) -> None:
        if self.fullscreen_window:
            self.fullscreen_window.destroy()
            self.fullscreen_window = None
            return
        window = tk.Toplevel(self)
        window.attributes("-fullscreen", True)
        window.configure(background="black")
        window.bind("<Escape>", lambda _e: self.toggle_fullscreen())
        self.fullscreen_window = window
        label = tk.Label(window, background="black")
        label.pack(fill=tk.BOTH, expand=True)

        def update() -> None:
            if not window.winfo_exists():
                return
            if self.source_image:
                ratio = min(window.winfo_width() / self.source_image.width, window.winfo_height() / self.source_image.height)
                size = (int(self.source_image.width * ratio), int(self.source_image.height * ratio))
                photo = ImageTk.PhotoImage(self.source_image.resize(size, Image.Resampling.BILINEAR))
                label.configure(image=photo)
                label.image = photo
            window.after(250, update)

        window.protocol("WM_DELETE_WINDOW", self.toggle_fullscreen)
        update()

    def close(self) -> None:
        if self.fullscreen_window:
            self.fullscreen_window.destroy()
            self.fullscreen_window = None
        self.client.close()
        self.on_event(self, "close", None)
