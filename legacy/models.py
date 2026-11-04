#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据模型定义模块
包含枚举类型和数据类
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional


class NormalizationMode(Enum):
    """
    归一化模式枚举
    用于图像预处理的不同归一化方法
    """
    IDENTITY = 0                    # 无归一化
    HISTOGRAM_EQUALIZATION = 1      # 直方图均衡化
    MEAN_AND_STD_DEV = 2            # 均值和标准差
    MATCH_TAILS = 3                 # 匹配尾部
    ROBUST_LINE_FIT = 4             # 稳健线性拟合
    LOCAL_CORRECTION = 5            # 局部校正
    ENHANCED_LOCAL_CORRECTION = 6   # 增强局部校正
    
    def get_display_name(self) -> str:
        """获取显示名称"""
        names = {
            NormalizationMode.IDENTITY: "Identity (无归一化)",
            NormalizationMode.HISTOGRAM_EQUALIZATION: "HistogramEqualization (直方图均衡化)",
            NormalizationMode.MEAN_AND_STD_DEV: "MeanAndStdDev (均值标准差)",
            NormalizationMode.MATCH_TAILS: "MatchTails (匹配尾部)",
            NormalizationMode.ROBUST_LINE_FIT: "RobustLineFit (稳健线性拟合)",
            NormalizationMode.LOCAL_CORRECTION: "LocalCorrection (局部校正)",
            NormalizationMode.ENHANCED_LOCAL_CORRECTION: "EnhancedLocalCorrection (增强局部校正)",
        }
        return names.get(self, str(self))


class InterpolationMode(Enum):
    """
    插值模式枚举
    用于图像变换时的像素插值方法
    """
    BILINEAR = 0           # 双线性插值
    NEAREST_NEIGHBOR = 1   # 最近邻插值
    BICUBIC = 2            # 双三次插值
    
    def get_display_name(self) -> str:
        """获取显示名称"""
        names = {
            InterpolationMode.BILINEAR: "Bilinear (双线性)",
            InterpolationMode.NEAREST_NEIGHBOR: "NearestNeighbor (最近邻)",
            InterpolationMode.BICUBIC: "Bicubic (双三次)",
        }
        return names.get(self, str(self))


@dataclass
class BatchResult:
    """
    批量处理结果类
    存储单张图像的处理结果信息
    """
    file_name: str = ""              # 文件名
    defect_pixel_count: int = 0      # 缺陷像素数
    defect_ratio: str = "0.00%"      # 缺陷比例
    result: str = ""                 # 检测结果
    process_time: str = "0ms"        # 处理时间


@dataclass
class RegionParams:
    """
    训练区域参数类
    """
    origin_x: float = 0.0            # 原点X
    origin_y: float = 0.0            # 原点Y
    width: float = 100.0             # 宽度
    height: float = 100.0            # 高度
    rotation: float = 0.0            # 旋转角度
    skew: float = 0.0                # 倾斜角度


@dataclass
class PoseParams:
    """
    训练原点Pose参数类
    """
    translate_x: float = 0.0         # 平移X
    translate_y: float = 0.0         # 平移Y
    scale: float = 1.0               # 缩放
    aspect_ratio: float = 1.0        # 纵横比
    rotation: float = 0.0            # 旋转角度
    skew: float = 0.0                # 倾斜角度


@dataclass
class ThresholdParams:
    """
    阈值参数类
    """
    sobel_scale: float = 1.0         # Sobel缩放
    sobel_offset: float = 0.0        # Sobel偏移
    threshold_scale: float = 2.0     # 阈值比例
    threshold_offset: float = 10.0   # 阈值偏移
