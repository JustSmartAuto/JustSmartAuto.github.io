// panorama_cuda：SIFT / Deghost / Baseline / Binarize 四个子命令的命令行入口。
//
//   panorama_cuda sift     <img1> <img2> [max_keypoints]
//   panorama_cuda baseline <prev> <cur> <out.png> [sharpness] [ramp]
//   panorama_cuda deghost  <prev> <cur> <out.png> [feather]
//   panorama_cuda binarize <in> <out.png> [sauvola|otsu] [k] [window]
#include "binarize_cuda.h"
#include "cuda_common.cuh"
#include "fusion_cuda.h"
#include "image_io.h"
#include "sift_cuda.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

void print_usage(const char* exe) {
    std::printf(
        "用法:\n"
        "  %s sift     <img1> <img2> [max_keypoints]\n"
        "  %s baseline <prev> <cur> <out.png> [sharpness] [ramp]\n"
        "  %s deghost  <prev> <cur> <out.png> [feather]\n"
        "  %s binarize <in> <out.png> [sauvola|otsu] [k] [window]\n",
        exe, exe, exe, exe);
}

void print_device() {
    cudaDeviceProp prop{};
    if (cudaGetDeviceProperties(&prop, 0) == cudaSuccess) {
        std::printf("GPU: %s (sm_%d%d, %d MB)\n", prop.name, prop.major, prop.minor,
                    static_cast<int>(prop.totalGlobalMem >> 20));
    }
}

std::vector<uint8_t> to_gray(const std::vector<uint8_t>& rgb) {
    const size_t n = rgb.size() / 3;
    std::vector<uint8_t> g(n);
    for (size_t i = 0; i < n; ++i) {
        const float v = 0.299f * rgb[i * 3] + 0.587f * rgb[i * 3 + 1] +
                        0.114f * rgb[i * 3 + 2];
        g[i] = static_cast<uint8_t>(v < 0.0f ? 0.0f : (v > 255.0f ? 255.0f : v));
    }
    return g;
}

std::vector<uint8_t> make_mask(const std::vector<uint8_t>& rgb) {
    const size_t n = rgb.size() / 3;
    std::vector<uint8_t> m(n);
    for (size_t i = 0; i < n; ++i) {
        const int s = rgb[i * 3] + rgb[i * 3 + 1] + rgb[i * 3 + 2];
        m[i] = (s > 24) ? 255 : 0;
    }
    return m;
}

double elapsed_ms(const std::chrono::steady_clock::time_point& a,
                  const std::chrono::steady_clock::time_point& b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}

int run_sift(const char* pathA, const char* pathB, int maxKeypoints) {
    int wa = 0, ha = 0, wb = 0, hb = 0;
    std::vector<uint8_t> rgbA;
    std::vector<uint8_t> rgbB;
    if (!load_rgb(pathA, &wa, &ha, &rgbA)) return 1;
    if (!load_rgb(pathB, &wb, &hb, &rgbB)) return 1;

    const std::vector<uint8_t> grayA = to_gray(rgbA);
    const std::vector<uint8_t> grayB = to_gray(rgbB);

    DeviceBuffer<uint8_t> dGrayA(grayA.size());
    DeviceBuffer<uint8_t> dGrayB(grayB.size());
    CUDA_CHECK(cudaMemcpy(dGrayA.data(), grayA.data(), grayA.size(),
                          cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dGrayB.data(), grayB.data(), grayB.size(),
                          cudaMemcpyHostToDevice));

    DeviceBuffer<SiftKeypoint> keyA(static_cast<size_t>(maxKeypoints));
    DeviceBuffer<SiftKeypoint> keyB(static_cast<size_t>(maxKeypoints));

    const auto t0 = std::chrono::steady_clock::now();
    const int na = sift_extract_cuda(dGrayA.data(), wa, ha, keyA.data(), maxKeypoints);
    const int nb = sift_extract_cuda(dGrayB.data(), wb, hb, keyB.data(), maxKeypoints);

    int maxMatches = maxKeypoints < 8192 ? maxKeypoints : 8192;
    DeviceBuffer<SiftMatch> dMatches(static_cast<size_t>(maxMatches));
    const int nm = sift_match_cuda(keyA.data(), na, keyB.data(), nb, 0.75f,
                                   dMatches.data(), maxMatches);
    CUDA_CHECK(cudaDeviceSynchronize());
    const auto t1 = std::chrono::steady_clock::now();

    std::printf("图像1: %dx%d, 关键点 %d\n", wa, ha, na);
    std::printf("图像2: %dx%d, 关键点 %d\n", wb, hb, nb);
    std::printf("匹配数: %d (Lowe ratio = 0.75)\n", nm);
    std::printf("SIFT 总耗时: %.2f ms\n", elapsed_ms(t0, t1));

    if (nm > 0) {
        std::vector<SiftMatch> host(static_cast<size_t>(nm));
        CUDA_CHECK(cudaMemcpy(host.data(), dMatches.data(),
                              static_cast<size_t>(nm) * sizeof(SiftMatch),
                              cudaMemcpyDeviceToHost));
        const int show = nm < 5 ? nm : 5;
        for (int i = 0; i < show; ++i) {
            std::printf("  #%d  query=%d  train=%d  dist=%.4f\n", i, host[i].query,
                        host[i].train, host[i].distance);
        }
    }
    return 0;
}

int run_fusion(const char* prevPath, const char* curPath, const char* outPath,
               bool deghost, int feather, int sharpness, int ramp) {
    int wp = 0, hp = 0, wc = 0, hc = 0;
    std::vector<uint8_t> rgbPrev;
    std::vector<uint8_t> rgbCur;
    if (!load_rgb(prevPath, &wp, &hp, &rgbPrev)) return 1;
    if (!load_rgb(curPath, &wc, &hc, &rgbCur)) return 1;
    if (wp != wc || hp != hc) {
        std::fprintf(stderr, "两幅图尺寸不一致: %dx%d vs %dx%d\n", wp, hp, wc, hc);
        return 1;
    }
    if (wp <= 0 || hp <= 0) return 1;

    const size_t n = static_cast<size_t>(wp) * hp;
    const std::vector<uint8_t> maskPrev = make_mask(rgbPrev);
    const std::vector<uint8_t> maskCur = make_mask(rgbCur);

    DeviceBuffer<Pixel> dPrev(n);
    DeviceBuffer<Pixel> dCur(n);
    DeviceBuffer<uint8_t> dMaskPrev(n);
    DeviceBuffer<uint8_t> dMaskCur(n);
    DeviceBuffer<Pixel> dOut(n);

    CUDA_CHECK(cudaMemcpy(dPrev.data(), rgbPrev.data(), n * 3, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dCur.data(), rgbCur.data(), n * 3, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dMaskPrev.data(), maskPrev.data(), n, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dMaskCur.data(), maskCur.data(), n, cudaMemcpyHostToDevice));

    const auto t0 = std::chrono::steady_clock::now();
    if (deghost) {
        FusionStats stats{1.0f, 1.0f, 1.0f, 0, 0};
        deghost_fuse_cuda(dPrev.data(), dCur.data(), dMaskPrev.data(), dMaskCur.data(),
                          dOut.data(), wp, hp, feather, &stats);
        CUDA_CHECK(cudaDeviceSynchronize());
        const auto t1 = std::chrono::steady_clock::now();
        std::printf("Deghost 融合 %dx%d, feather=%d\n", wp, hp, feather);
        std::printf("  增益 R/G/B: %.4f / %.4f / %.4f\n", stats.gainR, stats.gainG,
                    stats.gainB);
        std::printf("  缝合线列范围: [%d, %d]\n", stats.seamMin, stats.seamMax);
        std::printf("  耗时: %.2f ms\n", elapsed_ms(t0, t1));
    } else {
        baseline_fuse_cuda(dPrev.data(), dCur.data(), dMaskPrev.data(), dMaskCur.data(),
                           dOut.data(), wp, hp, ramp, sharpness);
        CUDA_CHECK(cudaDeviceSynchronize());
        const auto t1 = std::chrono::steady_clock::now();
        std::printf("Baseline 融合 %dx%d, sharpness=%d, ramp=%d\n", wp, hp, sharpness,
                    ramp);
        std::printf("  耗时: %.2f ms\n", elapsed_ms(t0, t1));
    }

    std::vector<uint8_t> hostOut(n * 3);
    CUDA_CHECK(cudaMemcpy(hostOut.data(), dOut.data(), n * 3, cudaMemcpyDeviceToHost));
    if (!save_rgb(outPath, wp, hp, hostOut.data())) return 1;
    std::printf("输出: %s\n", outPath);
    return 0;
}

int run_binarize(const char* inPath, const char* outPath, const char* mode, float k,
                 int window) {
    int w = 0, h = 0;
    std::vector<uint8_t> gray;
    if (!load_gray(inPath, &w, &h, &gray)) return 1;

    const size_t n = static_cast<size_t>(w) * h;
    DeviceBuffer<uint8_t> dGray(n);
    DeviceBuffer<uint8_t> dOut(n);
    CUDA_CHECK(cudaMemcpy(dGray.data(), gray.data(), n, cudaMemcpyHostToDevice));

    const auto t0 = std::chrono::steady_clock::now();
    const bool useOtsu = (mode != nullptr) && (std::strcmp(mode, "otsu") == 0);
    if (useOtsu) {
        otsu_binarize_cuda(dGray.data(), dOut.data(), w, h);
    } else {
        sauvola_binarize_cuda(dGray.data(), dOut.data(), w, h, window, k);
    }
    CUDA_CHECK(cudaDeviceSynchronize());
    const auto t1 = std::chrono::steady_clock::now();

    if (useOtsu) {
        std::printf("Otsu 二值化 %dx%d\n", w, h);
    } else {
        std::printf("Sauvola 二值化 %dx%d, k=%.3f, window=%d\n", w, h, k, window);
    }
    std::printf("  耗时: %.2f ms\n", elapsed_ms(t0, t1));

    std::vector<uint8_t> hostOut(n);
    CUDA_CHECK(cudaMemcpy(hostOut.data(), dOut.data(), n, cudaMemcpyDeviceToHost));
    if (!save_gray(outPath, w, h, hostOut.data())) return 1;
    std::printf("输出: %s\n", outPath);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }
    print_device();

    const std::string cmd = argv[1];
    if (cmd == "sift") {
        if (argc < 4) {
            print_usage(argv[0]);
            return 1;
        }
        const int maxKeypoints = (argc >= 5) ? std::atoi(argv[4]) : 20000;
        return run_sift(argv[2], argv[3], maxKeypoints > 0 ? maxKeypoints : 20000);
    }
    if (cmd == "baseline") {
        if (argc < 5) {
            print_usage(argv[0]);
            return 1;
        }
        const int sharpness = (argc >= 6) ? std::atoi(argv[5]) : 3;
        const int ramp = (argc >= 7) ? std::atoi(argv[6]) : 64;
        return run_fusion(argv[2], argv[3], argv[4], false, 32, sharpness, ramp);
    }
    if (cmd == "deghost") {
        if (argc < 5) {
            print_usage(argv[0]);
            return 1;
        }
        const int feather = (argc >= 6) ? std::atoi(argv[5]) : 32;
        return run_fusion(argv[2], argv[3], argv[4], true, feather, 3, 64);
    }
    if (cmd == "binarize") {
        if (argc < 4) {
            print_usage(argv[0]);
            return 1;
        }
        const char* mode = (argc >= 5) ? argv[4] : "sauvola";
        const float k = (argc >= 6) ? static_cast<float>(std::atof(argv[5])) : 0.15f;
        const int window = (argc >= 7) ? std::atoi(argv[6]) : 51;
        return run_binarize(argv[2], argv[3], mode, k, window);
    }

    print_usage(argv[0]);
    return 1;
}
