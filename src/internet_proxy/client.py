"""代理客户端：运行在局域网内其余工控机上。

职责：
    - 本机 :cfg.port（默认 10082）提供标准 SOCKS5（免认证，CONNECT/UDP ASSOCIATE）
    - CONNECT：把会话封装成 `encode_address(目标)` 前缀的隧道经 TCP 送达网关
    - UDP ASSOCIATE：剥 SOCKS5 头 → 逐包以地址头前缀发网关 UDP 通道；
      网关应答剥头后原样回送 SOCS5 应答头

网关（服务端）与客户端之间只出现 TCP 隧道 / UDP 地址头两种形态，免认证。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import threading

from .config import Config
from .relay import encode_address, pipe

log = logging.getLogger("internet_proxy.client")

UDP_FLOW_IDLE = 300  # 单目标上行 socket 空闲保留时长（秒）


def is_lan_host(host: str) -> bool:
    """是否局域网/本机地址：规则代理模式下这些目标直连，不经网关."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False  # 域名一律交给网关解析
    return ip.is_private or ip.is_loopback or ip.is_link_local


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


def _socks5_udp_header(host: str, port: int) -> bytes:
    """构造 SOCKS5 UDP 应答头（RSV:2 FRAG:1 ATYP + 远端地址 + 端口）.

    这里必须填真实远端地址：tun2socks 等客户端按它匹配 NAT 映射，
    填 0.0.0.0:0 会被判为无法匹配而丢包。
    """
    raw_port = port.to_bytes(2, "big")
    try:
        return b"\x00\x00\x00\x01" + socket.inet_pton(socket.AF_INET, host) + raw_port
    except OSError:
        try:
            return b"\x00\x00\x00\x04" + socket.inet_pton(socket.AF_INET6, host) + raw_port
        except OSError:
            raw = host.encode("idna") if host.isascii() else host.encode("utf-8")
            return b"\x00\x00\x00\x03" + bytes((len(raw),)) + raw + raw_port


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
        """CONNECT：规则模式下局域网目标直连，其余经网关 TCP 隧道双向泵."""
        direct = self.cfg.proxy_mode == "rule" and is_lan_host(host)
        try:
            if direct:
                up = socket.create_connection((host, port), timeout=10)
                log.info("直连(局域网) %s:%s", host, port)
            else:
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
        """UDP ASSOCIATE：每个目标独立上行 socket，应答回填真实源地址.

        网关的回包不带目标信息，若所有目标共用一个上行 socket 就无法判断
        应答属于哪个目标，只能把 SOCKS5 头写成 0.0.0.0:0；tun2socks 会把
        这种包当作“无法匹配 NAT 映射”丢弃（日志 drop packet from 0.0.0.0:0）。
        按目标拆 socket 后，回包所在的 socket 即代表目标，应答头可填真实地址。
        """
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        relay.bind(("0.0.0.0", 0))
        local_port = relay.getsockname()[1]
        try:
            # BND.ADDR 必须是客户端本机（应用把 UDP 报文发到这里的中继端口），
            # 填网关 IP 会导致应用把报文直接发到网关，DNS 等 UDP 必然失败。
            bnd_addr = conn.getsockname()[0]
            conn.sendall(
                b"\x05\x00\x00\x01"
                + socket.inet_aton(bnd_addr)
                + local_port.to_bytes(2, "big")
            )
        except OSError:
            conn.close()
            relay.close()
            return
        gw = (self.cfg.gateway, self.gw_port)
        app_addr: list[tuple] = []
        flows: dict[tuple[str, int], socket.socket] = {}
        flows_lock = threading.Lock()
        closed = threading.Event()
        direct_holder: list[socket.socket] = []  # 规则模式下局域网目标的直连 UDP socket

        def reply_to_app(src_host: str, src_port: int, data: bytes) -> None:
            dst = app_addr[-1] if app_addr else None
            if dst is None:
                return
            head = _socks5_udp_header(src_host, src_port)
            try:
                relay.sendto(head + data, dst)
            except OSError:
                pass

        def upstream_loop(sock: socket.socket, target: tuple[str, int]) -> None:
            """网关应答：socket 专属某个目标，按该目标回填应答头."""
            sock.settimeout(UDP_FLOW_IDLE)
            while not self._stop.is_set() and not closed.is_set():
                try:
                    data, _ = sock.recvfrom(65535)
                except (OSError, socket.timeout):
                    break
                reply_to_app(target[0], target[1], data)
            with flows_lock:
                if flows.get(target) is sock:
                    flows.pop(target, None)
            try:
                sock.close()
            except OSError:
                pass

        def upstream_for(target: tuple[str, int]) -> socket.socket:
            with flows_lock:
                sock = flows.get(target)
                if sock is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.bind(("0.0.0.0", 0))
                    flows[target] = sock
                    threading.Thread(
                        target=upstream_loop, args=(sock, target), daemon=True
                    ).start()
                return sock

        def direct_loop(sock: socket.socket) -> None:
            """局域网直连应答：recvfrom 的来源就是真实源，直接回填."""
            while not self._stop.is_set() and not closed.is_set():
                try:
                    data, addr = sock.recvfrom(65535)
                except OSError:
                    break
                reply_to_app(addr[0], addr[1], data)

        def direct_sock() -> socket.socket:
            if not direct_holder:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("0.0.0.0", 0))
                direct_holder.append(sock)
                threading.Thread(target=direct_loop, args=(sock,), daemon=True).start()
            return direct_holder[0]

        def relay_loop() -> None:
            """应用 → 目标：规则模式下局域网目标直连，其余经网关上行 socket."""
            log.debug("UDP relay_loop 启动 relay_port=%s gw=%s", relay.getsockname()[1], gw)
            while not self._stop.is_set() and not closed.is_set():
                try:
                    data, addr = relay.recvfrom(65535)
                except OSError:
                    break
                parsed = _parse_socks5_udp(data)
                if parsed is None:
                    continue
                if not app_addr or app_addr[-1] != addr:
                    app_addr.append(addr)
                inner_host, inner_port, payload = parsed
                try:
                    if (
                        self.cfg.proxy_mode == "rule"
                        and is_lan_host(inner_host)
                        and inner_port != 53
                    ):
                        direct_sock().sendto(payload, (inner_host, inner_port))
                    else:
                        upstream_for((inner_host, inner_port)).sendto(
                            encode_address(inner_host, inner_port) + payload, gw
                        )
                except OSError:
                    break

        threading.Thread(target=relay_loop, daemon=True).start()
        self._await_control_close(conn, relay)
        closed.set()
        with flows_lock:
            sockets = list(flows.values())
            flows.clear()
        for sock in sockets + direct_holder:
            try:
                sock.close()
            except OSError:
                pass

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
