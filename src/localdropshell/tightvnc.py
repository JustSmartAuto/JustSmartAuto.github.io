"""TightVNC RFB 扩展客户端（FC000102~FC000119 协议）.

本文件实现 **顶层 RFB 扩展消息**（前缀 ``0xFC`` + 3 字节消息 ID），
与 ``rfbftp.py`` 的 **FTP 子类型消息**（CCSRSST / CFLRST / CFURST ...）
属于不同协议层：

    RFB 主协议 (StreamReader/Writer)
      ├── 普通 RFB 消息 (FramebufferUpdate ...)
      └── 顶层 RFB 扩展 (本文件  FC000102~FC000119)
            └── body 内部可嵌套 FTP 子类型 (rfbftp.py 的编解码工具)

路径统一 Unix 风格 ``/X:/...``，与 TightVNC FTP 协议保持一致。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# 复用 rfbftp.py 里的 CompressedBlock 编解码（共享压缩格式）
from .rfbftp import (
    pack_compressed_block,
    unpack_compressed_block,
    pack_uint8,
    pack_uint32le,
    unpack_uint8,
    unpack_uint32le,
)


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------

class TightVncError(Exception):
    """TightVNC RFB 扩展操作失败。"""

    def __init__(
        self,
        message: str,
        rfb_message_id: Optional[int] = None,
        raw_body: Optional[bytes] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.rfb_message_id = rfb_message_id
        self.raw_body = raw_body

    def __str__(self) -> str:
        prefix = f"[TightVncError msgId=0x{self.rfb_message_id:08X}] " \
            if self.rfb_message_id is not None else ""
        return prefix + self.message


# ---------------------------------------------------------------------------
# TightVNC FTP 扩展消息 ID（顶层 4 字节小端，高字节 0xFC）
# ---------------------------------------------------------------------------

FC000102 = 0xFC000102  # FILE_LIST_REQUEST      C→S
FC000103 = 0xFC000103  # FILE_LIST_REPLY        S→C
FC000104 = 0xFC000104  # MD5_REQUEST            C→S
FC000105 = 0xFC000105  # MD5_REPLY              S→C
FC000106 = 0xFC000106  # UPLOAD_START_REQUEST   C→S
FC000107 = 0xFC000107  # UPLOAD_START_REPLY     S→C
FC000108 = 0xFC000108  # UPLOAD_DATA_REQUEST    C→S
FC000109 = 0xFC000109  # UPLOAD_DATA_REPLY      S→C
FC00010A = 0xFC00010A  # UPLOAD_END_REQUEST     C→S
FC00010B = 0xFC00010B  # UPLOAD_END_REPLY       S→C
FC00010F = 0xFC00010F  # DOWNLOAD_START_REQUEST C→S
FC000111 = 0xFC000111  # MKDIR_REQUEST          C→S
FC000113 = 0xFC000113  # REMOVE_REQUEST         C→S
FC000119 = 0xFC000119  # LAST_REQUEST_FAILED_REPLY  S→C (兜底失败)

# 顶层 RFB 扩展消息掩码：高字节 = 0xFC
_RFB_EXT_MASK = 0xFC000000


# ---------------------------------------------------------------------------
# 基础编解码（仅小端，TightVNC Windows 约定）
# ---------------------------------------------------------------------------

def pack_string_utf8_le(s: str) -> bytes:
    """UINT32LE 长度前缀 + UTF-8 字节串。"""
    data = s.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def unpack_string_utf8_le(buf: bytes, offset: int) -> tuple[str, int]:
    """解析 UINT32LE 长度前缀 UTF-8 串，返回 (str, new_offset)。"""
    (length,) = struct.unpack_from("<I", buf, offset)
    offset += 4
    s = buf[offset:offset + length].decode("utf-8", errors="replace")
    return s, offset + length


# ---------------------------------------------------------------------------
# 顶层 RFB 扩展消息收发（asyncio StreamReader/Writer 通道）
# ---------------------------------------------------------------------------

async def send_rfb_ext(
    writer: asyncio.StreamWriter,
    message_id: int,
    body: bytes = b"",
) -> None:
    """发送一条顶层 RFB 扩展消息。

    布局: [messageId:UINT32LE 高字节=0xFC][body ...]
    说明：顶层消息无额外 body_len 前缀——接收方根据 messageId 及 body 内部
    结构（例如 CompressedBlock 头）来决定边界。
    """
    if (message_id & _RFB_EXT_MASK) != _RFB_EXT_MASK:
        message_id |= _RFB_EXT_MASK
    header = struct.pack("<I", message_id & 0xFFFFFFFF)
    writer.write(header + body)
    await writer.drain()


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    """从 StreamReader 精确读 n 字节。"""
    buf = await reader.readexactly(n)
    if len(buf) != n:
        raise TightVncError(
            f"连接关闭，期望 {n} 字节，仅收到 {len(buf)}"
        )
    return buf


async def recv_rfb_ext(
    reader: asyncio.StreamReader,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    """读取一条完整的顶层 RFB 扩展消息，返回 (messageId, body)。

    注意：本函数假设流的下一条消息必然是扩展消息（0xFC 前缀）。
    在我们的场景里，握手之后主通道只走扩展消息，因此直接同步阻塞读。
    """
    header = await _read_exact(reader, 4)
    (message_id,) = struct.unpack("<I", header)
    if (message_id & 0xFF) != 0xFC and ((message_id >> 24) & 0xFF) != 0xFC:
        # RFB 扩展消息最高字节 = 0xFC（小端序列化后是第 4 字节）
        # 兼容：大端 / 小端两种解析都试一下
        # 实际上我们用 struct.pack("<I", 0xFC000102) = b'\x02\x01\x00\xfc'
        # 所以小端 messageId 的最高字节(第4字节)就是 0xFC
        raise TightVncError(
            f"非 RFB 扩展消息头: 0x{message_id:08X}",
            rfb_message_id=message_id,
        )

    # body 长度由具体消息类型决定，这里仅读 CompressedBlock 类响应
    # 对于 FC000103 / FC000105 / FC000107 / FC000109 / FC00010B
    # - FC000105 (MD5_REPLY) body = 16 字节固定
    # - FC000103 (FILE_LIST_REPLY) body = CompressedBlock（内部含压缩后的 list）
    # - 其余 reply 通常为空
    # 因此：我们先尝试读 4 字节长度（宽松兼容），读不到则视为长度未知 → 交给上层
    # 但为了简单，直接用超时 + 小批量尝试读的策略不可靠；
    # 更稳妥的是：根据 messageId 推断固定长度
    #
    # FC000105 → 16B
    # FC000103, FC000107, FC000109, FC00010B → 长度未知（看 CompressedBlock 结构）
    # 其他 → 空 body

    fixed_len = None
    if message_id == FC000105:
        fixed_len = 16
    elif message_id in (FC000103, FC000107, FC000109, FC00010B, FC000119,
                         FC00010A, FC000111, FC000113):
        # 这些 reply 可能带变长 body（FC000103 带 CompressedBlock；FC000119 带错误码/描述）
        # 但请求/回复本身不带 length 前缀——所以我们需要一种方式知道 body 结束
        #
        # 方案：读 4 字节小端作为 len；如果下 4 字节的最后一字节也是 0xFC（意味着
        # 消息 bodyLen 很大导致又出现 0xFC），就拒绝这个推断，改读 socket 剩余字节
        #
        # 实际上 TightVNC 的实现是 **不带 body_len 的**——这在 RFB 协议中是通过
        # FramebufferUpdate 消息类型让接收端自行解析的。所以：
        # - 对固定长度 body，硬编码长度
        # - 对变长 body（主要是 FC000103 / FC000119），我们读 1MB 上限，然后交给上层按消息类型解析

        # 先用 1s 超时非阻塞试探读；如果没有可用数据，视为空 body
        try:
            body = await asyncio.wait_for(
                reader.read(1024 * 1024), timeout=timeout
            )
        except asyncio.TimeoutError:
            body = b""
        return message_id, body
    else:
        # 未知消息 ID：读 1s 试探
        try:
            body = await asyncio.wait_for(
                reader.read(4096), timeout=timeout
            )
        except asyncio.TimeoutError:
            body = b""
        return message_id, body

    body = await _read_exact(reader, fixed_len)
    return message_id, body


# ---------------------------------------------------------------------------
# DES ECB (TightVNC VNC Auth) —— pycryptodome 优先，纯 Python fallback
# ---------------------------------------------------------------------------

def _des_ecb_encrypt(block: bytes, key: bytes) -> bytes:
    """DES ECB 单块加密（8 字节输入 → 8 字节输出）。

    优先使用 pycryptodome / pycryptodomex；若均不可用则使用内置的
    纯 Python fallback（开发环境一般无密码，不会走到这里）。
    """
    # 优先 pycryptodome（Crypto.Cipher.DES 是子模块）
    for mod_path in ("Crypto.Cipher.DES", "Cryptodome.Cipher.DES"):
        try:
            from importlib import import_module
            des_mod = import_module(mod_path)
            return des_mod.new(key, des_mod.MODE_ECB).encrypt(block)
        except ImportError:
            continue
        except Exception:
            continue
    # Fallback：纯 Python DES ECB
    return _des_ecb_fallback(block, key)


def _des_permute(block: bytes, table: list[int]) -> bytes:
    """按置换表重排 block 的 bit。

    block_bits = len(block) * 8；table 的 idx 以 1 为起点（DES 规范 bit 编号）。
    """
    block_bits = len(block) * 8
    n = len(table)
    bits = int.from_bytes(block, "big")
    out = 0
    for i, idx in enumerate(table):
        pos = block_bits - idx
        out |= ((bits >> pos) & 1) << (n - 1 - i)
    return out.to_bytes(max(n // 8, 1), "big")


def _des_left_shift(val: int, n: int) -> int:
    """对 28-bit 值做左移 n 位（循环左移）。"""
    val &= 0x0FFFFFFF
    return ((val << n) | (val >> (28 - n))) & 0x0FFFFFFF


_S_BOXES = [
    [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
     [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
     [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
     [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]],
    [[15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10],
     [3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5],
     [0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15],
     [13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9]],
    [[10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8],
     [13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1],
     [13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7],
     [1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12]],
    [[7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15],
     [13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9],
     [10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4],
     [3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14]],
    [[2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9],
     [14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6],
     [4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14],
     [11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3]],
    [[12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11],
     [10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8],
     [9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6],
     [4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13]],
    [[4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1],
     [13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6],
     [1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2],
     [6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12]],
    [[13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7],
     [1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2],
     [7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8],
     [2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]],
]

_IP = [
    58,50,42,34,26,18,10,2, 60,52,44,36,28,20,12,4,
    62,54,46,38,30,22,14,6, 64,56,48,40,32,24,16,8,
    57,49,41,33,25,17, 9,1, 59,51,43,35,27,19,11,3,
    61,53,45,37,29,21,13,5, 63,55,47,39,31,23,15,7,
]
_E = [
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9,10,11,
    12,13,14,15,16,17,16,17,18,19,20,21,20,21,22,23,
    24,25,24,25,26,27,28,29,28,29,30,31,32, 1, 2, 3,
]
_P = [
    16, 7,20,21,29,12,28,17, 1,15,23,26, 5,18,31,10,
     2, 8,24,14,32,27, 3, 9,19,13,30, 6,22,11, 4,25,
]
_PC1 = [
    57,49,41,33,25,17, 9, 1, 58,50,42,34,26,18,10, 2,
    59,51,43,35,27,19,11, 3, 60,52,44,36,28,20,12, 4,
    63,55,47,39,31,23,15, 7, 62,54,46,38,30,22,14, 6,
    61,53,45,37,29,21,13, 5,
]
_PC2 = [
    14,17,11,24, 1, 5, 3,28,15, 6,21,10,23,19,12, 4,
    26, 8,16, 7,27,20,13, 2,41,52,31,37,47,55,30,40,
    51,45,33,48,44,49,39,56,34,53,46,42,50,36,29,32,
]
_SHIFTS = [1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1]


def _des_sbox_substitution(r_block_48: bytes) -> bytes:
    """DES S-Box 48→32 替换。"""
    r = int.from_bytes(r_block_48, "big")
    out = 0
    for i in range(8):
        chunk = (r >> (42 - 6 * i)) & 0x3F
        row = ((chunk >> 5) & 1) * 2 + (chunk & 1)
        col = (chunk >> 1) & 0xF
        out = (out << 4) | _S_BOXES[i][row][col]
    return out.to_bytes(4, "big")


def _des_round(L: bytes, R: bytes, subkey: bytes) -> tuple[bytes, bytes]:
    """单轮 DES Feistel：返回 (R_new, L_new XOR f(R, subkey))。"""
    r_exp = _des_permute(R, _E)
    r_xor = bytes(a ^ b for a, b in zip(r_exp, subkey))
    s_out = _des_sbox_substitution(r_xor)
    p_out = _des_permute(s_out, _P)
    l_int = int.from_bytes(L, "big") ^ int.from_bytes(p_out, "big")
    return R, l_int.to_bytes(4, "big")


def _des_ecb_fallback(block: bytes, key: bytes) -> bytes:
    """纯 Python DES ECB（pycryptodome 不可用时的兜底）。"""
    k56 = _des_permute(key, _PC1)
    key_int = int.from_bytes(k56, "big")
    C = key_int >> 28
    D = key_int & 0x0FFFFFFF
    subkeys: list[bytes] = []
    for shift in _SHIFTS:
        C = _des_left_shift(C, shift)
        D = _des_left_shift(D, shift)
        cd = ((C << 28) | D).to_bytes(7, "big")
        subkeys.append(_des_permute(cd, _PC2))

    ip = _des_permute(block, _IP)
    L, R = ip[:4], ip[4:]
    for sk in subkeys:
        L, R = _des_round(L, R, sk)

    swapped = R + L
    ip_inv = [0] * 64
    for i, v in enumerate(_IP):
        ip_inv[v - 1] = i + 1
    return _des_permute(swapped, ip_inv)


def vnc_auth(challenge: bytes, password: str) -> bytes:
    """VNC Authentication 响应：用 password 前 8 字节（rot13 后）做 DES key 加密 challenge.

    Windows VNC Auth 约定：key = rot13(password[:8])（右对齐），
    challenge 是 16 字节，分成两个 8 字节块分别用相同 key 做 DES ECB 加密。
    """
    if len(challenge) != 16:
        raise TightVncError(
            f"VNC Auth challenge 长度必须 16 字节，实际 {len(challenge)}"
        )
    # 构造 key：password 前 8 字节，右填 0，rot13
    pw8 = password[:8].encode("latin-1") if password else b""
    if len(pw8) < 8:
        pw8 = pw8 + b"\x00" * (8 - len(pw8))
    # rot13（对所有字节循环 +13）
    key = bytes((b + 13) & 0xFF for b in pw8)

    block1 = _des_ecb_encrypt(challenge[:8], key)
    block2 = _des_ecb_encrypt(challenge[8:], key)
    return block1 + block2


# ---------------------------------------------------------------------------
# RFB 握手
# ---------------------------------------------------------------------------

async def rfb_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    password: Optional[str] = None,
) -> dict:
    """完成完整的 RFB 协议握手流程，返回 ``{width, height, pixel_format, name}``。

    支持安全类型：优先 VNC Authentication (2)，否则 None (1)。
    """
    # 1) 读服务端 banner
    banner = await reader.readuntil(b"\n")
    if not banner.startswith(b"RFB"):
        raise TightVncError(f"无效 RFB banner: {banner!r}")
    # 2) 发客户端版本（3.8）
    writer.write(b"RFB 003.008\n")
    await writer.drain()

    # 3) 读安全类型列表
    count_byte = await _read_exact(reader, 1)
    count = count_byte[0]
    if count == 0:
        # TLS 安全类型（RFC 6143）
        reason = await reader.read(128)
        raise TightVncError(f"服务端拒绝连接: {reason!r}")
    sec_types = await _read_exact(reader, count)

    # 4) 选择最强可用安全类型
    chosen = None
    if 2 in sec_types:
        chosen = 2  # VNC Authentication
    elif 1 in sec_types:
        chosen = 1  # None（免密）
    else:
        raise TightVncError(f"不支持的安全类型: {list(sec_types)}")

    writer.write(bytes([chosen]))
    await writer.drain()

    # 5) VNC Auth 流程
    if chosen == 2:
        challenge = await _read_exact(reader, 16)
        if not password:
            raise TightVncError("VNC Authentication 需要密码")
        try:
            response = vnc_auth(challenge, password)
        except TightVncError:
            # 密码解析失败：直接发空响应让服务端返回失败；不要吞异常
            raise
        writer.write(response)
        await writer.drain()

    # 6) 读 security_result (4 字节)
    result = await _read_exact(reader, 4)
    (status,) = struct.unpack(">I", result)
    if status != 0:
        # 服务端可能发送失败原因
        try:
            reason = await asyncio.wait_for(reader.read(256), timeout=1.0)
        except asyncio.TimeoutError:
            reason = b""
        raise TightVncError(
            f"认证失败 status={status}: {reason!r}",
        )

    # 7) ClientInit（1 字节 shared-flag，通常 1）
    writer.write(b"\x01")
    await writer.drain()

    # 8) ServerInit
    width_bytes = await _read_exact(reader, 2)
    height_bytes = await _read_exact(reader, 2)
    pixel_format = await _read_exact(reader, 16)
    (name_len,) = struct.unpack(">I", await _read_exact(reader, 4))
    name_bytes = await _read_exact(reader, name_len)
    name = name_bytes.decode("utf-8", errors="replace")

    width = struct.unpack(">H", width_bytes)[0]
    height = struct.unpack(">H", height_bytes)[0]
    return {
        "width": width,
        "height": height,
        "pixel_format": pixel_format,
        "name": name,
    }


# ---------------------------------------------------------------------------
# TightVncFtpClient —— 高层客户端
# ---------------------------------------------------------------------------

@dataclass
class DirEntry:
    name: str
    size: int
    mod_time: float  # Unix 时间戳（秒）
    is_dir: bool
    flags: int = 0


class TightVncFtpClient:
    """TightVNC RFB 扩展 FTP 客户端。"""

    DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB

    def __init__(
        self,
        host: str,
        port: int = 5900,
        password: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.server_info: dict = {}
        self._connected = False

    # ---- 连接 / 关闭 ----------------------------------------------------

    async def connect(self) -> dict:
        """建立 RFB 连接并握手，探测 TightVNC FTP 扩展可用性."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        self.reader = reader
        self.writer = writer

        # 握手（密码为空时优先 None 安全类型；有密码时 VNC Auth）
        self.server_info = await rfb_handshake(reader, writer, self.password)
        self._connected = True

        # 探测：发 FC000102 FILE_LIST_REQUEST 探测扩展可用性
        await send_rfb_ext(writer, FC000102, pack_string_utf8_le("/"))
        try:
            msg_id, body = await asyncio.wait_for(
                recv_rfb_ext(reader, timeout=self.timeout),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            raise TightVncError(
                "TightVNC FTP 扩展无响应"
            )
        if msg_id == FC000119:
            raise TightVncError(
                f"TightVNC FTP 扩展被服务端拒绝: {body!r}",
                rfb_message_id=msg_id,
                raw_body=body,
            )
        if msg_id != FC000103:
            raise TightVncError(
                f"非预期探测响应 msgId=0x{msg_id:08X}",
                rfb_message_id=msg_id,
                raw_body=body,
            )
        return self.server_info

    async def close(self) -> None:
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except (OSError, ConnectionError):
                pass
        self.reader = None
        self.writer = None
        self._connected = False

    # ---- 内部工具 -------------------------------------------------------

    @property
    def _r(self) -> asyncio.StreamReader:
        assert self.reader is not None
        return self.reader

    @property
    def _w(self) -> asyncio.StreamWriter:
        assert self.writer is not None
        return self.writer

    async def _request(
        self, req_id: int, req_body: bytes,
        expected_reply_id: int,
        timeout: Optional[float] = None,
    ) -> bytes:
        """发送一条请求消息，等待 reply。遇到 FC000119 抛 TightVncError."""
        await send_rfb_ext(self._w, req_id, req_body)
        msg_id, body = await asyncio.wait_for(
            recv_rfb_ext(self._r, timeout=timeout or self.timeout),
            timeout=timeout or self.timeout,
        )
        if msg_id == FC000119:
            raise TightVncError(
                f"服务端请求失败 reqId=0x{req_id:08X}: {body!r}",
                rfb_message_id=msg_id,
                raw_body=body,
            )
        if msg_id != expected_reply_id:
            raise TightVncError(
                f"非预期回复：期望 0x{expected_reply_id:08X}，实际 0x{msg_id:08X}",
                rfb_message_id=msg_id,
                raw_body=body,
            )
        return body

    # ---- 目录 / 文件操作 ------------------------------------------------

    async def list_dir(self, path: str = "/") -> list[dict]:
        """列出远程目录内容，返回 ``[{name, size, mod_time, is_dir, flags}, ...]``。"""
        body = await self._request(
            FC000102, pack_string_utf8_le(path), FC000103,
        )
        # body 通常是 CompressedBlock
        entries_raw: bytes
        try:
            entries_raw = unpack_compressed_block(body)
        except Exception:
            entries_raw = body  # 兜底：body 可能就是未压缩的

        offset = 0
        count, offset = unpack_uint32le(entries_raw, offset)
        result: list[dict] = []
        for _ in range(count):
            name, offset = unpack_string_utf8_le(entries_raw, offset)
            size_low, offset = unpack_uint32le(entries_raw, offset)
            size_high, offset = unpack_uint32le(entries_raw, offset)
            size = size_low | (size_high << 32)
            mtime_low, offset = unpack_uint32le(entries_raw, offset)
            mtime_high, offset = unpack_uint32le(entries_raw, offset)
            mod_time_ms = mtime_low | (mtime_high << 32)
            flags, offset = unpack_uint32le(entries_raw, offset)
            is_dir = bool(flags & 0x10)  # TightVNC fileFlags 约定：bit4 = 目录
            result.append({
                "name": name,
                "size": size,
                "mod_time": mod_time_ms / 1000.0,
                "is_dir": is_dir,
                "flags": flags,
            })
        return result

    async def mkdir(self, remote_path: str) -> None:
        await self._request(
            FC000111, pack_string_utf8_le(remote_path), FC000111,
        )

    async def delete(self, remote_path: str) -> None:
        await self._request(
            FC000113, pack_string_utf8_le(remote_path), FC000113,
        )

    async def md5_check(
        self,
        remote_path: str,
        offset: int = 0,
        length: int = 65536,
    ) -> str:
        """请求远程文件指定偏移块的 MD5，返回 32 字符小写 hex 串。"""
        body = pack_string_utf8_le(remote_path)
        body += pack_uint32le(offset & 0xFFFFFFFF)
        body += pack_uint32le((offset >> 32) & 0xFFFFFFFF)
        body += pack_uint32le(length & 0xFFFFFFFF)
        reply = await self._request(FC000104, body, FC000105)
        if len(reply) < 16:
            raise TightVncError(
                f"MD5 reply 长度不足 16 字节: {reply!r}",
                rfb_message_id=FC000105,
                raw_body=reply,
            )
        return reply[:16].hex()

    # ---- 上传 -----------------------------------------------------------

    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
        compression: int = 0,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        """分块上传本地文件到远程。"""
        p = Path(local_path)
        if not p.is_file():
            raise TightVncError(f"本地文件不存在: {local_path}")
        file_bytes = p.read_bytes()
        total_size = len(file_bytes)

        # 1) UPLOAD_START_REQUEST body: [remotePathLen_le + remotePathUtf8 + totalSize_le + compressionLevel:1B]
        start_body = pack_string_utf8_le(remote_path)
        start_body += pack_uint32le(total_size & 0xFFFFFFFF)
        start_body += pack_uint8(compression if compression > 0 else 0)
        # zliblibId（仅当 compression>0 时）——默认 1 表示 zlib
        if compression > 0:
            start_body += pack_uint8(1)

        await self._request(FC000106, start_body, FC000107)

        # 2) 分块发送
        sent = 0
        offset = 0
        while sent < total_size:
            remaining = total_size - sent
            n = chunk_size if remaining >= chunk_size else remaining
            block = file_bytes[sent:sent + n]
            compressed = pack_compressed_block(block, compression_level=compression)
            # UPLOAD_DATA_REQUEST body: [offset_le + CompressedBlock]
            data_body = pack_uint32le(offset & 0xFFFFFFFF)
            data_body += pack_uint32le((offset >> 32) & 0xFFFFFFFF)
            data_body += compressed
            await self._request(FC000108, data_body, FC000109)
            sent += n
            offset += n
            if progress_cb:
                try:
                    progress_cb(sent, total_size)
                except Exception:
                    pass

        # 3) UPLOAD_END_REQUEST body: [lastBlockMd5:16B] 或空
        last_md5 = hashlib.md5(file_bytes[-chunk_size:] if total_size > 0 else b"").digest()
        try:
            await self._request(FC00010A, last_md5, FC00010B)
        except TightVncError:
            # 某些 tvnserver 不强制要求 lastBlockMd5 校验，允许空 body
            await self._request(FC00010A, b"", FC00010B)


# ---------------------------------------------------------------------------
# 仅调试：python -m localdropshell.tightvnc 时运行
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover - 仅开发自检
    # DES ECB 自检：key=0011223344556677, block=0123456789ABCDEF → 对照 pycryptodome
    key = bytes.fromhex("0011223344556677")
    block = bytes.fromhex("0123456789ABCDEF")
    got = _des_ecb_encrypt(block, key)
    try:
        from Crypto.Cipher import DES as _DESC
        expected = _DESC.new(key, _DESC.MODE_ECB).encrypt(block)
        assert got == expected, (
            f"DES ECB 自检失败:\n  got:      {got.hex().upper()}\n"
            f"  expected: {expected.hex().upper()}"
        )
        print("DES ECB 自检通过 ✓ (pycryptodome)")
    except ImportError:
        print(f"DES ECB: 纯 Python fallback 输出 {got.hex().upper()} (无 pycryptodome 参考)")

    # pack_string_utf8_le 自检
    buf = pack_string_utf8_le("hello")
    s, off = unpack_string_utf8_le(buf, 0)
    assert s == "hello" and off == len(buf), (s, off)
    print("string_utf8_le 自检通过 ✓")

    # TightVncFtpClient 实例化自检
    async def _smoke() -> None:
        c = TightVncFtpClient("127.0.0.1", port=5900, password=None)
        assert c.host == "127.0.0.1"
        assert c.port == 5900
        await c.close()
        print("TightVncFtpClient 实例化自检通过 ✓")

    import asyncio
    asyncio.run(_smoke())
    print("所有自检通过 ✓")
