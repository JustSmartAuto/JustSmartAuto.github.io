"""Win32 拖放封装 — 基于 tkinterdnd2.

依赖: tkinterdnd2 / pathlib / os / logging / sys.

tkinterdnd2 在 Windows 上返回的路径可能形如 `C:/path with space/file.txt`
或使用 tkdnd 的大括号包裹 list 格式, 这里统一用 ``splitlist`` 解析.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

try:
    from tkinterdnd2 import DND_FILES
except ImportError:  # pragma: no cover — 降级
    DND_FILES = None  # type: ignore[assignment]


logger = logging.getLogger("localdropshell.ui.tk_drop")


def _split_tkdnd_paths(raw: str) -> list[str]:
    """把 tkdnd 返回的原始字符串解析成路径列表.

    tkdnd 在 Windows 上的返回值有两种常见形态:

    1. 简单空格分隔:  ``C:/a/b.txt C:/c/d.txt``
    2. 含空格的大括号包裹: ``{C:/path with space/a.txt} C:/other.txt``

    用简单的大括号扫描即可正确切分, 避免把路径里的空格当作分隔符.
    """

    if not raw:
        return []

    results: list[str] = []
    buf: list[str] = []
    in_brace = False

    for ch in raw:
        if ch == "{":
            in_brace = True
            continue  # 不把大括号写进路径
        if ch == "}":
            if in_brace:
                in_brace = False
                if buf:
                    results.append("".join(buf).strip())
                    buf.clear()
            continue
        if ch.isspace() and not in_brace:
            if buf:
                results.append("".join(buf).strip())
                buf.clear()
            continue
        buf.append(ch)

    if buf:
        results.append("".join(buf).strip())

    # 过滤空串 + 去重 (保持顺序)
    seen: set[str] = set()
    unique: list[str] = []
    for p in results:
        if not p:
            continue
        try:
            real = os.path.normpath(p)
        except Exception:
            real = p
        if real not in seen:
            seen.add(real)
            unique.append(real)

    return unique


def enable_drop_dnd(
    widget,
    callback: Callable[[list[str]], None],
) -> None:
    """为 widget 启用 Windows 文件拖放 (tkinterdnd2).

    Parameters
    ----------
    widget:
        任意 Tk widget, 通常是 Treeview / Frame / Canvas.
    callback:
        路径列表回调. 在 Tk 主线程触发 (Drop 事件本身就是主线程事件),
        可安全操作 widget.
    """

    if DND_FILES is None:  # pragma: no cover — 无 tkinterdnd2 时静默跳过
        logger.debug("tkinterdnd2 未安装, 跳过 enable_drop_dnd")
        return

    def _handler(event) -> None:
        try:
            # event.data 就是 tkdnd 的原始字符串
            raw = event.data or ""
            paths = _split_tkdnd_paths(raw)
            if not paths:
                logger.debug("Drop 事件解析为空: %r", raw)
                return
            logger.info("拖入 %d 个路径", len(paths))
            # 直接在主线程调用 callback
            try:
                callback(paths)
            except Exception as exc:
                logger.warning("Drop callback 异常: %s", exc)
        except Exception as exc:
            logger.warning("Drop 事件处理异常: %s", exc)

    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", _handler)
        logger.debug("DnD 已挂载到 %s", widget)
    except Exception as exc:
        # 某些 widget (如部分 Frame) 可能对 drop_target_register 行为不同
        logger.warning("drop_target_register 失败: %s", exc)


def enable_drag_out(widget, provider: Callable[[], list[str]]) -> None:
    """把 widget 注册为拖拽源，可把文件拖到资源管理器.

    provider 在拖拽开始时（主线程）同步调用，返回要拖出的本地路径列表；
    因此它应当尽量快速返回（例如已缓存的临时文件）。
    """

    if DND_FILES is None:  # pragma: no cover
        logger.debug("tkinterdnd2 未安装, 跳过 enable_drag_out")
        return

    try:
        widget.drag_source_register(1, DND_FILES)
    except Exception as exc:
        logger.warning("drag_source_register 失败: %s", exc)
        return

    def _on_drag_init(_event):
        try:
            paths = provider() or []
        except Exception as exc:
            logger.warning("拖出准备失败: %s", exc)
            paths = []
        if not paths:
            return ("", "", "")
        data = " ".join("{" + str(p).replace("}", "\\}") + "}" for p in paths)
        return ("copy", DND_FILES, data)

    try:
        widget.dnd_bind("<<DragInitCmd>>", _on_drag_init)
        logger.debug("拖出已挂载到 %s", widget)
    except Exception as exc:
        logger.warning("绑定 <<DragInitCmd>> 失败: %s", exc)
