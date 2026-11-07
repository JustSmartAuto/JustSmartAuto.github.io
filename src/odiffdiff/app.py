"""应用入口。

用法：
    odiff-diff
    python -m odiffdiff.app
"""

from __future__ import annotations


def main() -> None:
    from odiffdiff.ui import ImageDiffPyApp

    app = ImageDiffPyApp()
    app.run()


if __name__ == "__main__":
    main()
