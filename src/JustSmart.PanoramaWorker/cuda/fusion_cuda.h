// 融合模块：Baseline（距离变换权重）与 Deghost（增益匹配 + 缝合线 DP）。
// 输入假定两幅图已由上层（SIFT + RANSAC + ECC）配准并 warp 到同一画布。
#pragma once

#include <cstdint>
#include <cuda_runtime.h>

#include "cuda_common.cuh"

struct FusionStats {
    float gainR;
    float gainG;
    float gainB;
    int seamMin;
    int seamMax;
};

// 基于有效掩膜距离变换的加权平均融合：
//   weight = min(1, dist / ramp)，再按 sharpness 连续平方（等价于 C# 的 Mul 循环），
//   输出 = (prev*wp + cur*wc) / (wp + wc + 1e-6)。
void baseline_fuse_cuda(const Pixel* dPrev, const Pixel* dCur,
                        const uint8_t* dMaskPrev, const uint8_t* dMaskCur,
                        Pixel* dOut, int w, int h, int ramp, int sharpness,
                        cudaStream_t stream = 0);

// 去鬼影融合：重叠区增益匹配 → 颜色差 + 梯度差代价图 → ±1 列最佳缝合线 DP →
// 缝合带 smoothstep 过渡 + 色差一致性选择。
void deghost_fuse_cuda(const Pixel* dPrev, const Pixel* dCur,
                       const uint8_t* dMaskPrev, const uint8_t* dMaskCur,
                       Pixel* dOut, int w, int h, int feather,
                       FusionStats* stats = nullptr, cudaStream_t stream = 0);
