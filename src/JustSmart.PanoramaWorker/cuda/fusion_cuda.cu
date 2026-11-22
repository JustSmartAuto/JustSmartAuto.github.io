// 融合实现。
// Baseline：掩膜外为种子，Jump Flooding 计算到掩膜边界的近似欧氏距离；
// Deghost ：与 PanoramaStitcher.FusePairDeghost 的逐像素分支保持一致。
#include "fusion_cuda.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace {

// ---------------------------------------------------------------------------
// Baseline：Jump Flooding 距离变换
// ---------------------------------------------------------------------------

// 以 mask==0 的像素为种子（对应 OpenCV DistanceTransform 的零像素）。
__global__ void seed_sites_kernel(const uint8_t* mask, int2* sites, int w, int h) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const size_t i = static_cast<size_t>(y) * w + x;
    int2 s;
    if (mask[i] == 0) {
        s.x = x;
        s.y = y;
    } else {
        s.x = -1;
        s.y = -1;
    }
    sites[i] = s;
}

__global__ void jfa_pass_kernel(const int2* in, int2* out, int w, int h, int step) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;

    int2 best = in[static_cast<size_t>(y) * w + x];
    long bestD = (best.x >= 0) ? static_cast<long>(best.x - x) * (best.x - x) +
                                     static_cast<long>(best.y - y) * (best.y - y)
                               : 0x3fffffffL;

    for (int dy = -step; dy <= step; dy += step) {
        const int ny = y + dy;
        if (ny < 0 || ny >= h) continue;
        for (int dx = -step; dx <= step; dx += step) {
            if (dx == 0 && dy == 0) continue;
            const int nx = x + dx;
            if (nx < 0 || nx >= w) continue;
            const int2 s = in[static_cast<size_t>(ny) * w + nx];
            if (s.x < 0) continue;
            const long d = static_cast<long>(s.x - x) * (s.x - x) +
                           static_cast<long>(s.y - y) * (s.y - y);
            if (d < bestD) {
                bestD = d;
                best = s;
            }
        }
    }
    out[static_cast<size_t>(y) * w + x] = best;
}

__global__ void dist_from_sites_kernel(const int2* sites, float* dist, int w, int h) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const size_t i = static_cast<size_t>(y) * w + x;
    const int2 s = sites[i];
    if (s.x < 0) {
        dist[i] = 1e30f;
        return;
    }
    const float dx = static_cast<float>(s.x - x);
    const float dy = static_cast<float>(s.y - y);
    dist[i] = sqrtf(dx * dx + dy * dy);
}

__global__ void weight_kernel(const float* dist, const uint8_t* mask, float* weight,
                              int n, float invRamp, int sharpness) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float w = dist[i] * invRamp;
    if (w > 1.0f) w = 1.0f;
    if (w < 0.0f) w = 0.0f;
    for (int s = 1; s < sharpness; ++s) w *= w;
    if (mask[i] == 0) w = 0.0f;
    weight[i] = w;
}

__global__ void blend_weighted_kernel(const Pixel* a, const Pixel* b, const float* wa,
                                      const float* wb, Pixel* out, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float ea = wa[i];
    const float eb = wb[i];
    const float inv = 1.0f / (ea + eb + 1e-6f);
    Pixel p = a[i];
    Pixel q = b[i];
    out[i].r = static_cast<uint8_t>((p.r * ea + q.r * eb) * inv);
    out[i].g = static_cast<uint8_t>((p.g * ea + q.g * eb) * inv);
    out[i].b = static_cast<uint8_t>((p.b * ea + q.b * eb) * inv);
}

// 对单张掩膜执行 JFA，得到到最近背景像素的距离。
void distance_transform(const uint8_t* dMask, float* dDist, int2* dSitesA,
                        int2* dSitesB, int w, int h, cudaStream_t stream) {
    dim3 block(16, 16);
    dim3 grid(divUp(w, 16), divUp(h, 16));
    seed_sites_kernel<<<grid, block, 0, stream>>>(dMask, dSitesA, w, h);
    CUDA_CHECK_LAST();

    int maxDim = std::max(w, h);
    int start = 1;
    while (start < maxDim) start <<= 1;

    int2* cur = dSitesA;
    int2* next = dSitesB;
    for (int step = start >> 1; step >= 1; step >>= 1) {
        jfa_pass_kernel<<<grid, block, 0, stream>>>(cur, next, w, h, step);
        CUDA_CHECK_LAST();
        std::swap(cur, next);
    }
    dist_from_sites_kernel<<<grid, block, 0, stream>>>(cur, dDist, w, h);
    CUDA_CHECK_LAST();
}

// ---------------------------------------------------------------------------
// Deghost
// ---------------------------------------------------------------------------

__device__ uint8_t apply_gain_u8(uint8_t v, float g) {
    float x = rintf(static_cast<float>(v) * g);
    if (x < 0.0f) x = 0.0f;
    if (x > 255.0f) x = 255.0f;
    return static_cast<uint8_t>(x);
}

__global__ void overlap_kernel(const uint8_t* a, const uint8_t* b, uint8_t* ov, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    ov[i] = (a[i] != 0 && b[i] != 0) ? 255 : 0;
}

// 重叠区逐通道求和，blockDim.x 必须为 256。
__global__ void gain_sum_kernel(const Pixel* a, const Pixel* b, const uint8_t* ov,
                                int n, double* sums) {
    __shared__ double sh[6][256];
    const int t = threadIdx.x;
    for (int c = 0; c < 6; ++c) sh[c][t] = 0.0;
    __syncthreads();

    double acc[6] = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    for (int i = blockIdx.x * blockDim.x + t; i < n; i += blockDim.x * gridDim.x) {
        if (!ov[i]) continue;
        acc[0] += a[i].r;
        acc[1] += a[i].g;
        acc[2] += a[i].b;
        acc[3] += b[i].r;
        acc[4] += b[i].g;
        acc[5] += b[i].b;
    }
    for (int c = 0; c < 6; ++c) sh[c][t] = acc[c];
    __syncthreads();

    for (int s = blockDim.x >> 1; s > 0; s >>= 1) {
        if (t < s) {
            for (int c = 0; c < 6; ++c) sh[c][t] += sh[c][t + s];
        }
        __syncthreads();
    }
    if (t == 0) {
        for (int c = 0; c < 6; ++c) atomicAdd(&sums[c], sh[c][t]);
    }
}

// 应用增益得到 curGain，同时生成两者的灰度图。
__global__ void prep_kernel(const Pixel* prev, const Pixel* cur, Pixel* curGain,
                            float* grayP, float* grayC, int n, float g0, float g1,
                            float g2) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const Pixel p = prev[i];
    const Pixel q = cur[i];
    Pixel qg;
    qg.r = apply_gain_u8(q.r, g0);
    qg.g = apply_gain_u8(q.g, g1);
    qg.b = apply_gain_u8(q.b, g2);
    curGain[i] = qg;
    grayP[i] = 0.299f * p.r + 0.587f * p.g + 0.114f * p.b;
    grayC[i] = 0.299f * qg.r + 0.587f * qg.g + 0.114f * qg.b;
}

__device__ float sample_gray(const float* g, int w, int h, int x, int y) {
    if (x < 0) x = 0;
    if (x >= w) x = w - 1;
    if (y < 0) y = 0;
    if (y >= h) y = h - 1;
    return g[static_cast<size_t>(y) * w + x];
}

// 代价图：|grayP - grayC| + 0.5 * (|gx 差| + |gy 差|)，Sobel 3x3；非重叠区给 1e6。
__global__ void cost_kernel(const float* gp, const float* gc, const uint8_t* ov,
                            float* cost, int w, int h) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;

    const float px = sample_gray(gp, w, h, x + 1, y - 1) +
                     2.0f * sample_gray(gp, w, h, x + 1, y) +
                     sample_gray(gp, w, h, x + 1, y + 1) -
                     sample_gray(gp, w, h, x - 1, y - 1) -
                     2.0f * sample_gray(gp, w, h, x - 1, y) -
                     sample_gray(gp, w, h, x - 1, y + 1);
    const float py = sample_gray(gp, w, h, x - 1, y + 1) +
                     2.0f * sample_gray(gp, w, h, x, y + 1) +
                     sample_gray(gp, w, h, x + 1, y + 1) -
                     sample_gray(gp, w, h, x - 1, y - 1) -
                     2.0f * sample_gray(gp, w, h, x, y - 1) -
                     sample_gray(gp, w, h, x + 1, y - 1);
    const float qx = sample_gray(gc, w, h, x + 1, y - 1) +
                     2.0f * sample_gray(gc, w, h, x + 1, y) +
                     sample_gray(gc, w, h, x + 1, y + 1) -
                     sample_gray(gc, w, h, x - 1, y - 1) -
                     2.0f * sample_gray(gc, w, h, x - 1, y) -
                     sample_gray(gc, w, h, x - 1, y + 1);
    const float qy = sample_gray(gc, w, h, x - 1, y + 1) +
                     2.0f * sample_gray(gc, w, h, x, y + 1) +
                     sample_gray(gc, w, h, x + 1, y + 1) -
                     sample_gray(gc, w, h, x - 1, y - 1) -
                     2.0f * sample_gray(gc, w, h, x, y - 1) -
                     sample_gray(gc, w, h, x + 1, y - 1);

    const size_t i = static_cast<size_t>(y) * w + x;
    if (!ov[i]) {
        cost[i] = 1e6f;
        return;
    }
    const float c = fabsf(gp[i] - gc[i]) + 0.5f * (fabsf(px - qx) + fabsf(py - qy));
    cost[i] = c;
}

// 最佳缝合线 DP：单 block，逐行在 ±1 列内取最小累计代价。
__global__ void seam_dp_kernel(const float* cost, int w, int h, float* dpA,
                               float* dpB, int32_t* parent, int32_t* seam) {
    const int t = threadIdx.x;
    const int nt = blockDim.x;

    for (int x = t; x < w; x += nt) dpA[x] = cost[x];
    __syncthreads();

    float* prev = dpA;
    float* curr = dpB;
    for (int y = 1; y < h; ++y) {
        const float* row = cost + static_cast<size_t>(y) * w;
        for (int x = t; x < w; x += nt) {
            float best = prev[x];
            int bestX = x;
            if (x > 0 && prev[x - 1] < best) {
                best = prev[x - 1];
                bestX = x - 1;
            }
            if (x + 1 < w && prev[x + 1] < best) {
                best = prev[x + 1];
                bestX = x + 1;
            }
            curr[x] = row[x] + best;
            parent[static_cast<size_t>(y) * w + x] = bestX;
        }
        __syncthreads();
        float* tmp = prev;
        prev = curr;
        curr = tmp;
    }

    if (t == 0) {
        int bx = 0;
        float best = prev[0];
        for (int x = 1; x < w; ++x) {
            if (prev[x] < best) {
                best = prev[x];
                bx = x;
            }
        }
        seam[h - 1] = bx;
        for (int y = h - 1; y > 0; --y) {
            bx = parent[static_cast<size_t>(y) * w + bx];
            seam[y - 1] = bx;
        }
    }
}

__global__ void deghost_blend_kernel(const Pixel* prev, const Pixel* curGain,
                                     const uint8_t* maskPrev, const uint8_t* maskCur,
                                     const uint8_t* ov, const float* grayP,
                                     const float* grayC, const int32_t* seam,
                                     Pixel* out, int w, int h, int feather) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;

    const size_t i = static_cast<size_t>(y) * w + x;
    const int sx = seam[y];
    const bool inOverlap = ov[i] != 0;
    const bool curOnly = !inOverlap && maskCur[i] != 0;
    const bool prevOnly = !inOverlap && maskPrev[i] != 0;

    Pixel r = prev[i];
    if (prevOnly || (inOverlap && x < sx - feather)) {
        r = prev[i];
    } else if (curOnly || (inOverlap && x > sx + feather)) {
        r = curGain[i];
    } else if (inOverlap) {
        const int dist = x > sx ? x - sx : sx - x;
        const int dd = dist < feather ? dist : feather;
        const float wgt = (feather <= 0)
                              ? 0.0f
                              : 0.5f * (1.0f - cosf(3.14159265358979323846f * dd / feather));
        float wp = 1.0f - wgt;
        float wc = wgt;
        if (fabsf(grayP[i] - grayC[i]) > 14.0f) {
            if (dist <= feather / 2) {
                wp = 1.0f;
                wc = 0.0f;
            } else {
                wp = 0.0f;
                wc = 1.0f;
            }
        }
        const Pixel p = prev[i];
        const Pixel q = curGain[i];
        r.r = static_cast<uint8_t>(wp * p.r + wc * q.r);
        r.g = static_cast<uint8_t>(wp * p.g + wc * q.g);
        r.b = static_cast<uint8_t>(wp * p.b + wc * q.b);
    }
    out[i] = r;
}

}  // namespace

void baseline_fuse_cuda(const Pixel* dPrev, const Pixel* dCur,
                        const uint8_t* dMaskPrev, const uint8_t* dMaskCur,
                        Pixel* dOut, int w, int h, int ramp, int sharpness,
                        cudaStream_t stream) {
    if (!dPrev || !dCur || !dMaskPrev || !dMaskCur || !dOut) return;
    if (w <= 0 || h <= 0) return;
    if (ramp < 1) ramp = 1;
    if (sharpness < 1) sharpness = 1;

    const size_t n = static_cast<size_t>(w) * h;

    DeviceBuffer<int2> sitesA(n);
    DeviceBuffer<int2> sitesB(n);
    DeviceBuffer<float> distA(n);
    DeviceBuffer<float> distB(n);
    DeviceBuffer<float> weightA(n);
    DeviceBuffer<float> weightB(n);

    distance_transform(dMaskPrev, distA.data(), sitesA.data(), sitesB.data(), w, h, stream);
    distance_transform(dMaskCur, distB.data(), sitesA.data(), sitesB.data(), w, h, stream);

    const float invRamp = 1.0f / static_cast<float>(ramp);
    const int blocks = divUp(static_cast<int>(n), 256);
    weight_kernel<<<blocks, 256, 0, stream>>>(distA.data(), dMaskPrev, weightA.data(),
                                              static_cast<int>(n), invRamp, sharpness);
    weight_kernel<<<blocks, 256, 0, stream>>>(distB.data(), dMaskCur, weightB.data(),
                                              static_cast<int>(n), invRamp, sharpness);
    blend_weighted_kernel<<<blocks, 256, 0, stream>>>(dPrev, dCur, weightA.data(),
                                                      weightB.data(), dOut,
                                                      static_cast<int>(n));
    CUDA_CHECK_LAST();
}

void deghost_fuse_cuda(const Pixel* dPrev, const Pixel* dCur,
                       const uint8_t* dMaskPrev, const uint8_t* dMaskCur,
                       Pixel* dOut, int w, int h, int feather,
                       FusionStats* stats, cudaStream_t stream) {
    if (!dPrev || !dCur || !dMaskPrev || !dMaskCur || !dOut) return;
    if (w <= 0 || h <= 0) return;
    if (feather < 1) feather = 1;

    const size_t n = static_cast<size_t>(w) * h;
    const int ni = static_cast<int>(n);

    DeviceBuffer<uint8_t> overlap(n);
    DeviceBuffer<double> sums(6);
    DeviceBuffer<Pixel> curGain(n);
    DeviceBuffer<float> grayP(n);
    DeviceBuffer<float> grayC(n);
    DeviceBuffer<float> cost(n);
    DeviceBuffer<float> dpA(w);
    DeviceBuffer<float> dpB(w);
    DeviceBuffer<int32_t> parent(n);
    DeviceBuffer<int32_t> seam(h);

    const int blocks = divUp(ni, 256);
    dim3 block(16, 16);
    dim3 grid(divUp(w, 16), divUp(h, 16));

    overlap_kernel<<<blocks, 256, 0, stream>>>(dMaskPrev, dMaskCur, overlap.data(), ni);
    CUDA_CHECK_LAST();

    sums.zero();
    gain_sum_kernel<<<blocks, 256, 0, stream>>>(dPrev, dCur, overlap.data(), ni,
                                                sums.data());
    CUDA_CHECK_LAST();

    double hostSums[6] = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    CUDA_CHECK(cudaMemcpyAsync(hostSums, sums.data(), sizeof(hostSums),
                               cudaMemcpyDeviceToHost, stream));
    CUDA_CHECK(cudaStreamSynchronize(stream));

    float gain[3] = {1.0f, 1.0f, 1.0f};
    for (int c = 0; c < 3; ++c) {
        const double sumP = hostSums[c];
        const double sumC = hostSums[3 + c];
        double g = (sumC > 1e-3) ? (sumP / sumC) : 1.0;
        if (g < 0.6) g = 0.6;
        if (g > 1.7) g = 1.7;
        gain[c] = static_cast<float>(g);
    }

    prep_kernel<<<blocks, 256, 0, stream>>>(dPrev, dCur, curGain.data(), grayP.data(),
                                            grayC.data(), ni, gain[0], gain[1], gain[2]);
    CUDA_CHECK_LAST();

    cost_kernel<<<grid, block, 0, stream>>>(grayP.data(), grayC.data(), overlap.data(),
                                            cost.data(), w, h);
    CUDA_CHECK_LAST();

    seam_dp_kernel<<<1, 256, 0, stream>>>(cost.data(), w, h, dpA.data(), dpB.data(),
                                          parent.data(), seam.data());
    CUDA_CHECK_LAST();

    deghost_blend_kernel<<<grid, block, 0, stream>>>(
        dPrev, curGain.data(), dMaskPrev, dMaskCur, overlap.data(), grayP.data(),
        grayC.data(), seam.data(), dOut, w, h, feather);
    CUDA_CHECK_LAST();

    if (stats) {
        std::vector<int32_t> hostSeam(static_cast<size_t>(h), 0);
        CUDA_CHECK(cudaMemcpyAsync(hostSeam.data(), seam.data(),
                                   static_cast<size_t>(h) * sizeof(int32_t),
                                   cudaMemcpyDeviceToHost, stream));
        CUDA_CHECK(cudaStreamSynchronize(stream));
        int mn = hostSeam[0];
        int mx = hostSeam[0];
        for (int y = 1; y < h; ++y) {
            mn = std::min(mn, static_cast<int>(hostSeam[y]));
            mx = std::max(mx, static_cast<int>(hostSeam[y]));
        }
        stats->gainR = gain[0];
        stats->gainG = gain[1];
        stats->gainB = gain[2];
        stats->seamMin = mn;
        stats->seamMax = mx;
    }
}
