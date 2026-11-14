"""TightVNC 2.8 的 RFB 文件传输扩展（同步 socket 实现）.

协议要点（已按 TightVNC 2.8.90 源码 ft-common/FTMessage.h、
ft-client-lib/FileTransferRequestSender.cpp、FileTransferReplyBuffer.cpp 核对）：

* 必须使用 **Tight(16) 安全类型** 连接。服务端只有在
  ``RfbInitializer::doTightAuth`` 之后才会把 ``m_tightEnabled`` 置位并注册
  FTP 扩展消息；用 None(1) 连接时发送 0xFC 前缀消息会被直接断开。
* 所有整数均为 **大端**（RFB 约定）。消息头是 UINT32 messageId
  (0xFC0001xx)，首字节 0xFC 即 RFB 扩展消息标记。
* 字符串为 UINT32 长度 + UTF-8 字节。
* 文件列表 / 下载数据回复是 CompressedBlock：
  UINT8 compressionLevel + UINT32 compressedSize +
  UINT32 uncompressedSize + 数据（level=1 时 zlib 压缩）。

Python 3.10+。
"""

from __future__ import annotations

import socket
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Any, Optional


class FtpError(Exception):
    """RFB FTP 操作失败."""

    def __init__(self, message: str, message_id: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.message_id = message_id

    def __str__(self) -> str:
        if self.message_id is not None:
            return f"[FtpError 0x{self.message_id:08X}] {self.message}"
        return self.message


# ---------------------------------------------------------------------------
# 消息 ID（TightVNC 2.x FTP 扩展，全部大端）
# ---------------------------------------------------------------------------

COMPRESSION_SUPPORT_REQUEST = 0xFC000100
COMPRESSION_SUPPORT_REPLY = 0xFC000101
FILE_LIST_REQUEST = 0xFC000102
FILE_LIST_REPLY = 0xFC000103
MD5_REQUEST = 0xFC000104
MD5_REPLY = 0xFC000105
UPLOAD_START_REQUEST = 0xFC000106
UPLOAD_START_REPLY = 0xFC000107
UPLOAD_DATA_REQUEST = 0xFC000108
UPLOAD_DATA_REPLY = 0xFC000109
UPLOAD_END_REQUEST = 0xFC00010A
UPLOAD_END_REPLY = 0xFC00010B
DOWNLOAD_START_REQUEST = 0xFC00010C
DOWNLOAD_START_REPLY = 0xFC00010D
DOWNLOAD_DATA_REQUEST = 0xFC00010E
DOWNLOAD_DATA_REPLY = 0xFC00010F
DOWNLOAD_END_REPLY = 0xFC000110
MKDIR_REQUEST = 0xFC000111
MKDIR_REPLY = 0xFC000112
REMOVE_REQUEST = 0xFC000113
REMOVE_REPLY = 0xFC000114
RENAME_REQUEST = 0xFC000115
RENAME_REPLY = 0xFC000116
DIRSIZE_REQUEST = 0xFC000117
DIRSIZE_REPLY = 0xFC000118
LAST_REQUEST_FAILED_REPLY = 0xFC000119

# 文件项 flags
FILE_FLAG_DIR = 0x1
FILE_FLAG_EXECUTABLE = 0x2

SEC_TYPE_NONE = 1
SEC_TYPE_TIGHT = 16
AUTH_CAP_VNC = 2  # AuthDefs::VNC

DEFAULT_CHUNK = 64 * 1024


# ---------------------------------------------------------------------------
# 握手用 DES（VNC 认证）
# ---------------------------------------------------------------------------

# VNC 认证的固定密钥（RFC 6143 / vncviewer 约定，注意字节顺序）
_VNC_KEY = (23, 82, 107, 6, 35, 78, 88, 7)


def _des_encrypt(challenge: bytes, password: str) -> bytes:
    """把 password 补齐 8 字节后用共享密钥 DES-ECB 加密 16 字节 challenge."""
    try:
        from Crypto.Cipher import DES  # pycryptodome
    except ImportError:
        DES = None

    key = password[:8].ljust(8, "\0").encode("latin-1", "ignore")

    if DES is not None:
        cipher = DES.new(key, DES.MODE_ECB)
        return cipher.encrypt(challenge[:8]) + cipher.encrypt(challenge[8:16])

    raise FtpError(
        "连接需要 VNC 密码，但未安装 pycryptodome（pip install pycryptodome）"
    )


def vnc_auth_response(challenge: bytes, password: str) -> bytes:
    """按 VNC 约定生成 16 字节认证响应."""
    key = password[:8].ljust(8, "\0")
    # 第一步：用固定密钥 DES 加密 password
    fixed = _des_encrypt_bytes(bytes(_VNC_KEY), key.encode("latin-1", "ignore"))
    # 第二步：用加密后的 password 作为密钥加密 challenge
    return _des_encrypt_bytes(fixed, challenge[:16])


def _des_encrypt_bytes(key8: bytes, data8: bytes) -> bytes:
    try:
        from Crypto.Cipher import DES

        return DES.new(key8, DES.MODE_ECB).encrypt(data8)
    except ImportError:
        return _des_impl(key8, data8)


# --- 纯 Python DES（仅 VNC 认证用；未装 pycryptodome 时的兜底） ------------

_PC1 = [
    57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4,
]
_PC2 = [
    14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32,
]
_IP = [
    58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
]
_FP = [
    40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25,
]
_E = [
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9,
    8, 9, 10, 11, 12, 13, 12, 13, 14, 15, 16, 17,
    16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1,
]
_P = [
    16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25,
]
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

_SBOX = [
    [
        14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
        0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
        4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
        15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13,
    ],
    [
        15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
        3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
        0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
        13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9,
    ],
    [
        10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
        13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
        13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
        1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12,
    ],
    [
        7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
        13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
        10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
        3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14,
    ],
    [
        2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
        14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
        4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
        11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3,
    ],
    [
        12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
        10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
        9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
        4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13,
    ],
    [
        4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
        13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
        1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
        6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12,
    ],
    [
        13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
        1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
        7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
        2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11,
    ],
]


def _permute(block: int, table: list[int], width: int) -> int:
    out = 0
    for pos in table:
        out = (out << 1) | ((block >> (width - pos)) & 1)
    return out


def _des_impl(key: bytes, data: bytes) -> bytes:
    """纯 Python DES-ECB 单块加密（8 字节 key，8 字节 data）."""
    key_int = int.from_bytes(key, "big")
    perm = _permute(key_int, _PC1, 64)
    c = perm >> 28
    d = perm & 0xFFFFFFF
    subkeys = []
    for shift in _SHIFTS:
        c = ((c << shift) | (c >> (28 - shift))) & 0xFFFFFFF
        d = ((d << shift) | (d >> (28 - shift))) & 0xFFFFFFF
        subkeys.append(_permute((c << 28) | d, _PC2, 56))

    block = _permute(int.from_bytes(data, "big"), _IP, 64)
    left = block >> 32
    right = block & 0xFFFFFFFF
    for k in subkeys:
        expanded = _permute(right, _E, 32) ^ k
        s_out = 0
        for i in range(8):
            six = (expanded >> (42 - 6 * i)) & 0x3F
            row = ((six >> 4) & 0x2) | (six & 0x1)
            col = (six >> 1) & 0xF
            s_out = (s_out << 4) | _SBOX[i][row * 16 + col]
        f_out = _permute(s_out, _P, 32)
        left, right = right, left ^ f_out
    pre = (right << 32) | left
    return _permute(pre, _FP, 64).to_bytes(8, "big")


# ---------------------------------------------------------------------------
# 握手
# ---------------------------------------------------------------------------

@dataclass
class RfbServerInfo:
    width: int
    height: int
    name: str
    tight_enabled: bool
    file_transfer: bool


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise FtpError("VNC 连接已关闭")
        buf += chunk
    return buf


def _recv_u8(sock: socket.socket) -> int:
    return _recv_exact(sock, 1)[0]


def _recv_u32(sock: socket.socket) -> int:
    return struct.unpack(">I", _recv_exact(sock, 4))[0]


def _recv_utf8(sock: socket.socket) -> str:
    n = _recv_u32(sock)
    return _recv_exact(sock, n).decode("utf-8", "replace")


def _pack_utf8(s: str) -> bytes:
    data = s.encode("utf-8")
    return struct.pack(">I", len(data)) + data


def _handshake(sock: socket.socket, password: str | None) -> RfbServerInfo:
    """完成 RFB 握手；优先 Tight(16)，返回服务器信息."""
    banner = b""
    while not banner.endswith(b"\n"):
        chunk = sock.recv(16)
        if not chunk:
            raise FtpError("VNC 服务端未返回协议版本")
        banner += chunk
    major, minor = 3, 8
    try:
        ver = banner.strip().decode("ascii", "replace").split()[-1]
        major, minor = (int(x) for x in ver.split(".")[2:4])
    except Exception:
        pass

    sock.sendall(b"RFB 003.008\n")

    if (major, minor) >= (3, 7):
        count = _recv_u8(sock)
        if count == 0:
            reason_len = _recv_u32(sock)
            reason = _recv_exact(sock, reason_len).decode("utf-8", "replace")
            raise FtpError(f"VNC 服务端拒绝连接: {reason}")
        sec_types = [_recv_u8(sock) for _ in range(count)]
    else:
        sec_types = [struct.unpack(">I", _recv_exact(sock, 4))[0]]

    if SEC_TYPE_TIGHT not in sec_types:
        raise FtpError(
            "该 VNC 服务端未提供 Tight 安全类型，无法使用文件传输扩展"
            f"（可用安全类型: {sec_types}）。请改用 TightVNC 2.x 服务端。"
        )

    sock.sendall(bytes([SEC_TYPE_TIGHT]))
    tunnel_count = _recv_u32(sock)
    for _ in range(tunnel_count):
        raise FtpError("暂不支持 Tight 隧道类型")

    auth_count = _recv_u32(sock)
    auth_caps = []
    for _ in range(auth_count):
        code = _recv_u32(sock)
        _recv_exact(sock, 4)  # vendor signature
        _recv_exact(sock, 8)  # name signature
        auth_caps.append(code)

    if auth_count:
        if AUTH_CAP_VNC not in auth_caps:
            raise FtpError(f"服务端只支持未实现的认证方式: {auth_caps}")
        sock.sendall(struct.pack(">I", AUTH_CAP_VNC))
        challenge = _recv_exact(sock, 16)
        if not password:
            raise FtpError("该 VNC 服务端要求密码认证，请在远程地址中提供密码")
        sock.sendall(vnc_auth_response(challenge, password))

    status = _recv_u32(sock)
    if status != 0:
        if status == 1:
            raise FtpError("VNC 认证失败（密码错误）")
        raise FtpError(f"VNC 认证失败 (status={status})")

    sock.sendall(b"\x01")  # ClientInit shared=1

    width, height = struct.unpack(">HH", _recv_exact(sock, 4))
    _recv_exact(sock, 16)  # pixel format
    name = _recv_utf8(sock)

    # Tight 模式下紧随 ServerInit 的是 interaction caps
    srv_cnt, cl_cnt, enc_cnt, _pad = struct.unpack(">HHHH", _recv_exact(sock, 8))
    caps = []
    for _ in range(srv_cnt + cl_cnt + enc_cnt):
        code = _recv_u32(sock)
        _recv_exact(sock, 4)
        _recv_exact(sock, 8)
        caps.append(code)

    return RfbServerInfo(
        width=width,
        height=height,
        name=name,
        tight_enabled=True,
        file_transfer=FILE_LIST_REPLY in caps,
    )


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

class FtpSession:
    """TightVNC RFB 文件传输会话（同步）. 连接建立即完成 VNC 握手."""
    DEFAULT_CHUNK_SIZE = DEFAULT_CHUNK

    def __init__(
        self,
        sock: socket.socket | tuple[str, int],
        password: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        """sock 可以是已连接的 socket，也可以是 (host, port) 元组."""
        if isinstance(sock, tuple):
            self.sock = socket.create_connection(sock, timeout=timeout)
            self._owns_socket = True
        else:
            self.sock = sock
            self._owns_socket = False
        self.sock.settimeout(timeout)
        self.server_info = _handshake(self.sock, password)
        if not self.server_info.file_transfer:
            raise FtpError("服务端 Tight 会话未声明文件传输扩展")

    # ---- 基础收发 --------------------------------------------------------

    def _send(self, payload: bytes) -> None:
        self.sock.sendall(payload)

    def _read_message_id(self) -> int:
        return struct.unpack(">I", _recv_exact(self.sock, 4))[0]

    def _read_failure(self, mid: int) -> FtpError:
        try:
            message = _recv_utf8(self.sock)
        except Exception:
            message = "服务端返回失败（无错误描述）"
        return FtpError(f"远程操作失败: {message}", mid)

    def _expect(self, expected: int) -> None:
        mid = self._read_message_id()
        if mid == LAST_REQUEST_FAILED_REPLY:
            raise self._read_failure(mid)
        if mid != expected:
            raise FtpError(f"响应不匹配: 期望 0x{expected:08X}，实际 0x{mid:08X}", mid)

    def _read_compressed_block(self) -> bytes:
        level = _recv_u8(self.sock)
        csize = _recv_u32(self.sock)
        usize = _recv_u32(self.sock)
        data = _recv_exact(self.sock, csize) if csize else b""
        if level:
            if usize and csize:
                return zlib.decompress(data)
            return b""
        return data

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    # ---- 文件列表 --------------------------------------------------------

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        """列出远程目录. path 使用 TightVNC 风格："/" 列盘符，"/C:" 列 C 盘."""
        self._send(
            struct.pack(">I", FILE_LIST_REQUEST)
            + b"\x00"
            + _pack_utf8(path)
        )
        self._expect(FILE_LIST_REPLY)
        raw = self._read_compressed_block()
        if not raw:
            return []

        count = struct.unpack(">I", raw[:4])[0]
        offset = 4
        entries: list[dict[str, Any]] = []
        for _ in range(count):
            size, mtime, flags = struct.unpack(">QQH", raw[offset:offset + 18])
            offset += 18
            name_len = struct.unpack(">I", raw[offset:offset + 4])[0]
            offset += 4
            name = raw[offset:offset + name_len].decode("utf-8", "replace").rstrip("\x00")
            offset += name_len
            entries.append({
                "name": name,
                "size": size,
                "mod_time": mtime / 1000.0 if mtime else 0.0,
                "is_dir": bool(flags & FILE_FLAG_DIR),
            })
        return entries

    # ---- 目录操作 --------------------------------------------------------

    def mkdir(self, path: str) -> None:
        self._send(struct.pack(">I", MKDIR_REQUEST) + _pack_utf8(path))
        self._expect(MKDIR_REPLY)

    def mkdir_parents(self, path: str) -> None:
        """逐级创建父目录，已存在则忽略（盘符层不创建）."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if len(parts) <= 1:
            return
        current = ""
        for part in parts[:-1]:
            if not current and part.upper().endswith(":"):
                current = f"/{part}"
                continue
            parent = current or "/"
            current = f"{current}/{part}"
            try:
                self.mkdir(current)
            except FtpError:
                # 目录已存在时服务端同样返回失败，确认一次再决定是否抛出
                if self._dir_exists(parent, part):
                    continue
                raise

    def _dir_exists(self, parent_path: str, name: str) -> bool:
        try:
            entries = self.list_dir(parent_path or "/")
        except FtpError:
            return False
        return any(
            e["is_dir"] and str(e["name"]).lower() == name.lower() for e in entries
        )

    def remove(self, path: str) -> None:
        self._send(struct.pack(">I", REMOVE_REQUEST) + _pack_utf8(path))
        self._expect(REMOVE_REPLY)

    def rename(self, src: str, dst: str) -> None:
        self._send(
            struct.pack(">I", RENAME_REQUEST) + _pack_utf8(src) + _pack_utf8(dst)
        )
        self._expect(RENAME_REPLY)

    def dir_size(self, path: str) -> int:
        self._send(struct.pack(">I", DIRSIZE_REQUEST) + _pack_utf8(path))
        self._expect(DIRSIZE_REPLY)
        return struct.unpack(">Q", _recv_exact(self.sock, 8))[0]

    # ---- 上传 ------------------------------------------------------------

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        overwrite: bool = True,
        chunk_size: int = DEFAULT_CHUNK,
    ) -> int:
        """上传本地文件，返回字节数."""
        size = 0
        self._send(
            struct.pack(">I", UPLOAD_START_REQUEST)
            + _pack_utf8(remote_path)
            + (b"\x01" if overwrite else b"\x00")
            + struct.pack(">Q", 0)
        )
        self._expect(UPLOAD_START_REPLY)

        with open(local_path, "rb") as f:
            while True:
                block = f.read(chunk_size)
                if not block:
                    break
                self._send(
                    struct.pack(">I", UPLOAD_DATA_REQUEST)
                    + b"\x00"
                    + struct.pack(">II", len(block), len(block))
                    + block
                )
                self._expect(UPLOAD_DATA_REPLY)
                size += len(block)

        import os

        mtime_ms = int(os.stat(local_path).st_mtime * 1000)
        self._send(
            struct.pack(">I", UPLOAD_END_REQUEST)
            + struct.pack(">H", 0)
            + struct.pack(">Q", mtime_ms)
        )
        self._expect(UPLOAD_END_REPLY)
        return size

    # ---- 下载 ------------------------------------------------------------

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        offset: int = 0,
        chunk_size: int = DEFAULT_CHUNK,
    ) -> int:
        """下载远程文件到本地，返回写入字节数."""
        self._send(
            struct.pack(">I", DOWNLOAD_START_REQUEST)
            + _pack_utf8(remote_path)
            + struct.pack(">Q", offset)
        )
        self._expect(DOWNLOAD_START_REPLY)

        written = 0
        with open(local_path, "wb") as f:
            while True:
                self._send(
                    struct.pack(">I", DOWNLOAD_DATA_REQUEST)
                    + b"\x00"
                    + struct.pack(">I", chunk_size)
                )
                mid = self._read_message_id()
                if mid == LAST_REQUEST_FAILED_REPLY:
                    raise self._read_failure(mid)
                if mid == DOWNLOAD_END_REPLY:
                    _recv_u8(self.sock)                    # file flags
                    _recv_exact(self.sock, 8)              # modification time
                    break
                if mid != DOWNLOAD_DATA_REPLY:
                    raise FtpError(
                        f"下载响应不匹配: 0x{mid:08X}", mid
                    )
                data = self._read_compressed_block()
                if not data:
                    break
                f.write(data)
                written += len(data)
        return written

    def read_file(self, remote_path: str) -> bytes:
        """读取远程文件内容到内存（小文件用）."""
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix="lds-rfb-", suffix=".tmp")
        import os

        os.close(fd)
        try:
            self.download_file(remote_path, tmp)
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def md5_check(self, remote_path: str, offset: int = 0, length: int = 65536) -> str:
        """服务端 MD5 扩展（TightVNC 2.8 服务端默认未实现，用作可选校验）."""
        raise FtpError("服务端未实现 MD5 校验扩展")
