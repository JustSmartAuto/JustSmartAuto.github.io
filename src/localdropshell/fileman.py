"""Web 文件管理器（参考 filebrowser-quantum）.

路由（统一挂在 aiohttp 服务端，默认端口 10022）：

    GET  /files/                     Win95 风格文件管理页面（assets/files/index.html）
    POST /api/files/login            {"user","pass"} → 设置会话 Cookie（justsmart/justsmart）
    POST /api/files/logout           注销
    GET  /api/files/session          {"auth": true/false}
    GET  /api/files/list?path=       列目录；path 为空时返回 C:/ D:/ 等盘符
    GET  /api/files/download?path=   下载文件
    POST /api/files/upload           multipart 上传（path=目标目录, file=文件）
    POST /api/files/op               {"op": mkdir|delete|rename, ...}

权限模型：匿名可浏览/下载所有盘；对 **C 盘** 的写操作（上传/新建/删除/重命名）
必须登录。其它盘写操作不限制。
"""

from __future__ import annotations

import json
import logging
import secrets
import string
import time
from pathlib import Path
from typing import Any

import aiohttp.web as web

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
    """
    raw = (raw or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return None  # 盘符根
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
    try:
        target = _resolve(request.query.get("path", ""))
    except ValueError as e:
        return _err(str(e), 400)
    if target is None or not target.is_file():
        return _err("文件不存在", 404)
    return web.FileResponse(
        target,
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


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
    app.router.add_get("/api/files/list", list_handler)
    app.router.add_get("/api/files/download", download_handler)
    app.router.add_post("/api/files/upload", upload_handler)
    app.router.add_post("/api/files/op", op_handler)
