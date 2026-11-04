# PyInstaller 打包配置
# 运行: uv run pyinstaller pdf2img.spec

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["src/pdf2img/main.py"],
    pathex=["src"],
    hookspath=["."],
    runtime_hooks=["rthook-pymupdf.py"],
    hiddenimports=["pymupdf", "tkinterdnd2"],
    datas=[],
    binaries=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PDF转图片工具",
    icon="logo.ico",
    console=False,
    upx=False,
)
