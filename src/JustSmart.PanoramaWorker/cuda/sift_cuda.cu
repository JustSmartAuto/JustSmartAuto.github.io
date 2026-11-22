// SIFT 的 CUDA 实现：
//   1) 构建高斯/DoG 金字塔（host 端按 octave 依次启动，device 端做模糊与差分）；
//   2) 26 邻域极值 + Hessian 边缘剔除，得到候选点；
//   3) 36 桶方向直方图确定主方向（可产生多方向副本）；
//   4) 4x4x8 = 128 维描述子（三线性插值 + 归一化 + 0.2 截断）；
//   5) 暴力匹配 + Lowe 比率测试。
#include "sift_cuda.h"

#include "cuda_common.cuh"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace {

constexpr int kScales = 3;                         // 每个 octave 的 DoG 尺度数
constexpr int kGauss = kScales + 3;                // 高斯图数量（6）
constexpr int kMaxOctaves = 4;                     // 最多 octave 数
constexpr float kBaseSigma = 1.6f;                 // 基准 sigma
constexpr float kSigmaStep = 1.2589254117941673f;  // 2^(1/3)
constexpr float kContrastThr = 4.0f;               // 0-255 灰度下的对比度阈值
constexpr float kPi = 3.14159265358979323846f;
constexpr float kTwoPi = 6.28318530717958647692f;

struct Candidate {
    int x;
    int y;
    int scaleIdx;
};

std::vector<float> make_gauss_kernel(float sigma, int* radiusOut) {
    const int r = static_cast<int>(std::ceil(3.0f * sigma));
    std::vector<float> k(static_cast<size_t>(2 * r + 1), 0.0f);
    const float inv = 1.0f / (2.0f * sigma * sigma);
    float sum = 0.0f;
    for (int i = -r; i <= r; ++i) {
        const float v = std::exp(-static_cast<float>(i * i) * inv);
        k[static_cast<size_t>(i + r)] = v;
        sum += v;
    }
    if (sum > 0.0f) {
        for (float& v : k) v /= sum;
    }
    if (radiusOut) *radiusOut = r;
    return k;
}

__global__ void u8_to_float_kernel(const uint8_t* src, float* dst, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) dst[i] = static_cast<float>(src[i]);
}

__global__ void downsample2_kernel(const float* src, float* dst, int sw, int sh,
                                   int dw, int dh) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= dw || y >= dh) return;
    const int sx = 2 * x;
    const int sy = 2 * y;
    const int sx1 = (sx + 1 < sw) ? sx + 1 : sw - 1;
    const int sy1 = (sy + 1 < sh) ? sy + 1 : sh - 1;
    const float s = src[static_cast<size_t>(sy) * sw + sx] +
                    src[static_cast<size_t>(sy) * sw + sx1] +
                    src[static_cast<size_t>(sy1) * sw + sx] +
                    src[static_cast<size_t>(sy1) * sw + sx1];
    dst[static_cast<size_t>(y) * dw + x] = 0.25f * s;
}

__global__ void blur_h_kernel(const float* src, float* dst, int w, int h,
                              const float* kern, int r) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float* row = src + static_cast<size_t>(y) * w;
    float s = 0.0f;
    for (int k = -r; k <= r; ++k) {
        int xx = x + k;
        if (xx < 0) xx = 0;
        if (xx >= w) xx = w - 1;
        s += row[xx] * kern[k + r];
    }
    dst[static_cast<size_t>(y) * w + x] = s;
}

__global__ void blur_v_kernel(const float* src, float* dst, int w, int h,
                              const float* kern, int r) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float s = 0.0f;
    for (int k = -r; k <= r; ++k) {
        int yy = y + k;
        if (yy < 0) yy = 0;
        if (yy >= h) yy = h - 1;
        s += src[static_cast<size_t>(yy) * w + x] * kern[k + r];
    }
    dst[static_cast<size_t>(y) * w + x] = s;
}

__global__ void subtract_kernel(const float* a, const float* b, float* dst, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) dst[i] = b[i] - a[i];
}

// DoG 极值检测 + Hessian 边缘响应剔除。
__global__ void extrema_kernel(const float* dPrev, const float* dCur,
                               const float* dNext, int w, int h, int scaleIdx,
                               float contrThr, Candidate* cands, int* count,
                               int maxCand) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x <= 0 || y <= 0 || x >= w - 1 || y >= h - 1) return;

    const size_t i = static_cast<size_t>(y) * w + x;
    const float c = dCur[i];
    if (fabsf(c) < contrThr) return;

    bool isMax = true;
    bool isMin = true;
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            if (dx == 0 && dy == 0) continue;
            const size_t j = static_cast<size_t>(y + dy) * w + (x + dx);
            const float a = dPrev[j];
            const float b = dCur[j];
            const float d = dNext[j];
            if (a > c || b > c || d > c) isMax = false;
            if (a < c || b < c || d < c) isMin = false;
        }
    }
    if (!isMax && !isMin) return;

    const float dxx = dCur[i - 1] + dCur[i + 1] - 2.0f * c;
    const float dyy = dCur[i - w] + dCur[i + w] - 2.0f * c;
    const float dxy = 0.25f * (dCur[i - w + 1] + dCur[i + w - 1] -
                               dCur[i - w - 1] - dCur[i + w + 1]);
    const float tr = dxx + dyy;
    const float det = dxx * dyy - dxy * dxy;
    if (det <= 0.0f) return;
    if (tr * tr / det >= 12.1f) return;

    const int k = atomicAdd(count, 1);
    if (k < maxCand) {
        cands[k].x = x;
        cands[k].y = y;
        cands[k].scaleIdx = scaleIdx;
    }
}

// 方向分配：36 桶梯度直方图，取所有 >= 0.8*max 的局部峰，生成多方向副本。
__global__ void orientation_kernel(const float* img, int w, int h,
                                   const Candidate* cands, int ncand,
                                   SiftKeypoint* keys, int* kcount, int maxKeys,
                                   int octave, int gaussIdx) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= ncand) return;

    const Candidate cd = cands[idx];
    const float sigma = kBaseSigma * powf(kSigmaStep, static_cast<float>(cd.scaleIdx) + 0.5f);
    int radius = static_cast<int>(4.5f * sigma + 0.5f);
    if (radius < 1) radius = 1;
    if (radius > 64) radius = 64;

    float hist[36];
    for (int i = 0; i < 36; ++i) hist[i] = 0.0f;

    const float gaussFactor = 1.0f / (2.0f * 1.5f * sigma * 1.5f * sigma);
    for (int dy = -radius; dy <= radius; ++dy) {
        const int yy = cd.y + dy;
        if (yy <= 0 || yy >= h - 1) continue;
        for (int dx = -radius; dx <= radius; ++dx) {
            const int xx = cd.x + dx;
            if (xx <= 0 || xx >= w - 1) continue;
            const size_t j = static_cast<size_t>(yy) * w + xx;
            const float gx = img[j + 1] - img[j - 1];
            const float gy = img[j + w] - img[j - w];
            const float mag = sqrtf(gx * gx + gy * gy);
            if (mag < 1e-6f) continue;
            const float ori = atan2f(gy, gx);
            int bin = static_cast<int>((ori + kPi) * (36.0f / kTwoPi));
            bin %= 36;
            if (bin < 0) bin += 36;
            const float wgt = expf(-static_cast<float>(dx * dx + dy * dy) * gaussFactor);
            hist[bin] += mag * wgt;
        }
    }

    for (int pass = 0; pass < 3; ++pass) {
        float tmp[36];
        for (int i = 0; i < 36; ++i) {
            tmp[i] = 0.25f * hist[(i + 35) % 36] + 0.5f * hist[i] +
                     0.25f * hist[(i + 1) % 36];
        }
        for (int i = 0; i < 36; ++i) hist[i] = tmp[i];
    }

    float maxv = 0.0f;
    for (int i = 0; i < 36; ++i) {
        if (hist[i] > maxv) maxv = hist[i];
    }
    if (maxv <= 0.0f) return;

    float peaks[4];
    int np = 0;
    for (int i = 0; i < 36 && np < 4; ++i) {
        const float l = hist[(i + 35) % 36];
        const float r = hist[(i + 1) % 36];
        if (hist[i] > l && hist[i] >= r && hist[i] >= 0.8f * maxv) {
            const float denom = l + r - 2.0f * hist[i];
            float off = (denom != 0.0f) ? (0.5f * (l - r) / denom) : 0.0f;
            if (off > 0.5f) off = 0.5f;
            if (off < -0.5f) off = -0.5f;
            peaks[np++] = (static_cast<float>(i) + off + 0.5f) * (kTwoPi / 36.0f) - kPi;
        }
    }
    if (np == 0) return;

    const int base = atomicAdd(kcount, np);
    const float scaleFactor = static_cast<float>(1 << octave);
    for (int i = 0; i < np && base + i < maxKeys; ++i) {
        SiftKeypoint& kp = keys[base + i];
        kp.x = (static_cast<float>(cd.x) + 0.5f) * scaleFactor - 0.5f;
        kp.y = (static_cast<float>(cd.y) + 0.5f) * scaleFactor - 0.5f;
        kp.ox = static_cast<float>(cd.x);
        kp.oy = static_cast<float>(cd.y);
        kp.scale = sigma * scaleFactor;
        kp.angle = peaks[i];
        kp.octave = octave;
        kp.gaussIdx = gaussIdx;
    }
}

// 描述子：4x4 子区域 × 8 方向，三线性插值后归一化。
__global__ void descriptor_kernel(SiftKeypoint* keys, int n, float** gaussImgs,
                                  const int* gaussW, const int* gaussH) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;

    const int gaussIdx = keys[idx].gaussIdx;
    const int octave = keys[idx].octave;
    const float ox = keys[idx].ox;
    const float oy = keys[idx].oy;
    const float angle = keys[idx].angle;

    const float* img = gaussImgs[gaussIdx];
    const int w = gaussW[octave];
    const int h = gaussH[octave];
    if (!img || w <= 0 || h <= 0) return;

    const int scaleIdx = gaussIdx % kGauss;
    const float sigma = kBaseSigma * powf(kSigmaStep, static_cast<float>(scaleIdx) + 0.5f);
    const float histWidth = 3.0f * sigma;
    int radius = static_cast<int>(histWidth * 1.41421356f * 2.5f + 0.5f);
    const int maxRadius = (w > h) ? w : h;
    if (radius > maxRadius) radius = maxRadius;
    if (radius < 4) radius = 4;

    const float cosA = cosf(angle);
    const float sinA = sinf(angle);
    const float invHist = 1.0f / histWidth;

    float hist[128];
    for (int i = 0; i < 128; ++i) hist[i] = 0.0f;

    const int cx = static_cast<int>(ox);
    const int cy = static_cast<int>(oy);
    for (int dy = -radius; dy <= radius; ++dy) {
        const int yy = cy + dy;
        if (yy <= 0 || yy >= h - 1) continue;
        for (int dx = -radius; dx <= radius; ++dx) {
            const int xx = cx + dx;
            if (xx <= 0 || xx >= w - 1) continue;

            const float rx = (static_cast<float>(dx) * cosA +
                              static_cast<float>(dy) * sinA) * invHist;
            const float ry = (-static_cast<float>(dx) * sinA +
                              static_cast<float>(dy) * cosA) * invHist;
            const float rbin = ry + 1.5f;
            const float cbin = rx + 1.5f;
            if (rbin <= -1.0f || rbin >= 4.0f || cbin <= -1.0f || cbin >= 4.0f)
                continue;

            const size_t j = static_cast<size_t>(yy) * w + xx;
            const float gx = img[j + 1] - img[j - 1];
            const float gy = img[j + w] - img[j - w];
            const float mag = sqrtf(gx * gx + gy * gy);
            if (mag < 1e-6f) continue;

            float ori = atan2f(gy, gx) - angle;
            ori = fmodf(ori, kTwoPi);
            if (ori < 0.0f) ori += kTwoPi;
            const float obin = ori * (8.0f / kTwoPi);

            const int r0 = static_cast<int>(floorf(rbin));
            const int c0 = static_cast<int>(floorf(cbin));
            const int o0 = static_cast<int>(floorf(obin));
            const float dr = rbin - static_cast<float>(r0);
            const float dc = cbin - static_cast<float>(c0);
            const float dov = obin - static_cast<float>(o0);
            const float wgt = expf(-(rx * rx + ry * ry) * 0.5f) * mag;

            for (int rr = 0; rr <= 1; ++rr) {
                const int ri = r0 + rr;
                if (ri < 0 || ri > 3) continue;
                const float wr = (rr == 0) ? (1.0f - dr) : dr;
                for (int cc = 0; cc <= 1; ++cc) {
                    const int ci = c0 + cc;
                    if (ci < 0 || ci > 3) continue;
                    const float wc = (cc == 0) ? (1.0f - dc) : dc;
                    for (int oo = 0; oo <= 1; ++oo) {
                        const int oi = (o0 + oo) % 8;
                        const float wo = (oo == 0) ? (1.0f - dov) : dov;
                        hist[(ri * 4 + ci) * 8 + oi] += wgt * wr * wc * wo;
                    }
                }
            }
        }
    }

    float norm = 0.0f;
    for (int i = 0; i < 128; ++i) norm += hist[i] * hist[i];
    norm = sqrtf(norm);
    if (norm < 1e-6f) {
        for (int i = 0; i < 128; ++i) keys[idx].desc[i] = 0.0f;
        return;
    }
    const float inv = 1.0f / norm;
    for (int i = 0; i < 128; ++i) {
        hist[i] *= inv;
        if (hist[i] > 0.2f) hist[i] = 0.2f;
    }
    float norm2 = 0.0f;
    for (int i = 0; i < 128; ++i) norm2 += hist[i] * hist[i];
    norm2 = sqrtf(norm2);
    if (norm2 > 1e-6f) {
        const float inv2 = 1.0f / norm2;
        for (int i = 0; i < 128; ++i) hist[i] *= inv2;
    }
    for (int i = 0; i < 128; ++i) keys[idx].desc[i] = hist[i];
}

__global__ void match_kernel(const SiftKeypoint* a, int na, const SiftKeypoint* b,
                             int nb, float ratio2, SiftMatch* out, int* count,
                             int maxMatches) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= na) return;

    float best = 1e30f;
    float second = 1e30f;
    int bestJ = -1;
    for (int j = 0; j < nb; ++j) {
        float d = 0.0f;
        bool skip = false;
        for (int t = 0; t < 128; ++t) {
            const float diff = a[i].desc[t] - b[j].desc[t];
            d += diff * diff;
            if (d >= second) {
                skip = true;
                break;
            }
        }
        if (skip) continue;
        if (d < best) {
            second = best;
            best = d;
            bestJ = j;
        } else if (d < second) {
            second = d;
        }
    }

    if (bestJ >= 0 && best < ratio2 * second) {
        const int k = atomicAdd(count, 1);
        if (k < maxMatches) {
            out[k].query = i;
            out[k].train = bestJ;
            out[k].distance = sqrtf(best);
        }
    }
}

}  // namespace

int sift_extract_cuda(const uint8_t* dGray, int w, int h, SiftKeypoint* dOut,
                      int maxKeypoints, cudaStream_t stream) {
    if (!dGray || !dOut || w < 32 || h < 32 || maxKeypoints <= 0) return 0;

    std::vector<int> owList;
    std::vector<int> ohList;
    int ow = w;
    int oh = h;
    while (static_cast<int>(owList.size()) < kMaxOctaves && ow >= 32 && oh >= 32) {
        owList.push_back(ow);
        ohList.push_back(oh);
        ow >>= 1;
        oh >>= 1;
    }
    const int octaves = static_cast<int>(owList.size());
    if (octaves == 0) return 0;

    std::vector<DeviceBuffer<float>> gauss(static_cast<size_t>(octaves) * kGauss);
    DeviceBuffer<float> dog[kGauss - 1];
    for (int i = 0; i < kGauss - 1; ++i) dog[i].allocate(static_cast<size_t>(w) * h);

    DeviceBuffer<float> base(static_cast<size_t>(w) * h);
    DeviceBuffer<float> tmp(static_cast<size_t>(w) * h);
    DeviceBuffer<Candidate> cand(40000);
    DeviceBuffer<int> candCount(1);
    DeviceBuffer<int> keyCount(1);
    keyCount.zero();

    u8_to_float_kernel<<<divUp(w * h, 256), 256, 0, stream>>>(dGray, base.data(), w * h);
    CUDA_CHECK_LAST();

    for (int o = 0; o < octaves; ++o) {
        const int cw = owList[o];
        const int ch = ohList[o];
        const int cn = cw * ch;

        if (o > 0) {
            const float* srcBase = gauss[static_cast<size_t>(o - 1) * kGauss + kScales].data();
            dim3 block(16, 16);
            dim3 grid(divUp(cw, 16), divUp(ch, 16));
            downsample2_kernel<<<grid, block, 0, stream>>>(
                srcBase, base.data(), owList[o - 1], ohList[o - 1], cw, ch);
            CUDA_CHECK_LAST();
        }

        for (int i = 0; i < kGauss; ++i) {
            const float sigma = kBaseSigma * powf(kSigmaStep, static_cast<float>(i));
            int radius = 0;
            std::vector<float> kern = make_gauss_kernel(sigma, &radius);
            DeviceBuffer<float> dk(kern.size());
            CUDA_CHECK(cudaMemcpyAsync(dk.data(), kern.data(),
                                       kern.size() * sizeof(float),
                                       cudaMemcpyHostToDevice, stream));

            gauss[static_cast<size_t>(o) * kGauss + i].allocate(static_cast<size_t>(cn));
            float* dst = gauss[static_cast<size_t>(o) * kGauss + i].data();

            dim3 block(16, 16);
            dim3 grid(divUp(cw, 16), divUp(ch, 16));
            blur_h_kernel<<<grid, block, 0, stream>>>(base.data(), tmp.data(), cw, ch,
                                                      dk.data(), radius);
            blur_v_kernel<<<grid, block, 0, stream>>>(tmp.data(), dst, cw, ch,
                                                      dk.data(), radius);
            CUDA_CHECK_LAST();
        }

        for (int s = 0; s < kGauss - 1; ++s) {
            subtract_kernel<<<divUp(cn, 256), 256, 0, stream>>>(
                gauss[static_cast<size_t>(o) * kGauss + s].data(),
                gauss[static_cast<size_t>(o) * kGauss + s + 1].data(),
                dog[s].data(), cn);
            CUDA_CHECK_LAST();
        }

        dim3 block(16, 16);
        dim3 grid(divUp(cw, 16), divUp(ch, 16));
        for (int s = 1; s <= kScales; ++s) {
            candCount.zero();
            extrema_kernel<<<grid, block, 0, stream>>>(
                dog[s - 1].data(), dog[s].data(), dog[s + 1].data(), cw, ch, s,
                kContrastThr, cand.data(), candCount.data(), 40000);
            CUDA_CHECK_LAST();

            int ncand = 0;
            CUDA_CHECK(cudaMemcpyAsync(&ncand, candCount.data(), sizeof(int),
                                       cudaMemcpyDeviceToHost, stream));
            CUDA_CHECK(cudaStreamSynchronize(stream));
            if (ncand > 40000) ncand = 40000;
            if (ncand <= 0) continue;

            orientation_kernel<<<divUp(ncand, 128), 128, 0, stream>>>(
                gauss[static_cast<size_t>(o) * kGauss + s].data(), cw, ch, cand.data(),
                ncand, dOut, keyCount.data(), maxKeypoints, o,
                o * kGauss + s);
            CUDA_CHECK_LAST();
        }
    }

    int total = 0;
    CUDA_CHECK(cudaMemcpyAsync(&total, keyCount.data(), sizeof(int),
                               cudaMemcpyDeviceToHost, stream));
    CUDA_CHECK(cudaStreamSynchronize(stream));
    if (total > maxKeypoints) total = maxKeypoints;
    if (total <= 0) return 0;

    std::vector<float*> hPtrs(static_cast<size_t>(octaves) * kGauss, nullptr);
    for (int o = 0; o < octaves; ++o) {
        for (int i = 0; i < kGauss; ++i) {
            hPtrs[static_cast<size_t>(o) * kGauss + i] =
                gauss[static_cast<size_t>(o) * kGauss + i].data();
        }
    }
    DeviceBuffer<float*> dPtrs(hPtrs.size());
    CUDA_CHECK(cudaMemcpyAsync(dPtrs.data(), hPtrs.data(),
                               hPtrs.size() * sizeof(float*), cudaMemcpyHostToDevice,
                               stream));
    DeviceBuffer<int> dW(static_cast<size_t>(octaves));
    DeviceBuffer<int> dH(static_cast<size_t>(octaves));
    CUDA_CHECK(cudaMemcpyAsync(dW.data(), owList.data(),
                               static_cast<size_t>(octaves) * sizeof(int),
                               cudaMemcpyHostToDevice, stream));
    CUDA_CHECK(cudaMemcpyAsync(dH.data(), ohList.data(),
                               static_cast<size_t>(octaves) * sizeof(int),
                               cudaMemcpyHostToDevice, stream));

    descriptor_kernel<<<divUp(total, 64), 64, 0, stream>>>(dOut, total, dPtrs.data(),
                                                           dW.data(), dH.data());
    CUDA_CHECK_LAST();
    CUDA_CHECK(cudaStreamSynchronize(stream));
    return total;
}

int sift_match_cuda(const SiftKeypoint* dA, int na, const SiftKeypoint* dB, int nb,
                    float ratio, SiftMatch* dOut, int maxMatches,
                    cudaStream_t stream) {
    if (!dA || !dB || !dOut || na <= 0 || nb <= 0 || maxMatches <= 0) return 0;
    if (ratio <= 0.0f || ratio >= 1.0f) ratio = 0.75f;

    DeviceBuffer<int> count(1);
    count.zero();
    match_kernel<<<divUp(na, 128), 128, 0, stream>>>(dA, na, dB, nb, ratio * ratio,
                                                     dOut, count.data(), maxMatches);
    CUDA_CHECK_LAST();

    int total = 0;
    CUDA_CHECK(cudaMemcpyAsync(&total, count.data(), sizeof(int),
                               cudaMemcpyDeviceToHost, stream));
    CUDA_CHECK(cudaStreamSynchronize(stream));
    if (total > maxMatches) total = maxMatches;
    return total;
}
