// SIFT 特征提取与匹配的 CUDA 接口。
#pragma once

#include <cstdint>
#include <cuda_runtime.h>

struct SiftKeypoint {
    float x;       // 原图坐标
    float y;
    float scale;   // 原图尺度
    float angle;   // 主方向（弧度）
    float ox;      // 所在 octave 图像坐标
    float oy;
    int octave;    // octave 序号
    int gaussIdx;  // 高斯图索引 = octave * 6 + scaleIdx
    float desc[128];
};

struct SiftMatch {
    int query;
    int train;
    float distance;
};

// 从设备端灰度图提取 SIFT 关键点，写入 dOut（容量 maxKeypoints），返回实际数量。
int sift_extract_cuda(const uint8_t* dGray, int w, int h, SiftKeypoint* dOut,
                      int maxKeypoints, cudaStream_t stream = 0);

// 暴力匹配 + Lowe 比率测试（ratio 为距离比阈值，常用 0.75）。
int sift_match_cuda(const SiftKeypoint* dA, int na, const SiftKeypoint* dB, int nb,
                    float ratio, SiftMatch* dOut, int maxMatches,
                    cudaStream_t stream = 0);
