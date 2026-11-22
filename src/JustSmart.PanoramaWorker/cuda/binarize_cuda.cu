// 二值化实现：
//   1) Sauvola —— 用积分图 + 平方积分图求局部均值/标准差；
//   2) Otsu    —— 256 桶直方图（共享内存归约 + 全局原子加）。
#include "binarize_cuda.h"

#include "cuda_common.cuh"

#include <cmath>
#include <cstdio>
#include <vector>

namespace {

// 行方向前缀和：integ[(y+1)*stride + (x+1)] = 本行 0..x 的累计值。
// kSquare=true 时累加像素平方，用于求局部方差。
template <bool kSquare>
__global__ void integral_rows_kernel(const uint8_t* src, uint64_t* integ, int w,
                                     int h, int stride) {
    int y = blockIdx.x * blockDim.x + threadIdx.x;
    if (y >= h) return;
    const uint8_t* row = src + static_cast<size_t>(y) * w;
    uint64_t* dst = integ + static_cast<size_t>(y + 1) * stride;
    uint64_t acc = 0;
    for (int x = 0; x < w; ++x) {
        uint64_t v = row[x];
        if (kSquare) v *= v;
        acc += v;
        dst[x + 1] = acc;
    }
}

// 列方向前缀和：在行前缀和基础上向下累加。
__global__ void integral_cols_kernel(uint64_t* integ, int w, int h, int stride) {
    int c = blockIdx.x * blockDim.x + threadIdx.x + 1;
    if (c > w) return;
    uint64_t acc = 0;
    for (int y = 1; y <= h; ++y) {
        uint64_t* p = integ + static_cast<size_t>(y) * stride + c;
        acc += *p;
        *p = acc;
    }
}

__device__ uint64_t rect_sum(const uint64_t* integ, int stride, int x0, int y0,
                             int x1, int y1) {
    // 闭区间 [x0,x1] x [y0,y1]
    return integ[static_cast<size_t>(y1 + 1) * stride + (x1 + 1)] -
           integ[static_cast<size_t>(y0) * stride + (x1 + 1)] -
           integ[static_cast<size_t>(y1 + 1) * stride + x0] +
           integ[static_cast<size_t>(y0) * stride + x0];
}

__global__ void sauvola_kernel(const uint8_t* gray, const uint64_t* sum,
                               const uint64_t* sqsum, uint8_t* out, int w, int h,
                               int stride, int radius, float k) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;

    int x0 = x - radius;
    int y0 = y - radius;
    int x1 = x + radius;
    int y1 = y + radius;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > w - 1) x1 = w - 1;
    if (y1 > h - 1) y1 = h - 1;

    const int n = (x1 - x0 + 1) * (y1 - y0 + 1);
    const double s = static_cast<double>(rect_sum(sum, stride, x0, y0, x1, y1));
    const double s2 = static_cast<double>(rect_sum(sqsum, stride, x0, y0, x1, y1));
    const double mean = s / n;
    double var = s2 / n - mean * mean;
    if (var < 0.0) var = 0.0;
    const double stddev = sqrt(var);
    const double thr = mean * (1.0 + static_cast<double>(k) * (stddev / 128.0 - 1.0));
    out[static_cast<size_t>(y) * w + x] =
        (gray[static_cast<size_t>(y) * w + x] > thr) ? 255 : 0;
}

__global__ void histogram_kernel(const uint8_t* gray, int n, unsigned int* hist) {
    __shared__ unsigned int sh[256];
    const int t = threadIdx.x;
    if (t < 256) sh[t] = 0u;
    __syncthreads();
    for (int i = blockIdx.x * blockDim.x + t; i < n; i += blockDim.x * gridDim.x) {
        atomicAdd(&sh[gray[i]], 1u);
    }
    __syncthreads();
    if (t < 256) atomicAdd(&hist[t], sh[t]);
}

__global__ void otsu_apply_kernel(const uint8_t* gray, uint8_t* out, int n,
                                  int threshold) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = (gray[i] > threshold) ? 255 : 0;
}

}  // namespace

void sauvola_binarize_cuda(const uint8_t* dGray, uint8_t* dOut, int w, int h,
                           int window, float k, cudaStream_t stream) {
    if (!dGray || !dOut || w <= 0 || h <= 0) return;
    if (window < 3) window = 3;
    if ((window & 1) == 0) ++window;
    const int radius = window / 2;

    const int stride = w + 1;
    const size_t cells = static_cast<size_t>(h + 1) * stride;

    DeviceBuffer<uint64_t> sum(cells);
    DeviceBuffer<uint64_t> sqsum(cells);
    CUDA_CHECK(cudaMemsetAsync(sum.data(), 0, cells * sizeof(uint64_t), stream));
    CUDA_CHECK(cudaMemsetAsync(sqsum.data(), 0, cells * sizeof(uint64_t), stream));

    const int rowBlocks = divUp(h, 128);
    integral_rows_kernel<false><<<rowBlocks, 128, 0, stream>>>(dGray, sum.data(), w, h, stride);
    integral_rows_kernel<true><<<rowBlocks, 128, 0, stream>>>(dGray, sqsum.data(), w, h, stride);
    CUDA_CHECK_LAST();

    const int colBlocks = divUp(w, 128);
    integral_cols_kernel<<<colBlocks, 128, 0, stream>>>(sum.data(), w, h, stride);
    integral_cols_kernel<<<colBlocks, 128, 0, stream>>>(sqsum.data(), w, h, stride);
    CUDA_CHECK_LAST();

    dim3 block(16, 16);
    dim3 grid(divUp(w, 16), divUp(h, 16));
    sauvola_kernel<<<grid, block, 0, stream>>>(dGray, sum.data(), sqsum.data(), dOut,
                                               w, h, stride, radius, k);
    CUDA_CHECK_LAST();
}

void otsu_binarize_cuda(const uint8_t* dGray, uint8_t* dOut, int w, int h,
                        cudaStream_t stream) {
    if (!dGray || !dOut || w <= 0 || h <= 0) return;
    const int n = w * h;

    DeviceBuffer<unsigned int> hist(256);
    CUDA_CHECK(cudaMemsetAsync(hist.data(), 0, 256 * sizeof(unsigned int), stream));

    int blocks = divUp(n, 256);
    if (blocks > 4096) blocks = 4096;
    histogram_kernel<<<blocks, 256, 0, stream>>>(dGray, n, hist.data());
    CUDA_CHECK_LAST();

    unsigned int hHist[256] = {0};
    CUDA_CHECK(cudaMemcpyAsync(hHist, hist.data(), sizeof(hHist),
                               cudaMemcpyDeviceToHost, stream));
    CUDA_CHECK(cudaStreamSynchronize(stream));

    double total = 0.0;
    double sumAll = 0.0;
    for (int i = 0; i < 256; ++i) {
        total += hHist[i];
        sumAll += static_cast<double>(i) * hHist[i];
    }

    int threshold = 0;
    if (total > 0.0) {
        double sumB = 0.0;
        double wB = 0.0;
        double maxVar = -1.0;
        for (int t = 0; t < 256; ++t) {
            wB += hHist[t];
            if (wB <= 0.0) continue;
            const double wF = total - wB;
            if (wF <= 0.0) break;
            sumB += static_cast<double>(t) * hHist[t];
            const double mB = sumB / wB;
            const double mF = (sumAll - sumB) / wF;
            const double var = wB * wF * (mB - mF) * (mB - mF);
            if (var > maxVar) {
                maxVar = var;
                threshold = t;
            }
        }
    }

    otsu_apply_kernel<<<divUp(n, 256), 256, 0, stream>>>(dGray, dOut, n, threshold);
    CUDA_CHECK_LAST();
}
