"""odiff-diff — 图像差分 diff 器。

基于 odiff.exe（Zig 实现的 SIMD 加速像素级图像差异工具）+ Python Tk GUI，
提供左右图片并排 + diff 结果三栏对比，支持阈值 / 抗锯齿 / 差异颜色等参数。
"""

from __future__ import annotations

__version__ = "26.11.4.1"
