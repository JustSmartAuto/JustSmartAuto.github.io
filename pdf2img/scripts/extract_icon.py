"""Extract the icon from a PE file into a .ico using ctypes + Win32 APIs."""
import ctypes
from ctypes import wintypes

RT_ICON = 3
RT_GROUP_ICON = 14

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

kernel32.EnumResourceNamesW.restype = ctypes.c_bool
kernel32.EnumResourceNamesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
kernel32.LoadLibraryExW.restype = ctypes.c_void_p
kernel32.FindResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
kernel32.LoadResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
kernel32.FindResourceW.restype = ctypes.c_void_p
kernel32.LoadResource.restype = ctypes.c_void_p
kernel32.LockResource.restype = ctypes.c_void_p
kernel32.LockResource.argtypes = [ctypes.c_void_p]

VOIDP = ctypes.c_void_p
cb = ctypes.WINFUNCTYPE(ctypes.c_bool, VOIDP, VOIDP, VOIDP, VOIDP)


def extract_icon(exe_path, ico_path):
    hsrc = kernel32.LoadLibraryExW(exe_path, None, 0x00000020)  # LOAD_LIBRARY_AS_IMAGE_RESOURCE
    assert hsrc, "LoadLibraryExW failed"

    group_ids = []

    def enum_group(hmod, lptype, lpname, lparam):
        group_ids.append(lpname)
        return True

    kernel32.EnumResourceNamesW(hsrc, RT_GROUP_ICON, cb(enum_group), None)
    assert group_ids, "no group icon found"
    gid = group_ids[0]

    # ICONDIR header
    hgres = kernel32.LoadResource(hsrc, kernel32.FindResourceW(hsrc, gid, RT_GROUP_ICON))
    gdata = ctypes.cast(kernel32.LockResource(hgres), ctypes.POINTER(ctypes.c_ubyte))
    reserved, typ, count = ctypes.cast(gdata, ctypes.POINTER(wintypes.WORD * 3)).contents
    entries = []
    for i in range(count):
        b = ctypes.cast(
            ctypes.addressof(gdata.contents) + 6 + i * 14,
            ctypes.POINTER(ctypes.c_ubyte * 14),
        ).contents
        width, height, colors, _r, planes, bitcount, _x, _y = b[:8]
        size = int.from_bytes(bytes(b[8:12]), "little")
        resid = int.from_bytes(bytes(b[12:14]), "little")
        entries.append({
            "width": 0 if width == 0 else width,
            "height": 0 if height == 0 else height,
            "colors": colors,
            "planes": planes,
            "bitcount": bitcount,
            "size": size,
            "resid": resid,
        })

    images = []
    for e in entries:
        hgres = kernel32.LoadResource(hsrc, kernel32.FindResourceW(hsrc, e["resid"], RT_ICON))
        p = ctypes.cast(kernel32.LockResource(hgres), ctypes.POINTER(ctypes.c_ubyte))
        images.append(bytes(bytearray(p[i] for i in range(e["size"]))))

    offset = 6 + 16 * len(entries)
    with open(ico_path, "wb") as f:
        f.write(reserved.to_bytes(2, "little"))
        f.write(typ.to_bytes(2, "little"))
        f.write(count.to_bytes(2, "little"))
        for e, img in zip(entries, images):
            hdr = bytes([e["width"] & 0xFF, e["height"] & 0xFF, e["colors"], 0,
                         e["planes"].to_bytes(2, "little")[0], e["planes"].to_bytes(2, "little")[1],
                         e["bitcount"].to_bytes(2, "little")[0], e["bitcount"].to_bytes(2, "little")[1],
                         ]) if False else b""
            f.write(bytes([min(e["width"], 255), min(e["height"], 255), e["colors"], 0]))
            f.write(e["planes"].to_bytes(2, "little"))
            f.write(e["bitcount"].to_bytes(2, "little"))
            f.write(e["size"].to_bytes(4, "little"))
            f.write(offset.to_bytes(4, "little"))
            offset += e["size"]
        for img in images:
            f.write(img)


if __name__ == "__main__":
    extract_icon(r"D:\JustStupid\imgproc图像处理_2604111701\PDF转图片工具.exe", "logo.ico")
    print("ok")
