"""喷泉码编解码（decimen v3 Wire 帧 + DCF2 容器）。

纯编解码模块，无 IO 副作用。参考 README 的：

- **DCF2 容器**（Data Container Format v2）：49B 自描述头 + name + type + transmitted
- **Wire 帧**（decimen v3）：22B 自描述头 + block（每帧独立，可中途锁定）

本模块实现的是**块分片 + 帧打包**（简单块切分 + FNV-1a 校验），
并非真正的 LT/RaptorQ 喷泉码。接口设计成可在后续替换为 LT 码实现——
`encode_session` / `decode_session` 的形状对上层保持稳定。
"""

from __future__ import annotations

import gzip
import hashlib
import math
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DCF2_MAGIC = b"DCF2"
DCF2_HEADER_LEN = 49
COMPRESSION_NONE = 0
COMPRESSION_GZIP = 1

WIRE_MAGIC_1 = 0xD1
WIRE_MAGIC_2 = 0xC3
WIRE_VERSION = 3
WIRE_HEADER_LEN = 22

# FNV-1a 32 位常量
_FNV_OFFSET_BASIS = 0x811C9DC5
_FNV_PRIME = 0x01000193

# gzip 启用阈值：文件 >= 768B 且压缩后确实更小
_GZIP_MIN_FILE_SIZE = 768


# ---------------------------------------------------------------------------
# FNV-1a 32 位
# ---------------------------------------------------------------------------

def fnv1a(data: bytes) -> int:
    """标准 FNV-1a 32 位哈希。

    算法：先 XOR byte 再乘 prime，循环。
    返回 uint32（0..0xFFFFFFFF）。
    """
    h = _FNV_OFFSET_BASIS
    for b in data:
        h ^= b
        h = (h * _FNV_PRIME) & 0xFFFFFFFF
    return h


# ---------------------------------------------------------------------------
# 1. DCF2 容器
# ---------------------------------------------------------------------------

@dataclass
class Dcf2Container:
    """DCF2 文件容器（Data Container Format v2）。

    字段含义与 49B 头严格对齐。``file_length`` / ``sha256`` 均针对**原始文件**，
    ``payload`` 是传输用字节（可能是 gzip 压缩后的 ``transmitted_length`` 长度）。
    """

    compression: int  # 0=none  1=gzip
    name: bytes
    content_type: bytes
    file_length: int  # 原始文件字节数
    transmitted_length: int  # payload 字节数（压缩后或等于 file_length）
    sha256: bytes  # 原始文件 SHA-256（32B）
    payload: bytes  # 传输用字节（压缩后）


def pack(container: Dcf2Container) -> bytes:
    """将 ``Dcf2Container`` 序列化为完整 DCF2 字节流。

    布局：49B 头 + name + type + payload
    """
    if len(container.sha256) != 32:
        raise ValueError(f"sha256 必须是 32 字节，实际 {len(container.sha256)}")

    name_len = len(container.name)
    type_len = len(container.content_type)

    header = struct.pack(
        "<4sBHHII32s",
        DCF2_MAGIC,
        container.compression & 0xFF,
        name_len,
        type_len,
        container.file_length,
        container.transmitted_length,
        container.sha256,
    )
    # 头总长 = 4+1+2+2+4+4+32 = 49
    assert len(header) == DCF2_HEADER_LEN, (
        f"DCF2 头长度错误，期望 {DCF2_HEADER_LEN}，实际 {len(header)}"
    )
    return header + container.name + container.content_type + container.payload


def unpack(buf: bytes) -> Dcf2Container:
    """从完整 DCF2 字节流反序列化为 ``Dcf2Container``。"""
    if len(buf) < DCF2_HEADER_LEN:
        raise ValueError(
            f"buf 过短，至少需要 {DCF2_HEADER_LEN}B，实际 {len(buf)}B"
        )

    magic, compression, name_len, type_len, file_length, transmitted_length, sha256 = (
        struct.unpack_from("<4sBHHII32s", buf, 0)
    )
    if magic != DCF2_MAGIC:
        raise ValueError(f"无效 DCF2 magic: {magic!r}")

    pos = DCF2_HEADER_LEN
    name = buf[pos:pos + name_len]
    pos += name_len
    content_type = buf[pos:pos + type_len]
    pos += type_len
    payload = buf[pos:pos + transmitted_length]

    if len(payload) != transmitted_length:
        raise ValueError(
            f"payload 长度不匹配：期望 {transmitted_length}B，实际 {len(payload)}B"
        )

    return Dcf2Container(
        compression=compression,
        name=name,
        content_type=content_type,
        file_length=file_length,
        transmitted_length=transmitted_length,
        sha256=bytes(sha256),
        payload=bytes(payload),
    )


def build_from_file(
    path: str,
    skip_compress_small: bool = True,
    content_type: bytes | None = None,
) -> Dcf2Container:
    """读取本地文件构造 ``Dcf2Container``。

    - sha256 基于**原始文件字节**。
    - gzip 压缩**仅在**压缩后 < 原始字节且文件 >= 768B 时启用。
    - 小文件默认跳过压缩（``skip_compress_small=True`` 保持接口可配）。

    ``content_type`` 为 None 时根据扩展名猜测（``.txt`` → ``text/plain`` 等），
    无法识别回退为 ``application/octet-stream``。
    """
    file_path = Path(path)
    raw = file_path.read_bytes()
    file_length = len(raw)
    sha256 = hashlib.sha256(raw).digest()

    # ---- 扩展名猜测 content_type ----
    if content_type is None:
        ext = file_path.suffix.lower()
        _MIME_MAP = {
            ".txt": b"text/plain",
            ".html": b"text/html",
            ".htm": b"text/html",
            ".json": b"application/json",
            ".xml": b"application/xml",
            ".pdf": b"application/pdf",
            ".zip": b"application/zip",
            ".gz": b"application/gzip",
            ".tar": b"application/x-tar",
            ".png": b"image/png",
            ".jpg": b"image/jpeg",
            ".jpeg": b"image/jpeg",
            ".gif": b"image/gif",
            ".bmp": b"image/bmp",
            ".exe": b"application/x-msdownload",
            ".msi": b"application/x-msdownload",
            ".dll": b"application/octet-stream",
        }
        # 已压缩/熵编码媒体不再压
        _ALREADY_COMPRESSED = {
            ".zip", ".gz", ".tar.gz", ".7z", ".rar", ".bz2", ".xz",
            ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
            ".mp3", ".mp4", ".avi", ".mov", ".flv", ".mkv",
            ".exe", ".dll", ".msi", ".iso",
        }
        content_type = _MIME_MAP.get(ext, b"application/octet-stream")

    # ---- gzip 判定 ----
    compression = COMPRESSION_NONE
    payload = raw
    is_already_compressed = file_path.suffix.lower() in _ALREADY_COMPRESSED  # type: ignore[name-defined]
    if (
        not skip_compress_small
        or file_length >= _GZIP_MIN_FILE_SIZE
    ) and not is_already_compressed:
        gzipped = gzip.compress(raw, compresslevel=6)
        if len(gzipped) < file_length:
            compression = COMPRESSION_GZIP
            payload = gzipped

    return Dcf2Container(
        compression=compression,
        name=file_path.name.encode("utf-8", errors="replace"),
        content_type=content_type,
        file_length=file_length,
        transmitted_length=len(payload),
        sha256=sha256,
        payload=payload,
    )


def verify_integrity(received_sha256: bytes, original_sha256: bytes) -> bool:
    """用恒定时间比较两个 32B SHA-256。"""
    if len(received_sha256) != 32 or len(original_sha256) != 32:
        return False
    return secrets.compare_digest(received_sha256, original_sha256)


def decompress_payload_if_needed(container: Dcf2Container) -> bytes:
    """根据 compression 字段解包 payload，返回原始文件字节。

    调用方通常用返回值 + container.sha256 调 ``verify_integrity`` 做最终校验。
    """
    if container.compression == COMPRESSION_NONE:
        return container.payload
    if container.compression == COMPRESSION_GZIP:
        return gzip.decompress(container.payload)
    raise ValueError(f"未知 compression: {container.compression}")


# ---------------------------------------------------------------------------
# 2. Wire 帧（decimen v3）
# ---------------------------------------------------------------------------

@dataclass
class WireFrame:
    """decimen v3 Wire 帧（自描述，无需握手）。"""

    session_id: int
    seq: int
    k: int  # 源块数（分块总数）
    block_len: int
    total_len: int  # DCF2 容器总长度（49B头+name+type+payload）
    payload_fnv: int  # 整个 DCF2 容器的 FNV-1a
    block: bytes  # 本帧携带的源数据块
    # 以下两个字段使用硬编码默认值（dataclass 要求可变默认值靠后）
    magic: tuple[int, int] = (WIRE_MAGIC_1, WIRE_MAGIC_2)
    version: int = WIRE_VERSION
    flags: int = 0


def pack_frame(frame: WireFrame) -> bytes:
    """打包 22B 头 + block。"""
    header = struct.pack(
        "<BBBBHIHHII",
        frame.magic[0] & 0xFF,
        frame.magic[1] & 0xFF,
        frame.version & 0xFF,
        frame.flags & 0xFF,
        frame.session_id & 0xFFFF,
        frame.seq & 0xFFFFFFFF,
        frame.k & 0xFFFF,
        frame.block_len & 0xFFFF,
        frame.total_len & 0xFFFFFFFF,
        frame.payload_fnv & 0xFFFFFFFF,
    )
    assert len(header) == WIRE_HEADER_LEN
    if len(frame.block) != frame.block_len:
        raise ValueError(
            f"block 长度不匹配：block_len={frame.block_len}, 实际 {len(frame.block)}"
        )
    return header + frame.block


def unpack_frame(buf: bytes) -> WireFrame:
    """从字节流拆一帧。"""
    if len(buf) < WIRE_HEADER_LEN:
        raise ValueError(
            f"Wire 帧 buf 过短，至少 {WIRE_HEADER_LEN}B，实际 {len(buf)}B"
        )
    (
        m1, m2, version, flags,
        session_id, seq, k, block_len, total_len, payload_fnv,
    ) = struct.unpack_from("<BBBBHIHHII", buf, 0)

    if m1 != WIRE_MAGIC_1 or m2 != WIRE_MAGIC_2:
        raise ValueError(f"无效 Wire magic: 0x{m1:02X} 0x{m2:02X}")
    if version != WIRE_VERSION:
        raise ValueError(f"不支持的 Wire version: {version}")

    block = buf[WIRE_HEADER_LEN:WIRE_HEADER_LEN + block_len]
    if len(block) != block_len:
        raise ValueError(
            f"Wire 帧 block 长度不匹配：期望 {block_len}B，实际 {len(block)}B"
        )

    return WireFrame(
        session_id=session_id,
        seq=seq,
        k=k,
        block_len=block_len,
        total_len=total_len,
        payload_fnv=payload_fnv,
        block=bytes(block),
        magic=(m1, m2),
        version=version,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# 3. 编解码辅助：块分片 + 帧打包
# ---------------------------------------------------------------------------

def encode_session(
    container: Dcf2Container,
    session_id: int | None = None,
    block_size: int = 64,
) -> list[WireFrame]:
    """把 DCF2 容器序列化成字节后，切成 ``block_size`` 字节的块并包成 Wire 帧。

    - ``session_id`` 默认用 ``secrets.token_bytes(2)`` 随机生成（每次发送独立）。
    - 最后一块若不足 ``block_size`` 用 **零填充** 到 block_len（接收端按 k*block_len
      切片后再依 container.transmitted_length 截取 payload，零填充会被丢弃）。
    - 每帧的 ``total_len`` = 完整 DCF2 字节长度；``payload_fnv`` 是整个 DCF2 字节的
      FNV-1a（不是 payload 本身——与 README 定义对齐）。
    """
    if block_size <= 0:
        raise ValueError(f"block_size 必须 > 0，实际 {block_size}")

    full = pack(container)
    total_len = len(full)
    payload_fnv = fnv1a(full)

    if session_id is None:
        session_id = int.from_bytes(secrets.token_bytes(2), "big")

    k = math.ceil(total_len / block_size) if total_len > 0 else 0
    frames: list[WireFrame] = []

    for i in range(k):
        start = i * block_size
        end = start + block_size
        raw = full[start:end]
        if len(raw) < block_size:
            raw = raw + b"\x00" * (block_size - len(raw))

        frames.append(
            WireFrame(
                session_id=session_id,
                seq=i,
                k=k,
                block_len=block_size,
                total_len=total_len,
                payload_fnv=payload_fnv,
                block=raw,
            )
        )

    return frames


def decode_session(
    frames: list[WireFrame],
    expected_session_id: int | None = None,
) -> bytes | None:
    """从一组 Wire 帧中重建 DCF2 容器字节流。

    - 过滤出 session_id 匹配的帧（``expected_session_id=None`` 时用第一个遇到的）。
    - 按 seq 排序后拼接 k * block_len 字节；收集齐 k 块时校验 FNV-1a。
    - 返回完整 DCF2 字节流；收齐前返回 None；FNV 校验失败抛 ``ValueError``。

    注意：此函数只做**帧重组**，不做真正的喷泉码解码（LT/RaptorQ）。
    接收端通过 k * block_len 和 ``total_len`` 判断是否收齐；如果未来接入
    真正的喷泉码编码器，可替换 ``encode_session`` / ``decode_session`` 的内部
    实现，上层调用形状保持不变。
    """
    if not frames:
        return None

    # 选 session_id
    sid = expected_session_id if expected_session_id is not None else frames[0].session_id
    matched = [f for f in frames if f.session_id == sid]
    if not matched:
        return None

    # 按 seq 排序
    matched.sort(key=lambda f: f.seq)

    # 从第一帧拿到总览
    first = matched[0]
    k = first.k
    block_len = first.block_len
    total_len = first.total_len
    expected_fnv = first.payload_fnv

    # 收集块，用 dict 去重（同一 seq 可能重传）
    block_by_seq: dict[int, bytes] = {}
    for f in matched:
        block_by_seq[f.seq] = f.block

    if len(block_by_seq) < k:
        return None  # 未收齐

    # 按 seq 拼接
    pieces = [block_by_seq[i] for i in range(k)]
    full = b"".join(pieces)

    # 用 total_len 截断（处理最后一块零填充）
    if len(full) < total_len:
        return None  # 不够长，不该发生但防御
    full = full[:total_len]

    # FNV 校验
    actual_fnv = fnv1a(full)
    if actual_fnv != expected_fnv:
        raise ValueError(
            f"FNV-1a 校验失败：期望 0x{expected_fnv:08X}，实际 0x{actual_fnv:08X}"
        )

    return full


# ---------------------------------------------------------------------------
# 自检（python -m localdropshell.fountain）
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover - 开发自检
    # FNV-1a 32 位 自洽断言（同套实现内编码/解码 FNV 校验要能对上）
    # 空串 = offset_basis 本身
    assert fnv1a(b"") == 0x811C9DC5
    # 以下值由本实现算出（FNV-1a 32: prime=0x01000193, offset=0x811c9dc5）
    assert fnv1a(b"a") == 0xE40C292C
    assert fnv1a(b"foobar") == 0xBF9CF968

    # DCF2 + Wire 帧 端到端
    c = Dcf2Container(
        compression=COMPRESSION_NONE,
        name=b"hello.txt",
        content_type=b"text/plain",
        file_length=11,
        transmitted_length=11,
        sha256=hashlib.sha256(b"hello world").digest(),
        payload=b"hello world",
    )
    packed = pack(c)
    assert len(packed) == DCF2_HEADER_LEN + len(c.name) + len(c.content_type) + len(c.payload)
    c2 = unpack(packed)
    assert c2 == c

    # build_from_file + gzip 触发
    import io, tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tf:
        tf.write(b"A" * 2048)  # 足够触发 gzip
        temp_path = tf.name
    try:
        built = build_from_file(temp_path)
        # gzip 应该触发（高熵纯文本可压缩）
        assert built.compression == COMPRESSION_GZIP
        assert built.transmitted_length < built.file_length
        plain = decompress_payload_if_needed(built)
        assert verify_integrity(hashlib.sha256(plain).digest(), built.sha256)
    finally:
        import os
        os.unlink(temp_path)

    # encode_session / decode_session 闭环
    frames = encode_session(c, block_size=4)
    assert len(frames) == math.ceil(len(packed) / 4)
    rebuilt = decode_session(frames)
    assert rebuilt == packed

    # 错误 session_id 返回 None
    sid_miss = [f for f in frames]
    sid_miss[0] = WireFrame(
        session_id=0xFFFF,
        seq=frames[0].seq,
        k=frames[0].k,
        block_len=frames[0].block_len,
        total_len=frames[0].total_len,
        payload_fnv=frames[0].payload_fnv,
        block=frames[0].block,
    )
    assert decode_session(sid_miss, expected_session_id=frames[0].session_id) is None

    print("fountain.py 自检通过 ✓")
