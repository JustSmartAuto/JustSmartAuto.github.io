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
import posixpath
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp.web as web
import aiohttp.web_runner as web_runner
from aiohttp import WSMsgType

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


def _parse_rfb_target(body: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """从请求 body / multipart meta 解析可选的远程目标 host/port.

    未提供 host 时返回 (None, None)，表示使用服务端本机探测到的 VNC Server。
    显式指定 host 而未指定 port 时按 TightVNC 默认端口 5900 处理。
    """
    if not body:
        return None, None
    raw_host = body.get("host")
    host = raw_host.strip() if isinstance(raw_host, str) else ""
    if not host:
        return None, None

    raw_port = body.get("port")
    if raw_port in (None, ""):
        port = 5900
    else:
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            raise ValueError(f"port 必须是整数: {raw_port!r}") from None
        if not 1 <= port <= 65535:
            raise ValueError(f"port 超出范围 (1-65535): {port}")
    return host, port


def _open_rfb_ftp(
    app: web.Application,
    host: str | None = None,
    port: int | None = None,
) -> tuple[_rfbftp.FtpSession, socket.socket]:
    """连接 tightvnc (RFB FTP 扩展), 返回 (FtpSession, sock).

    host/port 未指定时连接本机探测到的 VNC Server
    （host 取 ``HTTP_SHELL_VNC_HOST``，默认 127.0.0.1）。
    调用方负责在使用完毕后关闭 sock. 连接失败抛 RuntimeError.
    """
    if host:
        target_port = port or 5900
    else:
        target_port = _get_vnc_tcp_port(app)
        if target_port is None:
            raise RuntimeError("未检测到可用的 VNC Server (tightvnc)")
        host = os.environ.get("HTTP_SHELL_VNC_HOST", "127.0.0.1")
    password = os.environ.get("LDS_VNC_PASSWORD") or None
    try:
        # FtpSession 构造时完成 RFB 握手（Tight 安全类型），
        # 只有 Tight 模式服务端才会注册文件传输扩展消息。
        ftp = _rfbftp.FtpSession((host, target_port), password=password, timeout=15.0)
    except OSError as e:
        raise RuntimeError(f"连接 VNC Server 失败 {host}:{target_port} ({e})") from e
    except _rfbftp.FtpError as e:
        raise RuntimeError(f"VNC 文件传输不可用 {host}:{target_port} — {e}") from e
    return ftp, ftp.sock


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
    """POST /upload — multipart 上传文件.

    * 未带 ``host`` 字段：上传到服务端本机路径（RFB FTP 可用时同步推送到本机 VNC Server）。
    * 带 ``host`` / ``port`` 字段：直接经 RFB FTP 写入该远程主机，不落本地磁盘。
    """
    started = time.perf_counter()
    sock = None
    ftp: _rfbftp.FtpSession | None = None
    tmp_path: Path | None = None
    try:
        data = await request.multipart()
        meta: dict[str, Any] = {}
        total_size = 0
        # 文件体必须在遍历 multipart 时立即读取 —— aiohttp 在 next() 之后
        # 会消耗掉上一个 part 的剩余数据，事后读会拿到空内容。
        while True:
            part = await data.next()
            if part is None:
                break
            filename = getattr(part, "filename", None)
            if filename:
                # 有 filename 的 part 视为文件体，直接落到系统临时文件
                meta["filename"] = filename
                if tmp_path is not None:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                fd, tmp_name = tempfile.mkstemp(prefix="lds-upload-", suffix=".part")
                tmp_path = Path(tmp_name)
                total_size = 0
                with os.fdopen(fd, "wb") as f:
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        f.write(chunk)
                        total_size += len(chunk)
            else:
                # 其它 part 收集为元数据
                try:
                    meta[part.name] = await part.text()
                except Exception:
                    meta[part.name] = ""

        try:
            remote_host, remote_port = _parse_rfb_target(meta)
        except ValueError as e:
            return json_error(str(e), 400)
        remote_mode = remote_host is not None

        target_path = meta.get("path") or meta.get("remote")
        if not target_path:
            return json_error("缺少 path 字段", 400)

        if remote_mode:
            remote_dir = (meta.get("dir") or meta.get("remote_dir") or "/").strip() or "/"
            if target_path.startswith("/"):
                remote_path = target_path
            else:
                remote_path = posixpath.join(remote_dir, target_path)
            local_path = None
        else:
            try:
                local_path = _validate_path(target_path)
            except ValueError as e:
                return json_error(str(e), 400)
            remote_path = target_path

        if tmp_path is None:  # 没有文件体 —— 当成空文件
            fd, tmp_name = tempfile.mkstemp(prefix="lds-upload-", suffix=".part")
            tmp_path = Path(tmp_name)
            os.close(fd)

        if remote_mode:
            try:
                ftp, sock = _open_rfb_ftp(request.app, remote_host, remote_port)
                offset = int(meta.get("offset") or 0)
                await _run_in_executor(
                    _rfb_upload_inplace, ftp, str(tmp_path), remote_path, offset, True,
                )
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("POST /upload -> rfb %s:%s path=%s bytes=%d ms=%d",
                        remote_host, remote_port, remote_path, total_size, elapsed_ms)
            return json_ok({
                "path": remote_path,
                "size": total_size,
                "target": f"{remote_host}:{remote_port}",
            })

        # 本地模式：RFB FTP 回传本机 VNC Server（存在时）
        assert local_path is not None
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return json_error(f"无法创建父目录: {e}", 500)

        if _get_vnc_tcp_port(request.app) is not None:
            try:
                ftp, sock = _open_rfb_ftp(request.app)
                offset = int(meta.get("offset") or 0)
                await _run_in_executor(
                    _rfb_upload_inplace,
                    ftp, str(tmp_path), target_path, offset, bool(meta.get("overwrite", True)),
                )
            except Exception as e:
                # RFB FTP 失败不影响本地已写入
                logger.warning("RFB FTP 上传失败, 保留本地: %s", e)

        try:
            os.replace(tmp_path, local_path)
            tmp_path = None
        except OSError as e:
            try:
                import shutil

                shutil.move(str(tmp_path), str(local_path))
                tmp_path = None
            except OSError as move_err:
                return json_error(f"保存文件失败: {move_err} (原错误: {e})", 500)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("POST /upload path=%s bytes=%d ms=%d", local_path, total_size, elapsed_ms)
        return json_ok({"path": str(local_path), "size": total_size})
    except Exception as exc:
        logger.exception("POST /upload 异常")
        return json_error(str(exc), 500)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
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
    """调用 FtpSession 上传；远程父目录不存在时逐级创建."""
    try:
        ftp.mkdir_parents(remote_path)
    except _rfbftp.FtpError as exc:
        logger.info("创建远程父目录失败（继续尝试上传）: %s", exc)
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
            remote_host, remote_port = _parse_rfb_target(body)
        except ValueError as e:
            return json_error(str(e), 400)
        if remote_host is not None:
            # 远程模式：经 RFB FTP 下载到临时文件，再流式返回给客户端
            ftp = None
            sock = None
            tmp_path = None
            try:
                fd, tmp_name = tempfile.mkstemp(prefix="lds-download-", suffix=".part")
                os.close(fd)
                tmp_path = Path(tmp_name)
                ftp, sock = _open_rfb_ftp(request.app, remote_host, remote_port)
                written = await _run_in_executor(
                    ftp.download_file, path, str(tmp_path), offset
                )
                headers = {
                    "Content-Length": str(written),
                    "Content-Type": "application/octet-stream",
                    "X-File-Path": path,
                    "X-File-Size": str(written),
                    "X-Offset": str(offset),
                }
                resp = web.StreamResponse(status=200, headers=headers)
                await resp.prepare(request)
                with open(tmp_path, "rb") as f:
                    while True:
                        buf = f.read(256 * 1024)
                        if not buf:
                            break
                        await resp.write(buf)
                await resp.write_eof()
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info("POST /download (rfb %s:%s) path=%s bytes=%d ms=%d",
                            remote_host, remote_port, path, written, elapsed_ms)
                return resp
            except Exception as exc:
                logger.info("RFB 下载失败 %s:%s: %s", remote_host, remote_port, exc)
                return json_error(
                    f"远程下载失败 {remote_host}:{remote_port} — {exc}", 502
                )
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                if tmp_path is not None:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

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
                await _run_in_executor(ftp.remove, path)
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
    """POST /rfb/file/list — 列出远程或本地目录.

    body 支持可选 ``host`` / ``port``：
      * 指定 host → 必须通过该主机的 RFB FTP 列举（失败直接报错，不回退本地）
      * 未指定 → 本机探测到的 VNC Server 优先，失败回退本地目录
    """
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        body = await request.json()
        path = body.get("path", "/")
        try:
            remote_host, remote_port = _parse_rfb_target(body)
        except ValueError as e:
            return json_error(str(e), 400)

        if remote_host is not None:
            try:
                ftp, sock = _open_rfb_ftp(request.app, remote_host, remote_port)
                entries = await _run_in_executor(ftp.list_dir, path)
            except Exception as e:
                logger.info("RFB list 远程目标失败 %s:%s: %s", remote_host, remote_port, e)
                return json_error(
                    f"连接远程 VNC 失败 {remote_host}:{remote_port} — {e}", 502
                )
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    sock = None
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("POST /rfb/file/list (rfb %s:%s) path=%s entries=%d ms=%d",
                        remote_host, remote_port, path, len(entries), elapsed_ms)
            return json_ok({
                "path": path,
                "entries": entries,
                "target": f"{remote_host}:{remote_port}",
            })

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
# 静态资源（noVNC / xterm）与 WebSocket 代理
# ---------------------------------------------------------------------------

#: 打包后位于 ``_MEIPASS/vnc_bundle``；开发时位于项目根 ``vnc_bundle/``；
#: 都没有时回退同级历史工程 ``http_shell_cli_vnc/public_fs``。
def _bundle_candidates() -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "vnc_bundle")
    candidates.append(project_root / "vnc_bundle")
    candidates.append(project_root.parent / "http_shell_cli_vnc" / "public_fs")
    return candidates


def _asset_dir(name: str) -> Path | None:
    """在候选目录中找 ``noVNC/`` / ``xterm/`` 子目录."""
    for base in _bundle_candidates():
        target = base / name
        if target.is_dir():
            return target
    return None


def _safe_static_file(base: Path, rel: str) -> Path | None:
    """把 URL 相对路径解析为 base 下的真实文件，拒绝目录穿越."""
    rel = rel.replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        return None
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _vnc_target(request: web.Request) -> tuple[str, int | None]:
    """解析 VNC 目标：查询参数优先，其次服务端本机探测结果."""
    host = (request.query.get("host") or "").strip() or "127.0.0.1"
    detected = _get_vnc_tcp_port(request.app)
    raw_port = (request.query.get("port") or "").strip()
    if not raw_port:
        return host, detected
    try:
        port = int(raw_port)
    except ValueError:
        return host, detected
    return host, port if 1 <= port <= 65535 else detected


def _vnc_entry_url(request: web.Request) -> str:
    """构造 noVNC 入口 URL.

    noVNC 的 ``host``/``port`` 是 **websockify**（即本服务）的地址，不是 VNC
    目标；VNC 目标通过 ``path`` 查询串传给 ``/websockify`` 代理。
    """
    target_host, target_port = _vnc_target(request)
    has_target = bool(request.query.get("host") or request.query.get("port") or target_port)
    page_host = request.url.host or "127.0.0.1"
    page_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    params: dict[str, str] = {
        "host": page_host,
        "port": str(page_port),
        "encrypt": "0",
        "path": "websockify?" + urlencode(
            {"host": target_host, "port": target_port or 5900}
        ),
        "resize": "scale",
    }
    if has_target:
        params["autoconnect"] = "true"
    return "/vnc/vnc.html?" + urlencode(params)


async def vnc_static_handler(request: web.Request) -> web.StreamResponse:
    """GET /vnc/{path} — 提供内嵌 noVNC 静态文件."""
    tail = request.match_info.get("tail", "")
    if not tail:
        return web.HTTPFound(_vnc_entry_url(request))
    base = _asset_dir("novnc")
    if base is None:
        return web.Response(
            status=503,
            text="noVNC 资源未找到：请准备 vnc_bundle/novnc（或重新打包完整版）",
        )
    target = _safe_static_file(base, tail)
    if target is None:
        raise web.HTTPNotFound()
    return web.FileResponse(target)


async def shell_static_handler(request: web.Request) -> web.StreamResponse:
    """GET /shell/{path} — 提供内嵌 xterm 控制台静态文件."""
    tail = request.match_info.get("tail", "") or "index.html"
    base = _asset_dir("xterm")
    if base is None:
        return web.Response(
            status=503,
            text="xterm 资源未找到：请准备 vnc_bundle/xterm（或重新打包完整版）",
        )
    target = _safe_static_file(base, tail)
    if target is None:
        raise web.HTTPNotFound()
    return web.FileResponse(target)


async def _pipe_tcp_to_ws(reader: asyncio.StreamReader, ws: web.WebSocketResponse) -> None:
    """VNC → 浏览器方向转发."""
    try:
        while True:
            data = await reader.read(65536)
            if not data or ws.closed:
                break
            await ws.send_bytes(data)
    except (ConnectionResetError, RuntimeError):
        pass
    finally:
        if not ws.closed:
            await ws.close()


async def websockify_handler(request: web.Request) -> web.StreamResponse:
    """GET /websockify?host=&port= — WebSocket ↔ VNC TCP 双向代理."""
    host, port = _vnc_target(request)
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=0)
    await ws.prepare(request)

    if not port:
        await ws.close(code=1011, message=b"no VNC target")
        return ws

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=8
        )
    except (OSError, asyncio.TimeoutError) as exc:
        logger.warning("websockify 连接 %s:%s 失败: %s", host, port, exc)
        await ws.close(code=1011, message=f"connect failed: {exc}".encode()[:110])
        return ws

    logger.info("websockify 已连接 %s:%s", host, port)
    up_task = asyncio.create_task(_pipe_tcp_to_ws(reader, ws))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE,
                              WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
    except (ConnectionResetError, RuntimeError):
        pass
    finally:
        up_task.cancel()
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, RuntimeError):
            pass
        logger.info("websockify 断开 %s:%s", host, port)
    return ws


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

    app.router.add_get("/websockify", websockify_handler)
    # 无尾斜杠时先重定向，保证页面内相对路径（core/、xterm.js 等）解析正确；
    # 必须注册在通配路由之前，否则 /shell 会被 /shell/{tail:.*} 抢先命中
    app.router.add_get("/vnc", lambda req: web.HTTPFound("/vnc/?" + req.query_string))
    app.router.add_get("/shell", lambda _req: web.HTTPFound("/shell/"))
    app.router.add_get("/vnc/{tail:.*}", vnc_static_handler)
    app.router.add_get("/shell/{tail:.*}", shell_static_handler)

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
