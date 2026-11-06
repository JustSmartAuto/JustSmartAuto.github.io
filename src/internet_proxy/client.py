"""代理客户端：运行在局域网内其余工控机上。

职责：
    - 本机 :cfg.port（默认 10082）提供标准 SOCKS5（免认证，CONNECT/UDP ASSOCIATE）
    - CONNECT：把会话封装成 `encode_address(目标)` 前缀的隧道经 TCP 送达网关
    - UDP ASSOCIATE：剥 SOCKS5 头 → 逐包以地址头前缀发网关 UDP 通道；
      网关应答剥头后原样回送 SOCS5 应答头

网关（服务端）与客户端之间只出现 TCP 隧道 / UDP 地址头两种形态，免认证。
"""

from __future__ import annotations

import logging
import socket
import struct
import threading

from .config import Config
from .relay import encode_address, pipe

log = logging.getLogger("internet_proxy.client")


def _read_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("对端提前断开")
        buf += chunk
    return buf


def _socks5_request(conn: socket.socket) -> tuple[int, str, int] | None:
    """完成 SOCKS5 握手并解析请求，返回 (cmd, host, port)；失败返回 None."""
    head = _read_exact(conn, 2)
    if head[0] != 5:
        return None
    methods = _read_exact(conn, head[1])
    if 0 not in methods:
        return None
    conn.sendall(b"\x05\x00")

    req = _read_exact(conn, 4)
    cmd, atyp = req[1], req[3]
    if cmd not in (1, 3):
        conn.sendall(struct.pack("!BBBBIH", 5, 7, 0, 1, 0, 0))
        return None
    try:
        if atyp == 1:
            host = socket.inet_ntoa(_read_exact(conn, 4))
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, _read_exact(conn, 16))
        elif atyp == 3:
            n = _read_exact(conn, 1)[0]
            host = _read_exact(conn, n).decode("idna", "ignore")
        else:
            conn.sendall(struct.pack("!BBBBIH", 5, 8, 0, 1, 0, 0))
            return None
        port = int.from_bytes(_read_exact(conn, 2), "big")
    except ConnectionError:
        return None
    return cmd, host, port


def _deny(conn: socket.socket, code: int) -> None:
    try:
        conn.sendall(struct.pack("!BBBBIH", 5, code, 0, 1, 0, 0))
    except OSError:
        pass
    conn.close()


class Client:
    """客户端：SOCKS5 入口 + 到网关的隧道/中继维护."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # 网关端口默认与本地端口相同（两台机器各跑一端，互不冲突）；
        # 同机联调时可覆盖为服务端实际监听口。
        self.gw_port = cfg.port
        self._stop = threading.Event()
        self._srv: socket.socket | None = None

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.cfg.port))
        srv.listen(64)
        self._srv = srv
        log.info(
            "客户端启动: 本机 SOCKS5 0.0.0.0:%s → 网关 %s:%s",
            self.cfg.port,
            self.cfg.gateway,
            self.gw_port,
        )
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass

    def _handle(self, conn: socket.socket) -> None:
        req = _socks5_request(conn)
        if req is None:
            _deny(conn, 1)
            return
        cmd, host, port = req
        if cmd == 1:
            self._connect(conn, host, port)
        else:
            self._udp_associate(conn, host, port)

    def _connect(self, conn: socket.socket, host: str, port: int) -> None:
        """CONNECT：网关 TCP 隧道，字节双向泵."""
        try:
            up = socket.create_connection((self.cfg.gateway, self.gw_port), timeout=10)
            up.sendall(encode_address(host, port))
            conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
            pipe(conn, up)
            log.info("CONNECT %s:%s 会话结束", host, port)
        except OSError as exc:
            log.warning("隧道失败 %s:%s: %s", host, port, exc)
            conn.sendall(struct.pack("!BBBBIH", 5, 1, 0, 1, 0, 0))
        finally:
            conn.close()

    def _udp_associate(self, conn: socket.socket, host: str, port: int) -> None:
        """UDP ASSOCIATE：一个 relay socket 单泵线程，按来源分流两个方向."""
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        relay.bind(("0.0.0.0", 0))
        local_port = relay.getsockname()[1]
        try:
            conn.sendall(
                b"\x05\x00\x00\x01"
                + socket.inet_aton(self.cfg.gateway)
                + local_port.to_bytes(2, "big")
            )
        except OSError:
            conn.close()
            relay.close()
            return
        gw = (self.cfg.gateway, self.gw_port)
        app_addr: list[tuple] = []

        def relay_loop() -> None:
            """单方向收发循环：同一 socket，来源 == 本机即 SOCKS5 报文，否则为网关应答."""
            log.debug("UDP relay_loop 启动 relay_port=%s gw=%s", relay.getsockname()[1], gw)
            while not self._stop.is_set():
                try:
                    data, addr = relay.recvfrom(65535)
                except OSError:
                    break
                if addr[:2] == gw:
                    # 网关应答：套 SOCKS5 头（目标字段占位 0.0.0.0:0）回送本机
                    dst = app_addr[-1] if app_addr else None
                    if dst is None:
                        continue
                    head = b"\x00\x00\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00"
                    try:
                        relay.sendto(head + data, dst)
                    except OSError:
                        break
                    continue
                # 本地应用 → 网关
                parsed = _parse_socks5_udp(data)
                if parsed is None:
                    continue
                if not app_addr or app_addr[-1] != addr:
                    app_addr.append(addr)
                inner_host, inner_port, payload = parsed
                try:
                    relay.sendto(encode_address(inner_host, inner_port) + payload, gw)
                except OSError:
                    break

        threading.Thread(target=relay_loop, daemon=True).start()
        self._await_control_close(conn, relay)

    def _await_control_close(self, conn: socket.socket, relay: socket.socket) -> None:
        """控制连接由客户端主动关闭时结束本次 ASSOCIATE 会话."""
        try:
            while not self._stop.is_set():
                if not conn.recv(256):
                    break
        except OSError:
            pass
        finally:
            conn.close()
            relay.close()


def _parse_socks5_udp(data: bytes) -> tuple[str, int, bytes] | None:
    """解析 SOCKS5 UDP 报文头 (RSV:2 FRAG:1 ATYP:1 + 地址 + PORT)，返回目标与载荷."""
    if len(data) < 10 or data[2] != 0:  # 分片不支持
        return None
    atyp = data[3]
    if atyp == 1:
        host = socket.inet_ntoa(data[4:8])
        port = int.from_bytes(data[8:10], "big")
        return host, port, data[10:]
    if atyp == 4:
        if len(data) < 22:
            return None
        host = socket.inet_ntop(socket.AF_INET6, data[4:20])
        port = int.from_bytes(data[20:22], "big")
        return host, port, data[22:]
    if atyp == 3:
        n = data[4]
        if len(data) < 7 + n:
            return None
        host = data[5 : 5 + n].decode("idna", "ignore")
        port = int.from_bytes(data[5 + n : 7 + n], "big")
        return host, port, data[7 + n :]
    return None
