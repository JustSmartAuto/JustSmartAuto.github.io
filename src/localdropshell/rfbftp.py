"""RFB FTP 子类型消息编解码（TightVNC 2.8.90 扩展协议）。

提供：
- 基础 struct 编解码（大端 / 小端）
- RFB FTP 消息子类型常量
- HTTP-like 包装的同步 Socket 层（send_request / recv_response）
- CompressedBlock 结构（TightVNC FTP 数据块压缩）
- 常用请求构造器（返回 body bytes）
- FtpSession 高层封装
- FtpError 异常
"""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------

class FtpError(Exception):
    """RFB FTP 操作失败。"""

    def __init__(self, message: str, rfb_subtype: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.rfb_subtype = rfb_subtype

    def __str__(self) -> str:
        if self.rfb_subtype is not None:
            return f"[FtpError subtype=0x{self.rfb_subtype:02X}] {self.message}"
        return self.message


# ---------------------------------------------------------------------------
# RFB FTP 消息子类型常量
# ---------------------------------------------------------------------------

CCSRSST = 0x11  # 压缩支持检查
CFLRST = 0x12  # 文件列表
CMDRST = 0x13  # 创建目录
CFRRST = 0x14  # 删除文件
CFMRST = 0x15  # 移动/重命名
CFURST = 0x16  # 开始上传
CUDRST = 0x17  # 上传数据块
CUERST = 0x18  # 上传结束
CFDRST = 0x19  # 开始下载
CDDRST = 0x1A  # 下载数据块
CDSRST = 0x1B  # 目录大小
CM5RST = 0x1C  # MD5 校验
LAST_REQUEST_FAILED = 0xFF  # 失败响应


# ---------------------------------------------------------------------------
# RFB FTP 扩展 ID（3 字节小端）
# ---------------------------------------------------------------------------

# TightVNC FTP 扩展 ID（常见值 0x000102，对应 TightVNC 的扩展号）
# 小端序列化后为 bytes([0x02, 0x01, 0x00])
TIGHTVNC_FTP_EXT_ID = 0x000102


# RFB 协议 prefix：Client-to-Server Extension 消息
# RFB 规范中，扩展消息以一个标识字节开头；TightVNC FTP 使用 0xFC 作为 prefix
RFB_FTP_PREFIX = 0xFC


# ---------------------------------------------------------------------------
# 基础编解码工具
# ---------------------------------------------------------------------------

def pack_uint8(v: int) -> bytes:
    return struct.pack("!B", v & 0xFF)


def pack_uint16be(v: int) -> bytes:
    return struct.pack("!H", v & 0xFFFF)


def pack_uint16le(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def pack_uint32be(v: int) -> bytes:
    return struct.pack("!I", v & 0xFFFFFFFF)


def pack_uint32le(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def pack_string_utf8(s: str) -> bytes:
    """UTF-8 编码字符串，附带 uint32be 长度前缀。"""
    data = s.encode("utf-8")
    return pack_uint32be(len(data)) + data


def unpack_uint8(buf: bytes, offset: int) -> tuple[int, int]:
    (v,) = struct.unpack_from("!B", buf, offset)
    return v, offset + 1


def unpack_uint16be(buf: bytes, offset: int) -> tuple[int, int]:
    (v,) = struct.unpack_from("!H", buf, offset)
    return v, offset + 2


def unpack_uint16le(buf: bytes, offset: int) -> tuple[int, int]:
    (v,) = struct.unpack_from("<H", buf, offset)
    return v, offset + 2


def unpack_uint32be(buf: bytes, offset: int) -> tuple[int, int]:
    (v,) = struct.unpack_from("!I", buf, offset)
    return v, offset + 4


def unpack_uint32le(buf: bytes, offset: int) -> tuple[int, int]:
    (v,) = struct.unpack_from("<I", buf, offset)
    return v, offset + 4


def unpack_string_utf8(buf: bytes, offset: int) -> tuple[str, int]:
    length, offset = unpack_uint32be(buf, offset)
    s = buf[offset:offset + length].decode("utf-8", errors="replace")
    return s, offset + length


# ---------------------------------------------------------------------------
# HTTP 包装协议（同步 Socket 层）
# ---------------------------------------------------------------------------

def _recv_exact(sock: socket.socket, n: int, timeout: float = 30.0) -> bytes:
    """从 socket 精确读取 n 字节。"""
    sock.settimeout(timeout)
    chunks = bytearray()
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise FtpError(f"连接关闭，期望 {n} 字节，仅收到 {len(chunks)}")
        chunks.extend(chunk)
        remaining -= len(chunk)
    return bytes(chunks)


def send_request(
    sock: socket.socket,
    subtype: int,
    body: bytes = b"",
    ext_id: int = TIGHTVNC_FTP_EXT_ID,
    prefix: int = RFB_FTP_PREFIX,
) -> None:
    """构造并发送完整的 RFB FTP 请求。

    布局: [prefix:uint8][ext_id:3bytes_LE][subtype:uint8][body:...]
    """
    header = (
        pack_uint8(prefix)
        + pack_uint32le(ext_id)[:3]  # 取小端最低 3 字节
        + pack_uint8(subtype)
    )
    sock.sendall(header + body)


def recv_response(
    sock: socket.socket,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    """读取完整的 RFB FTP 响应。

    返回 (subtype, body)。
    """
    # 先读 8 字节头: prefix(1) + ext_id(3) + subtype(1) + body_length(3)?
    # TightVNC FTP 响应头: prefix(1) + ext_id(3) + subtype(1) + body_len_be4?
    # 实际 TightVNC FTP 扩展响应头格式:
    #   [prefix:uint8][ext_id:3bytes_LE][subtype:uint8][body_length:uint32be]
    header = _recv_exact(sock, 12, timeout=timeout)  # 1+3+1+4 = 9，多读以防变体
    # 按约定的最小格式解析: prefix + 3B ext_id + subtype + 4B body_len
    # 但为了宽容，我们只强制读取 prefix + ext_id + subtype + length
    # 先解析 header 前 9 字节
    offset = 0
    prefix_byte, offset = unpack_uint8(header, offset)
    _ext_id_bytes = header[offset:offset + 3]
    offset += 3
    subtype, offset = unpack_uint8(header, offset)
    body_len, offset = unpack_uint32be(header, offset)

    # 读剩余 body
    body = _recv_exact(sock, body_len, timeout=timeout) if body_len > 0 else b""
    return subtype, body


# ---------------------------------------------------------------------------
# CompressedBlock 结构
# ---------------------------------------------------------------------------

def pack_compressed_block(data: bytes, compression_level: int = 0) -> bytes:
    """打包一个 CompressedBlock。

    布局: [compressionLevel:uint8][compressedSize:uint32le][uncompressedSize:uint32le][compressedData]
    compression_level=0 表示不压缩；1-9 使用 zlib 压缩。
    """
    if compression_level <= 0:
        level = 0
        compressed_data = data
    else:
        level = min(compression_level, 9)
        compressed_data = zlib.compress(data, level)

    header = (
        pack_uint8(level)
        + pack_uint32le(len(compressed_data))
        + pack_uint32le(len(data))
    )
    return header + compressed_data


def unpack_compressed_block(buf: bytes) -> bytes:
    """解包一个 CompressedBlock，返回原始未压缩数据。"""
    offset = 0
    level, offset = unpack_uint8(buf, offset)
    compressed_size, offset = unpack_uint32le(buf, offset)
    _uncompressed_size, offset = unpack_uint32le(buf, offset)
    compressed_data = buf[offset:offset + compressed_size]

    if level == 0:
        return compressed_data
    return zlib.decompress(compressed_data)


# ---------------------------------------------------------------------------
# 常用消息构造器（返回 body bytes，不含 RFB prefix/ext_id/subtype）
# ---------------------------------------------------------------------------

def make_mkdir_request(path: str) -> bytes:
    """CMDRST body: UTF-8 长度前缀字符串。"""
    return pack_string_utf8(path)


def make_upload_start_request(
    remote_path: str,
    total_size: int,
    overwrite: bool = True,
) -> bytes:
    """CFURST body: [path:string_utf8][total_size:uint64le][overwrite:uint8]"""
    body = pack_string_utf8(remote_path)
    # 注意：RFB FTP 文件大小通常用 uint32le 或 uint64le，这里用 uint64le 以支持大文件
    body += pack_uint32le(total_size & 0xFFFFFFFF)
    # 高 32 位
    body += pack_uint32le((total_size >> 32) & 0xFFFFFFFF)
    body += pack_uint8(1 if overwrite else 0)
    return body


def make_upload_data_request(
    offset: int,
    block: bytes,
    compression: int = 0,
) -> bytes:
    """CUDRST body: [offset:uint64le][block:CompressedBlock]"""
    body = pack_uint32le(offset & 0xFFFFFFFF)
    body += pack_uint32le((offset >> 32) & 0xFFFFFFFF)
    body += pack_compressed_block(block, compression)
    return body


def make_upload_end_request() -> bytes:
    """CUERST body: 空。"""
    return b""


def make_list_request(path: str) -> bytes:
    """CFLRST body: UTF-8 长度前缀字符串。"""
    return pack_string_utf8(path)


def make_delete_request(path: str) -> bytes:
    """CFRRST body: UTF-8 长度前缀字符串。"""
    return pack_string_utf8(path)


def make_move_request(src: str, dst: str) -> bytes:
    """CFMRST body: [src:string_utf8][dst:string_utf8]"""
    return pack_string_utf8(src) + pack_string_utf8(dst)


def make_download_start_request(remote_path: str, offset: int = 0) -> bytes:
    """CFDRST body: [path:string_utf8][offset:uint64le]"""
    body = pack_string_utf8(remote_path)
    body += pack_uint32le(offset & 0xFFFFFFFF)
    body += pack_uint32le((offset >> 32) & 0xFFFFFFFF)
    return body


def make_md5_request(path: str, offset: int, length: int) -> bytes:
    """CM5RST body: [path:string_utf8][offset:uint64le][length:uint32le]"""
    body = pack_string_utf8(path)
    body += pack_uint32le(offset & 0xFFFFFFFF)
    body += pack_uint32le((offset >> 32) & 0xFFFFFFFF)
    body += pack_uint32le(length & 0xFFFFFFFF)
    return body


# ---------------------------------------------------------------------------
# 响应解析辅助
# ---------------------------------------------------------------------------

@dataclass
class DirEntry:
    name: str
    size: int
    mod_time: float  # Unix 时间戳（秒）
    is_dir: bool


def parse_list_response(body: bytes) -> list[DirEntry]:
    """解析 CFLRST 响应 body。

    TightVNC FTP 列表响应体格式：
      [entry_count:uint32le]
      N × [
          name:string_utf8
          size:uint64le
          mod_time:uint64le  (Unix 毫秒时间戳)
          attr:uint8         (0=普通文件, 1=目录)
      ]
    """
    offset = 0
    count, offset = unpack_uint32le(body, offset)
    entries: list[DirEntry] = []
    for _ in range(count):
        name, offset = unpack_string_utf8(body, offset)
        size_low, offset = unpack_uint32le(body, offset)
        size_high, offset = unpack_uint32le(body, offset)
        size = size_low | (size_high << 32)
        mtime_low, offset = unpack_uint32le(body, offset)
        mtime_high, offset = unpack_uint32le(body, offset)
        mod_time_ms = mtime_low | (mtime_high << 32)
        attr, offset = unpack_uint8(body, offset)
        entries.append(
            DirEntry(
                name=name,
                size=size,
                mod_time=mod_time_ms / 1000.0,
                is_dir=(attr == 1),
            )
        )
    return entries


def parse_md5_response(body: bytes) -> str:
    """解析 CM5RST 响应 body，返回 16 字节 MD5 的 hex 字符串（32 字符小写）。

    格式：[md5:16bytes]
    """
    if len(body) < 16:
        return ""
    return body[:16].hex()


def parse_upload_response(body: bytes) -> int:
    """解析 CFURST / CUDRST / CUERST 响应，返回服务器分配的文件句柄或 0。

    TightVNC FTP 上传开始响应返回文件句柄（uint32le）。
    数据块/结束响应通常为空。
    """
    if len(body) >= 4:
        val, _ = unpack_uint32le(body, 0)
        return val
    return 0


def parse_download_response(body: bytes) -> tuple[int, int]:
    """解析 CFDRST 响应 body。

    返回 (file_handle, file_size)，均为 uint64le / uint32le 组合。
    """
    if len(body) < 12:
        return 0, 0
    offset = 0
    handle_low, offset = unpack_uint32le(body, offset)
    handle_high, offset = unpack_uint32le(body, offset)
    size_low, offset = unpack_uint32le(body, offset)
    size_high, offset = unpack_uint32le(body, offset)
    handle = handle_low | (handle_high << 32)
    size = size_low | (size_high << 32)
    return handle, size


# ---------------------------------------------------------------------------
# FtpSession —— 高层会话封装
# ---------------------------------------------------------------------------

class FtpSession:
    """基于已连接 RFB socket 的 FTP 高层会话。"""

    DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.handles: dict[str, int] = {}  # 远程路径 -> 文件句柄（预留）

    # ---- 基础收发 --------------------------------------------------------

    def _do_request(self, subtype: int, body: bytes = b"") -> tuple[int, bytes]:
        send_request(self.sock, subtype, body)
        resp_subtype, resp_body = recv_response(self.sock)
        if resp_subtype == LAST_REQUEST_FAILED:
            raise FtpError(
                f"RFB FTP 请求失败 subtype=0x{subtype:02X}",
                rfb_subtype=resp_subtype,
            )
        return resp_subtype, resp_body

    # ---- 高层 API --------------------------------------------------------

    def mkdir(self, path: str) -> None:
        resp_subtype, _ = self._do_request(CMDRST, make_mkdir_request(path))
        # 期望返回 subtype=CMDRST 或空响应
        if resp_subtype != CMDRST:
            raise FtpError(
                f"mkdir 响应 subtype 不匹配：期望 0x{CMDRST:02X}，实际 0x{resp_subtype:02X}",
                rfb_subtype=resp_subtype,
            )

    def delete(self, path: str) -> None:
        resp_subtype, _ = self._do_request(CFRRST, make_delete_request(path))
        if resp_subtype != CFRRST:
            raise FtpError(
                f"delete 响应 subtype 不匹配：期望 0x{CFRRST:02X}，实际 0x{resp_subtype:02X}",
                rfb_subtype=resp_subtype,
            )

    def move(self, src: str, dst: str) -> None:
        resp_subtype, _ = self._do_request(CFMRST, make_move_request(src, dst))
        if resp_subtype != CFMRST:
            raise FtpError(
                f"move 响应 subtype 不匹配：期望 0x{CFMRST:02X}，实际 0x{resp_subtype:02X}",
                rfb_subtype=resp_subtype,
            )

    def list_dir(self, path: str) -> list[dict]:
        _resp_subtype, body = self._do_request(CFLRST, make_list_request(path))
        entries = parse_list_response(body)
        return [
            {
                "name": e.name,
                "size": e.size,
                "mod_time": e.mod_time,
                "is_dir": e.is_dir,
            }
            for e in entries
        ]

    def md5_check(
        self,
        remote_path: str,
        offset: int = 0,
        length: int = 65536,
    ) -> str:
        resp_subtype, body = self._do_request(
            CM5RST, make_md5_request(remote_path, offset, length)
        )
        if resp_subtype != CM5RST:
            return ""
        return parse_md5_response(body)

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        compression: int = 0,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overwrite: bool = True,
    ) -> None:
        """分块上传本地文件到远程。

        Args:
            local_path: 本地文件路径。
            remote_path: 远程文件路径。
            progress_cb: 进度回调 `cb(已上传字节数, 总大小)`。
            compression: 压缩级别 0-9，0 表示不压缩。
            chunk_size: 每块字节数（未压缩）。
            overwrite: 是否覆盖已存在的远程文件。
        """
        total_size = os.path.getsize(local_path)

        # 1) 开始上传
        resp_subtype, resp_body = self._do_request(
            CFURST,
            make_upload_start_request(remote_path, total_size, overwrite=overwrite),
        )
        if resp_subtype != CFURST:
            raise FtpError(
                f"upload_start 响应 subtype 不匹配：期望 0x{CFURST:02X}，实际 0x{resp_subtype:02X}",
                rfb_subtype=resp_subtype,
            )

        # 2) 分块发送
        sent = 0
        with open(local_path, "rb") as f:
            while True:
                block = f.read(chunk_size)
                if not block:
                    break
                self._do_request(
                    CUDRST,
                    make_upload_data_request(sent, block, compression=compression),
                )
                sent += len(block)
                if progress_cb:
                    try:
                        progress_cb(sent, total_size)
                    except Exception:
                        # 回调失败不影响上传
                        pass

        # 3) 上传结束
        end_subtype, _ = self._do_request(CUERST, make_upload_end_request())
        if end_subtype != CUERST:
            raise FtpError(
                f"upload_end 响应 subtype 不匹配：期望 0x{CUERST:02X}，实际 0x{end_subtype:02X}",
                rfb_subtype=end_subtype,
            )


# ---------------------------------------------------------------------------
# 自检（python -m localdropshell.rfbftp 时运行）
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover - 仅开发自检
    import io

    # 基础编解码自检
    assert pack_uint8(0xFF) == b"\xff"
    assert pack_uint16be(0x1234) == b"\x12\x34"
    assert pack_uint16le(0x1234) == b"\x34\x12"
    assert pack_uint32be(0x11223344) == b"\x11\x22\x33\x44"
    assert pack_uint32le(0x11223344) == b"\x44\x33\x22\x11"

    buf = pack_string_utf8("hello")
    s, off = unpack_string_utf8(buf, 0)
    assert s == "hello" and off == len(buf), (s, off)

    # CompressedBlock 自检
    plain = b"A" * 1024
    packed = pack_compressed_block(plain, compression_level=6)
    # 头: 1 + 4 + 4 = 9
    assert packed[0] == 6
    decompressed = unpack_compressed_block(packed)
    assert decompressed == plain

    packed_nc = pack_compressed_block(plain, compression_level=0)
    assert unpack_compressed_block(packed_nc) == plain

    # 失败响应抛异常
    class _FailingSock:
        def __init__(self) -> None:
            self._sent: list[bytes] = []
            self._full: bytes = (
                pack_uint8(RFB_FTP_PREFIX)
                + pack_uint32le(TIGHTVNC_FTP_EXT_ID)[:3]
                + pack_uint8(LAST_REQUEST_FAILED)
                + pack_uint32be(4)
                + b"\x00\x00\x00\x00"
            )
            self._idx: int = 0

        def settimeout(self, _timeout: float) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            self._sent.append(data)

        def recv(self, n: int) -> bytes:
            if self._idx < len(self._full):
                chunk = self._full[self._idx:self._idx + n]
                self._idx += len(chunk)
                return chunk
            return b""

    fsock = _FailingSock()
    ftp = FtpSession(fsock)
    try:
        ftp.mkdir("/tmp/test")
    except FtpError as e:
        print(f"捕获到预期异常: {e}")
    else:
        raise AssertionError("期望 FtpError")

    print("所有自检通过 ✓")
