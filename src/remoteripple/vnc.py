from __future__ import annotations

import logging
import socket
import struct
import threading
import time
import zlib
from collections.abc import Callable
from typing import Any

from PIL import Image

from .rfbftp import FtpError, vnc_auth_response

LOG = logging.getLogger(__name__)

KEYSYMS = {
    "BackSpace": 0xFF08, "Tab": 0xFF09, "Return": 0xFF0D, "Escape": 0xFF1B,
    "Home": 0xFF50, "Left": 0xFF51, "Up": 0xFF52, "Right": 0xFF53,
    "Down": 0xFF54, "Prior": 0xFF55, "Next": 0xFF56, "End": 0xFF57,
    "Insert": 0xFF63, "Delete": 0xFFFF, "Pause": 0xFF13, "Scroll_Lock": 0xFF14,
    "Print": 0xFF61, "Shift_L": 0xFFE1, "Shift_R": 0xFFE2,
    "Control_L": 0xFFE3, "Control_R": 0xFFE4, "Caps_Lock": 0xFFE5,
    "Alt_L": 0xFFE9, "Alt_R": 0xFFEA, "Super_L": 0xFFEB, "Super_R": 0xFFEC,
    "Menu": 0xFF67, "Num_Lock": 0xFF7F, "space": 0x20,
}
KEYSYMS.update({f"F{i}": 0xFFBD + i for i in range(1, 25)})


class VncError(ConnectionError):
    pass


class VncClient:
    def __init__(
        self,
        host: str,
        port: int,
        password: str = "",
        on_event: Callable[[str, Any], None] | None = None,
        encoding: str = "自动",
        shared: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.on_event = on_event or (lambda _kind, _payload: None)
        self.encoding = encoding
        self.shared = shared
        self.sock: socket.socket | None = None
        self.image: Image.Image | None = None
        self.width = 0
        self.height = 0
        self.desktop_name = ""
        self.connected = False
        self.ignore_input = False
        self.clipboard_enabled = True
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._zrle = zlib.decompressobj()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"VNC-{self.host}:{self.port}", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self.connected = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        try:
            self.on_event("state", "正在连接")
            self.sock = socket.create_connection((self.host, self.port), timeout=15)
            self.sock.settimeout(1)
            self._handshake()
            self.connected = True
            self.on_event("connected", {"name": self.desktop_name, "width": self.width, "height": self.height})
            self.request_update(incremental=False)
            next_request = time.monotonic() + 0.1
            while not self._stop.is_set():
                self._read_message()
                if time.monotonic() >= next_request:
                    self.request_update(incremental=True)
                    next_request = time.monotonic() + 0.08
        except (OSError, VncError, FtpError, ValueError, zlib.error) as exc:
            if not self._stop.is_set():
                LOG.exception("VNC 会话失败")
                self.on_event("error", str(exc))
        finally:
            self.connected = False
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.on_event("disconnected", None)

    def _recv(self, size: int) -> bytes:
        if size < 0 or size > 512 * 1024 * 1024:
            raise VncError(f"非法数据长度: {size}")
        assert self.sock is not None
        data = bytearray()
        while len(data) < size:
            if self._stop.is_set():
                raise VncError("连接已停止")
            try:
                chunk = self.sock.recv(size - len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise VncError("远程主机已关闭连接")
            data.extend(chunk)
        return bytes(data)

    def _send(self, data: bytes) -> None:
        if not self.sock:
            return
        with self._send_lock:
            self.sock.sendall(data)

    def _handshake(self) -> None:
        banner = self._recv(12)
        if not banner.startswith(b"RFB "):
            raise VncError(f"无效 RFB 响应: {banner!r}")
        self._send(b"RFB 003.008\n")
        count = self._recv(1)[0]
        if count == 0:
            length = struct.unpack(">I", self._recv(4))[0]
            raise VncError(self._recv(length).decode("utf-8", "replace"))
        security_types = list(self._recv(count))
        if 16 in security_types:
            self._tight_auth()
        elif 2 in security_types:
            self._send(b"\x02")
            if not self.password:
                raise VncError("服务端要求 VNC 密码")
            self._send(vnc_auth_response(self._recv(16), self.password))
        elif 1 in security_types:
            self._send(b"\x01")
        else:
            raise VncError(f"不支持的安全类型: {security_types}")
        status = struct.unpack(">I", self._recv(4))[0]
        if status:
            raise VncError("VNC 认证失败，请检查密码")
        self._send(b"\x01" if self.shared else b"\x00")
        self.width, self.height = struct.unpack(">HH", self._recv(4))
        self._recv(16)
        name_length = struct.unpack(">I", self._recv(4))[0]
        self.desktop_name = self._recv(name_length).decode("utf-8", "replace")
        self.image = Image.new("RGB", (self.width, self.height), "black")
        pixel_format = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        self._send(struct.pack(">BBxx", 0, 1) + pixel_format)
        encoding_map = {"原始": 0, "Hextile": 5, "ZRLE": 16}
        preferred = encoding_map.get(self.encoding)
        encodings = [0, 1, 5, 16]
        if preferred in encodings:
            encodings.remove(preferred)
            encodings.insert(0, preferred)
        encodings += [-223, -239]
        self._send(struct.pack(">BxH", 2, len(encodings)) + b"".join(struct.pack(">i", value) for value in encodings))

    def _tight_auth(self) -> None:
        self._send(b"\x10")
        tunnels = struct.unpack(">I", self._recv(4))[0]
        if tunnels:
            raise VncError("暂不支持 Tight 隧道")
        auth_count = struct.unpack(">I", self._recv(4))[0]
        capabilities = []
        for _ in range(auth_count):
            code = struct.unpack(">I", self._recv(4))[0]
            self._recv(12)
            capabilities.append(code)
        if auth_count:
            if 2 not in capabilities:
                raise VncError(f"不支持的 Tight 认证方式: {capabilities}")
            self._send(struct.pack(">I", 2))
            if not self.password:
                raise VncError("服务端要求 VNC 密码")
            self._send(vnc_auth_response(self._recv(16), self.password))

    def request_update(self, incremental: bool = True) -> None:
        if self.sock and self.width and self.height:
            self._send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.width, self.height))

    def _read_message(self) -> None:
        message_type = self._recv(1)[0]
        if message_type == 0:
            self._recv(3)
            count = struct.unpack(">H", self._recv(2))[0]
            for _ in range(count):
                x, y, width, height, encoding = struct.unpack(">HHHHi", self._recv(12))
                self._decode_rectangle(x, y, width, height, encoding)
            if self.image:
                self.on_event("frame", self.image.copy())
        elif message_type == 1:
            self._recv(3)
            first, count = struct.unpack(">HH", self._recv(4))
            self._recv(first * 0 + count * 6)
        elif message_type == 2:
            self.on_event("bell", None)
        elif message_type == 3:
            self._recv(3)
            length = struct.unpack(">I", self._recv(4))[0]
            text = self._recv(length).decode("utf-8", "replace").rstrip("\x00")
            if self.clipboard_enabled:
                self.on_event("clipboard", text)
        else:
            raise VncError(f"未知服务器消息: {message_type}")

    def _decode_rectangle(self, x: int, y: int, width: int, height: int, encoding: int) -> None:
        if not self.image or width <= 0 or height <= 0:
            raise VncError("无效的帧缓冲区矩形")
        if encoding == 0:
            tile = Image.frombytes("RGB", (width, height), self._recv(width * height * 4), "raw", "BGRX")
            self.image.paste(tile, (x, y))
        elif encoding == 1:
            src_x, src_y = struct.unpack(">HH", self._recv(4))
            tile = self.image.crop((src_x, src_y, src_x + width, src_y + height))
            self.image.paste(tile, (x, y))
        elif encoding == 5:
            self._decode_hextile(x, y, width, height)
        elif encoding == 16:
            self._decode_zrle(x, y, width, height)
        elif encoding == -223:
            self.width, self.height = width, height
            resized = Image.new("RGB", (width, height), "black")
            resized.paste(self.image, (0, 0))
            self.image = resized
            self.on_event("resized", (width, height))
        elif encoding == -239:
            self._recv(width * height * 4 + ((width + 7) // 8) * height)
        else:
            raise VncError(f"收到未协商的编码: {encoding}")

    def _pixel(self) -> tuple[int, int, int]:
        blue, green, red = self._recv(3)
        return red, green, blue

    def _decode_hextile(self, x: int, y: int, width: int, height: int) -> None:
        assert self.image is not None
        background = (0, 0, 0)
        foreground = (255, 255, 255)
        for tile_y in range(0, height, 16):
            tile_h = min(16, height - tile_y)
            for tile_x in range(0, width, 16):
                tile_w = min(16, width - tile_x)
                flags = self._recv(1)[0]
                if flags & 1:
                    raw = self._recv(tile_w * tile_h * 4)
                    tile = Image.frombytes("RGB", (tile_w, tile_h), raw, "raw", "BGRX")
                    self.image.paste(tile, (x + tile_x, y + tile_y))
                    continue
                if flags & 2:
                    background = self._pixel()
                if flags & 4:
                    foreground = self._pixel()
                tile = Image.new("RGB", (tile_w, tile_h), background)
                count = self._recv(1)[0] if flags & 8 else 0
                for _ in range(count):
                    color = self._pixel() if flags & 16 else foreground
                    packed = self._recv(1)[0]
                    sub_x, sub_y = packed >> 4, packed & 15
                    packed = self._recv(1)[0]
                    sub_w, sub_h = (packed >> 4) + 1, (packed & 15) + 1
                    tile.paste(color, (sub_x, sub_y, sub_x + sub_w, sub_y + sub_h))
                self.image.paste(tile, (x + tile_x, y + tile_y))

    def _decode_zrle(self, x: int, y: int, width: int, height: int) -> None:
        assert self.image is not None
        length = struct.unpack(">I", self._recv(4))[0]
        raw = self._zrle.decompress(self._recv(length))
        offset = 0

        def take(count: int) -> bytes:
            nonlocal offset
            if offset + count > len(raw):
                raise VncError("ZRLE 数据不完整")
            result = raw[offset:offset + count]
            offset += count
            return result

        for tile_y in range(0, height, 64):
            tile_h = min(64, height - tile_y)
            for tile_x in range(0, width, 64):
                tile_w = min(64, width - tile_x)
                palette_size = take(1)[0]
                palette = []
                for _ in range(palette_size):
                    blue, green, red = take(3)
                    palette.append((red, green, blue))
                tile = Image.new("RGB", (tile_w, tile_h))
                pixels = tile.load()
                if palette_size == 0:
                    for py in range(tile_h):
                        for px in range(tile_w):
                            blue, green, red = take(3)
                            pixels[px, py] = red, green, blue
                elif palette_size == 1:
                    tile.paste(palette[0], (0, 0, tile_w, tile_h))
                else:
                    for py in range(tile_h):
                        px = 0
                        while px < tile_w:
                            value = take(1)[0]
                            run = 1
                            if value == 128:
                                color = tuple(take(3))
                                color = color[2], color[1], color[0]
                                run = take(1)[0] + 1
                            elif value > 128:
                                color = palette[value - 128]
                                run = take(1)[0] + 1
                            elif value in (2, 4, 8, 16) and value == palette_size:
                                bits = {2: 1, 4: 2, 8: 4, 16: 8}[value]
                                packed = take((tile_w * bits + 7) // 8)
                                for index in range(tile_w):
                                    bit = index * bits
                                    shift = 8 - bits - (bit % 8)
                                    palette_index = (packed[bit // 8] >> shift) & (value - 1)
                                    pixels[index, py] = palette[palette_index]
                                px = tile_w
                                continue
                            else:
                                color = palette[value]
                            for _ in range(run):
                                if px < tile_w:
                                    pixels[px, py] = color
                                    px += 1
                self.image.paste(tile, (x + tile_x, y + tile_y))

    def pointer(self, x: int, y: int, mask: int) -> None:
        if self.connected and not self.ignore_input:
            x = max(0, min(self.width - 1, int(x)))
            y = max(0, min(self.height - 1, int(y)))
            self._send(struct.pack(">BBHH", 5, mask & 0xFF, x, y))

    def key(self, keysym: int, down: bool) -> None:
        if self.connected and not self.ignore_input:
            self._send(struct.pack(">BBxxI", 4, int(down), keysym))

    def key_event(self, keysym_name: str, char: str, down: bool) -> None:
        keysym = KEYSYMS.get(keysym_name)
        if keysym is None and char:
            keysym = ord(char.lower())
        if keysym is None and len(keysym_name) == 1:
            keysym = ord(keysym_name.lower())
        if keysym is not None:
            self.key(keysym, down)

    def send_cad(self) -> None:
        for key, down in ((0xFFE3, True), (0xFFE9, True), (0xFFFF, True), (0xFFFF, False), (0xFFE9, False), (0xFFE3, False)):
            self.key(key, down)

    def send_clipboard(self, text: str) -> None:
        if self.connected and self.clipboard_enabled:
            raw = text.encode("utf-8", "replace")
            self._send(struct.pack(">BBxxxI", 6, 0, len(raw)) + raw)

    def screenshot(self) -> Image.Image | None:
        return self.image.copy() if self.image else None
