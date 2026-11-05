"""pywebview JS API：扫描文件夹、预览载荷、批量生成。

生成流程（每对数据）：
    Python 准备载荷（全分辨率底图 + 重写后的 SVG）
      → 在 WebView2 内 drawImage 到 3× 尺寸 canvas
      → canvas.toDataURL('image/jpeg', 0.95) 回传
      → 写入 {文件名}_叠加图.jpg
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

import webview

from svgoverlay import VERSION
from svgoverlay.overlay import build_export_payload, build_preview_payload
from svgoverlay.pairs import Pair, scan_folder

SUBDIR_NAME = "叠加图"

logger = logging.getLogger(__name__)


class Renderer:
    """驱动窗口内 canvas 合成并等待 JPEG 数据回传。"""

    TIMEOUT = 180

    def __init__(self, window: webview.Window) -> None:
        self._window = window
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._lock = threading.Lock()

    def deliver(self, token: str, data_url: str | None, error: str | None) -> None:
        with self._lock:
            entry = self._pending.get(token)
        if entry is None:
            return
        event, holder = entry
        holder["data"] = data_url
        holder["error"] = error
        event.set()

    def render(self, payload: dict) -> bytes:
        token = uuid.uuid4().hex
        event = threading.Event()
        holder: dict = {}
        with self._lock:
            self._pending[token] = (event, holder)
        try:
            script = (
                "window.SVGOV.renderAndEncode("
                f"{json.dumps(payload, ensure_ascii=False)}, {json.dumps(token)});"
            )
            self._window.evaluate_js(script)
            if not event.wait(self.TIMEOUT):
                raise TimeoutError("浏览器渲染超时")
            if holder.get("error"):
                raise RuntimeError(str(holder["error"]))
            data_url = holder.get("data") or ""
            if not data_url.startswith("data:image/jpeg;base64,"):
                raise RuntimeError("浏览器未返回 JPEG 数据")
            return base64.b64decode(data_url.split(",", 1)[1])
        finally:
            with self._lock:
                self._pending.pop(token, None)


class OverlayApi:
    """暴露给网页的接口（window.pywebview.api.*）。"""

    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._renderer: Renderer | None = None
        self._folder: Path | None = None
        self._pairs: list[Pair] = []
        self._stats: dict = {}
        self._initial_folder: str = ""
        self._to_subdir = False
        self._busy = False
        self._cancel = threading.Event()

    # —— 初始化 / 扫描 ————————————————————————————————

    def init(self) -> dict:
        self._renderer = Renderer(self._window)  # type: ignore[arg-type]
        return {
            "version": VERSION,
            # 拖放导航恢复主界面后会重新 init，返回当前文件夹让 UI 自动恢复列表
            "folder": str(self._folder) if self._folder else self._initial_folder,
            "ready": True,
        }

    def choose_folder(self) -> dict:
        if self._busy:
            return {"error": "正在生成中，请稍候"}
        selected = self._window.create_file_dialog(webview.FOLDER_DIALOG)  # type: ignore[union-attr]
        if not selected:
            return {"cancelled": True}
        return self.load_folder(selected[0])

    def scan_folder(self, folder: str) -> dict:
        if self._busy:
            return {"error": "正在生成中，请稍候"}
        return self.load_folder(folder)

    def load_folder(self, folder: str) -> dict:
        path = Path(folder).expanduser()
        if not path.is_dir():
            return {"error": f"不是有效的文件夹：{folder}"}
        path = path.resolve()
        pairs, stats = scan_folder(path)
        self._folder, self._pairs, self._stats = path, pairs, stats
        logger.info("扫描 %s：%s 对有效数据", path, stats["pairs"])
        return {
            "folder": str(path),
            "pairs": [self._row(p) for p in pairs],
            "stats": stats,
        }

    def _row(self, pair: Pair) -> dict:
        return {
            "stem": pair.stem,
            "imageName": pair.image.name,
            "svgName": pair.svg.name,
            "format": pair.fmt,
            "size": f"{pair.width}×{pair.height}",
            "outputName": pair.output.name,
            "outputExists": pair.output_exists,
        }

    # —— 预览 ————————————————————————————————————————

    def preview(self, stem: str) -> dict:
        pair = next((p for p in self._pairs if p.stem == stem), None)
        if pair is None:
            return {"error": f"未找到数据：{stem}"}
        try:
            return build_preview_payload(pair)
        except Exception as exc:  # noqa: BLE001
            logger.exception("预览失败：%s", pair.stem)
            return {"error": f"预览失败：{exc}"}

    # —— 批量生成 ————————————————————————————————————

    def generate(self, to_subdir: bool = False) -> dict:
        if self._busy:
            return {"error": "正在生成中"}
        if not self._pairs:
            return {"error": "当前文件夹没有有效数据（图片需有同名 SVG）"}
        self._busy = True
        self._to_subdir = bool(to_subdir)
        self._cancel.clear()
        pairs = list(self._pairs)
        threading.Thread(
            target=self._generate_worker, args=(pairs, self._to_subdir), daemon=True
        ).start()
        return {"started": True, "total": len(pairs), "subdir": self._to_subdir}

    def cancel(self) -> dict:
        if self._busy:
            self._cancel.set()
            self._push("onLog", {"text": "已请求取消，等待当前文件完成…"})
        return {"ok": True}

    def _generate_worker(self, pairs: list[Pair], to_subdir: bool) -> None:
        total = len(pairs)
        written = 0
        failed = 0
        cancelled = False
        started = time.monotonic()
        try:
            if to_subdir and self._folder is not None:
                subdir = self._folder / SUBDIR_NAME
                try:
                    subdir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    self._push("onLog", {"text": f"无法创建「{SUBDIR_NAME}」目录：{exc}"})
            for index, pair in enumerate(pairs, 1):
                if self._cancel.is_set():
                    cancelled = True
                    break
                self._push("onProgress", {
                    "index": index, "total": total, "stem": pair.stem,
                    "state": "生成中", "output": pair.output.name,
                })
                try:
                    if self._renderer is None:
                        raise RuntimeError("渲染器未初始化")
                    data = self._renderer.render(build_export_payload(pair))
                    output = self._output_path(pair, to_subdir)
                    temp = output.with_name(output.name + ".part")
                    temp.write_bytes(data)
                    os.replace(temp, output)
                    written += 1
                    self._push("onProgress", {
                        "index": index, "total": total, "stem": pair.stem,
                        "state": "已生成", "output": output.name,
                        "bytes": len(data), "written": True,
                    })
                    self._push("onLog", {
                        "text": f"{pair.stem} → {output.name}（{len(data) / 1024:.0f} KB）"
                    })
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.exception("生成失败：%s", pair.stem)
                    self._push("onProgress", {
                        "index": index, "total": total, "stem": pair.stem,
                        "state": "失败",
                    })
                    self._push("onLog", {"text": f"失败 {pair.stem}：{exc}"})
        finally:
            elapsed = time.monotonic() - started
            self._busy = False
            self._push("onDone", {
                "total": total,
                "written": written,
                "failed": failed,
                "cancelled": cancelled,
                "folder": str(self._folder) if self._folder else "",
                "elapsed": round(elapsed, 1),
            })

    def _output_path(self, pair: Pair, to_subdir: bool) -> Path:
        """输出路径：勾选时输出到所选文件夹下的「叠加图」子目录。"""
        if to_subdir and self._folder is not None:
            return self._folder / SUBDIR_NAME / pair.output.name
        return pair.output

    # —— 浏览器回调 ————————————————————————————————

    def render_result(self, token: str, data_url: str | None, error: str | None) -> dict:
        if self._renderer is not None:
            self._renderer.deliver(token, data_url, error)
        return {"ok": True}

    # —— 其他 ————————————————————————————————————————

    def open_output_folder(self) -> dict:
        if self._folder is None or not self._folder.is_dir():
            return {"error": "尚未选择文件夹"}
        subdir = self._folder / SUBDIR_NAME
        target = subdir if subdir.is_dir() else self._folder
        try:
            os.startfile(str(target))  # noqa: S606 - Windows 打开资源管理器
        except OSError as exc:
            return {"error": f"打开目录失败：{exc}"}
        return {"ok": True}

    def quit(self) -> dict:
        if self._window is not None:
            self._window.destroy()
        return {"ok": True}

    # —— 拖入文件夹（WebView2 导航 / tkinterdnd2 窗口回调） ——————

    def open_drop_window(self) -> dict:
        """打开原生拖放小窗口（tkinterdnd2），拖入文件夹后载入。"""
        from svgoverlay.dropwindow import open_drop_window

        open_drop_window(self.handle_drop)
        return {"ok": True}

    def handle_drop(self, paths: list[str]) -> None:
        if isinstance(paths, str):  # 回调可能传单个字符串，避免被逐字符遍历
            paths = [paths]
        target: Path | None = None
        for raw in paths:
            candidate = Path(raw)
            target = candidate if candidate.is_dir() else candidate.parent
            if target.is_dir():
                break
        if target is None:
            return
        self._push("onLog", {"text": f"拖入：{target}"})
        threading.Thread(target=self._drop_worker, args=(target,), daemon=True).start()

    def _drop_worker(self, folder: Path) -> None:
        result = self.load_folder(str(folder))
        self._push("onScanResult", result)

    def _push(self, function: str, payload: dict) -> None:
        if self._window is None:
            return
        try:
            script = (
                f"window.SVGOV && window.SVGOV.{function}("
                f"{json.dumps(payload, ensure_ascii=False)});"
            )
            self._window.evaluate_js(script)
        except Exception:  # noqa: BLE001 - 窗口关闭时忽略
            logger.debug("推送 %s 失败", function, exc_info=True)
