"""TCP/UDP 双引擎：线程化双向泵与游标。

- pipe(reader, writer)：TCP 两个 socket 之间的双向字节流转发
- dgram_relay(sock_a, sock_b)：UDP 两个套接字之间的双向报文转发

客户端把 SOCKS5 CONF 或 UDP 控制报文的原始目标地址封装进首字段，用
_variable_length_address() 编码成 1~17 字节随首包发送；服务端解码后建立
到真实目标的上游连接/报文通道，之后字节/报文原样对传。
"""

from __future__ import annotations

import socket
import threading

CHUNK = 65536


def encode_address(host: str, port: int) -> bytes:
    """变长地址编码：类型(1) + 地址 + 端口(2)，总长随地址变化."""
    if host.endswith("]"):
        host = host[1:-1]
    try:
        packed = socket.inet_pton(socket.AF_INET6, host)
        head = b"\x05" + packed
    except OSError:
        try:
            packed = socket.inet_pton(socket.AF_INET, host)
            head = b"\x01" + packed
        except OSError:
            raw = host.encode("idna") if host.isascii() else host.encode("utf-8")
            head = bytes((0x03, len(raw))) + raw
    return head + port.to_bytes(2, "big")


def decode_address(payload: bytes) -> tuple[str, int, int]:
    """解码 encode_address 输出；返回 (host, port, 消耗的字节数)."""
    atyp = payload[0]
    if atyp == 0x01:
        host = socket.inet_ntoa(payload[1:5])
        return host, int.from_bytes(payload[5:7], "big"), 7
    if atyp == 0x05:
        host = socket.inet_ntop(socket.AF_INET6, payload[1:17])
        return host, int.from_bytes(payload[17:19], "big"), 19
    if atyp == 0x03:
        n = payload[1]
        raw = payload[2 : 2 + n]
        host = raw.decode("ascii") if raw.isascii() else raw.decode("utf-8")
        return host, int.from_bytes(payload[2 + n : 4 + n], "big"), 4 + n
    raise ValueError(f"未知地址类型 {atyp:#x}")


def _spawn(fn, *args) -> threading.Thread:
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


def pipe(a: socket.socket, b: socket.socket) -> None:
    """a→b 与 b→a 同时开泵，并阻断到任一方关闭。"""
    ok = threading.Event()

    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(CHUNK)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            ok.set()
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    _spawn(_pump, a, b)
    _spawn(_pump, b, a)
    ok.wait()


def dgram_relay(sock_a: socket.socket, sock_b: socket.socket, peer_b=None) -> None:
    """UDP 双向泵；一个 socket 收到报文就转发到对端."""

    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data, addr = src.recvfrom(65535)
                dst.sendto(data, peer_b or addr)
        except OSError:
            pass

    _spawn(_pump, sock_a, sock_b)
    _spawn(_pump, sock_b, sock_a)
