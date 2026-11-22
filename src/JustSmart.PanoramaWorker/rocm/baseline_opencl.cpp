// AMD GPU（OpenCL 2.0 / AMD APP 运行时）加速的 Baseline 全景拼接（不依赖 OpenCV）。
//
// 完整复刻 PanoramaStitcher.Blend（Baseline）流程：
//   1) 处理尺度缩放（Area 平均）+ 灰度 + CLAHE(2.0, 8x8)；
//   2) GPU SIFT 提取与 Lowe ratio 匹配（复用 sift_gpu.h），并像 C# 一样尝试
//      全图与 40%/50%/60% 重叠区域候选，用灰度相关系数选优；
//   3) CPU 端 RANSAC 相似变换（对应 EstimateAffinePartial2D）；
//   4) 变换链累积：T[k] = T[k-1] * inv(M_k)；
//   5) GPU：仿射 warp（双线性、零边界）→ 掩膜 → JFA 精确欧氏距离 →
//      weight = min(dist/ramp,1)，再连乘 (sharpness-1) 次 → 加权累加 → 归一化；
//   6) 裁黑边（gray > 6 的包围盒）并输出 PNG。
//
// 与 C# 版的差异（均因为不依赖 OpenCV）：
//   - 用灰度相关系数代替 ECC 精化与 cc 评分，未做 NCC 模板兜底；
//   - 距离变换用精确欧氏 JFA 代替 OpenCV 的 3x3 掩膜近似。
//
// 用法：baseline_opencl <out.png> <img1> <img2> [img3 ...]
//        [--scale=0.5] [--ramp=80] [--sharpness=4] [--maxk=20000]
//        [--mininliers=20]

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

#include "opencl_common.h"
#include "sift_gpu.h"

#define STB_IMAGE_IMPLEMENTATION
#include "../stb_image/stb_image.h"
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "../stb_image/stb_image_write.h"

namespace {

// ============================================================================
// 几何
// ============================================================================

struct Point2 {
    double x;
    double y;
};

// 2x3 仿射：x' = a*x + b*y + c, y' = d*x + e*y + f
struct Affine2 {
    double a, b, c, d, e, f;
};

struct Mat3 {
    double m[9];
};

Mat3 Identity3() { return Mat3{{1, 0, 0, 0, 1, 0, 0, 0, 1}}; }

Mat3 Mul3(const Mat3& x, const Mat3& y) {
    Mat3 r;
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j) {
            r.m[i * 3 + j] = 0.0;
            for (int k = 0; k < 3; ++k) {
                r.m[i * 3 + j] += x.m[i * 3 + k] * y.m[k * 3 + j];
            }
        }
    }
    return r;
}

Mat3 FromAffine(const Affine2& a) {
    return Mat3{{a.a, a.b, a.c, a.d, a.e, a.f, 0, 0, 1}};
}

Affine2 ToAffine(const Mat3& m) {
    return Affine2{m.m[0], m.m[1], m.m[2], m.m[3], m.m[4], m.m[5]};
}

Affine2 InvertAffine(const Affine2& m) {
    const double det = m.a * m.e - m.b * m.d;
    const double inv = det != 0.0 ? 1.0 / det : 0.0;
    Affine2 r;
    r.a = m.e * inv;
    r.b = -m.b * inv;
    r.c = (m.b * m.f - m.e * m.c) * inv;
    r.d = -m.d * inv;
    r.e = m.a * inv;
    r.f = (m.d * m.c - m.a * m.f) * inv;
    return r;
}

// ============================================================================
// 图像预处理：Area 缩放、灰度、CLAHE
// ============================================================================

bool LoadRgb(const std::string& path, int* w, int* h, std::vector<uint8_t>* rgb) {
    int comp = 0;
    stbi_uc* data = stbi_load(path.c_str(), w, h, &comp, 3);
    if (!data) {
        std::fprintf(stderr, "stbi_load 失败: %s (%s)\n", path.c_str(),
                     stbi_failure_reason());
        return false;
    }
    rgb->assign(data, data + static_cast<size_t>(*w) * (*h) * 3);
    stbi_image_free(data);
    return true;
}

// 面积平均缩放（scale < 1 时为多像素平均，与 OpenCV INTER_AREA 对齐）。
void ResizeArea(const std::vector<uint8_t>& src, int sw, int sh, int dw, int dh,
                std::vector<uint8_t>* dst) {
    dst->assign(static_cast<size_t>(dw) * dh * 3, 0);
    for (int y = 0; y < dh; ++y) {
        int y0 = static_cast<int>(static_cast<long long>(y) * sh / dh);
        int y1 = static_cast<int>((static_cast<long long>(y + 1) * sh + dh - 1) / dh);
        if (y1 <= y0) y1 = y0 + 1;
        if (y0 < 0) y0 = 0;
        if (y1 > sh) y1 = sh;
        for (int x = 0; x < dw; ++x) {
            int x0 = static_cast<int>(static_cast<long long>(x) * sw / dw);
            int x1 = static_cast<int>((static_cast<long long>(x + 1) * sw + dw - 1) / dw);
            if (x1 <= x0) x1 = x0 + 1;
            if (x0 < 0) x0 = 0;
            if (x1 > sw) x1 = sw;
            double sum[3] = {0, 0, 0};
            int count = 0;
            for (int yy = y0; yy < y1; ++yy) {
                const uint8_t* row = src.data() +
                                     (static_cast<size_t>(yy) * sw + x0) * 3;
                for (int xx = x0; xx < x1; ++xx) {
                    sum[0] += row[0];
                    sum[1] += row[1];
                    sum[2] += row[2];
                    row += 3;
                    ++count;
                }
            }
            uint8_t* out = dst->data() + (static_cast<size_t>(y) * dw + x) * 3;
            if (count <= 0) count = 1;
            for (int c = 0; c < 3; ++c) {
                double v = sum[c] / count;
                out[c] = static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v + 0.5));
            }
        }
    }
}

// BGR/RGB 加权灰度（与 OpenCV cvtColor 的 0.299/0.587/0.114 一致）。
void ToGray(const std::vector<uint8_t>& rgb, std::vector<uint8_t>* gray) {
    const size_t n = rgb.size() / 3;
    gray->resize(n);
    for (size_t i = 0; i < n; ++i) {
        const int r = rgb[i * 3 + 0];
        const int g = rgb[i * 3 + 1];
        const int b = rgb[i * 3 + 2];
        (*gray)[i] = static_cast<uint8_t>((29 * b + 150 * g + 77 * r + 128) >> 8);
    }
}

// 对比度受限自适应直方图均衡（对应 Cv2.CreateCLAHE(2.0, Size(8, 8))）。
void ApplyClahe(std::vector<uint8_t>* gray, int w, int h, int tilesX, int tilesY,
                double clipLimit) {
    if (w <= 0 || h <= 0) return;
    const int histSize = 256;
    std::vector<std::array<int, 256>> luts(static_cast<size_t>(tilesX) * tilesY);

    for (int ty = 0; ty < tilesY; ++ty) {
        int y0 = ty * h / tilesY;
        int y1 = (ty + 1) * h / tilesY;
        if (y1 <= y0) y1 = y0 + 1;
        if (y0 < 0) y0 = 0;
        if (y1 > h) y1 = h;
        for (int tx = 0; tx < tilesX; ++tx) {
            int x0 = tx * w / tilesX;
            int x1 = (tx + 1) * w / tilesX;
            if (x1 <= x0) x1 = x0 + 1;
            if (x0 < 0) x0 = 0;
            if (x1 > w) x1 = w;

            int hist[256] = {0};
            int area = 0;
            for (int y = y0; y < y1; ++y) {
                const uint8_t* row = gray->data() + static_cast<size_t>(y) * w;
                for (int x = x0; x < x1; ++x) {
                    ++hist[row[x]];
                    ++area;
                }
            }
            std::array<int, 256>& lut = luts[static_cast<size_t>(ty) * tilesX + tx];
            if (area <= 0) {
                for (int v = 0; v < 256; ++v) lut[v] = v;
                continue;
            }

            int limit = static_cast<int>(clipLimit * area / histSize);
            if (limit < 1) limit = 1;
            int excess = 0;
            for (int v = 0; v < 256; ++v) {
                if (hist[v] > limit) {
                    excess += hist[v] - limit;
                    hist[v] = limit;
                }
            }
            const int batch = excess / 256;
            const int rem = excess % 256;
            for (int v = 0; v < 256; ++v) hist[v] += batch;
            for (int v = 0; v < rem; ++v) ++hist[v];

            int cum = 0;
            for (int v = 0; v < 256; ++v) {
                cum += hist[v];
                int val = static_cast<int>(255.0 * cum / area + 0.5);
                lut[v] = val > 255 ? 255 : (val < 0 ? 0 : val);
            }
        }
    }

    std::vector<uint8_t> out(gray->size());
    for (int y = 0; y < h; ++y) {
        double gy = ((y + 0.5) * tilesY / h) - 0.5;
        if (gy < 0) gy = 0;
        if (gy > tilesY - 1) gy = tilesY - 1;
        const int ty0 = static_cast<int>(std::floor(gy));
        const int ty1 = std::min(ty0 + 1, tilesY - 1);
        const double fy = gy - ty0;
        for (int x = 0; x < w; ++x) {
            double gx = ((x + 0.5) * tilesX / w) - 0.5;
            if (gx < 0) gx = 0;
            if (gx > tilesX - 1) gx = tilesX - 1;
            const int tx0 = static_cast<int>(std::floor(gx));
            const int tx1 = std::min(tx0 + 1, tilesX - 1);
            const double fx = gx - tx0;

            const int v = (*gray)[static_cast<size_t>(y) * w + x];
            const int v00 = luts[static_cast<size_t>(ty0) * tilesX + tx0][v];
            const int v01 = luts[static_cast<size_t>(ty0) * tilesX + tx1][v];
            const int v10 = luts[static_cast<size_t>(ty1) * tilesX + tx0][v];
            const int v11 = luts[static_cast<size_t>(ty1) * tilesX + tx1][v];
            const double top = (1 - fx) * v00 + fx * v01;
            const double bot = (1 - fx) * v10 + fx * v11;
            double val = (1 - fy) * top + fy * bot;
            int iv = static_cast<int>(val + 0.5);
            out[static_cast<size_t>(y) * w + x] =
                static_cast<uint8_t>(iv < 0 ? 0 : (iv > 255 ? 255 : iv));
        }
    }
    gray->swap(out);
}

// ============================================================================
// CPU 端 RANSAC 相似变换（对应 EstimateAffinePartial2D）
// ============================================================================

// 由两对点解出相似变换 p -> q（4 自由度：缩放 + 旋转 + 平移）。
bool SimilarityFromTwo(const Point2& p1, const Point2& p2, const Point2& q1,
                       const Point2& q2, Affine2* out) {
    const double dx1 = p2.x - p1.x;
    const double dy1 = p2.y - p1.y;
    const double dx2 = q2.x - q1.x;
    const double dy2 = q2.y - q1.y;
    const double den = dx1 * dx1 + dy1 * dy1;
    if (den < 1e-9) return false;
    const double ar = (dx2 * dx1 + dy2 * dy1) / den;   // Re((q2-q1)/(p2-p1))
    const double ai = (dy2 * dx1 - dx2 * dy1) / den;   // Im(...)
    out->a = ar;
    out->b = -ai;
    out->d = ai;
    out->e = ar;
    out->c = q1.x - (ar * p1.x - ai * p1.y);
    out->f = q1.y - (ai * p1.x + ar * p1.y);
    return true;
}

// 对给定内点做最小二乘精化（同 Umeyama 的 2D 相似解）。
bool RefineSimilarity(const std::vector<Point2>& src, const std::vector<Point2>& dst,
                     const std::vector<int>& idx, Affine2* out) {
    if (idx.size() < 2) return false;
    double pcx = 0, pcy = 0, qcx = 0, qcy = 0;
    for (int i : idx) {
        pcx += src[i].x;
        pcy += src[i].y;
        qcx += dst[i].x;
        qcy += dst[i].y;
    }
    const double inv = 1.0 / static_cast<double>(idx.size());
    pcx *= inv;
    pcy *= inv;
    qcx *= inv;
    qcy *= inv;

    double numA = 0, numB = 0, den = 0;
    for (int i : idx) {
        const double px = src[i].x - pcx;
        const double py = src[i].y - pcy;
        const double qx = dst[i].x - qcx;
        const double qy = dst[i].y - qcy;
        numA += px * qx + py * qy;
        numB += px * qy - py * qx;
        den += px * px + py * py;
    }
    if (den < 1e-9) return false;
    const double ar = numA / den;
    const double ai = numB / den;
    out->a = ar;
    out->b = -ai;
    out->d = ai;
    out->e = ar;
    out->c = qcx - (ar * pcx - ai * pcy);
    out->f = qcy - (ai * pcx + ar * pcy);
    return true;
}

bool EstimateSimilarityRansac(const std::vector<Point2>& src,
                              const std::vector<Point2>& dst, double thresh,
                              int maxIters, int minInliers, Affine2* out,
                              int* inliersOut) {
    const int n = static_cast<int>(src.size());
    if (n < 3) return false;
    const double thr2 = thresh * thresh;

    auto countInliers = [&](const Affine2& m, std::vector<int>* idx) {
        int cnt = 0;
        if (idx) idx->clear();
        for (int i = 0; i < n; ++i) {
            const double px = m.a * src[i].x + m.b * src[i].y + m.c - dst[i].x;
            const double py = m.d * src[i].x + m.e * src[i].y + m.f - dst[i].y;
            if (px * px + py * py <= thr2) {
                ++cnt;
                if (idx) idx->push_back(i);
            }
        }
        return cnt;
    };

    std::mt19937 rng(0xC0FFEEu);
    int bestCnt = 0;
    Affine2 best{1, 0, 0, 0, 1, 0};
    for (int iter = 0; iter < maxIters; ++iter) {
        const int i = static_cast<int>(rng() % static_cast<unsigned>(n));
        const int j = static_cast<int>(rng() % static_cast<unsigned>(n));
        if (i == j) continue;
        Affine2 m;
        if (!SimilarityFromTwo(src[i], src[j], dst[i], dst[j], &m)) continue;
        const int cnt = countInliers(m, nullptr);
        if (cnt > bestCnt) {
            bestCnt = cnt;
            best = m;
            if (bestCnt >= static_cast<int>(0.9 * n)) break;
        }
    }
    if (bestCnt < minInliers) {
        *inliersOut = bestCnt;
        return false;
    }

    std::vector<int> idx;
    for (int round = 0; round < 3; ++round) {
        countInliers(best, &idx);
        Affine2 refined;
        if (!RefineSimilarity(src, dst, idx, &refined)) break;
        const int cnt = countInliers(refined, nullptr);
        if (cnt < bestCnt) break;
        best = refined;
        bestCnt = cnt;
    }

    *out = best;
    *inliersOut = bestCnt;
    return true;
}

// 线性插值采样；越界返回 -1。
inline double SampleBilinear(const std::vector<uint8_t>& g, int w, int h, double x,
                             double y) {
    if (x < 0.0 || x > w - 1.0 || y < 0.0 || y > h - 1.0) return -1.0;
    const int x0 = static_cast<int>(std::floor(x));
    const int y0 = static_cast<int>(std::floor(y));
    const int x1 = std::min(x0 + 1, w - 1);
    const int y1 = std::min(y0 + 1, h - 1);
    const double fx = x - x0;
    const double fy = y - y0;
    const double v00 = g[static_cast<size_t>(y0) * w + x0];
    const double v10 = g[static_cast<size_t>(y0) * w + x1];
    const double v01 = g[static_cast<size_t>(y1) * w + x0];
    const double v11 = g[static_cast<size_t>(y1) * w + x1];
    return (1 - fx) * (1 - fy) * v00 + fx * (1 - fy) * v10 + (1 - fx) * fy * v01 +
           fx * fy * v11;
}

// 用灰度相关系数评价模型（近似 C# 中 ECC 的 cc）：在 A 上等间隔取点，
// 映射到 B 后做 Pearson 相关；两图亮度差异（增益/曝光）不影响结果。
double CorrelationScore(const std::vector<uint8_t>& ga, int wa, int ha,
                        const std::vector<uint8_t>& gb, int wb, int hb,
                        const Affine2& m, int stride) {
    double sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
    long long n = 0;
    for (int y = 0; y < ha; y += stride) {
        for (int x = 0; x < wa; x += stride) {
            const double bx = m.a * x + m.b * y + m.c;
            const double by = m.d * x + m.e * y + m.f;
            const double vb = SampleBilinear(gb, wb, hb, bx, by);
            if (vb < 0.0) continue;
            const double va = ga[static_cast<size_t>(y) * wa + x];
            sa += va;
            sb += vb;
            saa += va * va;
            sbb += vb * vb;
            sab += va * vb;
            ++n;
        }
    }
    if (n < 200) return -1.0;
    const double dn = static_cast<double>(n);
    const double cov = dn * sab - sa * sb;
    const double vA = dn * saa - sa * sa;
    const double vB = dn * sbb - sb * sb;
    if (vA <= 1e-9 || vB <= 1e-9) return -1.0;
    return cov / std::sqrt(vA * vB);
}

// ============================================================================
// OpenCL：Blend 阶段内核
// ============================================================================

const char* kBlendSource = R"CLC(
inline float sample_u8(__global const uchar* p, int x, int y, int w, int h) {
    if (x < 0 || x >= w || y < 0 || y >= h) return 0.0f;
    return (float)p[y * w + x];
}

__kernel void warp_affine(
    __global const uchar* srcR, __global const uchar* srcG, __global const uchar* srcB,
    __global uchar* dstR, __global uchar* dstG, __global uchar* dstB,
    __global uchar* mask,
    const int srcW, const int srcH, const int dstW, const int dstH,
    const float ia, const float ib, const float ic,
    const float id, const float ie, const float iff)
{
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= dstW || y >= dstH) return;
    const int i = y * dstW + x;

    const float sx = ia * (float)x + ib * (float)y + ic;
    const float sy = id * (float)x + ie * (float)y + iff;
    if (sx < 0.0f || sx >= (float)srcW || sy < 0.0f || sy >= (float)srcH) {
        dstR[i] = 0; dstG[i] = 0; dstB[i] = 0; mask[i] = 0;
        return;
    }

    const int x0 = (int)floor(sx);
    const int y0 = (int)floor(sy);
    const int x1 = x0 + 1;
    const int y1 = y0 + 1;
    const float fx = sx - (float)x0;
    const float fy = sy - (float)y0;
    const float w00 = (1.0f - fx) * (1.0f - fy);
    const float w10 = fx * (1.0f - fy);
    const float w01 = (1.0f - fx) * fy;
    const float w11 = fx * fy;

    const float r = w00 * sample_u8(srcR, x0, y0, srcW, srcH) +
                    w10 * sample_u8(srcR, x1, y0, srcW, srcH) +
                    w01 * sample_u8(srcR, x0, y1, srcW, srcH) +
                    w11 * sample_u8(srcR, x1, y1, srcW, srcH);
    const float g = w00 * sample_u8(srcG, x0, y0, srcW, srcH) +
                    w10 * sample_u8(srcG, x1, y0, srcW, srcH) +
                    w01 * sample_u8(srcG, x0, y1, srcW, srcH) +
                    w11 * sample_u8(srcG, x1, y1, srcW, srcH);
    const float b = w00 * sample_u8(srcB, x0, y0, srcW, srcH) +
                    w10 * sample_u8(srcB, x1, y0, srcW, srcH) +
                    w01 * sample_u8(srcB, x0, y1, srcW, srcH) +
                    w11 * sample_u8(srcB, x1, y1, srcW, srcH);

    const uchar ir = convert_uchar_sat_rte(r);
    const uchar ig = convert_uchar_sat_rte(g);
    const uchar ibv = convert_uchar_sat_rte(b);
    dstR[i] = ir; dstG[i] = ig; dstB[i] = ibv;
    const int gray = (29 * (int)ibv + 150 * (int)ig + 77 * (int)ir + 128) >> 8;
    mask[i] = gray > 0 ? (uchar)255 : (uchar)0;
}

__kernel void jfa_seed(__global const uchar* mask, __global int* sx,
                       __global int* sy, const int w, const int h)
{
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= w || y >= h) return;
    const int i = y * w + x;
    if (mask[i] == (uchar)0) {
        sx[i] = x;
        sy[i] = y;
    } else {
        sx[i] = -1;
        sy[i] = -1;
    }
}

__kernel void jfa_pass(__global const int* sx, __global const int* sy,
                       __global int* ox, __global int* oy,
                       const int w, const int h, const int step)
{
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= w || y >= h) return;
    const int i = y * w + x;

    int bestx = sx[i];
    int besty = sy[i];
    float bestd = 1e30f;
    if (bestx >= 0) {
        const float dx = (float)(x - bestx);
        const float dy = (float)(y - besty);
        bestd = dx * dx + dy * dy;
    }
    for (int oy2 = -1; oy2 <= 1; ++oy2) {
        for (int ox2 = -1; ox2 <= 1; ++ox2) {
            const int nx = x + ox2 * step;
            const int ny = y + oy2 * step;
            if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
            const int j = ny * w + nx;
            const int gx = sx[j];
            const int gy = sy[j];
            if (gx < 0) continue;
            const float dx = (float)(x - gx);
            const float dy = (float)(y - gy);
            const float d = dx * dx + dy * dy;
            if (d < bestd) {
                bestd = d;
                bestx = gx;
                besty = gy;
            }
        }
    }
    ox[i] = bestx;
    oy[i] = besty;
}

__kernel void accumulate(__global const uchar* pR, __global const uchar* pG,
                         __global const uchar* pB, __global const uchar* mask,
                         __global const int* sx, __global const int* sy,
                         __global float* acc, __global float* accW,
                         const int n, const int w, const float ramp,
                         const int sharpness)
{
    const int i = (int)get_global_id(0);
    if (i >= n) return;
    float wgt = 0.0f;
    if (mask[i] != (uchar)0 && sx[i] >= 0) {
        const float dx = (float)((i % w) - sx[i]);
        const float dy = (float)((i / w) - sy[i]);
        const float d = sqrt(dx * dx + dy * dy);
        wgt = d / ramp;
        if (wgt > 1.0f) wgt = 1.0f;
        for (int s = 1; s < sharpness; ++s) wgt *= wgt;
    }
    if (wgt > 0.0f) {
        acc[i] += (float)pR[i] * wgt;
        acc[n + i] += (float)pG[i] * wgt;
        acc[2 * n + i] += (float)pB[i] * wgt;
        accW[i] += wgt;
    }
}

__kernel void finalize(__global const float* acc, __global const float* accW,
                       __global uchar* out, const int n)
{
    const int i = (int)get_global_id(0);
    if (i >= n) return;
    const float inv = 1.0f / (accW[i] + 1e-6f);
    out[i] = convert_uchar_sat_rte(acc[i] * inv);
    out[n + i] = convert_uchar_sat_rte(acc[n + i] * inv);
    out[2 * n + i] = convert_uchar_sat_rte(acc[2 * n + i] * inv);
}
)CLC";

cl_program BuildProgramSource(OpenCl& cl, cl_context ctx, cl_device_id device,
                              const char* src, const char* label) {
    cl_int err = CL_SUCCESS;
    cl_program program = cl.CreateProgramWithSource(ctx, 1, &src, nullptr, &err);
    Check(cl, err, "clCreateProgramWithSource");
    err = cl.BuildProgram(program, 1, &device, "-cl-std=CL2.0", nullptr, nullptr);
    if (err != CL_SUCCESS) {
        size_t logSize = 0;
        cl.GetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, 0, nullptr,
                               &logSize);
        std::vector<char> log(logSize + 1, 0);
        cl.GetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, logSize,
                               log.data(), nullptr);
        std::fprintf(stderr, "%s build log:\n%s\n", label, log.data());
        std::exit(5);
    }
    return program;
}

void Launch2D(OpenCl& cl, cl_command_queue queue, cl_kernel k, int w, int h) {
    const size_t global[2] = {RoundUp(static_cast<size_t>(w), 16),
                              RoundUp(static_cast<size_t>(h), 16)};
    const size_t local[2] = {16, 16};
    Check(cl, cl.EnqueueNDRangeKernel(queue, k, 2, nullptr, global, local, 0,
                                      nullptr, nullptr),
          "clEnqueueNDRangeKernel");
}

void Launch1D(OpenCl& cl, cl_command_queue queue, cl_kernel k, size_t n) {
    const size_t global[1] = {RoundUp(n, 256)};
    const size_t local[1] = {256};
    Check(cl, cl.EnqueueNDRangeKernel(queue, k, 1, nullptr, global, local, 0,
                                      nullptr, nullptr),
          "clEnqueueNDRangeKernel");
}

void SetMem(OpenCl& cl, cl_kernel k, cl_uint i, cl_mem m) {
    Check(cl, cl.SetKernelArg(k, i, sizeof(cl_mem), &m), "clSetKernelArg");
}
void SetInt(OpenCl& cl, cl_kernel k, cl_uint i, int v) {
    Check(cl, cl.SetKernelArg(k, i, sizeof(int), &v), "clSetKernelArg");
}
void SetFloat(OpenCl& cl, cl_kernel k, cl_uint i, float v) {
    Check(cl, cl.SetKernelArg(k, i, sizeof(float), &v), "clSetKernelArg");
}

cl_mem MakeBuffer(OpenCl& cl, cl_context ctx, cl_bitfield flags, size_t bytes,
                  const void* host) {
    cl_int err = CL_SUCCESS;
    cl_bitfield f = flags;
    if (host) f |= CL_MEM_COPY_HOST_PTR;
    cl_mem m = cl.CreateBuffer(ctx, f, bytes, const_cast<void*>(host), &err);
    Check(cl, err, "clCreateBuffer");
    return m;
}

// ============================================================================
// 主流程
// ============================================================================

struct ImageData {
    int w = 0;
    int h = 0;
    std::vector<uint8_t> rgb;        // 处理尺度下的 RGB
    std::vector<uint8_t> grayPlain;  // 原始灰度（相关性评分用）
    std::vector<uint8_t> graySift;   // CLAHE 后灰度（SIFT 输入）
    cl_mem meta = nullptr;
    cl_mem desc = nullptr;
    int keyCount = 0;
    std::vector<KeyMeta> keys;
};

double ElapsedMs(const std::chrono::steady_clock::time_point& a,
                 const std::chrono::steady_clock::time_point& b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);

    std::string outPath;
    std::vector<std::string> imagePaths;
    double scale = 0.5;
    int ramp = 80;
    int sharpness = 4;
    int maxKeys = 20000;
    int minInliers = 20;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg.rfind("--", 0) == 0) {
            auto value = [&](const char* name) -> const char* {
                const std::string prefix = std::string("--") + name + "=";
                if (arg.rfind(prefix, 0) == 0) return argv[i] + prefix.size();
                return nullptr;
            };
            if (const char* v = value("scale")) scale = std::atof(v);
            else if (const char* v2 = value("ramp")) ramp = std::atoi(v2);
            else if (const char* v3 = value("sharpness")) sharpness = std::atoi(v3);
            else if (const char* v4 = value("maxk")) maxKeys = std::atoi(v4);
            else if (const char* v5 = value("mininliers")) minInliers = std::atoi(v5);
            continue;
        }
        if (outPath.empty()) {
            outPath = arg;
        } else {
            imagePaths.push_back(arg);
        }
    }

    if (outPath.empty() || imagePaths.size() < 2) {
        std::printf(
            "usage: baseline_opencl <out.png> <img1> <img2> [img3 ...]\n"
            "       [--scale=0.5] [--ramp=80] [--sharpness=4] [--maxk=20000]\n"
            "       [--mininliers=20]\n");
        return 2;
    }
    if (scale <= 0.0) scale = 1.0;
    if (ramp < 1) ramp = 1;
    if (sharpness < 1) sharpness = 1;
    if (maxKeys < 64) maxKeys = 64;

    // ---- 读入 + 缩放 + 灰度 + CLAHE ----
    std::vector<ImageData> images(imagePaths.size());
    for (size_t i = 0; i < imagePaths.size(); ++i) {
        int sw = 0, sh = 0;
        std::vector<uint8_t> full;
        if (!LoadRgb(imagePaths[i], &sw, &sh, &full)) return 6;

        int dw = static_cast<int>(sw * scale + 0.5);
        int dh = static_cast<int>(sh * scale + 0.5);
        if (dw < 16) dw = 16;
        if (dh < 16) dh = 16;
        if (dw > sw) dw = sw;
        if (dh > sh) dh = sh;

        ImageData& img = images[i];
        img.w = dw;
        img.h = dh;
        if (dw == sw && dh == sh) {
            img.rgb.swap(full);
        } else {
            ResizeArea(full, sw, sh, dw, dh, &img.rgb);
        }
        ToGray(img.rgb, &img.grayPlain);
        img.graySift = img.grayPlain;
        ApplyClahe(&img.graySift, dw, dh, 8, 8, 2.0);
        std::printf("图像%d: %s  %dx%d -> %dx%d\n", static_cast<int>(i) + 1,
                    imagePaths[i].c_str(), sw, sh, dw, dh);
    }

    // ---- OpenCL 初始化 ----
    OpenCl cl;
    if (!cl.Load()) return 3;
    cl_uint platformCount = 0;
    Check(cl, cl.GetPlatformIDs(0, nullptr, &platformCount), "clGetPlatformIDs");
    std::vector<cl_platform_id> platforms(platformCount);
    Check(cl, cl.GetPlatformIDs(platformCount, platforms.data(), nullptr),
          "clGetPlatformIDs");
    cl_platform_id amd = nullptr;
    for (cl_platform_id p : platforms) {
        const std::string name = PlatformName(cl, p);
        std::printf("platform: %s\n", name.c_str());
        if (name.find("AMD") != std::string::npos) amd = p;
    }
    if (!amd) {
        std::fprintf(stderr, "AMD OpenCL platform not found\n");
        return 4;
    }
    cl_device_id device = nullptr;
    Check(cl, cl.GetDeviceIDs(amd, CL_DEVICE_TYPE_GPU, 1, &device, nullptr),
          "clGetDeviceIDs");
    char devName[256] = {0};
    cl.GetDeviceInfo(device, CL_DEVICE_NAME, sizeof(devName), devName, nullptr);
    std::printf("device: %s\n", devName);

    cl_int err = CL_SUCCESS;
    cl_context context = cl.CreateContext(nullptr, 1, &device, nullptr, nullptr, &err);
    Check(cl, err, "clCreateContext");
    cl_command_queue queue = cl.CreateCommandQueue(context, device, 0, &err);
    Check(cl, err, "clCreateCommandQueue");

    cl_program siftProgram = BuildSiftProgram(cl, context, device);
    SiftGpu gpu{cl};
    gpu.ctx = context;
    gpu.queue = queue;
    gpu.Init(siftProgram);

    cl_program blendProgram =
        BuildProgramSource(cl, context, device, kBlendSource, "blend");
    auto makeKernel = [&](const char* name) {
        cl_kernel k = cl.CreateKernel(blendProgram, name, &err);
        Check(cl, err, "clCreateKernel");
        return k;
    };
    cl_kernel kWarp = makeKernel("warp_affine");
    cl_kernel kSeed = makeKernel("jfa_seed");
    cl_kernel kJfa = makeKernel("jfa_pass");
    cl_kernel kAcc = makeKernel("accumulate");
    cl_kernel kFinal = makeKernel("finalize");

    // ---- SIFT 提取 ----
    const auto tExtract0 = std::chrono::steady_clock::now();
    for (ImageData& img : images) {
        img.keyCount = gpu.Extract(img.graySift, img.w, img.h, maxKeys, &img.meta,
                                   &img.desc);
        if (img.keyCount > 0) {
            img.keys.resize(static_cast<size_t>(img.keyCount));
            Check(cl, cl.EnqueueReadBuffer(queue, img.meta, CL_TRUE, 0,
                                           img.keys.size() * sizeof(KeyMeta),
                                           img.keys.data(), 0, nullptr, nullptr),
                  "clEnqueueReadBuffer");
        }
    }
    Check(cl, cl.Finish(queue), "clFinish");
    const auto tExtract1 = std::chrono::steady_clock::now();

    // ---- 相邻对配准（多候选区域 SIFT + RANSAC + 灰度相关评分）----
    const auto tReg0 = std::chrono::steady_clock::now();
    const double kRatio = 0.75;
    const double kRansacThreshold = 4.0;
    const int kMinInliers = minInliers;
    const int maxMatches = std::min(maxKeys, 8192);

    struct RectI {
        int x, y, w, h;
    };
    struct CropSift {
        cl_mem meta = nullptr;
        cl_mem desc = nullptr;
        int count = 0;
        std::vector<KeyMeta> keys;
    };

    // 对 RGB 图的矩形区域做灰度 + CLAHE + GPU SIFT（等价于 C# DetectSift(子图)）。
    auto extractCrop = [&](const ImageData& img, const RectI& r, CropSift* out) {
        std::vector<uint8_t> crop(static_cast<size_t>(r.w) * r.h * 3);
        for (int y = 0; y < r.h; ++y) {
            const uint8_t* srcRow =
                img.rgb.data() + (static_cast<size_t>(y + r.y) * img.w + r.x) * 3;
            std::memcpy(crop.data() + static_cast<size_t>(y) * r.w * 3, srcRow,
                        static_cast<size_t>(r.w) * 3);
        }
        std::vector<uint8_t> gray;
        ToGray(crop, &gray);
        ApplyClahe(&gray, r.w, r.h, 8, 8, 2.0);
        out->count = gpu.Extract(gray, r.w, r.h, maxKeys, &out->meta, &out->desc, 1.0f);
        if (out->count > 0) {
            out->keys.resize(static_cast<size_t>(out->count));
            Check(cl, cl.EnqueueReadBuffer(queue, out->meta, CL_TRUE, 0,
                                           out->keys.size() * sizeof(KeyMeta),
                                           out->keys.data(), 0, nullptr, nullptr),
                  "clEnqueueReadBuffer");
        }
        return out->count > 0;
    };

    std::vector<Mat3> transforms(images.size(), Identity3());
    for (size_t k = 1; k < images.size(); ++k) {
        const ImageData& a = images[k - 1];
        const ImageData& b = images[k];

        // 与 C# MatchPair 相同的候选：全图 + A 右侧 40%/50%/60% 对 B 左侧。
        std::vector<std::pair<RectI, RectI>> cands;
        cands.push_back({RectI{0, 0, a.w, a.h}, RectI{0, 0, b.w, b.h}});
        for (double frac : {0.4, 0.5, 0.6}) {
            const int xa = static_cast<int>(a.w * (1.0 - frac));
            const int xb = static_cast<int>(b.w * frac);
            cands.push_back({RectI{xa, 0, a.w - xa, a.h}, RectI{0, 0, xb, b.h}});
        }

        double bestCc = -1.0;
        Affine2 bestModel{1, 0, 0, 0, 1, 0};
        int bestInliers = 0;
        int bestGood = 0;

        for (size_t ci = 0; ci < cands.size(); ++ci) {
            const RectI& ra = cands[ci].first;
            const RectI& rb = cands[ci].second;
            CropSift cropA;
            CropSift cropB;
            cl_mem metaA = nullptr, descA = nullptr, metaB = nullptr, descB = nullptr;
            const std::vector<KeyMeta>* keysA = nullptr;
            const std::vector<KeyMeta>* keysB = nullptr;
            int na = 0;
            int nb = 0;

            if (ci == 0) {
                metaA = a.meta;
                descA = a.desc;
                na = a.keyCount;
                keysA = &a.keys;
                metaB = b.meta;
                descB = b.desc;
                nb = b.keyCount;
                keysB = &b.keys;
            } else {
                if (!extractCrop(a, ra, &cropA)) continue;
                if (!extractCrop(b, rb, &cropB)) {
                    cl.ReleaseMemObject(cropA.meta);
                    cl.ReleaseMemObject(cropA.desc);
                    continue;
                }
                metaA = cropA.meta;
                descA = cropA.desc;
                na = cropA.count;
                keysA = &cropA.keys;
                metaB = cropB.meta;
                descB = cropB.desc;
                nb = cropB.count;
                keysB = &cropB.keys;
            }

            std::vector<MatchRec> matches;
            const int nm = gpu.Match(metaA, descA, na, metaB, descB, nb,
                                     static_cast<float>(kRatio), maxMatches,
                                     &matches);
            Check(cl, cl.Finish(queue), "clFinish");

            std::vector<Point2> src;
            std::vector<Point2> dst;
            src.reserve(matches.size());
            dst.reserve(matches.size());
            for (const MatchRec& m : matches) {
                if (m.query < 0 || m.query >= na) continue;
                if (m.train < 0 || m.train >= nb) continue;
                const KeyMeta& ka = (*keysA)[static_cast<size_t>(m.query)];
                const KeyMeta& kb = (*keysB)[static_cast<size_t>(m.train)];
                src.push_back(Point2{ka.x, ka.y});
                dst.push_back(Point2{kb.x, kb.y});
            }

            Affine2 model;
            int inliers = 0;
            bool ok = EstimateSimilarityRansac(src, dst, kRansacThreshold, 20000,
                                               kMinInliers, &model, &inliers);
            bool relaxed = false;
            if (!ok) {
                // 弱纹理 / 重复纹理场景：放宽内点下限，但最终要求相关度足够高。
                const int relaxedMin = std::max(6, kMinInliers / 3);
                ok = EstimateSimilarityRansac(src, dst, kRansacThreshold, 20000,
                                              relaxedMin, &model, &inliers);
                relaxed = ok;
            }
            if (ok) {
                // 局部候选坐标折算回整幅图像。
                model.c += static_cast<double>(rb.x - ra.x);
                const double cc =
                    CorrelationScore(a.grayPlain, a.w, a.h, b.grayPlain, b.w, b.h,
                                     model, 4);
                std::printf(
                    "  候选%d %dx%d@(%d,0)|%dx%d: 关键点 %d/%d，匹配 %d，"
                    "内点 %d%s，cc=%.4f\n",
                    static_cast<int>(ci), ra.w, ra.h, ra.x, rb.w, rb.h, na, nb, nm,
                    inliers, relaxed ? "(放宽)" : "", cc);
                if ((!relaxed || cc >= 0.5) && cc > bestCc) {
                    bestCc = cc;
                    bestModel = model;
                    bestInliers = inliers;
                    bestGood = nm;
                }
            } else {
                std::printf("  候选%d: 关键点 %d/%d，匹配 %d，内点 %d（淘汰）\n",
                            static_cast<int>(ci), na, nb, nm, inliers);
            }

            if (ci != 0) {
                cl.ReleaseMemObject(cropA.meta);
                cl.ReleaseMemObject(cropA.desc);
                cl.ReleaseMemObject(cropB.meta);
                cl.ReleaseMemObject(cropB.desc);
            }
        }

        if (bestCc < 0.1) {
            std::fprintf(stderr,
                         "图像对 %d-%d 配准失败：最佳灰度相关系数 %.4f < 0.1\n",
                         static_cast<int>(k), static_cast<int>(k) + 1, bestCc);
            return 7;
        }

        // p_b = M * p_a；变换链使用逆变换（b -> a）。
        const Affine2 inv = InvertAffine(bestModel);
        transforms[k] = Mul3(transforms[k - 1], FromAffine(inv));

        const double rotDeg =
            std::atan2(bestModel.d, bestModel.a) * 180.0 / 3.14159265358979323846;
        std::printf(
            "图像对 %d-%d: 匹配 %d，内点 %d，cc=%.4f，"
            "dx=%.2f dy=%.2f rot=%.2f°\n",
            static_cast<int>(k), static_cast<int>(k) + 1, bestGood, bestInliers,
            bestCc, bestModel.c, bestModel.f, rotDeg);
    }
    const auto tReg1 = std::chrono::steady_clock::now();

    // ---- 画布范围 ----
    double minX = 1e30, minY = 1e30, maxX = -1e30, maxY = -1e30;
    for (size_t i = 0; i < images.size(); ++i) {
        const Mat3& t = transforms[i];
        const double w = images[i].w;
        const double h = images[i].h;
        const Point2 corners[4] = {{0, 0}, {w, 0}, {w, h}, {0, h}};
        for (const Point2& p : corners) {
            const double x = t.m[0] * p.x + t.m[1] * p.y + t.m[2];
            const double y = t.m[3] * p.x + t.m[4] * p.y + t.m[5];
            minX = std::min(minX, x);
            minY = std::min(minY, y);
            maxX = std::max(maxX, x);
            maxY = std::max(maxY, y);
        }
    }
    const int canvasW = static_cast<int>(std::ceil(maxX - std::floor(minX)));
    const int canvasH = static_cast<int>(std::ceil(maxY - std::floor(minY)));
    if (canvasW <= 0 || canvasH <= 0 ||
        static_cast<long long>(canvasW) * canvasH > 400000000LL) {
        std::fprintf(stderr, "画布尺寸非法: %dx%d\n", canvasW, canvasH);
        return 8;
    }
    std::printf("画布: %dx%d\n", canvasW, canvasH);

    Mat3 shift = Identity3();
    shift.m[2] = -std::floor(minX);
    shift.m[5] = -std::floor(minY);

    // ---- GPU 融合 ----
    const auto tBlend0 = std::chrono::steady_clock::now();
    const size_t npix = static_cast<size_t>(canvasW) * canvasH;

    std::vector<float> zeroAcc(npix * 3, 0.0f);
    std::vector<float> zeroW(npix, 0.0f);
    cl_mem acc = MakeBuffer(cl, context, CL_MEM_READ_WRITE, zeroAcc.size() * sizeof(float),
                            zeroAcc.data());
    cl_mem accW = MakeBuffer(cl, context, CL_MEM_READ_WRITE, zeroW.size() * sizeof(float),
                             zeroW.data());
    std::vector<cl_mem> tempBuffers;
    auto temp = [&](size_t bytes, const void* host) {
        cl_mem m = MakeBuffer(cl, context,
                              CL_MEM_READ_WRITE | (host ? CL_MEM_COPY_HOST_PTR : 0),
                              bytes, host);
        tempBuffers.push_back(m);
        return m;
    };

    for (size_t i = 0; i < images.size(); ++i) {
        const ImageData& img = images[i];
        const size_t srcN = static_cast<size_t>(img.w) * img.h;

        std::vector<uint8_t> planeR(srcN), planeG(srcN), planeB(srcN);
        for (size_t t = 0; t < srcN; ++t) {
            planeR[t] = img.rgb[t * 3 + 0];
            planeG[t] = img.rgb[t * 3 + 1];
            planeB[t] = img.rgb[t * 3 + 2];
        }

        cl_mem srcR = temp(srcN, planeR.data());
        cl_mem srcG = temp(srcN, planeG.data());
        cl_mem srcB = temp(srcN, planeB.data());
        cl_mem dstR = temp(npix, nullptr);
        cl_mem dstG = temp(npix, nullptr);
        cl_mem dstB = temp(npix, nullptr);
        cl_mem mask = temp(npix, nullptr);

        const Mat3 full = Mul3(shift, transforms[i]);
        const Affine2 inv = InvertAffine(ToAffine(full));

        SetMem(cl, kWarp, 0, srcR);
        SetMem(cl, kWarp, 1, srcG);
        SetMem(cl, kWarp, 2, srcB);
        SetMem(cl, kWarp, 3, dstR);
        SetMem(cl, kWarp, 4, dstG);
        SetMem(cl, kWarp, 5, dstB);
        SetMem(cl, kWarp, 6, mask);
        SetInt(cl, kWarp, 7, img.w);
        SetInt(cl, kWarp, 8, img.h);
        SetInt(cl, kWarp, 9, canvasW);
        SetInt(cl, kWarp, 10, canvasH);
        SetFloat(cl, kWarp, 11, static_cast<float>(inv.a));
        SetFloat(cl, kWarp, 12, static_cast<float>(inv.b));
        SetFloat(cl, kWarp, 13, static_cast<float>(inv.c));
        SetFloat(cl, kWarp, 14, static_cast<float>(inv.d));
        SetFloat(cl, kWarp, 15, static_cast<float>(inv.e));
        SetFloat(cl, kWarp, 16, static_cast<float>(inv.f));
        Launch2D(cl, queue, kWarp, canvasW, canvasH);

        // JFA 精确欧氏距离：以 mask==0 为种子。
        cl_mem siteSx[2] = {temp(npix * sizeof(int), nullptr),
                            temp(npix * sizeof(int), nullptr)};
        cl_mem siteSy[2] = {temp(npix * sizeof(int), nullptr),
                            temp(npix * sizeof(int), nullptr)};

        SetMem(cl, kSeed, 0, mask);
        SetMem(cl, kSeed, 1, siteSx[0]);
        SetMem(cl, kSeed, 2, siteSy[0]);
        SetInt(cl, kSeed, 3, canvasW);
        SetInt(cl, kSeed, 4, canvasH);
        Launch2D(cl, queue, kSeed, canvasW, canvasH);

        int step = 1;
        while ((step << 1) < std::max(canvasW, canvasH)) step <<= 1;
        int cur = 0;
        for (; step >= 1; step >>= 1) {
            const int nxt = 1 - cur;
            SetMem(cl, kJfa, 0, siteSx[cur]);
            SetMem(cl, kJfa, 1, siteSy[cur]);
            SetMem(cl, kJfa, 2, siteSx[nxt]);
            SetMem(cl, kJfa, 3, siteSy[nxt]);
            SetInt(cl, kJfa, 4, canvasW);
            SetInt(cl, kJfa, 5, canvasH);
            SetInt(cl, kJfa, 6, step);
            Launch2D(cl, queue, kJfa, canvasW, canvasH);
            cur = nxt;
        }

        SetMem(cl, kAcc, 0, dstR);
        SetMem(cl, kAcc, 1, dstG);
        SetMem(cl, kAcc, 2, dstB);
        SetMem(cl, kAcc, 3, mask);
        SetMem(cl, kAcc, 4, siteSx[cur]);
        SetMem(cl, kAcc, 5, siteSy[cur]);
        SetMem(cl, kAcc, 6, acc);
        SetMem(cl, kAcc, 7, accW);
        SetInt(cl, kAcc, 8, static_cast<int>(npix));
        SetInt(cl, kAcc, 9, canvasW);
        SetFloat(cl, kAcc, 10, static_cast<float>(ramp));
        SetInt(cl, kAcc, 11, sharpness);
        Launch1D(cl, queue, kAcc, npix);

        for (cl_mem m : tempBuffers) {
            cl.ReleaseMemObject(m);
        }
        tempBuffers.clear();
    }

    cl_mem outBuf = MakeBuffer(cl, context, CL_MEM_WRITE_ONLY, npix * 3, nullptr);
    SetMem(cl, kFinal, 0, acc);
    SetMem(cl, kFinal, 1, accW);
    SetMem(cl, kFinal, 2, outBuf);
    SetInt(cl, kFinal, 3, static_cast<int>(npix));
    Launch1D(cl, queue, kFinal, npix);

    std::vector<uint8_t> planes(npix * 3);
    Check(cl, cl.EnqueueReadBuffer(queue, outBuf, CL_TRUE, 0, planes.size(),
                                   planes.data(), 0, nullptr, nullptr),
          "clEnqueueReadBuffer");
    Check(cl, cl.Finish(queue), "clFinish");
    const auto tBlend1 = std::chrono::steady_clock::now();

    // ---- 还原交织 RGB + 裁黑边 ----
    std::vector<uint8_t> canvas(npix * 3);
    for (size_t t = 0; t < npix; ++t) {
        canvas[t * 3 + 0] = planes[t];
        canvas[t * 3 + 1] = planes[npix + t];
        canvas[t * 3 + 2] = planes[2 * npix + t];
    }

    int bx0 = canvasW, bx1 = -1, by0 = canvasH, by1 = -1;
    for (int y = 0; y < canvasH; ++y) {
        const uint8_t* row = canvas.data() + static_cast<size_t>(y) * canvasW * 3;
        for (int x = 0; x < canvasW; ++x) {
            const int gray =
                (29 * row[x * 3 + 2] + 150 * row[x * 3 + 1] + 77 * row[x * 3 + 0] + 128) >> 8;
            if (gray > 6) {
                if (x < bx0) bx0 = x;
                if (x > bx1) bx1 = x;
                if (y < by0) by0 = y;
                if (y > by1) by1 = y;
            }
        }
    }

    int cropW = canvasW;
    int cropH = canvasH;
    std::vector<uint8_t> output;
    if (bx1 >= bx0 && by1 >= by0) {
        cropW = bx1 - bx0 + 1;
        cropH = by1 - by0 + 1;
        output.resize(static_cast<size_t>(cropW) * cropH * 3);
        for (int y = 0; y < cropH; ++y) {
            const uint8_t* srcRow =
                canvas.data() + (static_cast<size_t>(y + by0) * canvasW + bx0) * 3;
            std::memcpy(output.data() + static_cast<size_t>(y) * cropW * 3, srcRow,
                        static_cast<size_t>(cropW) * 3);
        }
    } else {
        output.swap(canvas);
    }

    if (!stbi_write_png(outPath.c_str(), cropW, cropH, 3, output.data(), cropW * 3)) {
        std::fprintf(stderr, "写入 PNG 失败: %s\n", outPath.c_str());
        return 9;
    }

    std::printf("输出: %s  %dx%d（画布 %dx%d）\n", outPath.c_str(), cropW, cropH,
                canvasW, canvasH);
    std::printf("SIFT 提取耗时: %.2f ms\n", ElapsedMs(tExtract0, tExtract1));
    std::printf("配准耗时: %.2f ms\n", ElapsedMs(tReg0, tReg1));
    std::printf("融合耗时: %.2f ms\n", ElapsedMs(tBlend0, tBlend1));

    // ---- 释放 ----
    for (ImageData& img : images) {
        if (img.meta) cl.ReleaseMemObject(img.meta);
        if (img.desc) cl.ReleaseMemObject(img.desc);
    }
    cl.ReleaseMemObject(acc);
    cl.ReleaseMemObject(accW);
    cl.ReleaseMemObject(outBuf);
    cl.ReleaseKernel(kWarp);
    cl.ReleaseKernel(kSeed);
    cl.ReleaseKernel(kJfa);
    cl.ReleaseKernel(kAcc);
    cl.ReleaseKernel(kFinal);
    cl.ReleaseProgram(blendProgram);
    gpu.Release();
    cl.ReleaseProgram(siftProgram);
    cl.ReleaseCommandQueue(queue);
    cl.ReleaseContext(context);
    return 0;
}
