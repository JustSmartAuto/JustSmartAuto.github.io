"""代理服务端：运行在唯一可上网的工控机上。

协议（全部免认证）：
    - TCP 通道（默认端口 10082）：客户端把 SOCKS5 会话封装成
      `encode_address(目标)` 前缀的隧道，首包内嵌真实目标，之后字节原样泵接
    - UDP 通道（默认端口 10082）：每个报文都以 `encode_address(目标)` 为前缀，
      服务端按 (客户端来源, 目标) 缓存上游 UDP socket 并回送应答

服务端只负责"把局域网内客户端的请求转发到真实外网"，原理与 VPN 近似。
"""

from __future__ import annotations

import logging
import socket
import threading

from .relay import decode_address, encode_address, pipe

log = logging.getLogger("internet_proxy.server")

UDP_FLOW_TTL = 300  # 上游 UDP socket 空闲保留时长（秒）


def _tcp_channel(conn: socket.socket) -> None:
    """处理单个客户端 TCP 隧道：首包内嵌真实目标，之后的字节原样双向泵."""
    try:
        header = conn.recv(19)  # 变长地址最长 19 字节
        if not header:
            return
        host, port, used = decode_address(header)
        log.info("TCP %s → %s:%s", conn.getpeername()[0], host, port)
        up = socket.create_connection((host, port), timeout=10)
        if used < len(header):
            up.sendall(header[used:])  # 首包携带的正文回填给上游
        pipe(conn, up)
    except OSError as exc:
        log.warning("TCP 通道异常: %s", exc)
    finally:
        try:
            conn.close()
        except OSError:
            pass


class _UdpChannel:
    """UDP 中继：按来源报文头解析目标，为每条流维持一个上游 socket."""

    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self.flows: dict[tuple[tuple, tuple], socket.socket] = {}
        self.lock = threading.Lock()

    def attach(self, sock: socket.socket) -> None:
        self.sock = sock
        threading.Thread(target=self._pump_insock, name="udp-insock", daemon=True).start()

    def _flow(self, src: tuple, target: tuple) -> socket.socket:
        """取/建 (客户端来源, 目标) 对应的上游 UDP socket。"""
        with self.lock:
            up = self.flows.get((src, target))
            if up is None:
                up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.flows[(src, target)] = up
                threading.Thread(
                    target=self._pump_reply, args=(up, src, target), daemon=True
                ).start()
            return up

    def _pump_insock(self) -> None:
        """客户端 → 外网：剥地址头后转发。"""
        while True:
            try:
                data, src = self.sock.recvfrom(65535)
                log.debug("UDP 收到 %d 字节 from %s", len(data), src)
            except OSError:
                break
            try:
                host, port, used = decode_address(data)
                payload = data[used:]
                target = (host, port)
                up = self._flow(src, target)
                up.sendto(payload, target)
            except (OSError, IndexError, ValueError) as exc:
                log.warning("UDP 转发失败: %s", exc)

    def _pump_reply(self, up: socket.socket, src: tuple, target: tuple) -> None:
        """外网 → 客户端：剥掉自身的报文后原样回送。"""
        try:
            up.settimeout(UDP_FLOW_TTL)
            while True:
                data, _ = up.recvfrom(65535)
                self.sock.sendto(data, src)
        except (OSError, socket.timeout):
            pass
        finally:
            with self.lock:
                self.flows.pop((src, target), None)
            try:
                up.close()
            except OSError:
                pass


def serve(host: str = "0.0.0.0", port: int = 10082, stop: threading.Event | None = None) -> None:
    """监听 TCP/UDP 端口（默认 10082），为局域网客户端代转发.

    stop 置位时关闭监听 socket 并返回（已有会话线程为 daemon，随进程结束）。
    """
    log.info("服务端启动 TCP/UDP %s:%s", host, port)

    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind((host, port))
    tcp.listen(64)
    tcp.settimeout(0.5)

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind((host, port))
    channel = _UdpChannel()
    channel.attach(udp)

    try:
        while stop is None or not stop.is_set():
            try:
                conn, _ = tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=_tcp_channel, args=(conn,), daemon=True).start()
    finally:
        tcp.close()
        udp.close()
        log.info("服务端已停止")
