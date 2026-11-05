"""传输调度器。

根据目标端能力和网络质量选择 TightVNC RFB FTP / HTTP / HTTP+WireFrame
三种路径之一；批量文件夹传输带并发限幅和 per-file 容错。

对 tightvnc / server 两个尚未实现的依赖做**可选导入**：
- tightvnc.py（``TightVncFtpClient``）缺失时走 HTTP 路径
- server.py（``ServerConfig``）仅作类型注解，缺失不影响运行
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import aiohttp

from . import fountain
from . import vnc as vnc_mod


# ---------------------------------------------------------------------------
# 可选的 tightvnc 依赖（缺失时降级为 HTTP）
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 仅在 tightvnc.py 就位时命中
    from . import tightvnc as _tightvnc

    _TightVncFtpClient = getattr(_tightvnc, "TightVncFtpClient", None)
except Exception:  # ImportError / AttributeError 都降级
    _tightvnc = None  # type: ignore[assignment]
    _TightVncFtpClient = None


# server.py 仅用于类型注解（本模块运行不硬依赖）
try:  # pragma: no cover
    from . import server as _server  # noqa: F401
except Exception:
    _server = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

# 探测 HTTP 健康检查的超时与次数
_HEALTH_TIMEOUT = 1.0
_HEALTH_ATTEMPTS = 3
_SIMPLEX_CONSECUTIVE_TIMEOUTS = 2


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class TransferResult:
    """一次 transfer_files / transfer_folder 的结果。"""

    success: bool  # 是否**全部**成功（空输入时也 True）
    sent: list[str] = field(default_factory=list)  # 成功发送的绝对路径
    failed: list[tuple[str, str]] = field(default_factory=list)  # (路径, 原因)
    bytes_sent: int = 0
    mode: str = "http"  # "tightvnc" | "http" | "http-fountain"


ProgressCb = Callable[[int, int], None]  # (已发送字节, 总字节)


# ---------------------------------------------------------------------------
# 1. 单工判定
# ---------------------------------------------------------------------------

def is_simplex(
    remote_url: str | None = None,
    rtt_threshold: float = 2.0,
    loss_threshold: float = 0.5,
) -> bool:
    """判断目标端是否处于"单工"状态（连通但无法稳定双向应答）。

    判定规则：
    1. 没有 remote_url 且本地没有可复用的 VNC Server → 返回 True（纯单工场景）
    2. 有 remote_url：用 HTTP GET ``{remote_url.rstrip('/')}/health`` 做三次探测
       - 连续 2 次超时 → True
       - 三次平均 RTT > ``rtt_threshold`` → True
       - 否则 False

    本函数是**同步**的，内部调用 ``asyncio.run`` 跑异步探测，
    上层（如 transfer_files/transfer_folder）本来就是 async，它们可以直接用
    :func:`_probe_health_async` 以避免事件循环开销。
    """
    if remote_url is None:
        det = vnc_mod.detect_vnc()
        if det.mode == "none":
            return True
        return False

    try:
        return asyncio.run(
            _probe_health_async(
                remote_url=remote_url,
                rtt_threshold=rtt_threshold,
                loss_threshold=loss_threshold,
            )
        )
    except RuntimeError:
        # 已在事件循环内（如被 transfer_files 调用），由调用方走 async 版本
        logger.warning("is_simplex 在事件循环内被直接调用，请用 is_simplex_async")
        return False


async def _probe_health_async(
    remote_url: str,
    rtt_threshold: float,
    loss_threshold: float,
) -> bool:
    """异步健康探测（供 transfer_files 内部使用）。"""
    url = remote_url.rstrip("/") + "/health"
    timeouts = 0
    rtts: list[float] = []

    for _ in range(_HEALTH_ATTEMPTS):
        start = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_HEALTH_TIMEOUT)) as resp:
                    # 非 2xx 也视为连通（只是服务端可能有问题）
                    await resp.read()
            rtts.append(time.monotonic() - start)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            timeouts += 1
            rtts.append(float("inf"))

    # 规则：连续两次超时
    if timeouts >= _SIMPLEX_CONSECUTIVE_TIMEOUTS:
        return True

    # 平均 RTT 超过阈值
    valid_rtts = [r for r in rtts if r != float("inf")]
    if valid_rtts and (sum(valid_rtts) / len(valid_rtts)) > rtt_threshold:
        return True

    # 丢包率（这里用超时比例近似）
    timeout_rate = timeouts / _HEALTH_ATTEMPTS
    if timeout_rate >= loss_threshold:
        return True

    return False


# ---------------------------------------------------------------------------
# 2. TightVNC RFB FTP 路径
# ---------------------------------------------------------------------------

def _try_tightvnc_upload(
    local_path: str,
    remote_host: str,
    remote_port: int,
    remote_path: str,
    progress_cb: ProgressCb | None,
) -> bool:
    """走 TightVNC RFB FTP 通道上传。返回是否成功。

    依赖 ``tightvnc.TightVncFtpClient``——模块缺失或运行失败时返回 False，
    让上层回退到 HTTP。
    """
    if _TightVncFtpClient is None:
        return False

    try:
        client = _TightVncFtpClient()
    except Exception as exc:
        logger.warning("TightVncFtpClient 实例化失败: %s", exc)
        return False

    try:
        client.connect(remote_host, remote_port)
        client.upload_file(local_path, remote_path, progress_cb=progress_cb)
        return True
    except Exception as exc:
        logger.warning("TightVNC FTP 上传失败 %s -> %s: %s", local_path, remote_path, exc)
        return False
    finally:
        try:
            if hasattr(client, "close"):
                client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 3. HTTP 上传路径（普通 / fountain 两种子路径）
# ---------------------------------------------------------------------------

async def _http_upload(
    local_path: str,
    remote_host: str,
    remote_port: int,
    remote_path: str,
    use_fountain: bool,
    progress_cb: ProgressCb | None,
) -> None:
    """HTTP POST /upload 上传。

    ``use_fountain=True`` 时先把文件包成 DCF2 容器，再编码成 Wire 帧，
    作为 multipart 的单独字段 ``frames=base64...`` 发送（服务端需配合解析）。
    """
    url = f"http://{remote_host}:{remote_port}/upload"

    if not use_fountain:
        await _http_upload_plain(
            url=url,
            local_path=local_path,
            remote_path=remote_path,
            progress_cb=progress_cb,
        )
        return

    await _http_upload_fountain(
        url=url,
        local_path=local_path,
        remote_path=remote_path,
        progress_cb=progress_cb,
    )


async def _http_upload_plain(
    url: str,
    local_path: str,
    remote_path: str,
    progress_cb: ProgressCb | None,
) -> None:
    """multipart/form-data POST /upload。

    aiohttp 的 FormData 走 multipart 流上传时，要做逐块进度追踪需要自定义
    BodyPartReader——这里简化为直接发送 FormData，成功后补一次 final progress
    回调。逐块进度可在未来接入 BodyPart 自定义实现时补上。
    """
    file_size = os.path.getsize(local_path)

    async with aiohttp.ClientSession() as session:
        # 异步 FormData：aiohttp 支持传入 str 路径自动读文件
        data = aiohttp.FormData()
        data.add_field("path", remote_path)
        data.add_field(
            "file",
            open(local_path, "rb"),
            filename=os.path.basename(local_path),
            content_type="application/octet-stream",
        )

        async with session.post(url, data=data) as resp:
            body = await resp.read()
            if resp.status >= 400:
                raise RuntimeError(
                    f"HTTP 上传失败: HTTP {resp.status}, body={body[:200]!r}"
                )

    if progress_cb:
        try:
            progress_cb(file_size, file_size)
        except Exception:
            pass


async def _http_upload_fountain(
    url: str,
    local_path: str,
    remote_path: str,
    progress_cb: ProgressCb | None,
) -> None:
    """单工路径：文件 → DCF2 → Wire 帧 → base64 → POST /upload (fountain=1)。"""
    import base64

    container = fountain.build_from_file(local_path)
    frames = fountain.encode_session(container)

    # 打包 frames（每帧先 pack_frame 再 base64）
    encoded = base64.b64encode(
        b"".join(fountain.pack_frame(fr) for fr in frames)
    ).decode("ascii")

    body = {
        "path": remote_path,
        "fountain": "1",
        "frames": encoded,
        "name": container.name.decode("utf-8", errors="replace"),
        "size": container.file_length,
        "sha256": container.sha256.hex(),
        "compression": container.compression,
    }

    file_size = os.path.getsize(local_path)
    sent = 0

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body) as resp:
            resp_body = await resp.read()
            if resp.status >= 400:
                raise RuntimeError(
                    f"HTTP fountain 上传失败: HTTP {resp.status}, body={resp_body[:200]!r}"
                )

    # 补齐进度回调
    if progress_cb:
        try:
            progress_cb(file_size, file_size)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 4. 双轨传输主入口
# ---------------------------------------------------------------------------

async def transfer_files(
    files: list[dict],
    remote_host: str,
    remote_port: int = 10022,
    remote_path: str = "/upload",
    prefer_tightvnc: bool | None = None,
    simplex: bool | None = None,
    progress_cb: ProgressCb | None = None,
) -> TransferResult:
    """按给定文件列表上传到远程端。

    Args:
        files: 每个 dict 必须有 ``local``（str，绝对或相对路径）。可选 ``remote``
            覆盖默认 remote_path/<basename>。
        remote_host: 目标主机。
        remote_port: 目标 HTTP 端口（默认 10022）。
        remote_path: 默认远程目录（未指定 ``remote`` 时使用）。
        prefer_tightvnc: True=强制走 TightVNC；False=强制 HTTP；None=自动探测。
        simplex: True=强制单工 fountain；False=普通；None=自动探测。
        progress_cb: ``cb(已发字节, 总字节)``；多文件时回调会被每个文件分别调用。
    """
    result = TransferResult(success=True)

    # --- 通道探测 ---
    use_tightvnc = False
    if prefer_tightvnc is None:
        det = vnc_mod.detect_vnc()
        if det.mode == "reuse":
            use_tightvnc = True
    else:
        use_tightvnc = prefer_tightvnc

    # --- 单工探测 ---
    remote_url = f"http://{remote_host}:{remote_port}"
    use_simplex = False
    if simplex is None:
        try:
            use_simplex = await _probe_health_async(remote_url, rtt_threshold=2.0, loss_threshold=0.5)
        except Exception:
            use_simplex = not use_tightvnc  # 探测失败 → 假设恶劣
    else:
        use_simplex = simplex

    # tightvnc 路径不会触发 fountain（它是可靠 RFB 通道）
    if use_tightvnc:
        mode = "tightvnc"
    elif use_simplex:
        mode = "http-fountain"
    else:
        mode = "http"

    result.mode = mode

    # --- 逐个文件传输 ---
    for entry in files:
        local_path = entry.get("local")
        if not local_path or not os.path.isfile(local_path):
            result.success = False
            result.failed.append((local_path or "", "文件不存在"))
            continue

        remote_file = entry.get("remote") or f"{remote_path.rstrip('/')}/{os.path.basename(local_path)}"
        file_size = os.path.getsize(local_path)

        try:
            if use_tightvnc:
                ok = await asyncio.to_thread(
                    _try_tightvnc_upload,
                    local_path, remote_host, remote_port, remote_file,
                    progress_cb,
                )
                if not ok:
                    raise RuntimeError("TightVNC FTP 上传失败（模块缺失或运行时错误）")
            else:
                await _http_upload(
                    local_path=local_path,
                    remote_host=remote_host,
                    remote_port=remote_port,
                    remote_path=remote_file,
                    use_fountain=use_simplex,
                    progress_cb=progress_cb,
                )

            result.sent.append(os.path.abspath(local_path))
            result.bytes_sent += file_size
        except Exception as exc:
            logger.exception("传输失败 %s -> %s", local_path, remote_file)
            result.success = False
            result.failed.append((local_path, str(exc)))

    return result


# ---------------------------------------------------------------------------
# 5. 批量文件夹传输
# ---------------------------------------------------------------------------

async def transfer_folder(
    local_folder: str,
    remote_host: str,
    remote_path: str = "/upload",
    remote_port: int = 10022,
    prefer_tightvnc: bool | None = None,
    simplex: bool | None = None,
    progress_cb: ProgressCb | None = None,
    concurrency: int = 4,
) -> TransferResult:
    """递归上传整个目录，并发度 ``concurrency``。

    子目录结构在远程端**保持**：本地子路径 `a/b/c.txt` 上传到
    `remote_path/a/b/c.txt`。单文件失败不阻塞整体。
    """
    root = Path(local_folder)
    if not root.is_dir():
        return TransferResult(
            success=False,
            failed=[(str(root), "不是有效目录")],
            mode="http",
        )

    # 先做一次性的通道探测（所有文件共享同一路径）
    use_tightvnc = False
    if prefer_tightvnc is None:
        det = vnc_mod.detect_vnc()
        if det.mode == "reuse":
            use_tightvnc = True
    else:
        use_tightvnc = prefer_tightvnc

    remote_url = f"http://{remote_host}:{remote_port}"
    use_simplex = False
    if simplex is None:
        try:
            use_simplex = await _probe_health_async(remote_url, rtt_threshold=2.0, loss_threshold=0.5)
        except Exception:
            use_simplex = not use_tightvnc
    else:
        use_simplex = simplex

    if use_tightvnc:
        mode = "tightvnc"
    elif use_simplex:
        mode = "http-fountain"
    else:
        mode = "http"

    semaphore = asyncio.Semaphore(max(1, concurrency))
    result = TransferResult(success=True, mode=mode)

    # ---- 收集所有文件 ----
    all_files: list[tuple[Path, str, int]] = []  # (local_abs, remote_rel, size)
    for dirpath, _dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        rel_dir = cur.relative_to(root).as_posix()
        for name in filenames:
            local_abs = cur / name
            size = local_abs.stat().st_size
            if rel_dir == ".":
                remote_rel = name
            else:
                remote_rel = f"{rel_dir}/{name}"
            all_files.append((local_abs, remote_rel, size))

    # ---- 并发调度 ----
    total_bytes = sum(s for _, _, s in all_files)
    sent_bytes = [0]  # 用 list 便于闭包修改
    lock = asyncio.Lock()

    async def _upload_one(local_abs: Path, remote_rel: str) -> None:
        full_remote = f"{remote_path.rstrip('/')}/{remote_rel}"
        file_size = local_abs.stat().st_size

        def _progress(sent: int, total: int) -> None:
            if progress_cb:
                try:
                    progress_cb(sent_bytes[0] + sent, total_bytes)
                except Exception:
                    pass

        async with semaphore:
            try:
                if use_tightvnc:
                    ok = await asyncio.to_thread(
                        _try_tightvnc_upload,
                        str(local_abs), remote_host, remote_port,
                        full_remote, _progress,
                    )
                    if not ok:
                        raise RuntimeError("TightVNC FTP 上传失败")
                else:
                    await _http_upload(
                        local_path=str(local_abs),
                        remote_host=remote_host,
                        remote_port=remote_port,
                        remote_path=full_remote,
                        use_fountain=use_simplex,
                        progress_cb=_progress,
                    )

                async with lock:
                    result.sent.append(str(local_abs))
                    result.bytes_sent += file_size
                    sent_bytes[0] += file_size
            except Exception as exc:
                logger.exception("文件夹传输失败 %s -> %s", local_abs, full_remote)
                async with lock:
                    result.success = False
                    result.failed.append((str(local_abs), str(exc)))

    tasks = [_upload_one(la, rr) for la, rr, _ in all_files]
    await asyncio.gather(*tasks, return_exceptions=False)

    # 最终进度回调
    if progress_cb and total_bytes > 0:
        try:
            progress_cb(result.bytes_sent, total_bytes)
        except Exception:
            pass

    return result
