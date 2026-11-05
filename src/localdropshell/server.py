"""HTTP Server — aiohttp 路由工厂 & 所有业务 handler.

依赖:
    localdropshell.shell.ShellExecutor
    localdropshell.rsync (build_block_set, compute_delta, apply_delta,
                          build_blockset_from_file, compute_block_size,
                          should_fallback)
    localdropshell.rfbftp (FtpSession, FtpError, 各子类型常量)
    localdropshell.vnc.detect_vnc

Python 3.10+ 语法. 仅写这个文件.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp.web as web
import aiohttp.web_runner as web_runner

from .shell import ShellExecutor
from . import rsync as _rsync
from . import rfbftp as _rfbftp
from . import vnc as _vnc


# ---------------------------------------------------------------------------
# 模块级 logger
# ---------------------------------------------------------------------------

logger = logging.getLogger("localdropshell.server")


# ---------------------------------------------------------------------------
# ServerConfig 数据类
# ---------------------------------------------------------------------------

def _env_host() -> str:
    return os.environ.get("HTTP_SHELL_HOST", "0.0.0.0")


def _env_port() -> int:
    raw = os.environ.get("HTTP_SHELL_PORT", "10022")
    try:
        return int(raw)
    except ValueError:
        return 10022


def _env_timeout() -> int:
    raw = os.environ.get("HTTP_SHELL_TIMEOUT", "30")
    try:
        return int(raw)
    except ValueError:
        return 30


@dataclass
class ServerConfig:
    """HTTP Server 运行时配置."""

    host: str = field(default_factory=_env_host)
    port: int = field(default_factory=_env_port)
    timeout: int = field(default_factory=_env_timeout)


# ---------------------------------------------------------------------------
# 路径安全辅助
# ---------------------------------------------------------------------------

def _validate_path(raw_path: str) -> Path:
    """校验并解析文件路径, 禁止 ``..`` 跨目录逃逸.

    规则:
      * 必须是非空字符串
      * 不得为空白
      * 若原始路径中包含 ``..``, 则要求 ``resolve()`` 后的路径
        必须与原始字符串 ``resolve()`` 后的对象完全相等 (等价于
        不允许 ``..`` 真正起作用)
      * 返回 ``Path.resolve(strict=False)``

    失败时抛 ValueError, 由上层 handler 捕获转换为 400 JSON 错误.
    """
    if not raw_path or not isinstance(raw_path, str):
        raise ValueError("path 不能为空")
    if raw_path.isspace():
        raise ValueError("path 不能全为空白")

    p = Path(raw_path)
    resolved = p.resolve(strict=False)

    # 含 .. 时的防逃逸检查
    if ".." in raw_path.split("/") or ".." in raw_path.split("\\"):
        # 若原始路径中有 "..", 则 resolve 后不能"更短"
        # 更直观的判据: 先把 path 标准化, 若包含了任何 ".." 段, 认为不合法
        parts = [seg for seg in p.parts if seg == ".."]
        if parts:
            raise ValueError(f"禁止路径穿越: {raw_path!r}")

    return resolved


# ---------------------------------------------------------------------------
# 统一 JSON 响应
# ---------------------------------------------------------------------------

def json_ok(data: dict[str, Any]) -> web.Response:
    """构造 status=200 的 JSON 成功响应."""
    return web.json_response(data, status=200, dumps=json.dumps)


def json_error(message: str, status: int = 500) -> web.Response:
    """构造带 error 字段的 JSON 错误响应."""
    return web.json_response({"ok": False, "error": message}, status=status, dumps=json.dumps)


# ---------------------------------------------------------------------------
# RFB FTP 辅助: 懒连接 tightvnc
# ---------------------------------------------------------------------------

def _get_vnc_tcp_port(app: web.Application) -> int | None:
    result = app.get("vnc_result")
    if result is None:
        return None
    return getattr(result, "port", None) or None


def _open_rfb_ftp(app: web.Application) -> tuple[_rfbftp.FtpSession, socket.socket]:
    """连接 tightvnc (RFB FTP 扩展), 返回 (FtpSession, sock).

    调用方负责在使用完毕后关闭 sock. 若未检测到 tightvnc 或连接失败,
    抛 RuntimeError.
    """
    port = _get_vnc_tcp_port(app)
    if port is None:
        raise RuntimeError("未检测到可用的 VNC Server (tightvnc)")
    host = os.environ.get("HTTP_SHELL_VNC_HOST", "127.0.0.1")
    try:
        sock = socket.create_connection((host, port), timeout=10.0)
    except OSError as e:
        raise RuntimeError(f"连接 VNC Server 失败 {host}:{port} ({e})") from e
    # 跳过 RFB 握手 (TightVNC FTP 要求先走 VNC 握手再发扩展消息).
    # 为简单起见, 这里直接发 FTP 扩展请求; 实际 tightvnc 对 RFB 握手
    # 失败会关闭连接, 本函数调用者将在后续 FtpSession 请求时收到异常.
    ftp = _rfbftp.FtpSession(sock)
    return ftp, sock


# ---------------------------------------------------------------------------
# 业务 handler
# ---------------------------------------------------------------------------

async def health_handler(request: web.Request) -> web.Response:
    """GET /health — 返回运行时状态."""
    try:
        app = request.app
        shell: ShellExecutor | None = app.get("shell")
        vnc_result = app.get("vnc_result")
        payload: dict[str, Any] = {
            "status": "ok",
            "time": time.time(),
            "shell": shell.shell_path if shell else None,
            "vnc_detected": bool(vnc_result and getattr(vnc_result, "port", None)),
        }
        if vnc_result is not None:
            payload["vnc"] = {
                "port": getattr(vnc_result, "port", None),
                "mode": getattr(vnc_result, "mode", None),
                "process_name": getattr(vnc_result, "process_name", None),
            }
        return json_ok(payload)
    except Exception as exc:  # pragma: no cover - 兜底
        return json_error(str(exc), 500)


async def exec_handler(request: web.Request) -> web.Response:
    """POST /exec — 执行 shell 命令."""
    started = time.perf_counter()
    try:
        body = await request.json()
        command = body.get("command")
        if not command or not isinstance(command, str):
            return json_error("缺少 command 字段", 400)
        timeout = body.get("timeout")
        work_dir = body.get("work_dir")
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                return json_error("timeout 必须是数字", 400)
        if work_dir is not None:
            try:
                _validate_path(work_dir)
            except ValueError as e:
                return json_error(str(e), 400)

        shell: ShellExecutor = request.app["shell"]
        result = await shell.exec(command, timeout=timeout, work_dir=work_dir)
        status = 200 if result.get("status") == "success" else 200
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /exec cmd=%r exit=%s ms=%d", command[:80],
                    result.get("exit_code"), elapsed_ms)
        return web.json_response(result, status=status, dumps=json.dumps)
    except Exception as exc:
        logger.exception("POST /exec 异常")
        return json_error(str(exc), 500)


async def upload_handler(request: web.Request) -> web.Response:
    """POST /upload — multipart 上传文件到本地."""
    started = time.perf_counter()
    sock = None
    ftp: _rfbftp.FtpSession | None = None
    try:
        vnc_port = _get_vnc_tcp_port(request.app)
        if vnc_port is not None:
            try:
                ftp, sock = _open_rfb_ftp(request.app)
            except Exception:
                ftp, sock = None, None

        data = await request.multipart()
        file_field: Any = None  # 真正的文件体字段 (带 filename)
        meta: dict[str, Any] = {}
        while True:
            part = await data.next()
            if part is None:
                break
            filename = getattr(part, "filename", None)
            if filename:
                # 有 filename 的 part 视为文件体
                file_field = part
                meta["filename"] = filename
            else:
                # 其它 part 收集为元数据
                try:
                    meta[part.name] = await part.text()
                except Exception:
                    meta[part.name] = ""

        target_path = meta.get("path") or meta.get("remote")
        if not target_path:
            return json_error("缺少 path 字段", 400)

        try:
            local_path = _validate_path(target_path)
        except ValueError as e:
            return json_error(str(e), 400)

        # 父目录自动创建
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return json_error(f"无法创建父目录: {e}", 500)

        # 先落盘到临时文件 (RFB FTP 模式也先本地缓存, 简化流程)
        tmp_path = local_path.with_name(local_path.name + ".part")
        total_size = 0
        with open(tmp_path, "wb") as f:
            if file_field is not None:
                while True:
                    chunk = await file_field.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
                    total_size += len(chunk)

        # RFB FTP 回传远程 (tightvnc 存在时)
        if ftp is not None:
            try:
                offset = int(meta.get("offset") or 0)
                await _run_in_executor(
                    _rfb_upload_inplace,
                    ftp, str(tmp_path), target_path, offset, bool(meta.get("overwrite", True)),
                )
            except Exception as e:
                # RFB FTP 失败不影响本地已写入 — 把本地文件重命名为最终名
                logger.warning("RFB FTP 上传失败, 保留本地: %s", e)

        try:
            os.replace(tmp_path, local_path)
        except OSError as e:
            return json_error(f"重命名临时文件失败: {e}", 500)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /upload path=%s bytes=%d ms=%d", local_path, total_size, elapsed_ms)
        return json_ok({"path": str(local_path), "size": total_size})
    except Exception as exc:
        logger.exception("POST /upload 异常")
        return json_error(str(exc), 500)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


async def _run_in_executor(func, *args):
    """把阻塞函数丢到默认线程池."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


def _rfb_upload_inplace(
    ftp: _rfbftp.FtpSession,
    local_path: str,
    remote_path: str,
    offset: int,
    overwrite: bool,
) -> None:
    """调用 FtpSession 上传; 若 offset > 0, 则先 mkdir 再整体上传 (简化版)."""
    # 先尝试创建远程父目录, 无视错误
    parent = str(Path(remote_path).parent)
    if parent and parent not in (".", "/", ""):
        try:
            ftp.mkdir(parent)
        except _rfbftp.FtpError:
            pass
    ftp.upload_file(local_path, remote_path, overwrite=overwrite)


async def download_handler(request: web.Request) -> web.Response:
    """POST /download — 下载本地文件.

    RFB FTP 远程下载需等 rfbftp.FtpSession 新增 ``download_file`` API
    后再在此处开启 tightvnc 优先分支. 当前仅支持本地路径.
    """
    started = time.perf_counter()
    try:
        body = await request.json()
        path = body.get("path")
        if not path:
            return json_error("缺少 path 字段", 400)
        try:
            offset = int(body.get("offset") or 0)
        except (TypeError, ValueError):
            return json_error("offset 必须是整数", 400)

        try:
            local_path = _validate_path(path)
        except ValueError as e:
            return json_error(str(e), 400)
        if not local_path.is_file():
            return json_error(f"文件不存在: {local_path}", 404)
        file_size = local_path.stat().st_size
        if offset < 0 or offset > file_size:
            return json_error(f"offset 超出范围: {offset}", 400)

        headers = {
            "Content-Length": str(file_size - offset),
            "Content-Type": "application/octet-stream",
            "X-File-Path": str(local_path),
            "X-File-Size": str(file_size),
            "X-Offset": str(offset),
        }
        resp = web.StreamResponse(status=200, headers=headers)
        await resp.prepare(request)

        CHUNK = 256 * 1024
        remaining = file_size - offset
        with open(local_path, "rb") as f:
            if offset > 0:
                f.seek(offset)
            while remaining > 0:
                buf = f.read(min(CHUNK, remaining))
                if not buf:
                    break
                await resp.write(buf)
                remaining -= len(buf)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /download path=%s bytes=%d ms=%d", local_path,
                    max(0, file_size - offset), elapsed_ms)
        await resp.write_eof()
        return resp
    except Exception as exc:
        logger.exception("POST /download 异常")
        return json_error(str(exc), 500)


async def delete_handler(request: web.Request) -> web.Response:
    """POST /delete — 删除文件."""
    started = time.perf_counter()
    try:
        body = await request.json()
        path = body.get("path")
        if not path:
            return json_error("缺少 path 字段", 400)

        # 优先尝试 tightvnc
        vnc_port = _get_vnc_tcp_port(request.app)
        if vnc_port is not None:
            sock: socket.socket | None = None
            try:
                ftp, sock = _open_rfb_ftp(request.app)
                await _run_in_executor(ftp.delete, path)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info("POST /delete (rfb) path=%s ms=%d", path, elapsed_ms)
                return json_ok({"path": path, "deleted": True})
            except Exception as e:
                logger.info("RFB delete 回退到本地: %s", e)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

        try:
            local_path = _validate_path(path)
        except ValueError as e:
            return json_error(str(e), 400)
        if not local_path.exists():
            return json_error(f"文件或目录不存在: {local_path}", 404)
        if local_path.is_dir():
            import shutil
            await _run_in_executor(shutil.rmtree, local_path)
        else:
            local_path.unlink()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /delete path=%s ms=%d", local_path, elapsed_ms)
        return json_ok({"path": str(local_path), "deleted": True})
    except Exception as exc:
        logger.exception("POST /delete 异常")
        return json_error(str(exc), 500)


# ---------------------------------------------------------------------------
# Rsync handlers
# ---------------------------------------------------------------------------

async def rsync_blockset_handler(request: web.Request) -> web.Response:
    """POST /rsync/blockset — 对本地文件生成 blockset."""
    started = time.perf_counter()
    try:
        body = await request.json()
        path = body.get("path")
        if not path:
            return json_error("缺少 path 字段", 400)
        try:
            local_path = _validate_path(path)
        except ValueError as e:
            return json_error(str(e), 400)
        if not local_path.is_file():
            return json_error(f"文件不存在: {local_path}", 404)

        seed = int(body.get("seed") or 0)
        data = local_path.read_bytes()
        block_size = _rsync.compute_block_size(len(data))
        blockset = _rsync.build_block_set(data, seed, block_size)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /rsync/blockset path=%s blocks=%d ms=%d",
                    local_path, len(blockset), elapsed_ms)
        return json_ok({
            "path": str(local_path),
            "file_size": len(data),
            "block_size": block_size,
            "blockset": blockset,
        })
    except Exception as exc:
        logger.exception("POST /rsync/blockset 异常")
        return json_error(str(exc), 500)


async def rsync_delta_handler(request: web.Request) -> web.Response:
    """POST /rsync/delta — 基于 src 和 dst 的 blockset 计算增量."""
    started = time.perf_counter()
    try:
        body = await request.json()
        src_path = body.get("src_path")
        dst_path = body.get("dst_path")
        seed = int(body.get("seed") or 0)
        direction = body.get("direction", "client_to_server")
        if not src_path or not dst_path:
            return json_error("缺少 src_path / dst_path", 400)

        try:
            src_p = _validate_path(src_path)
            dst_p = _validate_path(dst_path)
        except ValueError as e:
            return json_error(str(e), 400)

        # 默认 client_to_server: server 端有 dst, 客户端给 src 做 delta
        # 这里我们做服务端视角: src 是本地"客户端参考", dst 是本地"当前"
        if not src_p.is_file():
            return json_error(f"src 文件不存在: {src_p}", 404)
        if not dst_p.is_file():
            return json_error(f"dst 文件不存在: {dst_p}", 404)

        dst_data = dst_p.read_bytes()
        dst_bs = _rsync.build_block_set(
            dst_data, seed, _rsync.compute_block_size(len(dst_data))
        )
        src_data = src_p.read_bytes()
        delta = _rsync.compute_delta(src_data, dst_bs, seed)
        eff = _rsync.delta_efficiency(delta, len(src_data))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /rsync/delta src=%s dst=%s eff=%.3f ms=%d",
                    src_p, dst_p, eff, elapsed_ms)
        return json_ok({
            "src_path": str(src_p),
            "dst_path": str(dst_p),
            "seed": seed,
            "direction": direction,
            "src_size": len(src_data),
            "dst_size": len(dst_data),
            "delta": delta,
            "efficiency": eff,
        })
    except Exception as exc:
        logger.exception("POST /rsync/delta 异常")
        return json_error(str(exc), 500)


async def rsync_apply_handler(request: web.Request) -> web.Response:
    """POST /rsync/apply — 对 dst 应用 delta 写回."""
    started = time.perf_counter()
    try:
        body = await request.json()
        dst_path = body.get("dst_path")
        chunks = body.get("chunks")
        seed = int(body.get("seed") or 0)
        blockset = body.get("blockset")  # 客户端可能一并传来, 或服务器自己算
        if not dst_path or not isinstance(chunks, list):
            return json_error("缺少 dst_path / chunks (必须是 list)", 400)

        try:
            dst_p = _validate_path(dst_path)
        except ValueError as e:
            return json_error(str(e), 400)

        if not dst_p.is_file():
            return json_error(f"dst 文件不存在: {dst_p}", 404)

        dst_data = dst_p.read_bytes()
        if not blockset:
            blockset = _rsync.build_block_set(
                dst_data, seed, _rsync.compute_block_size(len(dst_data))
            )

        rebuilt = _rsync.apply_delta(dst_data, chunks, blockset)
        tmp_p = dst_p.with_name(dst_p.name + ".rsync")
        tmp_p.write_bytes(rebuilt)
        os.replace(tmp_p, dst_p)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /rsync/apply dst=%s new_size=%d ms=%d",
                    dst_p, len(rebuilt), elapsed_ms)
        return json_ok({
            "path": str(dst_p),
            "new_size": len(rebuilt),
            "applied_chunks": len(chunks),
        })
    except Exception as exc:
        logger.exception("POST /rsync/apply 异常")
        return json_error(str(exc), 500)


# ---------------------------------------------------------------------------
# RFB FTP handlers
# ---------------------------------------------------------------------------

async def rfb_list_handler(request: web.Request) -> web.Response:
    """POST /rfb/file/list — 列出远程或本地目录."""
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        body = await request.json()
        path = body.get("path", "/")
        vnc_port = _get_vnc_tcp_port(request.app)
        if vnc_port is not None:
            try:
                ftp, sock = _open_rfb_ftp(request.app)
                entries = await _run_in_executor(ftp.list_dir, path)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info("POST /rfb/file/list (rfb) path=%s entries=%d ms=%d",
                            path, len(entries), elapsed_ms)
                return json_ok({"path": path, "entries": entries})
            except Exception as e:
                logger.info("RFB list 回退到本地: %s", e)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None

        # 本地回退
        try:
            local_path = _validate_path(path)
        except ValueError as e:
            return json_error(str(e), 400)
        if not local_path.is_dir():
            return json_error(f"目录不存在: {local_path}", 404)
        entries: list[dict[str, Any]] = []
        for p in local_path.iterdir():
            try:
                st = p.stat()
                entries.append({
                    "name": p.name,
                    "size": st.st_size,
                    "mod_time": st.st_mtime,
                    "is_dir": p.is_dir(),
                })
            except OSError:
                continue
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /rfb/file/list path=%s entries=%d ms=%d",
                    local_path, len(entries), elapsed_ms)
        return json_ok({"path": str(local_path), "entries": entries})
    except Exception as exc:
        logger.exception("POST /rfb/file/list 异常")
        return json_error(str(exc), 500)


async def rfb_upload_handler(request: web.Request) -> web.Response:
    """POST /rfb/file/upload — 上传文件 (tightvnc 优先, 本地回退)."""
    # 复用 upload_handler 逻辑 (tightvnc 自动探测 + 本地回退)
    return await upload_handler(request)


async def rfb_download_handler(request: web.Request) -> web.Response:
    """POST /rfb/file/download — 下载文件 (tightvnc 优先, 本地回退)."""
    return await download_handler(request)


async def rfb_md5_handler(request: web.Request) -> web.Response:
    """POST /rfb/file/md5 — 对文件的 [offset, offset+length) 段做 MD5."""
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        body = await request.json()
        path = body.get("path")
        offset = int(body.get("offset") or 0)
        length = int(body.get("length") or 65536)
        if not path:
            return json_error("缺少 path 字段", 400)

        vnc_port = _get_vnc_tcp_port(request.app)
        if vnc_port is not None:
            try:
                ftp, sock = _open_rfb_ftp(request.app)
                digest = await _run_in_executor(
                    ftp.md5_check, path, offset, length,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info("POST /rfb/file/md5 (rfb) path=%s ms=%d", path, elapsed_ms)
                return json_ok({"path": path, "offset": offset, "length": length, "md5": digest})
            except Exception as e:
                logger.info("RFB md5 回退到本地: %s", e)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None

        try:
            local_path = _validate_path(path)
        except ValueError as e:
            return json_error(str(e), 400)
        if not local_path.is_file():
            return json_error(f"文件不存在: {local_path}", 404)

        with open(local_path, "rb") as f:
            f.seek(max(0, offset))
            data = f.read(length)
        digest = hashlib.md5(data).hexdigest()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /rfb/file/md5 path=%s ms=%d", local_path, elapsed_ms)
        return json_ok({
            "path": str(local_path),
            "offset": offset,
            "length": len(data),
            "md5": digest,
        })
    except Exception as exc:
        logger.exception("POST /rfb/file/md5 异常")
        return json_error(str(exc), 500)


# ---------------------------------------------------------------------------
# 静态 / 占位
# ---------------------------------------------------------------------------

async def vnc_static_handler(request: web.Request) -> web.Response:
    """GET /vnc/ — noVNC UI 入口 (实际静态由 UI 层提供)."""
    vnc_result = request.app.get("vnc_result")
    port = getattr(vnc_result, "port", None) if vnc_result else None
    payload = {
        "vnc_detected": bool(port),
        "port": port,
        "noVNC_url": f"/vnc/vnc.html?host=127.0.0.1&port={port}" if port else None,
    }
    return json_ok(payload)


async def shell_static_handler(request: web.Request) -> web.Response:
    """GET /shell/ — xterm.js UI 入口 (实际静态由 UI 层提供)."""
    return json_ok({"ok": True, "xterm_url": "/shell/index.html"})


async def websockify_handler(request: web.Request) -> web.Response:
    """GET /websockify — WebSocket 代理占位, 返回 501."""
    return web.json_response(
        {"ok": False, "error": "Not Implemented — 实际 WebSocket 代理在 vnc.py 中实现"},
        status=501,
    )


# ---------------------------------------------------------------------------
# 启动 & 主应用工厂
# ---------------------------------------------------------------------------

def create_app(config: ServerConfig | None = None) -> web.Application:
    """创建 aiohttp Application, 注册所有路由 & 全局对象.

    启动时自动调用 ``detect_vnc()`` 并缓存结果到 ``app["vnc_result"]``.
    """
    config = config or ServerConfig()
    app = web.Application(logger=logger)
    app["config"] = config
    app["shell"] = ShellExecutor(default_timeout=config.timeout)

    # --- 启动事件: detect_vnc ---
    async def _on_startup(app_inst: web.Application) -> None:
        try:
            vnc_result = await asyncio.to_thread(_vnc.detect_vnc)
        except Exception as exc:  # pragma: no cover - 探测失败不阻塞启动
            logger.warning("detect_vnc 失败: %s", exc)
            vnc_result = None
        app_inst["vnc_result"] = vnc_result
        if vnc_result and getattr(vnc_result, "port", None):
            logger.info("检测到 VNC Server: port=%s mode=%s",
                        vnc_result.port, vnc_result.mode)
        else:
            logger.info("未检测到 VNC Server")

    app.on_startup.append(_on_startup)

    # --- 路由注册 ---
    app.router.add_get("/health", health_handler)

    app.router.add_post("/exec", exec_handler)

    app.router.add_post("/upload", upload_handler)
    app.router.add_post("/download", download_handler)
    app.router.add_post("/delete", delete_handler)

    app.router.add_post("/rsync/blockset", rsync_blockset_handler)
    app.router.add_post("/rsync/delta", rsync_delta_handler)
    app.router.add_post("/rsync/apply", rsync_apply_handler)

    app.router.add_post("/rfb/file/list", rfb_list_handler)
    app.router.add_post("/rfb/file/upload", rfb_upload_handler)
    app.router.add_post("/rfb/file/download", rfb_download_handler)
    app.router.add_post("/rfb/file/md5", rfb_md5_handler)

    app.router.add_get("/vnc/", vnc_static_handler)
    app.router.add_get("/shell/", shell_static_handler)
    app.router.add_get("/websockify", websockify_handler)

    # 尾斜杠兼容: /vnc 和 /vnc/ 都能访问
    app.router.add_get("/vnc", vnc_static_handler)
    app.router.add_get("/shell", shell_static_handler)

    return app


async def start_server(
    config: ServerConfig | None = None,
) -> tuple[web_runner.AppRunner, web_runner.TCPSite]:
    """CLI 层启动入口: 构建 app → runner → site → start.

    返回 (runner, site), 调用方需要在退出时 await runner.cleanup().
    """
    config = config or ServerConfig()
    app = create_app(config)
    runner = web_runner.AppRunner(app)
    await runner.setup()
    site = web_runner.TCPSite(runner, config.host, config.port)
    await site.start()
    logger.info("HTTP Shell server listening on http://%s:%d", config.host, config.port)
    return runner, site
