"""内嵌远程 PowerShell：只用标准库实现的 HTTP Shell 服务端。

工控机上的 InternetProxyPy 以管理员身份运行（打包带 ``--uac-admin``），
因此在进程内直接暴露与 ``http_shell_cli_vnc_*.exe`` 兼容的 HTTP 接口，
agent 远程修问题（校时、改 DNS、重启服务、换 exe）时不用再到现场点 UAC。

接口（与本项目 .trae 技能里记录的 exe 行为一致）：

    GET  /health                         健康检查，status=running 才可继续
    POST /exec    {"command", "timeout", "work_dir"}
                                         → {"stdout","stderr","exit_code","timeout"}
    POST /upload  multipart：file + path  上传文件（path 以 / 结尾表示目录）
    POST /download 表单或 ?path=           下载文件

编码：
    命令用 ``-EncodedCommand``（UTF-16LE）传给 PowerShell，绕开命令行参数
    的 ANSI 码页，中文命令不会乱码；脚本开头把 ``[Console]::OutputEncoding``
    与 ``$OutputEncoding`` 设为 UTF-8，原生程序的输出也是 UTF-8；解码仍按
    UTF-8 → 系统 ANSI（mbcs）逐级回退，兼容不认控制台码页的老程序。

安全边界：无身份认证，仅用于内网/离线环境，不要暴露到公网。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
from html import unescape as _unescape_html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import __version__
from .config import DEFAULT_SHELL_PORT, config_dir

log = logging.getLogger("internet_proxy.remoteshell")

DEFAULT_PORT = DEFAULT_SHELL_PORT  # 与 assets 里的 http_shell 服务端默认端口一致
MAX_TIMEOUT = 3600
MAX_BODY = 256 * 1024 * 1024  # 上传/脚本体上限（软件本身约 50 MB，留足换版本的余量）
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# 输出统一按 UTF-8：不设的话 Windows PowerShell 5.1 重定向时按 GBK 输出；
# ProgressPreference 关掉模块自动加载等进度记录，避免 stderr 混入 CLIXML。
_ENCODING_PREFIX = (
    "$ProgressPreference='SilentlyContinue'\n"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
    "$OutputEncoding=[Text.Encoding]::UTF8\n"
)


def _shell() -> str | None:
    """优先 pwsh（对 UTF-8 更友好），退回 Windows PowerShell."""
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _decode(raw: bytes) -> str:
    """UTF-8 优先，解不开退回系统 ANSI 码页（中文 Windows 为 GBK）."""
    if not raw:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _clean_stderr(text: str) -> str:
    """Windows PowerShell 5.1 把错误记录序列化成 CLIXML 写到 stderr.

    直接回传给 agent 是一大坨 XML，这里取出 ``<S S="Error">`` 的内容并还原
    ``_x000D_`` 之类的转义；不认识的格式原样返回。
    """
    if not text.startswith("#< CLIXML"):
        return text
    parts = re.findall(r'<S S="Error">(.*?)</S>', text, re.S)
    if not parts:
        return text
    body = _unescape_html("".join(parts))
    body = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), body)
    return body


def run_powershell(
    command: str, timeout: int = 30, work_dir: str | None = None
) -> tuple[str, str, int, bool]:
    """执行一段 PowerShell，返回 (stdout, stderr, exit_code, timed_out)."""
    shell = _shell()
    if shell is None:
        return "", "未找到 pwsh 或 powershell", 127, False
    encoded = base64.b64encode(
        (_ENCODING_PREFIX + command).encode("utf-16-le")
    ).decode("ascii")
    cwd = Path(work_dir) if work_dir else None
    if cwd is not None and not cwd.is_dir():
        return "", f"工作目录不存在: {work_dir}", 1, False
    try:
        args = [shell, "-NoProfile", "-NonInteractive"]
        if Path(shell).stem.lower() == "powershell":
            # Windows PowerShell 5.1 把重定向的错误记录序列化成 CLIXML，
            # 加上 -OutputFormat Text 才是可读文本；pwsh 无此参数。
            args += ["-OutputFormat", "Text"]
        args += ["-EncodedCommand", encoded]
        proc = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        return "", f"启动 PowerShell 失败: {exc}", 1, False

    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        # 命令可能拉起子进程，整棵树一起结束，避免残留占住端口/文件
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=_NO_WINDOW,
        )
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = b"", b""
    code = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    return _decode(out), _clean_stderr(_decode(err)), code, timed_out


def _parse_multipart(body: bytes, boundary: str) -> dict[str, tuple[str, bytes]]:
    """极简 multipart/form-data 解析：返回 {字段名: (文件名, 数据)}."""
    fields: dict[str, tuple[str, bytes]] = {}
    marker = b"--" + boundary.encode("utf-8", "replace")
    for part in body.split(marker):
        part = part.lstrip(b"\r\n")
        if not part or part.startswith(b"--"):
            continue  # 结束标记
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = filename = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for piece in line.split(";")[1:]:
                    key, _, value = piece.partition("=")
                    key = key.strip().lower()
                    value = value.strip().strip('"')
                    if key == "name":
                        name = value
                    elif key == "filename":
                        filename = value
        if name:
            fields[name] = (filename, data)
    return fields


def _content_type(headers) -> tuple[str, str]:
    """拆出 Content-Type 与 boundary 参数."""
    raw = headers.get("Content-Type") or ""
    ctype, _, params = raw.partition(";")
    boundary = ""
    for piece in params.split(";"):
        key, _, value = piece.partition("=")
        if key.strip().lower() == "boundary":
            boundary = value.strip().strip('"')
    return ctype.strip().lower(), boundary


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, owner: "ShellServer") -> None:
        self.owner = owner
        super().__init__(address, handler)

    def server_bind(self) -> None:
        """跳过 HTTPServer 的 getfqdn 反查.

        工控机经常没有可用 DNS，反查会卡十几秒甚至几分钟，导致内嵌 Shell
        很久不监听；这里直接用绑定地址当 server_name。
        """
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def handle_error(self, request, client_address) -> None:
        """客户端断开是常态（curl/单次模式都会直接关连接），不打整段 traceback."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            log.debug("远程 Shell 客户端 %s 已断开", client_address)
            return
        log.warning("远程 Shell 处理 %s 出错: %s", client_address, exc)


class _Handler(BaseHTTPRequestHandler):
    server_version = "InternetProxyPy-HTTPShell"
    protocol_version = "HTTP/1.1"
    timeout = 300  # 大文件上传/慢命令时别被读超时打断

    # —— 响应工具 ——

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        # HTTP 头只能 latin-1：中文文件名放 RFC 5987 的 filename*，再用 ASCII 兜底
        fallback = path.name.encode("ascii", "replace").decode("ascii").replace('"', "_")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(path.name, safe='')}",
        )
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > MAX_BODY:
            raise ValueError(f"请求体超过 {MAX_BODY // (1024 * 1024)} MB")
        return self.rfile.read(length)

    def _fields(self, body: bytes) -> dict[str, tuple[str, bytes]]:
        """把 body 统一解析成 {字段: (文件名, 数据)}，兼容 multipart / 表单."""
        ctype, boundary = _content_type(self.headers)
        if ctype == "multipart/form-data" and boundary:
            return _parse_multipart(body, boundary)
        if ctype == "application/x-www-form-urlencoded":
            pairs = parse_qs(_decode(body), keep_blank_values=True)
            return {k: ("", v[0].encode("utf-8")) for k, v in pairs.items()}
        return {}

    # —— 路由 ——

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") in ("", "/health"):
            self._health()
        elif parsed.path.rstrip("/") == "/download":
            query = parse_qs(parsed.query)
            self._download(query.get("path", [""])[0])
        else:
            self._send_json({"error": "not found", "path": parsed.path}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/")
        try:
            body = self._body()  # 必须先读完：否则 keep-alive 下一条请求会串包
            if route == "/exec":
                self._exec(body)
            elif route == "/upload":
                self._upload(body)
            elif route == "/download":
                path = parse_qs(parsed.query).get("path", [""])[0]
                if not path:
                    path = _decode(self._fields(body).get("path", ("", b""))[1])
                self._download(path)
            elif route == "/health":
                self._health()
            else:
                self._send_json({"error": "not found", "path": parsed.path}, 404)
        except ValueError as exc:
            self.close_connection = True
            self._send_json({"error": str(exc)}, 400)
        except OSError as exc:
            log.warning("远程 Shell 处理 %s 失败: %s", route, exc)
            self._send_json({"error": str(exc)}, 500)

    # —— 接口实现 ——

    def _health(self) -> None:
        owner: ShellServer = self.server.owner  # type: ignore[attr-defined]
        self._send_json(
            {
                "status": "running",
                "shell": _shell() or "",
                "host": socket.gethostname(),
                "role": owner.role,
                "version": __version__,
                "uptime": round(time.monotonic() - owner.started, 1),
            }
        )

    def _exec(self, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = {"command": _decode(body)}  # 兼容直接把命令当 body 提交
        if not isinstance(payload, dict):
            self._send_json({"error": "请求体应为 JSON 对象"}, 400)
            return

        command = str(payload.get("command") or payload.get("cmd") or "")
        if not command.strip():
            self._send_json({"error": "缺少 command"}, 400)
            return
        try:
            timeout = int(payload.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout = 30
        timeout = min(max(timeout, 1), MAX_TIMEOUT)
        work_dir = payload.get("work_dir") or payload.get("cwd") or None

        log.info("远程 exec（%ss）: %s", timeout, command.strip().splitlines()[0][:200])
        out, err, code, timed_out = run_powershell(command, timeout, work_dir)
        self._send_json(
            {"stdout": out, "stderr": err, "exit_code": code, "timeout": timed_out}
        )

    def _upload(self, body: bytes) -> None:
        fields = self._fields(body)
        filename, data = fields.get("file", ("", b""))
        if not data and not filename:
            self._send_json({"error": "multipart 中缺少 file 字段"}, 400)
            return
        safe_name = Path(filename).name or "upload.bin"
        raw_path = _decode(fields.get("path", ("", b""))[1]).strip()
        target = Path(raw_path) if raw_path else config_dir() / "uploads" / safe_name
        if raw_path.endswith(("/", "\\")) or (target.exists() and target.is_dir()):
            target = target / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        log.info("远程上传 %s（%d 字节）", target, len(data))
        self._send_json({"ok": True, "path": str(target), "size": len(data)})

    def _download(self, raw_path: str) -> None:
        raw_path = (raw_path or "").strip()
        if not raw_path:
            self._send_json({"error": "缺少 path"}, 400)
            return
        path = Path(raw_path)
        if not path.is_file():
            self._send_json({"error": f"文件不存在: {raw_path}"}, 404)
            return
        log.info("远程下载 %s", path)
        self._send_file(path)

    def log_message(self, fmt: str, *args) -> None:
        log.debug("远程 Shell: " + fmt, *args)


class ShellServer:
    """内嵌远程 Shell 的生命周期；端口占用时只告警，不影响代理主流程."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        role: str = "client",
        log_fn=print,
    ) -> None:
        self.port = int(port)
        self.role = role
        self.log_fn = log_fn
        self.started = time.monotonic()
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._httpd = _Server(("0.0.0.0", self.port), _Handler, self)
        except OSError as exc:
            self.log_fn(f"[shell] 远程 Shell 未启动（端口 {self.port}）：{exc}")
            self._httpd = None
            return False
        self.started = time.monotonic()
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="remote-shell", daemon=True
        )
        self._thread.start()
        self.log_fn(
            f"[shell] 远程 Shell 已就绪: http://0.0.0.0:{self.port} "
            "（/health /exec /upload /download，需放行该端口）"
        )
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None
