#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图像处理核心模块
包含PatInspect算法的图像处理功能
"""

import numpy as np
from PIL import Image, ImageFilter
from typing import List, Tuple, Optional
from models import NormalizationMode, InterpolationMode


class ImageProcessor:
    """
    图像处理器类
    实现PatInspect算法的核心图像处理功能
    """
    
    @staticmethod
    def load_image(file_path: str) -> Optional[np.ndarray]:
        """
        加载图像文件
        
        Args:
            file_path: 图像文件路径
            
        Returns:
            图像数组，加载失败返回None
        """
        try:
            img = Image.open(file_path)
            # 转换为RGB模式
            if img.mode != 'RGB':
                img = img.convert('RGB')
            return np.array(img)
        except Exception as e:
            print(f"加载图像失败: {e}")
            return None
    
    @staticmethod
    def save_image(image: np.ndarray, file_path: str) -> bool:
        """
        保存图像到文件
        
        Args:
            image: 图像数组
            file_path: 保存路径
            
        Returns:
            保存成功返回True
        """
        try:
            # 确保数据类型为uint8
            if image.dtype != np.uint8:
                image = np.clip(image, 0, 255).astype(np.uint8)
            img = Image.fromarray(image)
            img.save(file_path)
            return True
        except Exception as e:
            print(f"保存图像失败: {e}")
            return False
    
    @staticmethod
    def clone_image(image: np.ndarray) -> np.ndarray:
        """
        克隆图像
        
        Args:
            image: 源图像
            
        Returns:
            克隆的图像副本
        """
        return image.copy()
    
    @staticmethod
    def create_trained_pattern(training_images: List[np.ndarray]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        创建训练模式（统计平均）
        
        Args:
            training_images: 训练图像列表
            
        Returns:
            (训练模式图像, 标准差图像) 元组
        """
        if not training_images:
            return None, None
        
        # 获取图像尺寸
        height, width = training_images[0].shape[:2]
        
        # 转换为浮点数数组进行计算
        images_float = [img.astype(np.float64) for img in training_images]
        
        # 计算平均值
        mean_image = np.mean(images_float, axis=0)
        
        # 计算标准差
        if len(training_images) > 1:
            std_dev_image = np.std(images_float, axis=0)
        else:
            # 单张图像时标准差为0
            std_dev_image = np.zeros_like(mean_image)
        
        # 转换为uint8
        mean_image = np.clip(mean_image, 0, 255).astype(np.uint8)
        std_dev_image = np.clip(std_dev_image, 0, 255).astype(np.uint8)
        
        return mean_image, std_dev_image
    
    @staticmethod
    def generate_sobel_edge_image(image: np.ndarray, scale: float = 1.0, offset: float = 0.0) -> np.ndarray:
        """
        生成Sobel边缘图像
        
        Args:
            image: 输入图像
            scale: Sobel缩放因子
            offset: Sobel偏移量
            
        Returns:
            Sobel边缘图像
        """
        # 转换为灰度图
        if len(image.shape) == 3:
            gray = np.mean(image, axis=2).astype(np.float64)
        else:
            gray = image.astype(np.float64)
        
        height, width = gray.shape
        
        # Sobel算子
        sobel_x = np.array([[-1, 0, 1],
                           [-2, 0, 2],
                           [-1, 0, 1]])
        sobel_y = np.array([[-1, -2, -1],
                           [0, 0, 0],
                           [1, 2, 1]])
        
        # 计算边缘
        edge_image = np.zeros_like(gray)
        
        for y in range(1, height - 1):
            for x in range(1, width - 1):
                patch = gray[y-1:y+2, x-1:x+2]
                gx = np.sum(patch * sobel_x)
                gy = np.sum(patch * sobel_y)
                magnitude = np.sqrt(gx * gx + gy * gy)
                edge_image[y, x] = magnitude * scale + offset
        
        # 裁剪并转换为uint8
        edge_image = np.clip(edge_image, 0, 255).astype(np.uint8)
        
        # 转换为3通道图像
        edge_rgb = np.stack([edge_image] * 3, axis=-1)
        
        return edge_rgb
    
    @staticmethod
    def generate_threshold_image(std_dev_image: Optional[np.ndarray], 
                                 sobel_edge_image: Optional[np.ndarray],
                                 training_count: int,
                                 threshold_scale: float = 2.0,
                                 threshold_offset: float = 10.0) -> np.ndarray:
        """
        生成阈值图像
        
        Args:
            std_dev_image: 标准差图像
            sobel_edge_image: Sobel边缘图像
            training_count: 训练图像数量
            threshold_scale: 阈值比例
            threshold_offset: 阈值偏移
            
        Returns:
            阈值图像
        """
        if training_count > 1 and std_dev_image is not None:
            # 统计训练: 使用标准差图像
            if len(std_dev_image.shape) == 3:
                avg_std_dev = np.mean(std_dev_image, axis=2)
            else:
                avg_std_dev = std_dev_image
            threshold = avg_std_dev * threshold_scale + threshold_offset
        elif sobel_edge_image is not None:
            # 单图像训练: 使用Sobel边缘图像
            if len(sobel_edge_image.shape) == 3:
                avg_edge = np.mean(sobel_edge_image, axis=2)
            else:
                avg_edge = sobel_edge_image
            threshold = avg_edge * threshold_scale + threshold_offset
        else:
            # 默认阈值
            if std_dev_image is not None:
                height, width = std_dev_image.shape[:2]
            elif sobel_edge_image is not None:
                height, width = sobel_edge_image.shape[:2]
            else:
                height, width = 100, 100
            threshold = np.full((height, width), threshold_offset)
        
        threshold = np.clip(threshold, 0, 255).astype(np.uint8)
        
        # 转换为3通道图像
        if len(threshold.shape) == 2:
            threshold_rgb = np.stack([threshold] * 3, axis=-1)
        else:
            threshold_rgb = threshold
            
        return threshold_rgb
    
    @staticmethod
    def normalize_image(image: np.ndarray, mode: NormalizationMode, 
                        trained_pattern: Optional[np.ndarray] = None,
                        neighborhood_width: int = 32,
                        neighborhood_height: int = 32) -> np.ndarray:
        """
        图像归一化
        
        Args:
            image: 输入图像
            mode: 归一化模式
            trained_pattern: 训练模式图像（用于某些归一化方法）
            neighborhood_width: 邻域宽度（用于局部校正）
            neighborhood_height: 邻域高度（用于局部校正）
            
        Returns:
            归一化后的图像
        """
        if mode == NormalizationMode.IDENTITY:
            return image.copy()
        
        elif mode == NormalizationMode.HISTOGRAM_EQUALIZATION:
            return ImageProcessor.histogram_equalization(image)
        
        elif mode == NormalizationMode.MEAN_AND_STD_DEV:
            return ImageProcessor.mean_std_dev_normalization(image, trained_pattern)
        
        elif mode == NormalizationMode.MATCH_TAILS:
            return ImageProcessor.match_tails_normalization(image)
        
        elif mode in (NormalizationMode.LOCAL_CORRECTION, 
                      NormalizationMode.ENHANCED_LOCAL_CORRECTION):
            return ImageProcessor.local_correction_normalization(
                image, neighborhood_width, neighborhood_height)
        
        return image.copy()
    
    @staticmethod
    def histogram_equalization(image: np.ndarray) -> np.ndarray:
        """
        直方图均衡化
        
        Args:
            image: 输入图像
            
        Returns:
            均衡化后的图像
        """
        result = image.copy()
        height, width = image.shape[:2]
        
        # 对每个通道进行均衡化
        for c in range(min(3, image.shape[2] if len(image.shape) > 2 else 1)):
            if len(image.shape) == 3:
                channel = image[:, :, c]
            else:
                channel = image
            
            # 计算直方图
            hist, bins = np.histogram(channel.flatten(), 256, [0, 256])
            
            # 计算累积分布函数
            cdf = hist.cumsum()
            cdf_normalized = cdf * 255 / cdf[-1]
            
            # 应用查找表
            if len(image.shape) == 3:
                result[:, :, c] = np.interp(channel.flatten(), bins[:-1], cdf_normalized).reshape(height, width).astype(np.uint8)
            else:
                result = np.interp(channel.flatten(), bins[:-1], cdf_normalized).reshape(height, width).astype(np.uint8)
        
        return result
    
    @staticmethod
    def mean_std_dev_normalization(image: np.ndarray, 
                                   trained_pattern: Optional[np.ndarray]) -> np.ndarray:
        """
        均值和标准差归一化
        
        Args:
            image: 输入图像
            trained_pattern: 训练模式图像
            
        Returns:
            归一化后的图像
        """
        result = image.astype(np.float64)
        
        # 计算输入图像的均值和标准差
        mean = np.mean(result)
        std_dev = np.std(result)
        
        if trained_pattern is not None:
            pattern_mean = np.mean(trained_pattern)
            pattern_std_dev = np.std(trained_pattern)
            
            # 避免除以零
            if std_dev > 0 and pattern_std_dev > 0:
                result = (result - mean) / std_dev * pattern_std_dev + pattern_mean
        
        return np.clip(result, 0, 255).astype(np.uint8)
    
    @staticmethod
    def match_tails_normalization(image: np.ndarray) -> np.ndarray:
        """
        匹配尾部归一化（简化实现，使用直方图均衡化）
        
        Args:
            image: 输入图像
            
        Returns:
            归一化后的图像
        """
        return ImageProcessor.histogram_equalization(image)
    
    @staticmethod
    def local_correction_normalization(image: np.ndarray,
                                       neighborhood_width: int = 32,
                                       neighborhood_height: int = 32) -> np.ndarray:
        """
        局部校正归一化
        
        Args:
            image: 输入图像
            neighborhood_width: 邻域宽度
            neighborhood_height: 邻域高度
            
        Returns:
            归一化后的图像
        """
        result = image.copy().astype(np.float64)
        height, width = image.shape[:2]
        
        for y in range(0, height, neighborhood_height):
            for x in range(0, width, neighborhood_width):
                block_h = min(neighborhood_height, height - y)
                block_w = min(neighborhood_width, width - x)
                
                # 提取块
                if len(image.shape) == 3:
                    block = image[y:y+block_h, x:x+block_w, :]
                    local_mean = np.mean(block)
                else:
                    block = image[y:y+block_h, x:x+block_w]
                    local_mean = np.mean(block)
                
                adjustment = 128 - local_mean
                
                # 应用调整
                if len(image.shape) == 3:
                    result[y:y+block_h, x:x+block_w, :] = np.clip(
                        result[y:y+block_h, x:x+block_w, :] + adjustment, 0, 255)
                else:
                    result[y:y+block_h, x:x+block_w] = np.clip(
                        result[y:y+block_h, x:x+block_w] + adjustment, 0, 255)
        
        return result.astype(np.uint8)
    
    @staticmethod
    def perform_inspection(input_image: np.ndarray,
                          trained_pattern: np.ndarray,
                          threshold_image: np.ndarray,
                          normalization_mode: NormalizationMode = NormalizationMode.HISTOGRAM_EQUALIZATION,
                          trained_pattern_for_norm: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        """
        执行检测
        
        Args:
            input_image: 输入图像
            trained_pattern: 训练模式图像
            threshold_image: 阈值图像
            normalization_mode: 归一化模式
            trained_pattern_for_norm: 用于归一化的训练模式
            
        Returns:
            (原始差异图像, 阈值差异图像, 匹配图像, 统计信息字典) 元组
        """
        # 归一化处理
        normalized_image = ImageProcessor.normalize_image(
            input_image, normalization_mode, trained_pattern_for_norm)
        
        # 计算原始差异图像
        raw_diff = np.abs(normalized_image.astype(np.float64) - trained_pattern.astype(np.float64))
        raw_diff = np.clip(raw_diff, 0, 255).astype(np.uint8)
        
        # 阈值处理
        threshold_diff = np.zeros_like(raw_diff)
        height, width = input_image.shape[:2]
        
        defect_pixel_count = 0
        total_defect_intensity = 0.0
        max_defect_intensity = 0
        
        for y in range(height):
            for x in range(width):
                if len(input_image.shape) == 3:
                    diff_val = np.mean(raw_diff[y, x, :3])
                    thresh_val = np.mean(threshold_image[y, x, :3])
                else:
                    diff_val = raw_diff[y, x]
                    thresh_val = threshold_image[y, x]
                
                if diff_val > thresh_val:
                    defect_value = max(0, int(diff_val - 10))  # threshold_offset
                    if len(input_image.shape) == 3:
                        threshold_diff[y, x, :3] = defect_value
                        threshold_diff[y, x, 3] = 255
                    else:
                        threshold_diff[y, x] = defect_value
                    
                    defect_pixel_count += 1
                    total_defect_intensity += defect_value
                    max_defect_intensity = max(max_defect_intensity, defect_value)
        
        # 计算统计信息
        total_pixels = height * width
        defect_ratio = (defect_pixel_count / total_pixels) * 100 if total_pixels > 0 else 0
        avg_defect_intensity = total_defect_intensity / defect_pixel_count if defect_pixel_count > 0 else 0
        
        stats = {
            'defect_pixel_count': defect_pixel_count,
            'defect_ratio': defect_ratio,
            'avg_defect_intensity': avg_defect_intensity,
            'max_defect_intensity': max_defect_intensity
        }
        
        return raw_diff, threshold_diff, normalized_image, stats
