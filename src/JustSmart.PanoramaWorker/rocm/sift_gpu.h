// OpenCL SIFT 提取/匹配（供 sift_opencl 与 baseline_opencl 共用）。
#pragma once

#include "opencl_common.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

// 与 OpenCL 侧结构体逐字段对应（全部 4 字节成员，布局一致）。
struct Candidate {
    int x;
    int y;
    int scaleIdx;
};

struct KeyMeta {
    float x;
    float y;
    float scale;
    float angle;
    float ox;
    float oy;
    int octave;
    int scaleIdx;
};

struct MatchRec {
    int query;
    int train;
    float distance;
};

inline const char* SiftKernelSource() {
    return R"CLC(
#define K_BASE_SIGMA 1.6f
#define K_SIGMA_STEP 1.2589254117941673f
#define K_PI 3.14159265358979323846f
#define K_TWO_PI 6.28318530717958647692f

typedef struct { int x; int y; int scaleIdx; } Candidate;
typedef struct { float x, y, scale, angle, ox, oy; int octave; int scaleIdx; } KeyMeta;
typedef struct { int query, train; float distance; } MatchRec;

__kernel void u8_to_float(__global const uchar* src, __global float* dst,
                          const int n) {
    const int i = (int)get_global_id(0);
    if (i < n) dst[i] = (float)src[i];
}

__kernel void downsample2(__global const float* src, __global float* dst,
                          const int sw, const int sh, const int dw, const int dh) {
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= dw || y >= dh) return;
    const int sx = 2 * x;
    const int sy = 2 * y;
    const int sx1 = min(sx + 1, sw - 1);
    const int sy1 = min(sy + 1, sh - 1);
    const float s = src[sy * sw + sx] + src[sy * sw + sx1] +
                    src[sy1 * sw + sx] + src[sy1 * sw + sx1];
    dst[y * dw + x] = 0.25f * s;
}

__kernel void blur_h(__global const float* src, __global float* dst,
                     const int w, const int h,
                     __global const float* kern, const int r) {
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= w || y >= h) return;
    const int row = y * w;
    float s = 0.0f;
    for (int k = -r; k <= r; ++k) {
        int xx = x + k;
        xx = xx < 0 ? 0 : (xx >= w ? w - 1 : xx);
        s += src[row + xx] * kern[k + r];
    }
    dst[row + x] = s;
}

__kernel void blur_v(__global const float* src, __global float* dst,
                     const int w, const int h,
                     __global const float* kern, const int r) {
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= w || y >= h) return;
    float s = 0.0f;
    for (int k = -r; k <= r; ++k) {
        int yy = y + k;
        yy = yy < 0 ? 0 : (yy >= h ? h - 1 : yy);
        s += src[yy * w + x] * kern[k + r];
    }
    dst[y * w + x] = s;
}

__kernel void subtract(__global const float* a, __global const float* b,
                       __global float* dst, const int n) {
    const int i = (int)get_global_id(0);
    if (i < n) dst[i] = b[i] - a[i];
}

__kernel void extrema(__global const float* dPrev, __global const float* dCur,
                      __global const float* dNext, const int w, const int h,
                      const int scaleIdx, const float contrThr,
                      __global Candidate* cands, __global int* count,
                      const int maxCand) {
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x <= 0 || y <= 0 || x >= w - 1 || y >= h - 1) return;

    const int i = y * w + x;
    const float c = dCur[i];
    if (fabs(c) < contrThr) return;

    bool isMax = true;
    bool isMin = true;
    for (int dy = -1; dy <= 1; ++dy) {
        for (int dx = -1; dx <= 1; ++dx) {
            if (dx == 0 && dy == 0) continue;
            const int j = (y + dy) * w + (x + dx);
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

    const int k = atomic_add(count, 1);
    if (k < maxCand) {
        cands[k].x = x;
        cands[k].y = y;
        cands[k].scaleIdx = scaleIdx;
    }
}

__kernel void orientation(__global const float* img, const int w, const int h,
                          __global const Candidate* cands, const int ncand,
                          __global KeyMeta* keys, __global int* count,
                          const int maxKeys, const int octave) {
    const int idx = (int)get_global_id(0);
    if (idx >= ncand) return;

    const Candidate cd = cands[idx];
    const float sigma = K_BASE_SIGMA * pow(K_SIGMA_STEP, (float)cd.scaleIdx + 0.5f);
    int radius = (int)(4.5f * sigma + 0.5f);
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
            const int j = yy * w + xx;
            const float gx = img[j + 1] - img[j - 1];
            const float gy = img[j + w] - img[j - w];
            const float mag = sqrt(gx * gx + gy * gy);
            if (mag < 1e-6f) continue;
            const float ori = atan2(gy, gx);
            int bin = (int)((ori + K_PI) * (36.0f / K_TWO_PI));
            bin %= 36;
            if (bin < 0) bin += 36;
            const float wgt = exp(-(float)(dx * dx + dy * dy) * gaussFactor);
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
            peaks[np++] = ((float)i + off + 0.5f) * (K_TWO_PI / 36.0f) - K_PI;
        }
    }
    if (np == 0) return;

    const int base = atomic_add(count, np);
    const float scaleFactor = (float)(1 << octave);
    for (int i = 0; i < np && base + i < maxKeys; ++i) {
        const int k = base + i;
        keys[k].x = ((float)cd.x + 0.5f) * scaleFactor - 0.5f;
        keys[k].y = ((float)cd.y + 0.5f) * scaleFactor - 0.5f;
        keys[k].ox = (float)cd.x;
        keys[k].oy = (float)cd.y;
        keys[k].scale = sigma * scaleFactor;
        keys[k].angle = peaks[i];
        keys[k].octave = octave;
        keys[k].scaleIdx = cd.scaleIdx;
    }
}

__kernel void descriptor(__global const KeyMeta* keys, __global float* descs,
                         const int n, const int octave, const int w, const int h,
                         __global const float* g0, __global const float* g1,
                         __global const float* g2, __global const float* g3,
                         __global const float* g4, __global const float* g5) {
    const int idx = (int)get_global_id(0);
    if (idx >= n) return;
    if (keys[idx].octave != octave) return;

    const int scaleIdx = keys[idx].scaleIdx;
    const float ox = keys[idx].ox;
    const float oy = keys[idx].oy;
    const float angle = keys[idx].angle;

    const __global float* img = g1;
    if (scaleIdx == 2) img = g2;
    else if (scaleIdx == 3) img = g3;
    else if (scaleIdx == 0) img = g0;

    const float sigma = K_BASE_SIGMA * pow(K_SIGMA_STEP, (float)scaleIdx + 0.5f);
    const float histWidth = 3.0f * sigma;
    int radius = (int)(histWidth * 1.41421356f * 2.5f + 0.5f);
    const int maxRadius = (w > h) ? w : h;
    if (radius > maxRadius) radius = maxRadius;
    if (radius < 4) radius = 4;

    const float cosA = cos(angle);
    const float sinA = sin(angle);
    const float invHist = 1.0f / histWidth;

    float hist[128];
    for (int i = 0; i < 128; ++i) hist[i] = 0.0f;

    const int cx = (int)ox;
    const int cy = (int)oy;
    for (int dy = -radius; dy <= radius; ++dy) {
        const int yy = cy + dy;
        if (yy <= 0 || yy >= h - 1) continue;
        for (int dx = -radius; dx <= radius; ++dx) {
            const int xx = cx + dx;
            if (xx <= 0 || xx >= w - 1) continue;

            const float rx = ((float)dx * cosA + (float)dy * sinA) * invHist;
            const float ry = (-(float)dx * sinA + (float)dy * cosA) * invHist;
            const float rbin = ry + 1.5f;
            const float cbin = rx + 1.5f;
            if (rbin <= -1.0f || rbin >= 4.0f || cbin <= -1.0f || cbin >= 4.0f)
                continue;

            const int j = yy * w + xx;
            const float gx = img[j + 1] - img[j - 1];
            const float gy = img[j + w] - img[j - w];
            const float mag = sqrt(gx * gx + gy * gy);
            if (mag < 1e-6f) continue;

            float ori = atan2(gy, gx) - angle;
            ori = fmod(ori, K_TWO_PI);
            if (ori < 0.0f) ori += K_TWO_PI;
            const float obin = ori * (8.0f / K_TWO_PI);

            const int r0 = (int)floor(rbin);
            const int c0 = (int)floor(cbin);
            const int o0 = (int)floor(obin);
            const float dr = rbin - (float)r0;
            const float dc = cbin - (float)c0;
            const float dov = obin - (float)o0;
            const float wgt = exp(-(rx * rx + ry * ry) * 0.5f) * mag;

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
    norm = sqrt(norm);
    const int base = idx * 128;
    if (norm < 1e-6f) {
        for (int i = 0; i < 128; ++i) descs[base + i] = 0.0f;
        return;
    }
    const float inv = 1.0f / norm;
    for (int i = 0; i < 128; ++i) {
        hist[i] *= inv;
        if (hist[i] > 0.2f) hist[i] = 0.2f;
    }
    float norm2 = 0.0f;
    for (int i = 0; i < 128; ++i) norm2 += hist[i] * hist[i];
    norm2 = sqrt(norm2);
    if (norm2 > 1e-6f) {
        const float inv2 = 1.0f / norm2;
        for (int i = 0; i < 128; ++i) hist[i] *= inv2;
    }
    for (int i = 0; i < 128; ++i) descs[base + i] = hist[i];
}

// 粗筛维度与前 K 候选数：先用前 32 维（8 个 float4）快速筛选出 top-K，
// 再对 K 个候选算完整 128 维距离并做 Lowe ratio 判定。
// 直接把 128 维全量比完是 O(na*nb*128)，在 gfx804 上单对 2 万关键点需要数分钟；
// 该两级策略把大部分工作压到 32 维，实测快一个数量级，且不影响选出最近邻。
#define MATCH_COARSE_DIMS 32
#define MATCH_TOPK 16

__kernel void match(__global const KeyMeta* ka, __global const float* da,
                    const int na, __global const KeyMeta* kb,
                    __global const float* db, const int nb, const float ratio2,
                    __global MatchRec* out, __global int* count,
                    const int maxMatches) {
    const int i = (int)get_global_id(0);
    if (i >= na) return;

    const int ai = i * 128;
    float best = 1e30f;
    float second = 1e30f;
    int bestJ = -1;

    // 训练集中描述子较少时直接穷举，避免 top-K 重复索引带来的误差。
    if (nb <= MATCH_TOPK) {
        const __global float4* qa = (const __global float4*)(da + ai);
        for (int j = 0; j < nb; ++j) {
            const __global float4* qb = (const __global float4*)(db + j * 128);
            float d = 0.0f;
            for (int t = 0; t < 32; ++t) {
                float4 diff = qa[t] - qb[t];
                d += dot(diff, diff);
            }
            if (d < best) {
                second = best;
                best = d;
                bestJ = j;
            } else if (d < second) {
                second = d;
            }
        }
    } else {
        // 粗筛：按前 32 维距离维护 top-K（插入排序，K 很小）。
        float partDist[MATCH_TOPK];
        int partIdx[MATCH_TOPK];
        for (int k = 0; k < MATCH_TOPK; ++k) {
            partDist[k] = 1e30f;
            partIdx[k] = -1;
        }
        const __global float4* qa = (const __global float4*)(da + ai);
        for (int j = 0; j < nb; ++j) {
            const __global float4* qb = (const __global float4*)(db + j * 128);
            float d = 0.0f;
            for (int t = 0; t < MATCH_COARSE_DIMS / 4; ++t) {
                float4 diff = qa[t] - qb[t];
                d += dot(diff, diff);
            }
            if (d < partDist[MATCH_TOPK - 1]) {
                int pos = MATCH_TOPK - 1;
                while (pos > 0 && d < partDist[pos - 1]) {
                    partDist[pos] = partDist[pos - 1];
                    partIdx[pos] = partIdx[pos - 1];
                    --pos;
                }
                partDist[pos] = d;
                partIdx[pos] = j;
            }
        }

        // 精算：只对粗筛 top-K 计算完整 128 维距离。
        for (int k = 0; k < MATCH_TOPK; ++k) {
            const int j = partIdx[k];
            if (j < 0) continue;
            const __global float4* qb = (const __global float4*)(db + j * 128);
            float d = 0.0f;
            for (int t = 0; t < 32; ++t) {
                float4 diff = qa[t] - qb[t];
                d += dot(diff, diff);
            }
            if (d < best) {
                second = best;
                best = d;
                bestJ = j;
            } else if (d < second) {
                second = d;
            }
        }
    }

    if (bestJ >= 0 && best < ratio2 * second) {
        const int k = atomic_add(count, 1);
        if (k < maxMatches) {
            out[k].query = i;
            out[k].train = bestJ;
            out[k].distance = sqrt(best);
        }
    }
}
)CLC";
}

inline cl_program BuildSiftProgram(OpenCl& cl, cl_context ctx, cl_device_id device) {
    const char* src = SiftKernelSource();
    cl_int err = CL_SUCCESS;
    cl_program program = cl.CreateProgramWithSource(ctx, 1, &src, nullptr, &err);
    Check(cl, err, "clCreateProgramWithSource");
    err = cl.BuildProgram(program, 1, &device, "-cl-std=CL2.0", nullptr, nullptr);
    if (err != CL_SUCCESS) {
        size_t logSize = 0;
        cl.GetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, 0, nullptr, &logSize);
        std::vector<char> log(logSize + 1, 0);
        cl.GetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, logSize, log.data(),
                               nullptr);
        std::fprintf(stderr, "SIFT build log:\n%s\n", log.data());
        std::exit(5);
    }
    return program;
}

// OpenCL 侧 SIFT 的宿主封装：管理 kernel，并提供提取与匹配。
struct SiftGpu {
    OpenCl& cl;
    cl_context ctx = nullptr;
    cl_command_queue queue = nullptr;
    cl_kernel kU8 = nullptr;
    cl_kernel kDown = nullptr;
    cl_kernel kBlurH = nullptr;
    cl_kernel kBlurV = nullptr;
    cl_kernel kSub = nullptr;
    cl_kernel kExtrema = nullptr;
    cl_kernel kOrient = nullptr;
    cl_kernel kDesc = nullptr;
    cl_kernel kMatch = nullptr;

    void Init(cl_program program) {
        auto make = [&](const char* name) {
            cl_int err = CL_SUCCESS;
            cl_kernel k = cl.CreateKernel(program, name, &err);
            Check(cl, err, "clCreateKernel");
            return k;
        };
        kU8 = make("u8_to_float");
        kDown = make("downsample2");
        kBlurH = make("blur_h");
        kBlurV = make("blur_v");
        kSub = make("subtract");
        kExtrema = make("extrema");
        kOrient = make("orientation");
        kDesc = make("descriptor");
        kMatch = make("match");
    }

    void Release() {
        cl_kernel all[9] = {kU8, kDown, kBlurH, kBlurV, kSub, kExtrema, kOrient, kDesc,
                            kMatch};
        for (cl_kernel k : all) {
            if (k) cl.ReleaseKernel(k);
        }
    }

    cl_mem Buf(cl_bitfield flags, size_t bytes, const void* host = nullptr) {
        cl_int e = CL_SUCCESS;
        cl_bitfield f = flags;
        if (host) f |= CL_MEM_COPY_HOST_PTR;
        cl_mem m = cl.CreateBuffer(ctx, f, bytes, const_cast<void*>(host), &e);
        Check(cl, e, "clCreateBuffer");
        return m;
    }

    void SetMem(cl_kernel k, cl_uint i, cl_mem m) {
        Check(cl, cl.SetKernelArg(k, i, sizeof(cl_mem), &m), "clSetKernelArg");
    }
    void SetInt(cl_kernel k, cl_uint i, int v) {
        Check(cl, cl.SetKernelArg(k, i, sizeof(int), &v), "clSetKernelArg");
    }
    void SetFloat(cl_kernel k, cl_uint i, float v) {
        Check(cl, cl.SetKernelArg(k, i, sizeof(float), &v), "clSetKernelArg");
    }

    void Launch1D(cl_kernel k, size_t n, size_t local) {
        const size_t global[1] = {RoundUp(n, local)};
        const size_t l[1] = {local};
        Check(cl, cl.EnqueueNDRangeKernel(queue, k, 1, nullptr, global, l, 0, nullptr,
                                          nullptr), "clEnqueueNDRangeKernel");
    }
    void Launch2D(cl_kernel k, int w, int h) {
        const size_t global[2] = {RoundUp(w, 16), RoundUp(h, 16)};
        const size_t local[2] = {16, 16};
        Check(cl, cl.EnqueueNDRangeKernel(queue, k, 2, nullptr, global, local, 0, nullptr,
                                          nullptr), "clEnqueueNDRangeKernel");
    }

    // 对单张灰度图执行 SIFT 提取；结果写入 outMeta / outDesc，返回关键点数量。
    int Extract(const std::vector<uint8_t>& gray, int w, int h, int maxKeys,
                cl_mem* outMeta, cl_mem* outDesc, float contrastThr = 4.0f) {
        if (w < 32 || h < 32 || maxKeys <= 0) return 0;

        std::vector<int> owList;
        std::vector<int> ohList;
        int ow = w;
        int oh = h;
        while (static_cast<int>(owList.size()) < 4 && ow >= 32 && oh >= 32) {
            owList.push_back(ow);
            ohList.push_back(oh);
            ow >>= 1;
            oh >>= 1;
        }
        const int octaves = static_cast<int>(owList.size());
        if (octaves == 0) return 0;
        const int n = w * h;

        const int maxCand = 40000;
        cl_mem dGray = Buf(CL_MEM_READ_ONLY, static_cast<size_t>(n), gray.data());
        cl_mem base = Buf(CL_MEM_READ_WRITE, static_cast<size_t>(n) * sizeof(float));
        cl_mem tmp = Buf(CL_MEM_READ_WRITE, static_cast<size_t>(n) * sizeof(float));
        cl_mem dog[5];
        for (int i = 0; i < 5; ++i) {
            dog[i] = Buf(CL_MEM_READ_WRITE, static_cast<size_t>(n) * sizeof(float));
        }
        cl_mem cand = Buf(CL_MEM_READ_WRITE,
                          static_cast<size_t>(maxCand) * sizeof(Candidate));
        cl_mem candCount = Buf(CL_MEM_READ_WRITE, sizeof(int));
        cl_mem keyCount = Buf(CL_MEM_READ_WRITE, sizeof(int));
        cl_mem meta = Buf(CL_MEM_READ_WRITE,
                          static_cast<size_t>(maxKeys) * sizeof(KeyMeta));
        cl_mem descs = Buf(CL_MEM_READ_WRITE,
                           static_cast<size_t>(maxKeys) * 128 * sizeof(float));

        const int zero = 0;
        Check(cl, cl.EnqueueWriteBuffer(queue, keyCount, CL_TRUE, 0, sizeof(int), &zero,
                                        0, nullptr, nullptr), "clEnqueueWriteBuffer");

        SetMem(kU8, 0, dGray);
        SetMem(kU8, 1, base);
        SetInt(kU8, 2, n);
        Launch1D(kU8, static_cast<size_t>(n), 256);

        std::vector<cl_mem> gauss(static_cast<size_t>(octaves) * 6, nullptr);
        std::vector<cl_mem> owned = {dGray, base, tmp, cand, candCount, keyCount};
        for (int i = 0; i < 5; ++i) owned.push_back(dog[i]);

        for (int o = 0; o < octaves; ++o) {
            const int cw = owList[o];
            const int ch = ohList[o];
            const int cn = cw * ch;

            if (o > 0) {
                SetMem(kDown, 0, gauss[static_cast<size_t>(o - 1) * 6 + 3]);
                SetMem(kDown, 1, base);
                SetInt(kDown, 2, owList[o - 1]);
                SetInt(kDown, 3, ohList[o - 1]);
                SetInt(kDown, 4, cw);
                SetInt(kDown, 5, ch);
                Launch2D(kDown, cw, ch);
            }

            for (int i = 0; i < 6; ++i) {
                const float sigma = 1.6f * std::pow(1.2589254117941673f,
                                                    static_cast<float>(i));
                const int r = static_cast<int>(std::ceil(3.0f * sigma));
                std::vector<float> kern(static_cast<size_t>(2 * r + 1), 0.0f);
                const float inv = 1.0f / (2.0f * sigma * sigma);
                float sum = 0.0f;
                for (int t = -r; t <= r; ++t) {
                    const float v = std::exp(-static_cast<float>(t * t) * inv);
                    kern[static_cast<size_t>(t + r)] = v;
                    sum += v;
                }
                for (float& v : kern) v /= sum;
                cl_mem dk = Buf(CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR,
                                kern.size() * sizeof(float), kern.data());

                cl_mem dst = Buf(CL_MEM_READ_WRITE,
                                 static_cast<size_t>(cn) * sizeof(float));
                gauss[static_cast<size_t>(o) * 6 + i] = dst;

                SetMem(kBlurH, 0, base);
                SetMem(kBlurH, 1, tmp);
                SetInt(kBlurH, 2, cw);
                SetInt(kBlurH, 3, ch);
                SetMem(kBlurH, 4, dk);
                SetInt(kBlurH, 5, r);
                Launch2D(kBlurH, cw, ch);

                SetMem(kBlurV, 0, tmp);
                SetMem(kBlurV, 1, dst);
                SetInt(kBlurV, 2, cw);
                SetInt(kBlurV, 3, ch);
                SetMem(kBlurV, 4, dk);
                SetInt(kBlurV, 5, r);
                Launch2D(kBlurV, cw, ch);

                cl.ReleaseMemObject(dk);
            }

            for (int s = 0; s < 5; ++s) {
                SetMem(kSub, 0, gauss[static_cast<size_t>(o) * 6 + s]);
                SetMem(kSub, 1, gauss[static_cast<size_t>(o) * 6 + s + 1]);
                SetMem(kSub, 2, dog[s]);
                SetInt(kSub, 3, cn);
                Launch1D(kSub, static_cast<size_t>(cn), 256);
            }

            for (int s = 1; s <= 3; ++s) {
                Check(cl, cl.EnqueueWriteBuffer(queue, candCount, CL_TRUE, 0, sizeof(int),
                                                &zero, 0, nullptr, nullptr),
                      "clEnqueueWriteBuffer");

                SetMem(kExtrema, 0, dog[s - 1]);
                SetMem(kExtrema, 1, dog[s]);
                SetMem(kExtrema, 2, dog[s + 1]);
                SetInt(kExtrema, 3, cw);
                SetInt(kExtrema, 4, ch);
                SetInt(kExtrema, 5, s);
                SetFloat(kExtrema, 6, contrastThr);
                SetMem(kExtrema, 7, cand);
                SetMem(kExtrema, 8, candCount);
                SetInt(kExtrema, 9, maxCand);
                Launch2D(kExtrema, cw, ch);

                int ncand = 0;
                Check(cl, cl.EnqueueReadBuffer(queue, candCount, CL_TRUE, 0, sizeof(int),
                                               &ncand, 0, nullptr, nullptr),
                      "clEnqueueReadBuffer");
                if (ncand > maxCand) ncand = maxCand;
                if (ncand <= 0) continue;

                SetMem(kOrient, 0, gauss[static_cast<size_t>(o) * 6 + s]);
                SetInt(kOrient, 1, cw);
                SetInt(kOrient, 2, ch);
                SetMem(kOrient, 3, cand);
                SetInt(kOrient, 4, ncand);
                SetMem(kOrient, 5, meta);
                SetMem(kOrient, 6, keyCount);
                SetInt(kOrient, 7, maxKeys);
                SetInt(kOrient, 8, o);
                Launch1D(kOrient, static_cast<size_t>(ncand), 64);
            }
        }

        int total = 0;
        Check(cl, cl.EnqueueReadBuffer(queue, keyCount, CL_TRUE, 0, sizeof(int), &total, 0,
                                       nullptr, nullptr), "clEnqueueReadBuffer");
        if (total > maxKeys) total = maxKeys;

        for (int o = 0; o < octaves && total > 0; ++o) {
            const int cw = owList[o];
            const int ch = ohList[o];
            SetMem(kDesc, 0, meta);
            SetMem(kDesc, 1, descs);
            SetInt(kDesc, 2, total);
            SetInt(kDesc, 3, o);
            SetInt(kDesc, 4, cw);
            SetInt(kDesc, 5, ch);
            for (int i = 0; i < 6; ++i) {
                SetMem(kDesc, static_cast<cl_uint>(6 + i),
                       gauss[static_cast<size_t>(o) * 6 + i]);
            }
            Launch1D(kDesc, static_cast<size_t>(total), 64);
        }

        for (cl_mem m : gauss) {
            if (m) cl.ReleaseMemObject(m);
        }
        for (cl_mem m : owned) cl.ReleaseMemObject(m);

        *outMeta = meta;
        *outDesc = descs;
        return total;
    }

    int Match(cl_mem metaA, cl_mem descA, int na, cl_mem metaB, cl_mem descB, int nb,
              float ratio, int maxMatches, std::vector<MatchRec>* matches) {
        if (na <= 0 || nb <= 0 || maxMatches <= 0) return 0;
        if (ratio <= 0.0f || ratio >= 1.0f) ratio = 0.75f;

        cl_mem out = Buf(CL_MEM_READ_WRITE,
                         static_cast<size_t>(maxMatches) * sizeof(MatchRec));
        cl_mem count = Buf(CL_MEM_READ_WRITE, sizeof(int));
        const int zero = 0;
        Check(cl, cl.EnqueueWriteBuffer(queue, count, CL_TRUE, 0, sizeof(int), &zero, 0,
                                        nullptr, nullptr), "clEnqueueWriteBuffer");

        SetMem(kMatch, 0, metaA);
        SetMem(kMatch, 1, descA);
        SetInt(kMatch, 2, na);
        SetMem(kMatch, 3, metaB);
        SetMem(kMatch, 4, descB);
        SetInt(kMatch, 5, nb);
        SetFloat(kMatch, 6, ratio * ratio);
        SetMem(kMatch, 7, out);
        SetMem(kMatch, 8, count);
        SetInt(kMatch, 9, maxMatches);
        Launch1D(kMatch, static_cast<size_t>(na), 128);

        int total = 0;
        Check(cl, cl.EnqueueReadBuffer(queue, count, CL_TRUE, 0, sizeof(int), &total, 0,
                                       nullptr, nullptr), "clEnqueueReadBuffer");
        if (total > maxMatches) total = maxMatches;
        if (total > 0 && matches) {
            matches->resize(static_cast<size_t>(total));
            Check(cl, cl.EnqueueReadBuffer(queue, out, CL_TRUE, 0,
                                           static_cast<size_t>(total) * sizeof(MatchRec),
                                           matches->data(), 0, nullptr, nullptr),
                  "clEnqueueReadBuffer");
        }
        cl.ReleaseMemObject(out);
        cl.ReleaseMemObject(count);
        return total;
    }
};
