// 动态阈值二值化（Sauvola）与全局阈值（Otsu）的 CUDA 接口。
#pragma once

#include <cstdint>
#include <cuda_runtime.h>

// Sauvola 局部阈值：threshold = mean * (1 + k * (std/128 - 1))。
// window 为邻域边长（奇数更自然，内部按 radius = window/2 处理）。
void sauvola_binarize_cuda(const uint8_t* dGray, uint8_t* dOut, int w, int h,
                           int window, float k, cudaStream_t stream = 0);

// Otsu 全局阈值：直方图统计后取类间方差最大的阈值。
void otsu_binarize_cuda(const uint8_t* dGray, uint8_t* dOut, int w, int h,
                        cudaStream_t stream = 0);
