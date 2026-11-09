"""Web 文件管理器（参考 filebrowser-quantum）.

路由（统一挂在 aiohttp 服务端，默认端口 10022）：

    GET  /files/                     Win95 风格文件管理页面（assets/files/index.html）
    POST /api/files/login            {"user","pass"} → 设置会话 Cookie（justsmart/justsmart）
    POST /api/files/logout           注销
    GET  /api/files/session          {"auth": true/false}
    GET  /api/files/list?path=       列目录；path 为空时返回 C:/ D:/ 等盘符
    GET  /api/files/download?path=   下载文件（支持 Range 单区间 → 206，页面端续传用）
    GET  /api/files/fountain?path=   LT 喷泉码帧无限流（Wire 帧 flags bit0=1），收齐即断
    POST /api/files/upload           multipart 上传（path=目标目录, file=文件）
    POST /api/files/op               {"op": mkdir|delete|rename, ...}

权限模型：匿名可浏览/下载所有盘；对 **C 盘** 的写操作（上传/新建/删除/重命名）
必须登录。其它盘写操作不限制。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import string
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp.web as web

from . import fountain as _fountain
from . import settings as _settings
from . import vnc as _vnc

logger = logging.getLogger("localdropshell.fileman")

COOKIE_NAME = "lds_fm"
AUTH_USER = "justsmart"
AUTH_PASS = "justsmart"

_sessions: set[str] = set()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _ok(data: dict[str, Any]) -> web.Response:
    return web.json_response(data, status=200, dumps=json.dumps)


def _err(message: str, status: int = 500) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status, dumps=json.dumps)


def _authed(request: web.Request) -> bool:
    return request.cookies.get(COOKIE_NAME, "") in _sessions


def _list_drives() -> list[str]:
    """返回存在的盘符根路径列表，如 ["C:/", "D:/"]."""
    drives = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                drives.append(f"{letter}:/")
        except OSError:
            continue
    return drives


def _resolve(raw: str) -> Path | None:
    """把页面传来的路径解析为本地绝对路径；空串表示盘符根（返回 None）.

    拒绝 ``..`` 目录穿越与非法路径时抛 ValueError.
    注意 ``Path("D:")`` 是**驱动器相对路径**（解析为 D 盘的当前目录），
    盘符必须补成 ``D:/`` 才是根目录。
    """
    raw = (raw or "").strip().replace("\\", "/")
    if not raw.strip("/"):
        return None  # 盘符根
    m = re.fullmatch(r"([A-Za-z]):/*", raw)
    if m:
        return Path(m.group(1).upper() + ":/")
    raw = raw.strip("/")
    if ".." in raw.split("/"):
        raise ValueError("路径不允许包含 ..")
    p = Path(raw)
    if not p.drive:  # 无盘符 → 拼到 C:
        p = Path("C:/") / raw
    return p


def _is_c_drive(path: Path) -> bool:
    return path.drive.upper().startswith("C:")


def _require_write_auth(request: web.Request, target: Path | None) -> web.Response | None:
    """C 盘写操作需登录；返回 None 表示放行，否则返回 403 响应."""
    if target is not None and _is_c_drive(target) and not _authed(request):
        return _err("操作 C 盘需要先登录（文件管理页右上角）", 403)
    return None


def _entry(p: Path) -> dict[str, Any]:
    try:
        st = p.stat()
        return {
            "name": p.name or str(p),
            "dir": p.is_dir(),
            "size": 0 if p.is_dir() else st.st_size,
            "mtime": int(st.st_mtime),
        }
    except OSError:
        return {"name": p.name or str(p), "dir": p.is_dir(), "size": 0, "mtime": 0}


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

async def files_page_handler(request: web.Request) -> web.StreamResponse:
    """GET /files/{path} — 文件管理页面静态资源."""
    tail = request.match_info.get("tail", "") or "index.html"
    base = _vnc.bundle_asset_dir()
    base = base / "files" if base is not None else None
    if base is None or not base.is_dir():
        return web.Response(status=503, text="文件管理页面资源未找到：请准备 assets/files")
    rel = tail.replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        raise web.HTTPNotFound()
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise web.HTTPNotFound()
    if not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

async def login_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _err("请求体需为 JSON", 400)
    if body.get("user") == AUTH_USER and body.get("pass") == AUTH_PASS:
        token = secrets.token_hex(16)
        _sessions.add(token)
        resp = _ok({"auth": True})
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Lax")
        logger.info("文件管理器登录成功")
        return resp
    return _err("账号或密码错误", 401)


async def logout_handler(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        _sessions.discard(token)
    resp = _ok({"auth": False})
    resp.del_cookie(COOKIE_NAME)
    return resp


async def session_handler(request: web.Request) -> web.Response:
    return _ok({"auth": _authed(request)})


async def device_handler(request: web.Request) -> web.Response:
    """GET /api/device — 设备昵称（首次访问生成并持久化到 settings.json）."""
    return _ok({"alias": _settings.device_alias()})


# ---------------------------------------------------------------------------
# 浏览 / 下载
# ---------------------------------------------------------------------------

async def list_handler(request: web.Request) -> web.Response:
    raw = request.query.get("path", "")
    try:
        target = _resolve(raw)
    except ValueError as e:
        return _err(str(e), 400)

    if target is None:  # 盘符根
        entries = [
            {"name": d, "dir": True, "size": 0, "mtime": 0} for d in _list_drives()
        ]
        return _ok({"path": "", "parent": None, "entries": entries, "auth": _authed(request)})

    if not target.is_dir():
        return _err(f"目录不存在: {target}", 404)
    try:
        entries = [_entry(p) for p in sorted(
            target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
        )]
    except OSError as e:
        return _err(f"无法读取目录: {e}", 500)

    parent = str(target.parent).replace("\\", "/")
    if target.parent == target:  # 已到盘符根，上一级回到盘符列表
        parent = ""
    return _ok({
        "path": str(target).replace("\\", "/"),
        "parent": parent,
        "entries": entries,
        "auth": _authed(request),
    })


async def download_handler(request: web.Request) -> web.StreamResponse:
    """下载文件；支持单区间 ``Range: bytes=start-end``（206），用于页面端续传."""
    try:
        target = _resolve(request.query.get("path", ""))
    except ValueError as e:
        return _err(str(e), 400)
    if target is None or not target.is_file():
        return _err("文件不存在", 404)

    size = target.stat().st_size
    disposition = f'attachment; filename="{target.name}"'
    range_header = (request.headers.get("Range") or "").strip()

    if not range_header:
        return web.FileResponse(
            target,
            headers={
                "Content-Disposition": disposition,
                "Accept-Ranges": "bytes",
            },
        )

    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    if not m or (not m.group(1) and not m.group(2)):
        return _err("仅支持单区间 bytes Range", 400)
    if m.group(1):  # bytes=start-end / bytes=start-
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:  # bytes=-N（末尾 N 字节）
        n = int(m.group(2))
        start = max(0, size - n)
        end = size - 1
    if start > end or start >= size:
        return web.Response(
            status=416,
            headers={"Content-Range": f"bytes */{size}"},
        )
    end = min(end, size - 1)
    length = end - start + 1

    resp = web.StreamResponse(
        status=206,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": disposition,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
        },
    )
    await resp.prepare(request)
    try:
        with open(target, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # 客户端中断（续传场景属正常）
    return resp


# ---------------------------------------------------------------------------
# 喷泉码下载（LT 帧无限流，收齐即断）
# ---------------------------------------------------------------------------

# 帧数保护上限的倍数：客户端收到 max_frames_mult * k 仍未译码即异常
FOUNTAIN_MAX_FRAMES_MULT = 20


async def fountain_handler(request: web.Request) -> web.StreamResponse:
    """GET /api/files/fountain?path=&block_size= — LT 喷泉帧无限流.

    每帧为 decimen v3 Wire 帧（flags bit0=1 表示 LT 异或帧），自描述：
    客户端按 (session_id, seq, k) 重算源块下标，收齐 k 块后主动断开。
    文件名/大小经响应头 X-Lds-Name（percent-encoded UTF-8）/ X-Lds-Size 提供，
    便于页面先弹保存对话框再接收。
    """
    try:
        target = _resolve(request.query.get("path", ""))
    except ValueError as e:
        return _err(str(e), 400)
    if target is None or not target.is_file():
        return _err("文件不存在", 404)

    size = target.stat().st_size
    if size == 0:
        return _err("空文件无需喷泉码传输", 400)
    if size >= 0xFFFFFFFF:
        return _err("文件超过 4GB，超出 Wire 帧 total_len 上限", 413)

    block_size_raw = request.query.get("block_size", "").strip()
    block_size: int | None = None
    if block_size_raw:
        try:
            block_size = int(block_size_raw)
        except ValueError:
            return _err("block_size 需为整数", 400)
        if not (1 <= block_size <= 0xFFFF):
            return _err("block_size 需在 1..65535 之间", 400)

    try:
        container = _fountain.build_from_file(str(target))
    except OSError as e:
        return _err(f"读取文件失败: {e}", 500)

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/octet-stream",
            "Cache-Control": "no-store",
            "X-Lds-Name": quote(target.name),
            "X-Lds-Size": str(size),
        },
    )
    await resp.prepare(request)

    frames = 0
    started = time.perf_counter()
    try:
        for frame in _fountain.lt_encode_stream(container, block_size=block_size):
            await resp.write(_fountain.pack_frame(frame))
            frames += 1
            # 每 256 帧让出一次事件循环，避免饿死其它连接
            if frames % 256 == 0:
                await asyncio.sleep(0)
            # 保护：客户端异常不断连时避免无限发送
            if frames > FOUNTAIN_MAX_FRAMES_MULT * frame.k:
                logger.warning("喷泉码会话帧数超限，主动断开 frames=%d k=%d", frames, frame.k)
                break
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # 客户端收齐断开，属正常
    finally:
        logger.info("喷泉码会话结束 frames=%d file=%s ms=%d",
                    frames, target, int((time.perf_counter() - started) * 1000))
    return resp


# ---------------------------------------------------------------------------
# 写操作（C 盘需登录）
# ---------------------------------------------------------------------------

async def upload_handler(request: web.Request) -> web.Response:
    """multipart：path=目标目录, file=文件体（可多文件依次传）."""
    started = time.perf_counter()
    reader = await request.multipart()
    target_dir_raw = ""
    saved: list[str] = []
    total = 0

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.filename:
            if not target_dir_raw:
                return _err("缺少 path 字段", 400)
            try:
                target_dir = _resolve(target_dir_raw)
            except ValueError as e:
                return _err(str(e), 400)
            if target_dir is None:
                return _err("请先进入具体盘符目录再上传", 400)
            denied = _require_write_auth(request, target_dir)
            if denied is not None:
                return denied
            filename = Path(part.filename.replace("\\", "/")).name
            if not filename:
                continue
            dest = target_dir / filename
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
                saved.append(str(dest))
            except OSError as e:
                return _err(f"保存失败: {e}", 500)
        else:
            if part.name == "path":
                target_dir_raw = (await part.text()).strip()
            else:
                await part.release()

    logger.info("文件管理上传 %d 个文件 %d 字节 ms=%d",
                len(saved), total, int((time.perf_counter() - started) * 1000))
    return _ok({"saved": saved, "size": total})


async def op_handler(request: web.Request) -> web.Response:
    """JSON: {"op": mkdir|delete|rename, "path": ..., "name"/"to": ...}."""
    try:
        body = await request.json()
    except Exception:
        return _err("请求体需为 JSON", 400)
    op = body.get("op")
    try:
        target = _resolve(str(body.get("path") or ""))
    except ValueError as e:
        return _err(str(e), 400)
    if target is None:
        return _err("盘符根不支持该操作", 400)

    denied = _require_write_auth(request, target)
    if denied is not None:
        return denied

    try:
        if op == "mkdir":
            name = str(body.get("name") or "").strip()
            if not name or "/" in name or "\\" in name:
                return _err("目录名非法", 400)
            (target / name).mkdir(parents=False, exist_ok=False)
            return _ok({"path": str(target / name)})

        if op == "delete":
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()
            return _ok({"deleted": str(target)})

        if op == "rename":
            to = str(body.get("to") or "").strip()
            if not to or "/" in to or "\\" in to:
                return _err("新名称非法", 400)
            target.rename(target.parent / to)
            return _ok({"path": str(target.parent / to)})

        return _err(f"未知操作: {op}", 400)
    except OSError as e:
        return _err(f"操作失败: {e}", 500)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def register(app: web.Application) -> None:
    """在 ``create_app`` 中调用，挂接文件管理器全部路由."""
    app.router.add_get("/files", lambda _req: web.HTTPFound("/files/"))
    app.router.add_get("/files/{tail:.*}", files_page_handler)
    app.router.add_post("/api/files/login", login_handler)
    app.router.add_post("/api/files/logout", logout_handler)
    app.router.add_get("/api/files/session", session_handler)
    app.router.add_get("/api/device", device_handler)
    app.router.add_get("/api/files/list", list_handler)
    app.router.add_get("/api/files/download", download_handler)
    app.router.add_get("/api/files/fountain", fountain_handler)
    app.router.add_post("/api/files/upload", upload_handler)
    app.router.add_post("/api/files/op", op_handler)
