"""CLI 三角色入口: gui (默认), server, client.

依赖:
    localdropshell.server   (create_app, start_server, ServerConfig)
    localdropshell.shell    (ShellExecutor)
    localdropshell.vnc      (detect_vnc)

Python 3.10+
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import logging
from dataclasses import dataclass
from pathlib import Path

import click


# ---------------------------------------------------------------------------
# 可选依赖守卫 —— 缺 aiohttp / pywebview 时给出明确提示
# ---------------------------------------------------------------------------

def _check_essential_deps() -> None:
    missing: list[str] = []
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        missing.append("aiohttp")
    try:
        import click  # noqa: F401
    except ImportError:
        missing.append("click")
    if missing:
        click.echo(
            f"[ERROR] 缺少依赖: {', '.join(missing)}\n"
            f"请先运行 `uv sync` 安装依赖",
            err=True,
        )
        sys.exit(2)


def _echo_safe(message: str) -> None:
    """--windowed 打包（无 stdout）时也能安全输出，避免启动阶段抛异常."""
    try:
        click.echo(message)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 延迟导入的重量级依赖
# ---------------------------------------------------------------------------

def _import_server_stuff():
    """导入 server / vnc / shell 模块. 失败时打印友好提示."""
    try:
        from localdropshell.server import create_app, start_server, ServerConfig
        from localdropshell.shell import ShellExecutor
        from localdropshell.vnc import detect_vnc
        return create_app, start_server, ServerConfig, ShellExecutor, detect_vnc
    except ImportError as exc:
        click.echo(
            f"[ERROR] 导入 localdropshell.* 模块失败: {exc}\n"
            f"请确认已在项目根目录运行 `uv sync` 安装依赖",
            err=True,
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# run_server
# ---------------------------------------------------------------------------

def run_server(host: str | None = None, port: int | None = None) -> None:
    """启动纯 aiohttp 服务端, 打印 banner, Ctrl+C 优雅退出."""
    _create_app, start_server, ServerConfig, _ShellExecutor, detect_vnc = _import_server_stuff()

    # 参数 > env > 硬编码默认
    cfg_host = host or os.environ.get("HTTP_SHELL_HOST", "0.0.0.0")
    cfg_port = port or _parse_env_int("HTTP_SHELL_PORT", 10022)

    cfg = ServerConfig(host=cfg_host, port=cfg_port)

    # 工控机首次部署：未安装 TightVNC 时用内嵌 MSI 静默提权安装（失败不阻塞启动）
    try:
        from localdropshell.vnc import ensure_tightvnc_installed

        ensure_tightvnc_installed(log=_echo_safe)
    except Exception as exc:
        _echo_safe(f"[tightvnc] 自动安装异常: {exc}")

    # 启动前先探测 VNC (用于 CLI 级 banner)
    vnc_info = detect_vnc()

    async def _main() -> None:
        runner, _site = await start_server(cfg)
        click.echo(f"LocalFileDropShell 服务端已启动 http://{cfg.host}:{cfg.port}")
        if vnc_info and getattr(vnc_info, "port", None):
            click.echo(
                f"  VNC Server: port={vnc_info.port} "
                f"process={vnc_info.process_name or 'unknown'} "
                f"mode={vnc_info.mode}"
            )
        else:
            click.echo("  VNC Server: 未检测到")
        click.echo("按 Ctrl+C 退出")
        try:
            await asyncio.Future()  # 永久阻塞
        finally:
            await runner.cleanup()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        click.echo("\n正在退出...")
        sys.exit(0)


# ---------------------------------------------------------------------------
# run_client —— 单次命令 & 交互式 shell
# ---------------------------------------------------------------------------

def run_client(
    url: str | None = None,
    host: str | None = None,
    port: int | None = None,
    command: str | None = None,
) -> None:
    """客户端入口.

    - command 非空 → 单次 POST /exec, 打印 stdout/stderr 后退出
    - command 为空 → 进入交互式 shell, 逐行处理 exit/cd/pwd/clear/!/
                     upload/download/rsync/rsync-pull/rfb-* / 普通命令
    """
    try:
        import aiohttp
    except ImportError:
        click.echo("[ERROR] 客户端需要 aiohttp, 请先运行 `uv sync`", err=True)
        sys.exit(2)

    # 组装 base_url
    base_url: str
    if url:
        base_url = url.rstrip("/")
    else:
        h = host or "127.0.0.1"
        p = port or 10022
        base_url = f"http://{h}:{p}"

    if command is not None:
        # ---- 单次命令 ----
        sys.exit(_one_shot(base_url, command))
    else:
        # ---- 交互式 ----
        try:
            asyncio.run(_interactive(base_url))
        except KeyboardInterrupt:
            click.echo("\n正在退出...")
            sys.exit(0)


# ---------- 单次命令 ----------

def _one_shot(base_url: str, command: str) -> int:
    """单次 POST /exec, 打印输出, 返回 exit_code."""
    import aiohttp

    async def _run() -> int:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/exec",
                    json={"command": command, "timeout": 30},
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        click.echo(f"[ERROR] HTTP {resp.status}: {text}", err=True)
                        return 1
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            click.echo(f"[ERROR] 连接 {base_url} 失败: {exc}", err=True)
            return 1
        except Exception as exc:
            click.echo(f"[ERROR] {exc}", err=True)
            return 1

        click.echo(data.get("stdout", ""), nl=False)
        click.echo(data.get("stderr", ""), nl=False, err=True)
        return data.get("exit_code", 0) or 0

    return asyncio.run(_run())


# ---------- 交互式 shell ----------

async def _interactive(base_url: str) -> None:
    """交互式客户端: 逐行读入, 支持 exit/cd/pwd/clear/!/upload/download/rsync/rfb-*.

    整个会话保持同一个 aiohttp.ClientSession.
    """
    import aiohttp

    work_dir: str = os.getcwd()  # 客户端侧维护

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                line = input("ldrop> ")
            except EOFError:
                click.echo()
                return
            except KeyboardInterrupt:
                click.echo("\n正在退出...")
                sys.exit(0)

            raw = line.strip()
            if not raw:
                continue

            # ---- exit / quit / q ----
            if raw.lower() in ("exit", "quit", "q"):
                return

            # ---- clear / cls ----
            if raw.lower() in ("clear", "cls"):
                click.clear()
                continue

            # ---- cd ----
            if raw.lower().startswith("cd"):
                parts = raw.split(None, 1)
                if len(parts) < 2:
                    # 无参数: 打印当前 work_dir
                    click.echo(work_dir)
                else:
                    new_dir = parts[1].strip()
                    try:
                        # 先本地尝试 (若本地存在)
                        candidate = Path(new_dir).expanduser()
                        if candidate.is_dir():
                            os.chdir(candidate)
                            work_dir = os.getcwd()
                            click.echo(work_dir)
                        else:
                            # 远程: 通过 /exec 试一下
                            async with session.post(
                                f"{base_url}/exec",
                                json={"command": f"cd {new_dir} && cd", "timeout": 10},
                            ) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    out = (
                                        data.get("stdout", "").strip()
                                        or data.get("stderr", "").strip()
                                    )
                                    if out:
                                        work_dir = out.splitlines()[-1].strip()
                                        click.echo(work_dir)
                                    else:
                                        click.echo(f"[cd] 无法切换到 {new_dir!r}", err=True)
                                else:
                                    click.echo(
                                        f"[ERROR] HTTP {resp.status}",
                                        err=True,
                                    )
                    except Exception as exc:
                        click.echo(f"[cd] {exc}", err=True)
                continue

            # ---- pwd ----
            if raw.lower() == "pwd":
                click.echo(work_dir)
                continue

            # ---- 本地 !<cmd> ----
            if raw.startswith("!"):
                local_cmd = raw[1:].strip()
                if not local_cmd:
                    continue
                try:
                    subprocess.run(local_cmd, shell=True, cwd=work_dir)
                except Exception as exc:
                    click.echo(f"[local] {exc}", err=True)
                continue

            # ---- upload <local> [remote] ----
            if raw.lower().startswith("upload"):
                parts = raw.split(None, 2)
                if len(parts) < 2:
                    click.echo("用法: upload <local> [remote]", err=True)
                    continue
                local = Path(parts[1]).expanduser()
                remote = parts[2] if len(parts) >= 3 else str(local.name)
                await _do_upload(session, base_url, str(local), remote)
                continue

            # ---- download <remote> [local] ----
            if raw.lower().startswith("download"):
                parts = raw.split(None, 2)
                if len(parts) < 2:
                    click.echo("用法: download <remote> [local]", err=True)
                    continue
                remote = parts[1]
                local = Path(parts[2] if len(parts) >= 3 else Path(remote).name).expanduser()
                await _do_download(session, base_url, remote, str(local))
                continue

            # ---- delete <path> ----
            if raw.lower().startswith("delete"):
                parts = raw.split(None, 1)
                if len(parts) < 2:
                    click.echo("用法: delete <path>", err=True)
                    continue
                await _do_delete(session, base_url, parts[1].strip())
                continue

            # ---- rsync <local> [remote] ----
            if raw.lower().startswith("rsync ") or raw.lower() == "rsync":
                parts = raw.split(None, 2)
                if len(parts) < 2:
                    click.echo("用法: rsync <local> [remote]  (push)", err=True)
                    continue
                local = Path(parts[1]).expanduser()
                remote = parts[2] if len(parts) >= 3 else str(local.name)
                await _do_rsync_push(session, base_url, str(local), remote)
                continue

            # ---- rsync-pull <remote> [local] ----
            if raw.lower().startswith("rsync-pull"):
                parts = raw.split(None, 2)
                if len(parts) < 2:
                    click.echo("用法: rsync-pull <remote> [local]", err=True)
                    continue
                remote = parts[1]
                local = Path(parts[2] if len(parts) >= 3 else Path(remote).name).expanduser()
                await _do_rsync_pull(session, base_url, remote, str(local))
                continue

            # ---- rfb-list [dir] ----
            if raw.lower().startswith("rfb-list"):
                parts = raw.split(None, 1)
                d = parts[1].strip() if len(parts) >= 2 else "/"
                await _do_rfb_list(session, base_url, d)
                continue

            # ---- rfb-upload <local> [remote] ----
            if raw.lower().startswith("rfb-upload"):
                parts = raw.split(None, 2)
                if len(parts) < 2:
                    click.echo("用法: rfb-upload <local> [remote]", err=True)
                    continue
                local = Path(parts[1]).expanduser()
                remote = parts[2] if len(parts) >= 3 else str(local.name)
                await _do_upload(session, base_url, str(local), remote, rfb=True)
                continue

            # ---- rfb-download <remote> [local] ----
            if raw.lower().startswith("rfb-download"):
                parts = raw.split(None, 2)
                if len(parts) < 2:
                    click.echo("用法: rfb-download <remote> [local]", err=True)
                    continue
                remote = parts[1]
                local = Path(parts[2] if len(parts) >= 3 else Path(remote).name).expanduser()
                await _do_download(session, base_url, remote, str(local), rfb=True)
                continue

            # ---- help ----
            if raw.lower() in ("help", "?"):
                click.echo(_HELP_TEXT)
                continue

            # ---- 默认: POST /exec 执行远程命令 ----
            try:
                async with session.post(
                    f"{base_url}/exec",
                    json={"command": raw, "timeout": 120, "work_dir": work_dir},
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        click.echo(f"[ERROR] HTTP {resp.status}: {text}", err=True)
                        continue
                    data = await resp.json()
                    click.echo(data.get("stdout", ""), nl=False)
                    click.echo(data.get("stderr", ""), nl=False, err=True)
                    # 同步 work_dir (远程端可能 cd 了)
                    new_cwd = data.get("work_dir")
                    if new_cwd and new_cwd != work_dir:
                        work_dir = new_cwd
            except aiohttp.ClientError as exc:
                click.echo(f"[ERROR] 网络异常: {exc}", err=True)
            except Exception as exc:
                click.echo(f"[ERROR] {exc}", err=True)


_HELP_TEXT = """\
可用命令:
  exit | quit | q            退出
  clear | cls                清屏
  help | ?                   显示帮助
  cd <dir>                   切换工作目录 (本地或远程)
  pwd                        打印当前工作目录
  !<cmd>                     在本地 subprocess 执行 <cmd>
  upload    <local> [remote] 本地文件上传到远程
  download  <remote> [local] 从远程下载到本地
  delete    <path>           删除远程文件/目录
  rsync     <local> [remote] 三阶段增量同步 (push)
  rsync-pull <remote> [local] 三阶段增量同步 (pull)
  rfb-list     [dir]         列出远程目录 (tightvnc 优先)
  rfb-upload   <local> [remote] RFB FTP 上传
  rfb-download <remote> [local] RFB FTP 下载
  其它任意输入               作为远程 shell 命令执行
"""


# ---------- 上传 ----------

async def _do_upload(
    session,
    base_url: str,
    local_path: str,
    remote_path: str,
    rfb: bool = False,
) -> None:
    import aiohttp

    lp = Path(local_path)
    if not lp.is_file():
        click.echo(f"[upload] 本地文件不存在: {lp}", err=True)
        return

    url = f"{base_url}/rfb/file/upload" if rfb else f"{base_url}/upload"
    file_size = lp.stat().st_size

    try:
        with click.progressbar(
            length=file_size,
            label=f"上传 {lp.name}",
            show_pos=True,
        ) as bar:
            def _chunk_reader():
                with open(lp, "rb") as f:
                    while True:
                        buf = f.read(64 * 1024)
                        if not buf:
                            break
                        bar.update(len(buf))
                        yield buf

            form = aiohttp.FormData()
            form.add_field("path", remote_path)
            form.add_field("file", _chunk_reader(), filename=lp.name)

            async with session.post(url, data=form) as resp:
                text = await resp.text()
                if resp.status != 200:
                    click.echo(f"[upload] HTTP {resp.status}: {text}", err=True)
                else:
                    try:
                        data = await resp.json()
                        click.echo(
                            f"[upload] 完成 -> {remote_path} "
                            f"({data.get('size', file_size)} bytes)"
                        )
                    except Exception:
                        click.echo(f"[upload] 完成 -> {remote_path}")
    except aiohttp.ClientError as exc:
        click.echo(f"[upload] 网络异常: {exc}", err=True)


# ---------- 下载 ----------

async def _do_download(
    session,
    base_url: str,
    remote_path: str,
    local_path: str,
    rfb: bool = False,
) -> None:
    import aiohttp

    url = f"{base_url}/rfb/file/download" if rfb else f"{base_url}/download"

    try:
        async with session.post(url, json={"path": remote_path}) as resp:
            if resp.status != 200:
                text = await resp.text()
                click.echo(f"[download] HTTP {resp.status}: {text}", err=True)
                return

            # 从响应头拿文件大小
            total = resp.headers.get("Content-Length")
            file_size = int(total) if total and total.isdigit() else 0

            out_path = Path(local_path).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with click.progressbar(
                length=file_size or 1,
                label=f"下载 {Path(remote_path).name}",
                show_pos=True,
            ) as bar:
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_any():
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))

            click.echo(f"[download] 完成 -> {out_path}")
    except aiohttp.ClientError as exc:
        click.echo(f"[download] 网络异常: {exc}", err=True)


# ---------- 删除 ----------

async def _do_delete(session, base_url: str, path: str) -> None:
    import aiohttp

    try:
        async with session.post(f"{base_url}/delete", json={"path": path}) as resp:
            if resp.status != 200:
                text = await resp.text()
                click.echo(f"[delete] HTTP {resp.status}: {text}", err=True)
            else:
                data = await resp.json()
                click.echo(f"[delete] {path} -> {'ok' if data.get('deleted') else 'fail'}")
    except aiohttp.ClientError as exc:
        click.echo(f"[delete] 网络异常: {exc}", err=True)


# ---------- rsync push (local -> remote) ----------

async def _do_rsync_push(
    session, base_url: str, local_path: str, remote_path: str
) -> None:
    """三阶段 rsync:
    1. 服务端对远端文件生成 blockset (若远端不存在则跳到 3 直接 upload)
    2. 客户端用 blockset 对本地文件做 delta → 服务端 apply
    3. 远端不存在或 fallback: 直接 upload
    """
    import aiohttp

    lp = Path(local_path)
    if not lp.is_file():
        click.echo(f"[rsync] 本地文件不存在: {lp}", err=True)
        return

    # 阶段 1: 请求远端 blockset
    try:
        async with session.post(
            f"{base_url}/rsync/blockset",
            json={"path": remote_path, "seed": 0},
        ) as resp:
            if resp.status == 404:
                # 远端无文件 → 回退到 upload
                click.echo(f"[rsync] 远端无 {remote_path}, 回退为完整上传")
                await _do_upload(session, base_url, local_path, remote_path)
                return
            if resp.status != 200:
                text = await resp.text()
                click.echo(f"[rsync] 阶段1失败 HTTP {resp.status}: {text}", err=True)
                return
            bs_data = await resp.json()
            blockset = bs_data.get("blockset")
            click.echo(f"[rsync] 远端 blockset 获取成功, blocks={len(blockset or [])}")
    except aiohttp.ClientError as exc:
        click.echo(f"[rsync] 网络异常: {exc}", err=True)
        return

    # 阶段 2 (简化): 客户端直接把整个本地文件送去服务器做 delta
    # 更完整的版本会在客户端本地调 rsync.compute_delta; 这里走
    # 服务端 delta handler (需要 src_path 在服务端也可见, 通常不可达),
    # 所以实际 push 还是走 upload. 留接口占位, 真正 delta 路径需要
    # 客户端先 build_block_set → 本地 compute_delta → apply.
    click.echo("[rsync] 当前实现走完整上传 (delta 路径待完善)")
    await _do_upload(session, base_url, local_path, remote_path)


# ---------- rsync pull (remote -> local) ----------

async def _do_rsync_pull(
    session, base_url: str, remote_path: str, local_path: str
) -> None:
    """拉方向: 先下载 blockset, 若本地存在则做 delta apply, 否则完整下载."""
    import aiohttp

    lp = Path(local_path).expanduser()

    # 先看本地是否已有文件
    if lp.is_file():
        click.echo(f"[rsync-pull] 本地已有 {lp}, 当前走完整覆盖下载")
    else:
        click.echo(f"[rsync-pull] 本地无 {lp}, 完整下载")
    await _do_download(session, base_url, remote_path, local_path)


# ---------- rfb list ----------

async def _do_rfb_list(session, base_url: str, path: str) -> None:
    import aiohttp

    try:
        async with session.post(
            f"{base_url}/rfb/file/list",
            json={"path": path},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                click.echo(f"[rfb-list] HTTP {resp.status}: {text}", err=True)
                return
            data = await resp.json()
            entries = data.get("entries", [])
            click.echo(f"[{path}] 共 {len(entries)} 项:")
            for e in entries:
                flag = "d" if e.get("is_dir") else "-"
                size = e.get("size", 0)
                name = e.get("name", "")
                click.echo(f"  {flag}  {size:>12}  {name}")
    except aiohttp.ClientError as exc:
        click.echo(f"[rfb-list] 网络异常: {exc}", err=True)


# ---------------------------------------------------------------------------
# run_gui
# ---------------------------------------------------------------------------

def run_gui(host: str | None = None, port: int | None = None, folder: str | None = None) -> None:
    """启动后台 aiohttp 服务端 + Tk 主窗口."""
    _create_app, start_server, ServerConfig, _ShellExecutor, _detect_vnc = _import_server_stuff()

    cfg_host = host or os.environ.get("HTTP_SHELL_HOST", "0.0.0.0")
    cfg_port = port or _parse_env_int("HTTP_SHELL_PORT", 10022)

    # 本地 loop 端口固定为 10022 供 Tk UI 调用
    ui_port = port or 10022
    ui_host = "127.0.0.1"

    # 工控机首次部署：未安装 TightVNC 时用内嵌 MSI 静默提权安装（失败不阻塞启动）
    try:
        from localdropshell.vnc import ensure_tightvnc_installed

        ensure_tightvnc_installed(log=_echo_safe)
    except Exception as exc:
        _echo_safe(f"[tightvnc] 自动安装异常: {exc}")

    def _run_server_thread() -> None:
        asyncio.run(start_server(ServerConfig(host=cfg_host, port=cfg_port)))

    t = threading.Thread(target=_run_server_thread, daemon=True, name="localdropshell-server")
    t.start()
    # 给服务端一点时间启动
    time.sleep(0.5)

    base_url = f"http://{ui_host}:{ui_port}"

    try:
        from localdropshell.ui.tk_main import run_tk_gui  # type: ignore[attr-defined]
    except ImportError as exc:
        click.echo(
            f"[ERROR] 无法导入 UI 模块 localdropshell.ui.tk_main: {exc}\n"
            f"请先运行 `uv sync` 安装依赖",
            err=True,
        )
        sys.exit(2)

    try:
        run_tk_gui(base_url=base_url, host=host, folder=folder)
    except KeyboardInterrupt:
        click.echo("\n正在退出...")
        sys.exit(0)


# ---------------------------------------------------------------------------
# 环境变量端口解析
# ---------------------------------------------------------------------------

def _parse_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Click CLI group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option(
    "--role",
    type=click.Choice(["gui", "server", "client"]),
    default="gui",
    show_default=True,
    help="运行角色: gui(默认)/server/client",
)
@click.option("--host", default=None, help="server/client: 目标地址")
@click.option("--port", type=int, default=None, help="server/client: 目标端口")
@click.option("--url", default=None, help="client: 完整 http://host:port URL (可覆盖 host/port)")
@click.option(
    "-c",
    "--command",
    default=None,
    help="client: 单次执行的命令; 省略则进入交互式 shell",
)
@click.option("--folder", default=None, help="gui: 启动时载入本地文件夹")
@click.pass_context
def cli(ctx: click.Context, role: str, host: str | None, port: int | None,
        url: str | None, command: str | None, folder: str | None) -> None:
    """LocalFileDropShell — 一体化远程运维工具.

    默认角色: gui (Tk 主界面). 切换角色用 --role server 或 --role client.
    """
    _check_essential_deps()

    try:
        if role == "gui":
            run_gui(host=host, port=port, folder=folder)
        elif role == "server":
            run_server(host=host, port=port)
        elif role == "client":
            run_client(url=url, host=host, port=port, command=command)
    except KeyboardInterrupt:
        click.echo("\n正在退出...")
        sys.exit(0)
    except ImportError as exc:
        click.echo(
            f"[ERROR] 缺少依赖或模块: {exc}\n"
            f"请先运行 `uv sync` 安装依赖",
            err=True,
        )
        sys.exit(2)
    except Exception as exc:
        click.echo(f"[ERROR] {exc}", err=True)
        sys.exit(1)


def main() -> None:
    """pyproject.toml 脚本入口."""
    # pywebview 子进程入口：必须绕开 click，让 webview.start() 跑在 main thread
    from localdropshell.webview_win import WINDOW_ENTRY_FLAG, run_from_argv

    if len(sys.argv) > 1 and sys.argv[1] == WINDOW_ENTRY_FLAG:
        raise SystemExit(run_from_argv(sys.argv[2:]))
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
