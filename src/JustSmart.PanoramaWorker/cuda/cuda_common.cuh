// CUDA 公共工具：错误检查、设备内存 RAII、基础像素类型。
#pragma once

#include <cuda_runtime.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <utility>

#define CUDA_CHECK(call)                                                           \
    do {                                                                           \
        cudaError_t err__ = (call);                                                \
        if (err__ != cudaSuccess) {                                                \
            std::fprintf(stderr, "CUDA error %s:%d: %s (%d)\n", __FILE__, __LINE__, \
                         cudaGetErrorString(err__), static_cast<int>(err__));      \
            std::exit(EXIT_FAILURE);                                               \
        }                                                                          \
    } while (0)

#define CUDA_CHECK_LAST() CUDA_CHECK(cudaGetLastError())

inline int divUp(int a, int b) { return (a + b - 1) / b; }

// 与 stb_image 的 3 通道 RGB 内存布局一致。
struct Pixel {
    uint8_t r;
    uint8_t g;
    uint8_t b;
};

template <typename T>
class DeviceBuffer {
public:
    DeviceBuffer() = default;
    explicit DeviceBuffer(size_t count) { allocate(count); }
    ~DeviceBuffer() { release(); }

    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;

    DeviceBuffer(DeviceBuffer&& other) noexcept : ptr_(other.ptr_), count_(other.count_) {
        other.ptr_ = nullptr;
        other.count_ = 0;
    }
    DeviceBuffer& operator=(DeviceBuffer&& other) noexcept {
        if (this != &other) {
            release();
            ptr_ = other.ptr_;
            count_ = other.count_;
            other.ptr_ = nullptr;
            other.count_ = 0;
        }
        return *this;
    }

    void allocate(size_t count) {
        release();
        if (count == 0) return;
        CUDA_CHECK(cudaMalloc(reinterpret_cast<void**>(&ptr_), count * sizeof(T)));
        count_ = count;
    }

    void release() {
        if (ptr_) {
            cudaFree(ptr_);
            ptr_ = nullptr;
            count_ = 0;
        }
    }

    void zero() {
        if (ptr_) CUDA_CHECK(cudaMemset(ptr_, 0, count_ * sizeof(T)));
    }

    T* data() { return ptr_; }
    const T* data() const { return ptr_; }
    size_t size() const { return count_; }

private:
    T* ptr_ = nullptr;
    size_t count_ = 0;
};
