"""收藏夹标签页 — 远程主机收藏（备注名 / ip:port / 文件传输默认路径）.

持久化位置见 :mod:`localdropshell.settings`：
``D:/JustSmart/LocalFileDropShell/favorites.json``，
不可写时回退 ``%APPDATA%/LocalFileDropShell/favorites.json``。

每条收藏提供两个快捷按钮：
    * 📁 文件传输 — 切到文件传输页并填入地址 / 远程目录
    * 🖥 远程桌面 — 切到 VNC 页并填入地址
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from localdropshell.settings import (
    Favorite,
    favorites_path,
    format_target,
    load_favorites,
    parse_target,
    save_favorites,
)
from localdropshell.ui.tk_main import Win95_COLORS, Win95_FONT, Win95_FONT_BOLD


logger = logging.getLogger("localdropshell.ui.tk_favorites")


def _btn_style() -> dict:
    return {
        "font": Win95_FONT,
        "bg": Win95_COLORS["chrome"],
        "fg": Win95_COLORS["ink"],
        "activebackground": Win95_COLORS["chrome_light"],
        "activeforeground": Win95_COLORS["ink"],
        "bd": 2,
        "relief": tk.RAISED,
        "padx": 8,
        "pady": 2,
    }


def _build_path_editor(
    parent: tk.Misc,
    initial: list[str] | None = None,
    list_height: int = 3,
) -> tuple[tk.Frame, Callable[[], list[str]], Callable[[list[str]], None]]:
    """快捷路径编辑器：输入框 + 添加 / 删除 + 路径列表.

    Returns
    -------
    (frame, get_paths, set_paths)
    """
    frame = tk.Frame(parent, bg=Win95_COLORS["chrome"])
    items: list[str] = []

    entry_var = tk.StringVar()
    top = tk.Frame(frame, bg=Win95_COLORS["chrome"])
    top.pack(fill=tk.X)
    entry = tk.Entry(top, textvariable=entry_var, font=Win95_FONT,
                     bd=2, relief=tk.SUNKEN)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    body = tk.Frame(frame, bg=Win95_COLORS["chrome"])
    body.pack(fill=tk.X, pady=(2, 0))
    listbox = tk.Listbox(
        body,
        height=list_height,
        font=Win95_FONT,
        bd=2,
        relief=tk.SUNKEN,
        selectmode=tk.EXTENDED,
        activestyle="none",
        exportselection=False,
    )
    listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
    sb = tk.Scrollbar(body, orient=tk.VERTICAL, command=listbox.yview)
    listbox.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.LEFT, fill=tk.Y)

    def _add() -> None:
        value = entry_var.get().strip()
        if not value:
            return
        if value not in items:
            items.append(value)
            listbox.insert(tk.END, value)
        entry_var.set("")
        entry.focus_set()

    def _remove() -> None:
        for idx in reversed(listbox.curselection()):
            listbox.delete(idx)
            del items[idx]

    tk.Button(top, text="添加", command=_add, **_btn_style()).pack(
        side=tk.LEFT, padx=(2, 0)
    )
    tk.Button(body, text="删除选中", command=_remove, **_btn_style()).pack(
        side=tk.LEFT, padx=(2, 0)
    )

    def _set_paths(values: list[str]) -> None:
        items[:] = []
        listbox.delete(0, tk.END)
        for value in values:
            text = str(value or "").strip()
            if text and text not in items:
                items.append(text)
                listbox.insert(tk.END, text)

    entry.bind("<Return>", lambda _e: _add())
    _set_paths(initial or [])

    return frame, (lambda: list(items)), _set_paths


def build_favorites_tab(
    parent: tk.Frame,
    open_transfer,
    open_vnc,
) -> dict:
    """构造收藏夹标签页.

    Parameters
    ----------
    open_transfer / open_vnc
        收藏项快捷按钮回调，参数为 :class:`Favorite`；
        ``open_transfer`` 额外接受可选的快捷路径参数。
    """

    favorites: list[Favorite] = load_favorites()

    root = tk.Frame(parent, bg=Win95_COLORS["chrome"])
    root.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    # ========= 顶部: 新增收藏表单 =========
    form = tk.LabelFrame(
        root,
        text="添加收藏",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT_BOLD,
        padx=8,
        pady=6,
    )
    form.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))

    name_var = tk.StringVar()
    addr_var = tk.StringVar()
    path_var = tk.StringVar(value="/")

    tk.Label(form, text="备注名:", bg=Win95_COLORS["chrome"],
             font=Win95_FONT).grid(row=0, column=0, sticky="w", padx=(0, 2))
    tk.Entry(form, textvariable=name_var, width=16, font=Win95_FONT,
             bd=2, relief=tk.SUNKEN).grid(row=0, column=1, sticky="w", padx=(0, 10))

    tk.Label(form, text="远程地址:", bg=Win95_COLORS["chrome"],
             font=Win95_FONT).grid(row=0, column=2, sticky="w", padx=(0, 2))
    addr_entry = tk.Entry(form, textvariable=addr_var, width=20, font=Win95_FONT,
                          bd=2, relief=tk.SUNKEN)
    addr_entry.grid(row=0, column=3, sticky="w", padx=(0, 10))

    tk.Label(form, text="文件传输默认路径:", bg=Win95_COLORS["chrome"],
             font=Win95_FONT).grid(row=0, column=4, sticky="w", padx=(0, 2))
    tk.Entry(form, textvariable=path_var, width=18, font=Win95_FONT,
             bd=2, relief=tk.SUNKEN).grid(row=0, column=5, sticky="w", padx=(0, 10))

    tk.Label(form, text="快捷路径:", bg=Win95_COLORS["chrome"],
             font=Win95_FONT).grid(row=1, column=0, sticky="nw", padx=(0, 2), pady=(6, 0))
    path_editor, get_shortcut_paths, set_shortcut_paths = _build_path_editor(
        form, list_height=2
    )
    path_editor.grid(row=1, column=1, columnspan=6, sticky="we", pady=(6, 0))

    status_lbl = tk.Label(
        form,
        text="",
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=Win95_FONT,
        anchor="w",
    )
    status_lbl.grid(row=2, column=0, columnspan=7, sticky="we", pady=(6, 0))

    def _set_status(text: str, err: bool = False) -> None:
        status_lbl.configure(text=text, fg="#c00000" if err else "#000080")

    # ========= 中部: 收藏列表（可滚动） =========
    list_frame = tk.LabelFrame(
        root,
        text="收藏夹（每条记录可直接跳转）",
        bg=Win95_COLORS["chrome"],
        fg=Win95_COLORS["ink"],
        font=Win95_FONT_BOLD,
        padx=6,
        pady=6,
    )
    list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 6))

    canvas = tk.Canvas(list_frame, bg=Win95_COLORS["chrome"], highlightthickness=0)
    vsb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    inner = tk.Frame(canvas, bg=Win95_COLORS["chrome"])
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_config(_event=None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_config(event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    inner.bind("<Configure>", _on_inner_config)
    canvas.bind("<Configure>", _on_canvas_config)

    def _on_wheel(event) -> None:
        canvas.yview_scroll(int(-event.delta / 120), "units")

    canvas.bind("<MouseWheel>", _on_wheel)

    # ========= 底部: 配置目录提示 + 刷新 =========
    bottom = tk.Frame(root, bg=Win95_COLORS["chrome"])
    bottom.pack(side=tk.BOTTOM, fill=tk.X)

    try:
        hint = f"配置文件: {favorites_path()}"
    except Exception as exc:  # pragma: no cover — 仅防御
        hint = f"配置文件不可用: {exc}"

    tk.Label(
        bottom,
        text=hint,
        bg=Win95_COLORS["chrome"],
        fg="#000080",
        font=Win95_FONT,
        anchor="w",
    ).pack(side=tk.LEFT, padx=4)

    # ========= 动作 =========
    def _persist(items: list[Favorite]) -> bool:
        try:
            save_favorites(items)
        except OSError as exc:
            _set_status(f"⚠ 保存失败: {exc}", err=True)
            return False
        favorites[:] = items
        return True

    def _on_save() -> None:
        name = name_var.get().strip()
        try:
            host, port = parse_target(addr_var.get())
        except ValueError as exc:
            _set_status(f"⚠ {exc}", err=True)
            return
        path = path_var.get().strip() or "/"
        fav = Favorite(
            name=name or host,
            host=host,
            port=port,
            path=path,
            paths=get_shortcut_paths(),
        )

        items = [f for f in favorites if not (f.host == host and f.port == port)]
        items.insert(0, fav)
        if not _persist(items):
            return

        name_var.set("")
        addr_var.set("")
        path_var.set("/")
        set_shortcut_paths([])
        _set_status(f"✅ 已保存 {fav.name} ({format_target(host, port)})")
        _render()

    def _delete(fav: Favorite) -> None:
        if not messagebox.askyesno(
            "删除收藏",
            f"确定删除收藏 “{fav.name}” ({fav.address()}) ？",
            parent=root,
        ):
            return
        items = [f for f in favorites if not (f.host == fav.host and f.port == fav.port)]
        if _persist(items):
            _set_status(f"已删除 {fav.name}")
            _render()

    def _load_to_form(fav: Favorite) -> None:
        name_var.set(fav.name)
        addr_var.set(fav.address())
        path_var.set(fav.path)
        set_shortcut_paths(fav.shortcut_paths())
        _set_status(f"已载入 {fav.name}，确认后点“保存收藏”覆盖同地址记录")

    def _edit(fav: Favorite) -> None:
        """弹出编辑对话框，直接修改该条收藏（改名 / 改地址 / 默认路径 / 快捷路径）."""
        win = tk.Toplevel(root)
        win.title("编辑收藏")
        win.configure(bg=Win95_COLORS["chrome"])
        win.resizable(False, False)
        win.transient(root.winfo_toplevel())

        name_e = tk.StringVar(value=fav.name)
        addr_e = tk.StringVar(value=fav.address())
        path_e = tk.StringVar(value=fav.path)

        body = tk.Frame(win, bg=Win95_COLORS["chrome"], padx=10, pady=8)
        body.pack(fill=tk.BOTH, expand=True)

        entries: list[tk.Entry] = []
        for row, (label, var) in enumerate(
            (("备注名:", name_e), ("远程地址:", addr_e), ("默认路径:", path_e))
        ):
            tk.Label(body, text=label, bg=Win95_COLORS["chrome"],
                     font=Win95_FONT).grid(row=row, column=0, sticky="e", pady=3)
            entry = tk.Entry(body, textvariable=var, width=28, font=Win95_FONT,
                             bd=2, relief=tk.SUNKEN)
            entry.grid(row=row, column=1, sticky="w", pady=3, padx=(4, 0))
            entries.append(entry)

        tk.Label(body, text="快捷路径:", bg=Win95_COLORS["chrome"],
                 font=Win95_FONT).grid(row=3, column=0, sticky="ne",
                                       pady=3, padx=(0, 4))
        path_editor, get_edit_paths, _set_edit_paths = _build_path_editor(body)
        path_editor.grid(row=3, column=1, sticky="we", pady=3)
        _set_edit_paths(fav.shortcut_paths())

        err_lbl = tk.Label(body, text="", bg=Win95_COLORS["chrome"],
                           fg="#c00000", font=Win95_FONT, anchor="w")
        err_lbl.grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))

        def _commit() -> None:
            try:
                host, port = parse_target(addr_e.get())
            except ValueError as exc:
                err_lbl.configure(text=f"⚠ {exc}")
                return
            if (host, port) != (fav.host, fav.port) and any(
                f.host == host and f.port == port for f in favorites
            ):
                err_lbl.configure(text=f"⚠ 地址 {format_target(host, port)} 已存在")
                return

            updated = Favorite(
                name=name_e.get().strip() or host,
                host=host,
                port=port,
                path=path_e.get().strip() or "/",
                paths=get_edit_paths(),
            )
            items: list[Favorite] = []
            replaced = False
            for item in favorites:
                if not replaced and item.host == fav.host and item.port == fav.port:
                    items.append(updated)
                    replaced = True
                else:
                    items.append(item)
            if not replaced:
                items.append(updated)

            if _persist(items):
                _set_status(f"✅ 已更新 {updated.name} ({updated.address()})")
                win.destroy()
                _render()

        btns = tk.Frame(body, bg=Win95_COLORS["chrome"])
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(6, 0))
        tk.Button(btns, text="保存修改", command=_commit,
                  **_btn_style()).pack(side=tk.LEFT, padx=2)
        tk.Button(btns, text="取消", command=win.destroy,
                  **_btn_style()).pack(side=tk.LEFT, padx=2)

        for entry in entries:
            entry.bind("<Return>", lambda _e: _commit())
        win.bind("<Escape>", lambda _e: win.destroy())

        win.update_idletasks()
        top = root.winfo_toplevel()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(
            f"+{top.winfo_rootx() + max(0, (top.winfo_width() - w) // 2)}"
            f"+{top.winfo_rooty() + max(0, (top.winfo_height() - h) // 3)}"
        )
        win.grab_set()
        entries[0].focus_set()

    def _call(action, fav: Favorite, label: str, *extra) -> None:
        try:
            action(fav, *extra)
        except Exception as exc:  # pragma: no cover — 仅防御
            logger.warning("%s 快捷跳转失败: %s", label, exc)
            _set_status(f"⚠ {label} 跳转失败: {exc}", err=True)

    def _render() -> None:
        for child in inner.winfo_children():
            child.destroy()

        if not favorites:
            tk.Label(
                inner,
                text="暂无收藏 — 在上方填写备注名 / 远程地址 / 默认路径后点“保存收藏”",
                bg=Win95_COLORS["chrome"],
                fg="#000080",
                font=Win95_FONT,
                anchor="w",
            ).pack(fill=tk.X, padx=6, pady=8)
            _on_inner_config()
            return

        for fav in favorites:
            row = tk.Frame(inner, bg="#ffffff", bd=1, relief=tk.SUNKEN)
            row.pack(fill=tk.X, padx=2, pady=3)

            top = tk.Frame(row, bg="#ffffff")
            top.pack(fill=tk.X)

            text_box = tk.Frame(top, bg="#ffffff")
            text_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, pady=3)

            tk.Label(
                text_box,
                text=fav.name,
                bg="#ffffff",
                fg=Win95_COLORS["ink"],
                font=Win95_FONT_BOLD,
                anchor="w",
            ).pack(fill=tk.X)
            tk.Label(
                text_box,
                text=f"地址: {fav.address()}    默认路径: {fav.path}",
                bg="#ffffff",
                fg="#000080",
                font=Win95_FONT,
                anchor="w",
            ).pack(fill=tk.X)

            btns = tk.Frame(top, bg="#ffffff")
            btns.pack(side=tk.RIGHT, padx=6, pady=3)

            tk.Button(
                btns, text="📁 文件传输",
                command=lambda f=fav: _call(open_transfer, f, "文件传输"),
                **_btn_style(),
            ).pack(side=tk.LEFT, padx=2)
            tk.Button(
                btns, text="🖥 远程桌面",
                command=lambda f=fav: _call(open_vnc, f, "远程桌面"),
                **_btn_style(),
            ).pack(side=tk.LEFT, padx=2)
            tk.Button(
                btns, text="✎ 载入",
                command=lambda f=fav: _load_to_form(f),
                **_btn_style(),
            ).pack(side=tk.LEFT, padx=2)
            tk.Button(
                btns, text="📝 编辑",
                command=lambda f=fav: _edit(f),
                **_btn_style(),
            ).pack(side=tk.LEFT, padx=2)
            tk.Button(
                btns, text="🗑 删除",
                command=lambda f=fav: _delete(f),
                **_btn_style(),
            ).pack(side=tk.LEFT, padx=2)

            shortcuts = fav.shortcut_paths()
            if shortcuts:
                path_line = tk.Frame(row, bg="#ffffff")
                path_line.pack(fill=tk.X, padx=6, pady=(0, 3))
                tk.Label(
                    path_line,
                    text="快捷路径:",
                    bg="#ffffff",
                    fg="#000080",
                    font=Win95_FONT,
                ).pack(side=tk.LEFT)
                for path in shortcuts:
                    tk.Button(
                        path_line,
                        text=f"📂 {path}",
                        command=lambda f=fav, p=path: _call(
                            open_transfer, f, f"快捷路径 {p}", p
                        ),
                        **_btn_style(),
                    ).pack(side=tk.LEFT, padx=2)

        _on_inner_config()

    def _reload() -> None:
        new_items = load_favorites()
        favorites[:] = new_items
        _set_status(f"已重新载入 {len(new_items)} 条收藏")
        _render()

    tk.Button(form, text="保存收藏", command=_on_save, **_btn_style()).grid(
        row=0, column=6, sticky="w"
    )
    tk.Button(bottom, text="重新载入配置文件", command=_reload,
              **_btn_style()).pack(side=tk.RIGHT, padx=4, pady=2)

    addr_entry.bind("<Return>", lambda _e: _on_save())

    _render()

    return {
        "root": root,
        "refresh": _render,
        "favorites": favorites,
    }
