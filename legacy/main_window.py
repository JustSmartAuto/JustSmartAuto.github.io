#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主窗口模块
实现PatInspect工具的主界面和交互逻辑
"""

import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import List, Optional
import threading
import time

from PIL import Image, ImageTk, ImageDraw
import numpy as np

from models import (
    NormalizationMode, InterpolationMode, BatchResult,
    RegionParams, PoseParams, ThresholdParams
)
from image_processor import ImageProcessor


class MainWindow:
    """
    主窗口类
    实现PatInspect工具的主界面
    """
    
    def __init__(self, root: tk.Tk):
        """
        构造函数
        
        Args:
            root: Tkinter根窗口
        """
        self.root = root
        self.root.title("PatInspect - 基于概率统计的图像差分检测")
        self.root.geometry("1400x850")
        
        # 初始化变量
        self._init_variables()
        
        # 创建界面
        self._create_menu()
        self._create_toolbar()
        self._create_main_content()
        self._create_statusbar()
        
        # 初始化默认值
        self._initialize_defaults()
    
    def _init_variables(self):
        """初始化变量"""
        # 当前输入图像
        self.current_input_image: Optional[np.ndarray] = None
        self.current_input_pil: Optional[Image.Image] = None
        
        # 训练模式图像
        self.trained_pattern: Optional[np.ndarray] = None
        self.std_dev_image: Optional[np.ndarray] = None
        self.threshold_image: Optional[np.ndarray] = None
        self.sobel_edge_image: Optional[np.ndarray] = None
        
        # 训练图像集合
        self.training_images: List[np.ndarray] = []
        
        # 训练状态
        self.is_trained = False
        self.is_stat_training = False
        
        # 参数对象
        self.region_params = RegionParams()
        self.pose_params = PoseParams()
        self.threshold_params = ThresholdParams()
        
        # 归一化模式
        self.normalization_mode = NormalizationMode.HISTOGRAM_EQUALIZATION
        
        # 插值模式
        self.interpolation_mode = InterpolationMode.BILINEAR
        
        # 缺陷显示颜色
        self.defect_color = "red"
        self.defect_opacity = 0.7
        
        # 图像缩放和平移
        self.zoom_scale = 1.0
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        
        # 批量处理取消标志
        self.batch_cancelled = False
        self.batch_thread: Optional[threading.Thread] = None
    
    def _create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="打开图像", command=self._menu_open_image)
        file_menu.add_command(label="保存图像", command=self._menu_save_image)
        file_menu.add_command(label="打开文件夹", command=self._menu_open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        
        # 训练菜单
        train_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="训练", menu=train_menu)
        train_menu.add_command(label="训练新模式", command=self._menu_train_new)
        self.menu_stat_train = train_menu.add_command(
            label="统计训练当前模式", 
            command=self._menu_stat_train,
            state=tk.DISABLED
        )
        self.menu_end_stat_train = train_menu.add_command(
            label="结束统计训练",
            command=self._menu_end_stat_train,
            state=tk.DISABLED
        )
        self.menu_cancel_train = train_menu.add_command(
            label="取消训练模式",
            command=self._menu_cancel_train,
            state=tk.DISABLED
        )
        train_menu.add_separator()
        train_menu.add_command(label="加载模式", command=self._menu_load_pattern)
        train_menu.add_command(label="保存模式", command=self._menu_save_pattern)
        
        # 运行菜单
        run_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="运行", menu=run_menu)
        run_menu.add_command(label="运行检测", command=self._menu_run)
        run_menu.add_command(label="批量检测文件夹", command=self._menu_run_folder)
        
        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self._menu_about)
    
    def _create_toolbar(self):
        """创建工具栏"""
        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        
        # 工具栏按钮
        ttk.Button(toolbar, text="▶ 运行", command=self._btn_run).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="⚡ 训练", command=self._btn_train).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(toolbar, text="🖼 打开", command=self._btn_open_image).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="💾 保存", command=self._btn_save_image).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="📂 文件夹", command=self._btn_open_folder).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(toolbar, text="🔍+", command=self._btn_zoom_in).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="🔍-", command=self._btn_zoom_out).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="⊡ 适应", command=self._btn_fit).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(toolbar, text="❓ 帮助", command=self._btn_help).pack(side=tk.LEFT, padx=2)
    
    def _create_main_content(self):
        """创建主内容区"""
        # 创建Notebook（Tab控件）
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 创建各个Tab
        self._create_train_param_tab()
        self._create_region_tab()
        self._create_run_param_tab()
        self._create_graphics_tab()
        self._create_batch_tab()
        
        # 绑定Tab切换事件
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
    
    def _create_train_param_tab(self):
        """创建训练参数Tab"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="训练参数")
        
        # 左右分栏
        left_frame = ttk.Frame(tab, width=480)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.pack_propagate(False)
        
        right_frame = ttk.Frame(tab)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧参数面板
        # 已训练的模式图像
        pattern_frame = ttk.LabelFrame(left_frame, text="已训练的模式图像")
        pattern_frame.pack(fill=tk.X, pady=5)
        
        self.pattern_label = tk.Label(pattern_frame, bg="#333333", height=10)
        self.pattern_label.pack(fill=tk.X, padx=5, pady=5)
        
        # 按钮行
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="加载模式", command=self._btn_load_pattern).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="保存模式", command=self._btn_save_pattern).pack(side=tk.LEFT, padx=5)
        
        self.trained_region_text = ttk.Label(left_frame, text="已经过统计训练的区域: 0")
        self.trained_region_text.pack(pady=5)
        
        # Sobel参数
        sobel_frame = ttk.LabelFrame(left_frame, text="单个图像模式 (Sobel边缘检测)")
        sobel_frame.pack(fill=tk.X, pady=5)
        
        # Sobel缩放
        ttk.Label(sobel_frame, text="Sobel 缩放:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.sobel_scale_var = tk.StringVar(value="1.0")
        ttk.Entry(sobel_frame, textvariable=self.sobel_scale_var, width=10).grid(row=0, column=1, padx=5, pady=3)
        ttk.Button(sobel_frame, text="▲", width=2, command=self._sobel_scale_up).grid(row=0, column=2, padx=2)
        ttk.Button(sobel_frame, text="▼", width=2, command=self._sobel_scale_down).grid(row=0, column=3, padx=2)
        
        # Sobel偏移
        ttk.Label(sobel_frame, text="Sobel 偏移:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.sobel_offset_var = tk.StringVar(value="0")
        ttk.Entry(sobel_frame, textvariable=self.sobel_offset_var, width=10).grid(row=1, column=1, padx=5, pady=3)
        ttk.Button(sobel_frame, text="▲", width=2, command=self._sobel_offset_up).grid(row=1, column=2, padx=2)
        ttk.Button(sobel_frame, text="▼", width=2, command=self._sobel_offset_down).grid(row=1, column=3, padx=2)
        
        # 阈值参数
        threshold_frame = ttk.LabelFrame(left_frame, text="统计训练模式 (阈值参数)")
        threshold_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(threshold_frame, text="阈值比例 (A):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.threshold_scale_var = tk.StringVar(value="2.0")
        ttk.Entry(threshold_frame, textvariable=self.threshold_scale_var, width=10).grid(row=0, column=1, padx=5, pady=3)
        ttk.Button(threshold_frame, text="▲", width=2, command=self._threshold_scale_up).grid(row=0, column=2, padx=2)
        ttk.Button(threshold_frame, text="▼", width=2, command=self._threshold_scale_down).grid(row=0, column=3, padx=2)
        
        ttk.Label(threshold_frame, text="阈值偏移 (B):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.threshold_offset_var = tk.StringVar(value="10")
        ttk.Entry(threshold_frame, textvariable=self.threshold_offset_var, width=10).grid(row=1, column=1, padx=5, pady=3)
        ttk.Button(threshold_frame, text="▲", width=2, command=self._threshold_offset_up).grid(row=1, column=2, padx=2)
        ttk.Button(threshold_frame, text="▼", width=2, command=self._threshold_offset_down).grid(row=1, column=3, padx=2)
        
        # 插值法
        interp_frame = ttk.Frame(left_frame)
        interp_frame.pack(fill=tk.X, pady=5)
        ttk.Label(interp_frame, text="插值法:").pack(side=tk.LEFT, padx=5)
        self.interpolation_combo = ttk.Combobox(interp_frame, values=[
            "Bilinear (双线性)",
            "NearestNeighbor (最近邻)",
            "Bicubic (双三次)"
        ], state="readonly", width=25)
        self.interpolation_combo.current(0)
        self.interpolation_combo.pack(side=tk.LEFT, padx=5)
        self.interpolation_combo.bind("<<ComboboxSelected>>", self._on_interpolation_changed)
        
        # 操作按钮
        ttk.Button(left_frame, text="抓取训练图像与原点", command=self._btn_grab_train_image).pack(fill=tk.X, pady=3)
        ttk.Button(left_frame, text="训练新模式", command=self._btn_train_new).pack(fill=tk.X, pady=3)
        
        self.btn_stat_train = ttk.Button(left_frame, text="统计训练当前模式", command=self._btn_stat_train, state=tk.DISABLED)
        self.btn_stat_train.pack(fill=tk.X, pady=3)
        
        self.btn_end_stat_train = ttk.Button(left_frame, text="结束统计训练", command=self._btn_end_stat_train, state=tk.DISABLED)
        self.btn_end_stat_train.pack(fill=tk.X, pady=3)
        
        self.btn_cancel_train = ttk.Button(left_frame, text="取消训练模式", command=self._btn_cancel_train, state=tk.DISABLED)
        self.btn_cancel_train.pack(fill=tk.X, pady=3)
        
        # 右侧图像显示
        image_frame = ttk.LabelFrame(right_frame, text="当前输入图像")
        image_frame.pack(fill=tk.BOTH, expand=True)
        
        self.train_param_canvas = tk.Canvas(image_frame, bg="#1a1a2e", highlightthickness=0)
        self.train_param_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 绑定鼠标事件
        self.train_param_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.train_param_canvas.bind("<Button-1>", self._on_mouse_down)
        self.train_param_canvas.bind("<B1-Motion>", self._on_mouse_move)
        self.train_param_canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
    
    def _create_region_tab(self):
        """创建训练区域与原点Tab"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="训练区域与原点")
        
        # 左右分栏
        left_frame = ttk.Frame(tab, width=480)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.pack_propagate(False)
        
        right_frame = ttk.Frame(tab)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧参数面板
        # 训练区域
        region_frame = ttk.LabelFrame(left_frame, text="训练区域")
        region_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(region_frame, text="区域模式:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.region_mode_combo = ttk.Combobox(region_frame, values=[
            "AffineTransform", "Transform", "None"
        ], state="readonly", width=20)
        self.region_mode_combo.current(0)
        self.region_mode_combo.grid(row=0, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="区域形状:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.region_shape_combo = ttk.Combobox(region_frame, values=[
            "CogRectangleAffine", "CogRectangle", "CogCircle", "CogEllipse"
        ], state="readonly", width=20)
        self.region_shape_combo.current(0)
        self.region_shape_combo.grid(row=1, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="原点 X:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=3)
        self.origin_x_var = tk.StringVar(value="0")
        ttk.Entry(region_frame, textvariable=self.origin_x_var, width=15).grid(row=2, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="原点 Y:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=3)
        self.origin_y_var = tk.StringVar(value="0")
        ttk.Entry(region_frame, textvariable=self.origin_y_var, width=15).grid(row=3, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="边长 X:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=3)
        self.side_x_var = tk.StringVar(value="100")
        ttk.Entry(region_frame, textvariable=self.side_x_var, width=15).grid(row=4, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="边长 Y:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=3)
        self.side_y_var = tk.StringVar(value="100")
        ttk.Entry(region_frame, textvariable=self.side_y_var, width=15).grid(row=5, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="旋转 (deg):").grid(row=6, column=0, sticky=tk.W, padx=5, pady=3)
        self.rotation_var = tk.StringVar(value="0")
        ttk.Entry(region_frame, textvariable=self.rotation_var, width=15).grid(row=6, column=1, padx=5, pady=3)
        
        ttk.Label(region_frame, text="倾斜 (deg):").grid(row=7, column=0, sticky=tk.W, padx=5, pady=3)
        self.skew_var = tk.StringVar(value="0")
        ttk.Entry(region_frame, textvariable=self.skew_var, width=15).grid(row=7, column=1, padx=5, pady=3)
        
        ttk.Button(region_frame, text="适应图像", command=self._btn_fit_to_image).grid(row=8, column=0, columnspan=2, pady=10)
        
        # 训练原点
        pose_frame = ttk.LabelFrame(left_frame, text="训练原点 (Pose)")
        pose_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(pose_frame, text="平移 X:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.translate_x_var = tk.StringVar(value="0")
        ttk.Entry(pose_frame, textvariable=self.translate_x_var, width=15).grid(row=0, column=1, padx=5, pady=3)
        
        ttk.Label(pose_frame, text="平移 Y:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.translate_y_var = tk.StringVar(value="0")
        ttk.Entry(pose_frame, textvariable=self.translate_y_var, width=15).grid(row=1, column=1, padx=5, pady=3)
        
        ttk.Label(pose_frame, text="缩放:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=3)
        self.scale_var = tk.StringVar(value="1")
        ttk.Entry(pose_frame, textvariable=self.scale_var, width=15).grid(row=2, column=1, padx=5, pady=3)
        
        ttk.Label(pose_frame, text="纵横比 (Y/X):").grid(row=3, column=0, sticky=tk.W, padx=5, pady=3)
        self.aspect_ratio_var = tk.StringVar(value="1")
        ttk.Entry(pose_frame, textvariable=self.aspect_ratio_var, width=15).grid(row=3, column=1, padx=5, pady=3)
        
        ttk.Label(pose_frame, text="旋转 (deg):").grid(row=4, column=0, sticky=tk.W, padx=5, pady=3)
        self.train_rotation_var = tk.StringVar(value="0")
        ttk.Entry(pose_frame, textvariable=self.train_rotation_var, width=15).grid(row=4, column=1, padx=5, pady=3)
        
        ttk.Label(pose_frame, text="倾斜 (deg):").grid(row=5, column=0, sticky=tk.W, padx=5, pady=3)
        self.train_skew_var = tk.StringVar(value="0")
        ttk.Entry(pose_frame, textvariable=self.train_skew_var, width=15).grid(row=5, column=1, padx=5, pady=3)
        
        # 右侧图像显示
        image_frame = ttk.LabelFrame(right_frame, text="当前输入图像 (带区域标记)")
        image_frame.pack(fill=tk.BOTH, expand=True)
        
        self.region_canvas = tk.Canvas(image_frame, bg="#1a1a2e", highlightthickness=0)
        self.region_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_run_param_tab(self):
        """创建运行参数Tab"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="运行参数")
        
        # 左右分栏
        left_frame = ttk.Frame(tab, width=480)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.pack_propagate(False)
        
        right_frame = ttk.Frame(tab)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧参数面板
        # 归一化
        norm_frame = ttk.LabelFrame(left_frame, text="图像归一化 (Normalization)")
        norm_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(norm_frame, text="归一化模式:").pack(anchor=tk.W, padx=5, pady=3)
        self.normalize_mode_combo = ttk.Combobox(norm_frame, values=[
            "Identity (无归一化)",
            "HistogramEqualization (直方图均衡化)",
            "MeanAndStdDev (均值标准差)",
            "MatchTails (匹配尾部)",
            "RobustLineFit (稳健线性拟合)",
            "LocalCorrection (局部校正)",
            "EnhancedLocalCorrection (增强局部校正)"
        ], state="readonly", width=35)
        self.normalize_mode_combo.current(1)
        self.normalize_mode_combo.pack(fill=tk.X, padx=5, pady=3)
        self.normalize_mode_combo.bind("<<ComboboxSelected>>", self._on_normalize_mode_changed)
        
        # 局部校正参数（默认隐藏）
        self.local_correction_frame = ttk.Frame(norm_frame)
        self.local_correction_frame.pack(fill=tk.X, padx=5, pady=5)
        self.local_correction_frame.pack_forget()  # 初始隐藏
        
        ttk.Label(self.local_correction_frame, text="邻域宽度:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.neighborhood_width_var = tk.StringVar(value="32")
        ttk.Entry(self.local_correction_frame, textvariable=self.neighborhood_width_var, width=10).grid(row=0, column=1, padx=5, pady=3)
        
        ttk.Label(self.local_correction_frame, text="邻域高度:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.neighborhood_height_var = tk.StringVar(value="32")
        ttk.Entry(self.local_correction_frame, textvariable=self.neighborhood_height_var, width=10).grid(row=1, column=1, padx=5, pady=3)
        
        # 匹配图像输出
        match_frame = ttk.LabelFrame(left_frame, text="匹配图像输出")
        match_frame.pack(fill=tk.X, pady=5)
        
        self.gen_non_normalized_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(match_frame, text="生成非规范化匹配图像", variable=self.gen_non_normalized_var).pack(anchor=tk.W, padx=5, pady=3)
        
        self.gen_normalized_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(match_frame, text="生成规范化匹配图像", variable=self.gen_normalized_var).pack(anchor=tk.W, padx=5, pady=3)
        
        # 阈值处理的差异图像
        diff_frame = ttk.LabelFrame(left_frame, text="阈值处理的差异图像 (检测结果)")
        diff_frame.pack(fill=tk.X, pady=5)
        
        self.gen_abs_diff_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(diff_frame, text="生成绝对差异图像", variable=self.gen_abs_diff_var).pack(anchor=tk.W, padx=5, pady=3)
        
        # 缺陷统计
        stats_frame = ttk.LabelFrame(left_frame, text="缺陷统计信息")
        stats_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(stats_frame, text="缺陷像素数:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.defect_pixel_count_var = tk.StringVar(value="0")
        ttk.Entry(stats_frame, textvariable=self.defect_pixel_count_var, state="readonly").grid(row=0, column=1, padx=5, pady=3)
        
        ttk.Label(stats_frame, text="缺陷比例:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.defect_ratio_var = tk.StringVar(value="0.00%")
        ttk.Entry(stats_frame, textvariable=self.defect_ratio_var, state="readonly").grid(row=1, column=1, padx=5, pady=3)
        
        ttk.Label(stats_frame, text="平均缺陷强度:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=3)
        self.avg_defect_intensity_var = tk.StringVar(value="0")
        ttk.Entry(stats_frame, textvariable=self.avg_defect_intensity_var, state="readonly").grid(row=2, column=1, padx=5, pady=3)
        
        ttk.Label(stats_frame, text="最大缺陷强度:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=3)
        self.max_defect_intensity_var = tk.StringVar(value="0")
        ttk.Entry(stats_frame, textvariable=self.max_defect_intensity_var, state="readonly").grid(row=3, column=1, padx=5, pady=3)
        
        # 右侧图像显示（2x2网格）
        # 输入图像
        input_frame = ttk.LabelFrame(right_frame, text="输入图像")
        input_frame.place(relx=0, rely=0, relwidth=0.5, relheight=0.5)
        
        self.run_input_canvas = tk.Canvas(input_frame, bg="#1a1a2e", highlightthickness=0)
        self.run_input_canvas.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # 匹配图像
        match_frame = ttk.LabelFrame(right_frame, text="匹配图像")
        match_frame.place(relx=0.5, rely=0, relwidth=0.5, relheight=0.5)
        
        self.match_canvas = tk.Canvas(match_frame, bg="#1a1a2e", highlightthickness=0)
        self.match_canvas.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # 原始差异图像
        raw_diff_frame = ttk.LabelFrame(right_frame, text="原始差异图像")
        raw_diff_frame.place(relx=0, rely=0.5, relwidth=0.5, relheight=0.5)
        
        self.raw_diff_canvas = tk.Canvas(raw_diff_frame, bg="#1a1a2e", highlightthickness=0)
        self.raw_diff_canvas.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # 阈值处理差异图像
        threshold_diff_frame = ttk.LabelFrame(right_frame, text="阈值处理差异图像 (缺陷检测结果)")
        threshold_diff_frame.place(relx=0.5, rely=0.5, relwidth=0.5, relheight=0.5)
        
        self.threshold_diff_canvas = tk.Canvas(threshold_diff_frame, bg="#1a1a2e", highlightthickness=0)
        self.threshold_diff_canvas.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
    
    def _create_graphics_tab(self):
        """创建图形Tab"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="图形")
        
        # 左右分栏
        left_frame = ttk.Frame(tab, width=480)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.pack_propagate(False)
        
        right_frame = ttk.Frame(tab)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧参数面板
        # 输入显示选项
        input_frame = ttk.LabelFrame(left_frame, text="输入显示选项")
        input_frame.pack(fill=tk.X, pady=5)
        
        self.show_train_mask_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(input_frame, text="显示训练图像掩模", variable=self.show_train_mask_var, 
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        # 已训练图像显示选项
        trained_frame = ttk.LabelFrame(left_frame, text="已训练图像显示选项")
        trained_frame.pack(fill=tk.X, pady=5)
        
        self.show_pattern_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(trained_frame, text="显示模式图像", variable=self.show_pattern_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        self.show_threshold_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(trained_frame, text="显示阈值图像", variable=self.show_threshold_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        self.show_std_dev_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trained_frame, text="显示标准差图像", variable=self.show_std_dev_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        self.show_origin_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(trained_frame, text="显示原点", variable=self.show_origin_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        self.show_region_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(trained_frame, text="显示区域", variable=self.show_region_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        # 结果显示选项
        result_frame = ttk.LabelFrame(left_frame, text="结果显示选项")
        result_frame.pack(fill=tk.X, pady=5)
        
        self.show_match_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(result_frame, text="显示匹配图像", variable=self.show_match_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        self.show_diff_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(result_frame, text="显示差异图像", variable=self.show_diff_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        self.show_defect_overlay_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(result_frame, text="缺陷叠加显示", variable=self.show_defect_overlay_var,
                       command=self._update_graphics).pack(anchor=tk.W, padx=5, pady=3)
        
        # 颜色设置
        color_frame = ttk.LabelFrame(left_frame, text="缺陷显示颜色")
        color_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(color_frame, text="缺陷颜色:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.defect_color_combo = ttk.Combobox(color_frame, values=[
            "红色", "绿色", "蓝色", "黄色", "白色"
        ], state="readonly", width=15)
        self.defect_color_combo.current(0)
        self.defect_color_combo.grid(row=0, column=1, padx=5, pady=3)
        self.defect_color_combo.bind("<<ComboboxSelected>>", self._on_defect_color_changed)
        
        ttk.Label(color_frame, text="透明度:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.defect_opacity_var = tk.DoubleVar(value=0.7)
        ttk.Scale(color_frame, from_=0, to=1, variable=self.defect_opacity_var,
                 orient=tk.HORIZONTAL, command=self._on_defect_opacity_changed).grid(row=1, column=1, padx=5, pady=3, sticky=tk.EW)
        
        # 右侧图像显示
        image_frame = ttk.LabelFrame(right_frame, text="综合显示")
        image_frame.pack(fill=tk.BOTH, expand=True)
        
        self.graphics_canvas = tk.Canvas(image_frame, bg="#1a1a2e", highlightthickness=0)
        self.graphics_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_batch_tab(self):
        """创建批量处理Tab"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="批量处理")
        
        # 左右分栏
        left_frame = ttk.Frame(tab, width=480)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        left_frame.pack_propagate(False)
        
        right_frame = ttk.Frame(tab)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧参数面板
        # 批量处理设置
        settings_frame = ttk.LabelFrame(left_frame, text="批量处理设置")
        settings_frame.pack(fill=tk.X, pady=5)
        
        # 输入文件夹
        ttk.Label(settings_frame, text="输入文件夹:").pack(anchor=tk.W, padx=5, pady=3)
        input_folder_frame = ttk.Frame(settings_frame)
        input_folder_frame.pack(fill=tk.X, padx=5, pady=3)
        
        self.batch_input_folder_var = tk.StringVar()
        ttk.Entry(input_folder_frame, textvariable=self.batch_input_folder_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(input_folder_frame, text="浏览...", command=self._btn_browse_input).pack(side=tk.LEFT, padx=5)
        
        # 输出文件夹
        ttk.Label(settings_frame, text="输出文件夹:").pack(anchor=tk.W, padx=5, pady=3)
        output_folder_frame = ttk.Frame(settings_frame)
        output_folder_frame.pack(fill=tk.X, padx=5, pady=3)
        
        self.batch_output_folder_var = tk.StringVar()
        ttk.Entry(output_folder_frame, textvariable=self.batch_output_folder_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(output_folder_frame, text="浏览...", command=self._btn_browse_output).pack(side=tk.LEFT, padx=5)
        
        # 保存选项
        self.save_diff_image_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="保存差异图像", variable=self.save_diff_image_var).pack(anchor=tk.W, padx=5, pady=3)
        
        self.save_result_image_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="保存结果图像", variable=self.save_result_image_var).pack(anchor=tk.W, padx=5, pady=3)
        
        self.save_report_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="保存检测报告", variable=self.save_report_var).pack(anchor=tk.W, padx=5, pady=3)
        
        # 缺陷判定阈值
        threshold_frame = ttk.LabelFrame(left_frame, text="缺陷判定阈值")
        threshold_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(threshold_frame, text="最大允许缺陷像素数:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.max_defect_pixels_var = tk.StringVar(value="1000")
        ttk.Entry(threshold_frame, textvariable=self.max_defect_pixels_var, width=10).grid(row=0, column=1, padx=5, pady=3)
        
        ttk.Label(threshold_frame, text="最大允许缺陷比例 (%):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.max_defect_ratio_var = tk.StringVar(value="1.0")
        ttk.Entry(threshold_frame, textvariable=self.max_defect_ratio_var, width=10).grid(row=1, column=1, padx=5, pady=3)
        
        # 操作按钮
        self.btn_start_batch = ttk.Button(left_frame, text="开始批量处理", command=self._btn_start_batch)
        self.btn_start_batch.pack(fill=tk.X, pady=10)
        
        self.btn_stop_batch = ttk.Button(left_frame, text="停止处理", command=self._btn_stop_batch, state=tk.DISABLED)
        self.btn_stop_batch.pack(fill=tk.X, pady=5)
        
        # 进度条
        self.batch_progress = ttk.Progressbar(left_frame, mode="determinate", maximum=100)
        self.batch_progress.pack(fill=tk.X, padx=5, pady=10)
        
        self.batch_status_text = ttk.Label(left_frame, text="就绪")
        self.batch_status_text.pack(pady=5)
        
        # 右侧结果显示
        result_frame = ttk.LabelFrame(right_frame, text="批量处理结果")
        result_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建Treeview
        columns = ("file_name", "defect_count", "defect_ratio", "result", "process_time")
        self.batch_tree = ttk.Treeview(result_frame, columns=columns, show="headings")
        
        self.batch_tree.heading("file_name", text="文件名")
        self.batch_tree.heading("defect_count", text="缺陷像素数")
        self.batch_tree.heading("defect_ratio", text="缺陷比例")
        self.batch_tree.heading("result", text="结果")
        self.batch_tree.heading("process_time", text="处理时间")
        
        self.batch_tree.column("file_name", width=200)
        self.batch_tree.column("defect_count", width=100)
        self.batch_tree.column("defect_ratio", width=100)
        self.batch_tree.column("result", width=80)
        self.batch_tree.column("process_time", width=100)
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=scrollbar.set)
        
        self.batch_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
    
    def _create_statusbar(self):
        """创建状态栏"""
        statusbar = ttk.Frame(self.root)
        statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_text = ttk.Label(statusbar, text="未训练", foreground="darkred", font=("", 9, "bold"))
        self.status_text.pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(statusbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        
        self.time_text = ttk.Label(statusbar, text="0.0000毫秒")
        self.time_text.pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(statusbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        
        self.image_info_text = ttk.Label(statusbar, text="图像: 无")
        self.image_info_text.pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(statusbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        
        self.train_count_text = ttk.Label(statusbar, text="训练图像数: 0")
        self.train_count_text.pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(statusbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        
        self.message_text = ttk.Label(statusbar, text="就绪")
        self.message_text.pack(side=tk.LEFT, padx=10)
    
    def _initialize_defaults(self):
        """初始化默认值"""
        self._update_status("未训练", "darkred")
        self._update_training_count(0)
    
    # ==================== 菜单事件处理 ====================
    
    def _menu_open_image(self):
        """打开图像菜单"""
        self._btn_open_image()
    
    def _menu_save_image(self):
        """保存图像菜单"""
        self._btn_save_image()
    
    def _menu_open_folder(self):
        """打开文件夹菜单"""
        self._btn_open_folder()
    
    def _menu_train_new(self):
        """训练新模式菜单"""
        self._btn_train_new()
    
    def _menu_stat_train(self):
        """统计训练菜单"""
        self._btn_stat_train()
    
    def _menu_end_stat_train(self):
        """结束统计训练菜单"""
        self._btn_end_stat_train()
    
    def _menu_cancel_train(self):
        """取消训练菜单"""
        self._btn_cancel_train()
    
    def _menu_load_pattern(self):
        """加载模式菜单"""
        self._btn_load_pattern()
    
    def _menu_save_pattern(self):
        """保存模式菜单"""
        self._btn_save_pattern()
    
    def _menu_run(self):
        """运行检测菜单"""
        self._btn_run()
    
    def _menu_run_folder(self):
        """批量检测文件夹菜单"""
        self.notebook.select(4)  # 切换到批量处理Tab
    
    def _menu_about(self):
        """关于菜单"""
        messagebox.showinfo(
            "关于",
            "PatInspect - 基于概率统计的图像差分检测工具\n\n"
            "版本: 1.0\n"
            "功能: 通过统计训练建立标准模式，检测图像缺陷\n\n"
            "原理: 比较输入图像与训练模式的差异，\n"
            "      通过阈值处理识别潜在缺陷区域。"
        )
    
    # ==================== 工具栏按钮事件 ====================
    
    def _btn_run(self):
        """运行检测按钮"""
        if not self.is_trained:
            messagebox.showwarning("提示", "请先训练模式图像！")
            return
        
        if self.current_input_image is None:
            messagebox.showwarning("提示", "请先加载输入图像！")
            return
        
        try:
            start_time = time.time()
            
            # 执行检测
            self._perform_inspection()
            
            elapsed = (time.time() - start_time) * 1000
            self.time_text.config(text=f"{elapsed:.4f}毫秒")
            self.message_text.config(text="检测完成")
            
        except Exception as e:
            messagebox.showerror("错误", f"检测失败: {str(e)}")
            self.message_text.config(text="检测失败")
    
    def _btn_train(self):
        """训练按钮"""
        self._btn_train_new()
    
    def _btn_open_image(self):
        """打开图像按钮"""
        file_path = filedialog.askopenfilename(
            title="打开图像",
            filetypes=[
                ("图像文件", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"),
                ("所有文件", "*.*")
            ]
        )
        
        if file_path:
            try:
                self._load_image(file_path)
                self.message_text.config(text=f"已加载: {os.path.basename(file_path)}")
            except Exception as e:
                messagebox.showerror("错误", f"加载图像失败: {str(e)}")
    
    def _btn_save_image(self):
        """保存图像按钮"""
        if self.current_input_image is None:
            messagebox.showwarning("提示", "没有可保存的图像！")
            return
        
        file_path = filedialog.asksaveasfilename(
            title="保存图像",
            defaultextension=".png",
            filetypes=[
                ("PNG图像", "*.png"),
                ("JPEG图像", "*.jpg"),
                ("BMP图像", "*.bmp")
            ]
        )
        
        if file_path:
            try:
                ImageProcessor.save_image(self.current_input_image, file_path)
                self.message_text.config(text=f"已保存: {os.path.basename(file_path)}")
            except Exception as e:
                messagebox.showerror("错误", f"保存图像失败: {str(e)}")
    
    def _btn_open_folder(self):
        """打开文件夹按钮"""
        folder_path = filedialog.askdirectory(title="选择包含图像的文件夹")
        
        if folder_path:
            # 查找图像文件
            extensions = ('.png', '.jpg', '.jpeg', '.bmp')
            image_files = [f for f in os.listdir(folder_path) 
                          if f.lower().endswith(extensions)]
            
            if image_files:
                first_image = os.path.join(folder_path, image_files[0])
                self._load_image(first_image)
                self.message_text.config(text=f"文件夹共 {len(image_files)} 张图像")
            else:
                messagebox.showinfo("提示", "文件夹中没有支持的图像文件！")
    
    def _btn_zoom_in(self):
        """放大按钮"""
        self.zoom_scale *= 1.2
        self._apply_zoom()
    
    def _btn_zoom_out(self):
        """缩小按钮"""
        self.zoom_scale /= 1.2
        if self.zoom_scale < 0.1:
            self.zoom_scale = 0.1
        self._apply_zoom()
    
    def _btn_fit(self):
        """适应窗口按钮"""
        self.zoom_scale = 1.0
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        self._apply_zoom()
    
    def _btn_help(self):
        """帮助按钮"""
        self._menu_about()
    
    # ==================== 训练参数Tab事件 ====================
    
    def _sobel_scale_up(self):
        """Sobel缩放增加"""
        try:
            value = float(self.sobel_scale_var.get()) + 0.1
            self.sobel_scale_var.set(f"{value:.1f}")
            self.threshold_params.sobel_scale = value
        except ValueError:
            pass
    
    def _sobel_scale_down(self):
        """Sobel缩放减少"""
        try:
            value = max(0.1, float(self.sobel_scale_var.get()) - 0.1)
            self.sobel_scale_var.set(f"{value:.1f}")
            self.threshold_params.sobel_scale = value
        except ValueError:
            pass
    
    def _sobel_offset_up(self):
        """Sobel偏移增加"""
        try:
            value = float(self.sobel_offset_var.get()) + 1
            self.sobel_offset_var.set(f"{value:.0f}")
            self.threshold_params.sobel_offset = value
        except ValueError:
            pass
    
    def _sobel_offset_down(self):
        """Sobel偏移减少"""
        try:
            value = max(0, float(self.sobel_offset_var.get()) - 1)
            self.sobel_offset_var.set(f"{value:.0f}")
            self.threshold_params.sobel_offset = value
        except ValueError:
            pass
    
    def _threshold_scale_up(self):
        """阈值比例增加"""
        try:
            value = float(self.threshold_scale_var.get()) + 0.5
            self.threshold_scale_var.set(f"{value:.1f}")
            self.threshold_params.threshold_scale = value
            if self.is_trained:
                self._generate_threshold_image()
        except ValueError:
            pass
    
    def _threshold_scale_down(self):
        """阈值比例减少"""
        try:
            value = max(0.1, float(self.threshold_scale_var.get()) - 0.5)
            self.threshold_scale_var.set(f"{value:.1f}")
            self.threshold_params.threshold_scale = value
            if self.is_trained:
                self._generate_threshold_image()
        except ValueError:
            pass
    
    def _threshold_offset_up(self):
        """阈值偏移增加"""
        try:
            value = float(self.threshold_offset_var.get()) + 5
            self.threshold_offset_var.set(f"{value:.0f}")
            self.threshold_params.threshold_offset = value
            if self.is_trained:
                self._generate_threshold_image()
        except ValueError:
            pass
    
    def _threshold_offset_down(self):
        """阈值偏移减少"""
        try:
            value = max(0, float(self.threshold_offset_var.get()) - 5)
            self.threshold_offset_var.set(f"{value:.0f}")
            self.threshold_params.threshold_offset = value
            if self.is_trained:
                self._generate_threshold_image()
        except ValueError:
            pass
    
    def _on_interpolation_changed(self, event=None):
        """插值模式改变"""
        self.interpolation_mode = InterpolationMode(self.interpolation_combo.current())
    
    def _btn_grab_train_image(self):
        """抓取训练图像与原点"""
        if self.current_input_image is None:
            messagebox.showwarning("提示", "请先加载输入图像！")
            return
        
        height, width = self.current_input_image.shape[:2]
        self.region_params.origin_x = width / 2.0
        self.region_params.origin_y = height / 2.0
        self.region_params.width = width * 0.8
        self.region_params.height = height * 0.8
        
        self._update_region_ui()
        self.message_text.config(text="已抓取训练图像与原点")
    
    def _btn_train_new(self):
        """训练新模式"""
        if self.current_input_image is None:
            messagebox.showwarning("提示", "请先加载输入图像！")
            return
        
        try:
            # 清除之前的训练数据
            self.training_images.clear()
            
            # 添加当前图像作为第一张训练图像
            self.training_images.append(ImageProcessor.clone_image(self.current_input_image))
            
            # 创建训练模式
            self._create_trained_pattern()
            
            # 更新状态
            self.is_trained = True
            self._update_status("已训练 (单图像)", "darkgreen")
            self._update_training_count(1)
            
            # 启用统计训练按钮
            self.btn_stat_train.config(state=tk.NORMAL)
            self.btn_cancel_train.config(state=tk.NORMAL)
            
            # 显示训练模式图像
            self._display_pattern_image()
            
            self.message_text.config(text="训练新模式完成 (单图像训练)")
            
        except Exception as e:
            messagebox.showerror("错误", f"训练失败: {str(e)}")
    
    def _btn_stat_train(self):
        """统计训练当前模式"""
        if self.current_input_image is None:
            messagebox.showwarning("提示", "请先加载输入图像！")
            return
        
        if not self.is_stat_training:
            self.is_stat_training = True
            self.btn_stat_train.config(text="添加训练图像")
            self.btn_end_stat_train.config(state=tk.NORMAL)
            self.message_text.config(text="开始统计训练，请添加更多训练图像")
        
        # 添加当前图像到训练集
        self.training_images.append(ImageProcessor.clone_image(self.current_input_image))
        
        # 更新训练模式
        self._create_trained_pattern()
        
        self._update_training_count(len(self.training_images))
        self._update_status(f"统计训练中 ({len(self.training_images)}张)", "darkblue")
        self.message_text.config(text=f"已添加训练图像 #{len(self.training_images)}")
    
    def _btn_end_stat_train(self):
        """结束统计训练"""
        self.is_stat_training = False
        self.btn_stat_train.config(text="统计训练当前模式")
        self.btn_end_stat_train.config(state=tk.DISABLED)
        
        self._update_status(f"已训练 ({len(self.training_images)}张图像)", "darkgreen")
        self.message_text.config(text="统计训练完成")
    
    def _btn_cancel_train(self):
        """取消训练模式"""
        self.training_images.clear()
        self.trained_pattern = None
        self.std_dev_image = None
        self.threshold_image = None
        self.sobel_edge_image = None
        self.is_trained = False
        self.is_stat_training = False
        
        self.btn_stat_train.config(text="统计训练当前模式", state=tk.DISABLED)
        self.btn_end_stat_train.config(state=tk.DISABLED)
        self.btn_cancel_train.config(state=tk.DISABLED)
        
        self.pattern_label.config(image="")
        self._update_status("未训练", "darkred")
        self._update_training_count(0)
        self.message_text.config(text="已取消训练模式")
    
    def _btn_load_pattern(self):
        """加载模式"""
        file_path = filedialog.askopenfilename(
            title="加载训练模式",
            filetypes=[("PNG图像", "*.png"), ("所有文件", "*.*")]
        )
        
        if file_path:
            try:
                self.trained_pattern = ImageProcessor.load_image(file_path)
                if self.trained_pattern is not None:
                    self.is_trained = True
                    self._display_pattern_image()
                    self._update_status("已加载", "darkgreen")
                    self.message_text.config(text="模式加载成功")
            except Exception as e:
                messagebox.showerror("错误", f"加载模式失败: {str(e)}")
    
    def _btn_save_pattern(self):
        """保存模式"""
        if not self.is_trained or self.trained_pattern is None:
            messagebox.showwarning("提示", "没有可保存的训练模式！")
            return
        
        file_path = filedialog.asksaveasfilename(
            title="保存训练模式",
            defaultextension=".png",
            filetypes=[("PNG图像", "*.png")]
        )
        
        if file_path:
            try:
                ImageProcessor.save_image(self.trained_pattern, file_path)
                self.message_text.config(text="模式保存成功")
            except Exception as e:
                messagebox.showerror("错误", f"保存模式失败: {str(e)}")
    
    # ==================== 训练区域与原点Tab事件 ====================
    
    def _btn_fit_to_image(self):
        """适应图像"""
        if self.current_input_image is not None:
            height, width = self.current_input_image.shape[:2]
            self.region_params.origin_x = width / 2.0
            self.region_params.origin_y = height / 2.0
            self.region_params.width = width
            self.region_params.height = height
            self.region_params.rotation = 0
            self.region_params.skew = 0
            self._update_region_ui()
    
    # ==================== 运行参数Tab事件 ====================
    
    def _on_normalize_mode_changed(self, event=None):
        """归一化模式改变"""
        self.normalization_mode = NormalizationMode(self.normalize_mode_combo.current())
        
        # 显示/隐藏局部校正参数
        if self.normalization_mode in (NormalizationMode.LOCAL_CORRECTION,
                                       NormalizationMode.ENHANCED_LOCAL_CORRECTION):
            self.local_correction_frame.pack(fill=tk.X, padx=5, pady=5, after=self.normalize_mode_combo.master)
        else:
            self.local_correction_frame.pack_forget()
    
    # ==================== 图形Tab事件 ====================
    
    def _on_defect_color_changed(self, event=None):
        """缺陷颜色改变"""
        colors = ["red", "green", "blue", "yellow", "white"]
        self.defect_color = colors[self.defect_color_combo.current()]
        self._update_graphics()
    
    def _on_defect_opacity_changed(self, event=None):
        """缺陷透明度改变"""
        self.defect_opacity = self.defect_opacity_var.get()
        self._update_graphics()
    
    def _update_graphics(self):
        """更新图形显示"""
        if self.current_input_image is None:
            return
        
        # 在图形画布上绘制
        self.graphics_canvas.delete("all")
        
        # 显示当前图像
        self._display_on_canvas(self.graphics_canvas, self.current_input_image)
        
        # 绘制训练区域
        if self.show_region_var.get():
            self._draw_region_on_canvas()
        
        # 绘制原点
        if self.show_origin_var.get():
            self._draw_origin_on_canvas()
    
    # ==================== 批量处理Tab事件 ====================
    
    def _btn_browse_input(self):
        """浏览输入文件夹"""
        folder_path = filedialog.askdirectory(title="选择输入图像文件夹")
        if folder_path:
            self.batch_input_folder_var.set(folder_path)
    
    def _btn_browse_output(self):
        """浏览输出文件夹"""
        folder_path = filedialog.askdirectory(title="选择输出结果文件夹")
        if folder_path:
            self.batch_output_folder_var.set(folder_path)
    
    def _btn_start_batch(self):
        """开始批量处理"""
        if not self.is_trained:
            messagebox.showwarning("提示", "请先训练模式！")
            return
        
        input_folder = self.batch_input_folder_var.get()
        if not input_folder:
            messagebox.showwarning("提示", "请选择输入文件夹！")
            return
        
        output_folder = self.batch_output_folder_var.get()
        if not output_folder:
            messagebox.showwarning("提示", "请选择输出文件夹！")
            return
        
        # 查找图像文件
        extensions = ('.png', '.jpg', '.jpeg', '.bmp')
        image_files = [f for f in os.listdir(input_folder) 
                      if f.lower().endswith(extensions)]
        
        if not image_files:
            messagebox.showwarning("提示", "输入文件夹中没有图像文件！")
            return
        
        # 清空之前的结果
        for item in self.batch_tree.get_children():
            self.batch_tree.delete(item)
        
        self.btn_start_batch.config(state=tk.DISABLED)
        self.btn_stop_batch.config(state=tk.NORMAL)
        self.batch_cancelled = False
        
        # 获取阈值参数
        max_defect_pixels = int(self.max_defect_pixels_var.get() or 1000)
        max_defect_ratio = float(self.max_defect_ratio_var.get() or 1.0)
        
        # 在新线程中执行批量处理
        self.batch_thread = threading.Thread(
            target=self._batch_process,
            args=(image_files, input_folder, output_folder, max_defect_pixels, max_defect_ratio)
        )
        self.batch_thread.start()
    
    def _batch_process(self, image_files, input_folder, output_folder, max_defect_pixels, max_defect_ratio):
        """批量处理（在后台线程中执行）"""
        results = []
        
        for i, filename in enumerate(image_files):
            if self.batch_cancelled:
                break
            
            # 更新进度
            progress = int((i + 1) / len(image_files) * 100)
            self.root.after(0, lambda p=progress: self.batch_progress.config(value=p))
            self.root.after(0, lambda f=filename: self.batch_status_text.config(
                text=f"处理中... ({i+1}/{len(image_files)}) {f}"))
            
            file_path = os.path.join(input_folder, filename)
            
            try:
                result = self._process_single_image(file_path, max_defect_pixels, max_defect_ratio, output_folder)
                results.append(result)
                
                # 更新结果显示
                self.root.after(0, lambda r=result: self._add_batch_result(r))
                
            except Exception as e:
                error_result = BatchResult(
                    file_name=filename,
                    result=f"错误: {str(e)}",
                    process_time="0ms"
                )
                results.append(error_result)
                self.root.after(0, lambda r=error_result: self._add_batch_result(r))
        
        # 保存报告
        if self.save_report_var.get() and not self.batch_cancelled:
            self._save_batch_report(results, output_folder)
        
        # 更新UI
        self.root.after(0, lambda: self.batch_status_text.config(
            text=f"处理完成，共 {len(image_files)} 张图像"))
        self.root.after(0, lambda: self.btn_start_batch.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.btn_stop_batch.config(state=tk.DISABLED))
    
    def _process_single_image(self, file_path, max_defect_pixels, max_defect_ratio, output_folder):
        """处理单张图像"""
        start_time = time.time()
        
        # 加载图像
        image = ImageProcessor.load_image(file_path)
        if image is None:
            raise Exception("无法加载图像")
        
        # 临时设置当前图像
        original_image = self.current_input_image
        self.current_input_image = image
        
        # 执行检测
        self._perform_inspection()
        
        # 获取结果
        defect_count = int(self.defect_pixel_count_var.get() or 0)
        defect_ratio = float((self.defect_ratio_var.get() or "0%").replace("%", ""))
        
        # 保存结果图像
        if self.save_diff_image_var.get():
            diff_path = os.path.join(output_folder, 
                os.path.splitext(os.path.basename(file_path))[0] + "_diff.png")
            # 这里应该保存阈值差异图像
        
        # 恢复原始图像
        self.current_input_image = original_image
        
        elapsed = (time.time() - start_time) * 1000
        
        return BatchResult(
            file_name=os.path.basename(file_path),
            defect_pixel_count=defect_count,
            defect_ratio=f"{defect_ratio:.2f}%",
            result="通过" if defect_count <= max_defect_pixels and defect_ratio <= max_defect_ratio else "失败",
            process_time=f"{elapsed:.0f}ms"
        )
    
    def _add_batch_result(self, result: BatchResult):
        """添加批量处理结果到表格"""
        self.batch_tree.insert("", tk.END, values=(
            result.file_name,
            result.defect_pixel_count,
            result.defect_ratio,
            result.result,
            result.process_time
        ))
    
    def _save_batch_report(self, results: list, output_folder: str):
        """保存批量处理报告"""
        report_path = os.path.join(output_folder, "report.csv")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("文件名,缺陷像素数,缺陷比例,结果,处理时间\n")
            for result in results:
                f.write(f"{result.file_name},{result.defect_pixel_count},{result.defect_ratio},{result.result},{result.process_time}\n")
    
    def _btn_stop_batch(self):
        """停止批量处理"""
        self.batch_cancelled = True
        self.batch_status_text.config(text="正在停止...")
    
    # ==================== 图像交互事件 ====================
    
    def _on_mousewheel(self, event):
        """鼠标滚轮事件"""
        if event.delta > 0:
            self.zoom_scale *= 1.1
        else:
            self.zoom_scale /= 1.1
        
        if self.zoom_scale < 0.1:
            self.zoom_scale = 0.1
        if self.zoom_scale > 10:
            self.zoom_scale = 10
        
        self._apply_zoom()
    
    def _on_mouse_down(self, event):
        """鼠标按下事件"""
        self._last_mouse_x = event.x
        self._last_mouse_y = event.y
        self._is_panning = True
    
    def _on_mouse_move(self, event):
        """鼠标移动事件"""
        if getattr(self, '_is_panning', False):
            dx = event.x - self._last_mouse_x
            dy = event.y - self._last_mouse_y
            self.pan_offset_x += dx
            self.pan_offset_y += dy
            self._last_mouse_x = event.x
            self._last_mouse_y = event.y
            self._apply_zoom()
    
    def _on_mouse_up(self, event):
        """鼠标释放事件"""
        self._is_panning = False
    
    def _on_tab_changed(self, event):
        """Tab切换事件"""
        current_tab = self.notebook.index(self.notebook.select())
        if current_tab == 3:  # 图形Tab
            self._update_graphics()
    
    # ==================== 核心图像处理算法 ====================
    
    def _load_image(self, file_path: str):
        """加载图像文件"""
        image = ImageProcessor.load_image(file_path)
        if image is not None:
            self.current_input_image = image
            self.current_input_pil = Image.fromarray(image)
            
            # 更新所有图像显示
            self._display_on_canvas(self.train_param_canvas, image)
            self._display_on_canvas(self.region_canvas, image)
            self._display_on_canvas(self.run_input_canvas, image)
            self._display_on_canvas(self.graphics_canvas, image)
            
            # 更新图像信息
            height, width = image.shape[:2]
            self.image_info_text.config(text=f"图像: {width}x{height}")
            
            # 重置缩放
            self.zoom_scale = 1.0
            self.pan_offset_x = 0
            self.pan_offset_y = 0
            self._apply_zoom()
    
    def _create_trained_pattern(self):
        """创建训练模式"""
        if not self.training_images:
            return
        
        self.trained_pattern, self.std_dev_image = ImageProcessor.create_trained_pattern(
            self.training_images)
        
        # 如果是单图像训练，生成Sobel边缘图像
        if len(self.training_images) == 1:
            self.sobel_edge_image = ImageProcessor.generate_sobel_edge_image(
                self.trained_pattern,
                self.threshold_params.sobel_scale,
                self.threshold_params.sobel_offset
            )
        
        # 生成阈值图像
        self._generate_threshold_image()
    
    def _generate_threshold_image(self):
        """生成阈值图像"""
        self.threshold_image = ImageProcessor.generate_threshold_image(
            self.std_dev_image,
            self.sobel_edge_image,
            len(self.training_images),
            self.threshold_params.threshold_scale,
            self.threshold_params.threshold_offset
        )
    
    def _perform_inspection(self):
        """执行检测"""
        if self.current_input_image is None or self.trained_pattern is None:
            return
        
        # 获取局部校正参数
        neighborhood_width = int(self.neighborhood_width_var.get() or 32)
        neighborhood_height = int(self.neighborhood_height_var.get() or 32)
        
        # 执行检测
        raw_diff, threshold_diff, match_image, stats = ImageProcessor.perform_inspection(
            self.current_input_image,
            self.trained_pattern,
            self.threshold_image,
            self.normalization_mode,
            self.trained_pattern
        )
        
        # 显示结果
        self._display_on_canvas(self.raw_diff_canvas, raw_diff)
        self._display_on_canvas(self.threshold_diff_canvas, threshold_diff)
        self._display_on_canvas(self.match_canvas, match_image)
        
        # 更新缺陷统计
        self.defect_pixel_count_var.set(str(stats['defect_pixel_count']))
        self.defect_ratio_var.set(f"{stats['defect_ratio']:.2f}%")
        self.avg_defect_intensity_var.set(f"{stats['avg_defect_intensity']:.1f}")
        self.max_defect_intensity_var.set(str(stats['max_defect_intensity']))
    
    # ==================== 辅助方法 ====================
    
    def _apply_zoom(self):
        """应用缩放"""
        # 更新所有画布的显示
        if self.current_input_image is not None:
            self._display_on_canvas(self.train_param_canvas, self.current_input_image)
            self._display_on_canvas(self.region_canvas, self.current_input_image)
            self._display_on_canvas(self.graphics_canvas, self.current_input_image)
    
    def _display_on_canvas(self, canvas: tk.Canvas, image: np.ndarray):
        """在画布上显示图像"""
        if image is None:
            return
        
        # 转换为PIL图像
        if len(image.shape) == 2:
            pil_image = Image.fromarray(image, mode='L')
        else:
            pil_image = Image.fromarray(image, mode='RGB')
        
        # 缩放图像
        if self.zoom_scale != 1.0:
            new_width = int(pil_image.width * self.zoom_scale)
            new_height = int(pil_image.height * self.zoom_scale)
            pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # 转换为Tkinter图像
        tk_image = ImageTk.PhotoImage(pil_image)
        
        # 保存引用防止被垃圾回收
        canvas.tk_image = tk_image
        
        # 清空画布并显示图像
        canvas.delete("all")
        canvas.create_image(
            canvas.winfo_width() // 2 + self.pan_offset_x,
            canvas.winfo_height() // 2 + self.pan_offset_y,
            image=tk_image,
            anchor=tk.CENTER
        )
    
    def _display_pattern_image(self):
        """显示训练模式图像"""
        if self.trained_pattern is not None:
            # 缩放显示
            pil_image = Image.fromarray(self.trained_pattern)
            pil_image = pil_image.resize((200, 150), Image.Resampling.LANCZOS)
            tk_image = ImageTk.PhotoImage(pil_image)
            self.pattern_label.tk_image = tk_image  # 保存引用
            self.pattern_label.config(image=tk_image)
    
    def _update_status(self, text: str, color: str):
        """更新状态文本"""
        self.status_text.config(text=text, foreground=color)
    
    def _update_training_count(self, count: int):
        """更新训练计数"""
        self.train_count_text.config(text=f"训练图像数: {count}")
        self.trained_region_text.config(text=f"已经过统计训练的区域: {count}")
    
    def _update_region_ui(self):
        """更新区域UI"""
        self.origin_x_var.set(f"{self.region_params.origin_x:.2f}")
        self.origin_y_var.set(f"{self.region_params.origin_y:.2f}")
        self.side_x_var.set(f"{self.region_params.width:.2f}")
        self.side_y_var.set(f"{self.region_params.height:.2f}")
        self.rotation_var.set(f"{self.region_params.rotation:.2f}")
        self.skew_var.set(f"{self.region_params.skew:.2f}")
    
    def _draw_region_on_canvas(self):
        """在画布上绘制训练区域"""
        if self.current_input_image is None:
            return
        
        canvas_width = self.graphics_canvas.winfo_width()
        canvas_height = self.graphics_canvas.winfo_height()
        
        # 计算区域位置（考虑缩放和平移）
        x = self.region_params.origin_x * self.zoom_scale + self.pan_offset_x + canvas_width // 2
        y = self.region_params.origin_y * self.zoom_scale + self.pan_offset_y + canvas_height // 2
        width = self.region_params.width * self.zoom_scale
        height = self.region_params.height * self.zoom_scale
        
        # 绘制矩形
        self.graphics_canvas.create_rectangle(
            x - width / 2, y - height / 2,
            x + width / 2, y + height / 2,
            outline="yellow", width=2
        )
    
    def _draw_origin_on_canvas(self):
        """在画布上绘制原点"""
        if self.current_input_image is None:
            return
        
        canvas_width = self.graphics_canvas.winfo_width()
        canvas_height = self.graphics_canvas.winfo_height()
        
        # 计算原点位置
        x = self.region_params.origin_x * self.zoom_scale + self.pan_offset_x + canvas_width // 2
        y = self.region_params.origin_y * self.zoom_scale + self.pan_offset_y + canvas_height // 2
        cross_size = 10
        
        # 绘制十字
        self.graphics_canvas.create_line(
            x - cross_size, y, x + cross_size, y,
            fill="red", width=2
        )
        self.graphics_canvas.create_line(
            x, y - cross_size, x, y + cross_size,
            fill="red", width=2
        )
