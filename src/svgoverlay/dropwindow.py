"""「拖入文件夹」辅助窗口：tkinter + tkinterdnd2 接收系统拖放。

WebView2 无法可靠把资源管理器拖放交给宿主程序（OLE 目标被 Chromium
截获，WM_DROPFILES 回退也不生效），这里用一个小的原生 tkinter 窗口
（tkinterdnd2 提供 tkdnd 扩展）接收拖入的文件夹，
取第一个文件夹路径回调给主程序后自动关闭。
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def open_drop_window(on_folder: Callable[[str], None]) -> None:
    """在独立线程打开一个置顶的拖放小窗口（tkinterdnd2）。"""

    def run() -> None:
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
        except Exception:  # noqa: BLE001
            logger.exception("tkinterdnd2 不可用，无法打开拖放窗口")
            return

        try:
            root = TkinterDnD.Tk()
        except Exception:  # noqa: BLE001
            logger.exception("创建拖放窗口失败")
            return

        root.title("拖入文件夹")
        root.geometry("440x180+80+80")
        root.attributes("-topmost", True)

        status = tk.Label(
            root,
            text="把文件夹拖到本窗口",
            font=("微软雅黑", 12),
            justify="center",
        )
        status.pack(expand=True, fill="both", padx=16, pady=16)

        def on_drop(event) -> None:  # noqa: ANN001
            paths = root.tk.splitlist(event.data)
            logger.info("拖放窗口收到：%s", paths)
            for raw in paths:
                folder = Path(raw)
                if folder.is_dir():
                    status.configure(text=f"已选择：{folder}")
                    try:
                        on_folder(str(folder))
                    except Exception:  # noqa: BLE001
                        logger.exception("处理拖入文件夹失败")
                    root.after(300, root.destroy)
                    return
            status.configure(text="请拖入文件夹（不是单个文件）")

        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", on_drop)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()
