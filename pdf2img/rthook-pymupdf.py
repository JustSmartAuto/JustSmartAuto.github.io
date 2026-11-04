# PyInstaller runtime hook: block pymupdf.layout (ML-based layout analysis).
# Its ONNX resources (~50MB) are not bundled; rendering to pixmap doesn't need it.
# Setting sys.modules entry to None makes `import pymupdf.layout` raise ImportError,
# which pymupdf handles gracefully (try/except ImportError in __init__).
import sys

sys.modules["pymupdf.layout"] = None
