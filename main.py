"""启动入口（根目录）：无论安装为包、`python main.py` 或 PyInstaller 打包都可用。

PyInstaller 的入口脚本与 `python cli.py` 直接运行都会把 cli.py 当成顶层模块，
相对导入（from .config import ...）随之失效；这里是带父包上下文的最薄包装。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from internet_proxy.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
