#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PatInspect - 基于概率统计的图像差分检测工具
主应用程序入口
"""

import tkinter as tk
from tkinter import ttk
from main_window import MainWindow


def main():
    """主入口函数"""
    # 创建主窗口
    root = tk.Tk()
    
    # 设置DPI感知（Windows）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    
    # 创建应用程序
    app = MainWindow(root)
    
    # 运行主循环
    root.mainloop()


if __name__ == "__main__":
    main()
