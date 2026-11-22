// AVX2 加速的 Deghost 逐像素融合内核。
// 输入为已配准到画布的平面数据（B/G/R 分离），只测融合本身：
// 增益应用 + 缝合线 smoothstep 过渡 + 色差一致性选择。
// 标量实现作为正确性基准，AVX2 实现每次处理 8 个像素。

#include <immintrin.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

namespace {

struct PairData {
    std::string name;
    int width = 0;
    int height = 0;
    int feather = 0;
    float gain[3] = {1.0f, 1.0f, 1.0f};
    std::vector<uint8_t> prev[3];
    std::vector<uint8_t> cur[3];
    std::vector<uint8_t> prevMask;
    std::vector<uint8_t> curMask;
    std::vector<uint8_t> overlap;
    std::vector<uint8_t> diff;
    std::vector<int32_t> seam;
    size_t pixels() const { return static_cast<size_t>(width) * height; }
};

bool ReadFile(const std::string& path, void* dst, size_t bytes) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        std::fprintf(stderr, "open failed: %s\n", path.c_str());
        return false;
    }
    in.read(static_cast<char*>(dst), static_cast<std::streamsize>(bytes));
    if (in.gcount() != static_cast<std::streamsize>(bytes)) {
        std::fprintf(stderr, "short read: %s\n", path.c_str());
        return false;
    }
    return true;
}

bool WriteFile(const std::string& path, const void* src, size_t bytes) {
    std::ofstream out(path, std::ios::binary);
    if (!out) return false;
    out.write(static_cast<const char*>(src), static_cast<std::streamsize>(bytes));
    return static_cast<bool>(out);
}

bool LoadPair(const std::string& dir, const std::string& name, PairData& d) {
    d.name = name;
    {
        std::ifstream meta(dir + "/meta.txt");
        if (!meta) {
            std::fprintf(stderr, "meta.txt missing: %s\n", dir.c_str());
            return false;
        }
        meta >> d.width >> d.height >> d.feather;
        meta >> d.gain[0] >> d.gain[1] >> d.gain[2];
        if (!meta) {
            std::fprintf(stderr, "meta.txt invalid: %s\n", dir.c_str());
            return false;
        }
    }

    const size_t n = d.pixels();
    for (int c = 0; c < 3; c++) {
        d.prev[c].resize(n);
        d.cur[c].resize(n);
        const char* pc = "bgr";
        std::string tag(1, pc[c]);
        if (!ReadFile(dir + "/prev_" + tag + ".u8", d.prev[c].data(), n)) return false;
        if (!ReadFile(dir + "/cur_" + tag + ".u8", d.cur[c].data(), n)) return false;
    }
    d.prevMask.resize(n);
    d.curMask.resize(n);
    d.overlap.resize(n);
    d.diff.resize(n);
    d.seam.resize(d.height);
    if (!ReadFile(dir + "/prevmask.u8", d.prevMask.data(), n)) return false;
    if (!ReadFile(dir + "/curmask.u8", d.curMask.data(), n)) return false;
    if (!ReadFile(dir + "/overlap.u8", d.overlap.data(), n)) return false;
    if (!ReadFile(dir + "/diff.u8", d.diff.data(), n)) return false;
    if (!ReadFile(dir + "/seam.i32", d.seam.data(), d.seam.size() * sizeof(int32_t))) return false;
    return true;
}

std::vector<float> BuildWeightLut(int feather) {
    std::vector<float> lut(static_cast<size_t>(std::max(feather, 0)) + 1, 0.0f);
    if (feather <= 0) return lut;
    for (int i = 0; i <= feather; i++) {
        double w = 0.5 * (1.0 - std::cos(3.14159265358979323846 * i / feather));
        lut[static_cast<size_t>(i)] = static_cast<float>(w);
    }
    return lut;
}

inline uint8_t ApplyGain(uint8_t v, float g) {
    int x = static_cast<int>(std::nearbyint(static_cast<float>(v) * g));
    return static_cast<uint8_t>(x < 0 ? 0 : (x > 255 ? 255 : x));
}

// 标量逐像素参考实现
void FuseScalar(const PairData& d, const std::vector<float>& lut,
                std::vector<uint8_t> out[3]) {
    const int W = d.width;
    const int H = d.height;
    const int feather = d.feather;
    const int halfFeather = feather / 2;
    for (int y = 0; y < H; y++) {
        const size_t row = static_cast<size_t>(y) * W;
        const int sx = d.seam[static_cast<size_t>(y)];
        for (int x = 0; x < W; x++) {
            const size_t i = row + x;
            const bool inOverlap = d.overlap[i] > 0;
            const bool hasPrev = d.prevMask[i] > 0;
            const bool hasCur = d.curMask[i] > 0;
            if (inOverlap && (x < sx - feather || x > sx + feather)) {
                // 缝合带外：左侧取 prev，右侧取 curGain（与 C# 分支一致）
                const bool takeCur = x > sx + feather;
                for (int c = 0; c < 3; c++) {
                    out[c][i] = takeCur ? ApplyGain(d.cur[c][i], d.gain[c]) : d.prev[c][i];
                }
            } else if (inOverlap) {
                const int dist = std::abs(x - sx);
                const int dd = std::min(dist, feather);
                float w = feather <= 0 ? 0.0f : lut[static_cast<size_t>(dd)];
                float wp = 1.0f - w;
                float wc = w;
                if (d.diff[i] > 14) {
                    if (dist <= halfFeather) {
                        wp = 1.0f;
                        wc = 0.0f;
                    } else {
                        wp = 0.0f;
                        wc = 1.0f;
                    }
                }
                for (int c = 0; c < 3; c++) {
                    const float p = d.prev[c][i];
                    const float q = ApplyGain(d.cur[c][i], d.gain[c]);
                    out[c][i] = static_cast<uint8_t>(wp * p + wc * q);
                }
            } else if (hasCur && !hasPrev) {
                for (int c = 0; c < 3; c++) {
                    out[c][i] = ApplyGain(d.cur[c][i], d.gain[c]);
                }
            } else {
                for (int c = 0; c < 3; c++) {
                    out[c][i] = d.prev[c][i];
                }
            }
        }
    }
}

inline __m256i Load8U8(const uint8_t* p) {
    return _mm256_cvtepu8_epi32(_mm_loadl_epi64(reinterpret_cast<const __m128i*>(p)));
}

inline void Store8U8(uint8_t* p, __m256i v) {
    const __m128i lo = _mm256_castsi256_si128(v);
    const __m128i hi = _mm256_extracti128_si256(v, 1);
    const __m128i u16 = _mm_packus_epi32(lo, hi);
    const __m128i u8 = _mm_packus_epi16(u16, u16);
    _mm_storel_epi64(reinterpret_cast<__m128i*>(p), u8);
}

inline __m256i Clamp255(__m256i v) {
    const __m256i zero = _mm256_setzero_si256();
    const __m256i hi = _mm256_set1_epi32(255);
    return _mm256_min_epi32(_mm256_max_epi32(v, zero), hi);
}

// 单像素尾处理（与标量逻辑一致）
inline void FuseTailPixel(const PairData& d, const std::vector<float>& lut,
                          int x, int y, std::vector<uint8_t> out[3]) {
    const size_t i = static_cast<size_t>(y) * d.width + x;
    const bool inOverlap = d.overlap[i] > 0;
    const bool hasPrev = d.prevMask[i] > 0;
    const bool hasCur = d.curMask[i] > 0;
    const int sx = d.seam[static_cast<size_t>(y)];
    if (inOverlap && (x < sx - d.feather || x > sx + d.feather)) {
        const bool takeCur = x > sx + d.feather;
        for (int c = 0; c < 3; c++) {
            out[c][i] = takeCur ? ApplyGain(d.cur[c][i], d.gain[c]) : d.prev[c][i];
        }
    } else if (inOverlap) {
        const int dist = std::abs(x - sx);
        const int dd = std::min(dist, d.feather);
        float w = d.feather <= 0 ? 0.0f : lut[static_cast<size_t>(dd)];
        float wp = 1.0f - w;
        float wc = w;
        if (d.diff[i] > 14) {
            if (dist <= d.feather / 2) {
                wp = 1.0f;
                wc = 0.0f;
            } else {
                wp = 0.0f;
                wc = 1.0f;
            }
        }
        for (int c = 0; c < 3; c++) {
            const float p = d.prev[c][i];
            const float q = ApplyGain(d.cur[c][i], d.gain[c]);
            out[c][i] = static_cast<uint8_t>(wp * p + wc * q);
        }
    } else if (hasCur && !hasPrev) {
        for (int c = 0; c < 3; c++) {
            out[c][i] = ApplyGain(d.cur[c][i], d.gain[c]);
        }
    } else {
        for (int c = 0; c < 3; c++) {
            out[c][i] = d.prev[c][i];
        }
    }
}

// AVX2：每次处理 8 个像素
void FuseAvx2(const PairData& d, const std::vector<float>& lut,
              std::vector<uint8_t> out[3]) {
    const int W = d.width;
    const int H = d.height;
    const int feather = d.feather;
    const float halfFeather = feather / 2.0f;
    const __m256 one = _mm256_set1_ps(1.0f);
    const __m256 g0 = _mm256_set1_ps(d.gain[0]);
    const __m256 g1 = _mm256_set1_ps(d.gain[1]);
    const __m256 g2 = _mm256_set1_ps(d.gain[2]);
    const __m256i featherV = _mm256_set1_epi32(feather);
    const __m256i fourteen = _mm256_set1_epi32(14);
    const __m256 halfV = _mm256_set1_ps(halfFeather);
    const __m256i lane = _mm256_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7);
    const float* lutBase = lut.data();

    for (int y = 0; y < H; y++) {
        const size_t row = static_cast<size_t>(y) * W;
        const __m256i sxV = _mm256_set1_epi32(d.seam[static_cast<size_t>(y)]);
        int x = 0;
        for (; x + 8 <= W; x += 8) {
            const size_t i = row + x;
            const __m256i xV = _mm256_add_epi32(_mm256_set1_epi32(x), lane);
            const __m256i ovM = _mm256_cmpgt_epi32(Load8U8(&d.overlap[i]), _mm256_setzero_si256());
            const __m256i prevM = _mm256_cmpgt_epi32(Load8U8(&d.prevMask[i]), _mm256_setzero_si256());
            const __m256i curM = _mm256_cmpgt_epi32(Load8U8(&d.curMask[i]), _mm256_setzero_si256());

            const __m256i dist = _mm256_abs_epi32(_mm256_sub_epi32(xV, sxV));
            const __m256i dmin = _mm256_min_epi32(dist, featherV);
            __m256 w = _mm256_i32gather_ps(lutBase, dmin, 4);
            const __m256 beyond = _mm256_castsi256_ps(_mm256_cmpgt_epi32(dist, featherV));
            w = _mm256_blendv_ps(w, one, beyond);

            const __m256 bigDiff = _mm256_castsi256_ps(
                _mm256_cmpgt_epi32(Load8U8(&d.diff[i]), fourteen));
            const __m256 nearSeam = _mm256_cmp_ps(_mm256_cvtepi32_ps(dist), halfV, _CMP_LE_OQ);
            __m256 wp = _mm256_sub_ps(one, w);
            __m256 wc = w;
            const __m256 wpHard = _mm256_blendv_ps(_mm256_setzero_ps(), one, nearSeam);
            const __m256 wcHard = _mm256_sub_ps(one, wpHard);
            wp = _mm256_blendv_ps(wp, wpHard, bigDiff);
            wc = _mm256_blendv_ps(wc, wcHard, bigDiff);

            __m256i curI[3];
            __m256 prevF[3];
            __m256i prevI[3];
            __m256i blended[3];
            const __m256 gv[3] = {g0, g1, g2};
            for (int c = 0; c < 3; c++) {
                curI[c] = Clamp255(_mm256_cvtps_epi32(
                    _mm256_mul_ps(_mm256_cvtepi32_ps(Load8U8(&d.cur[c][i])), gv[c])));
                prevI[c] = Load8U8(&d.prev[c][i]);
                prevF[c] = _mm256_cvtepi32_ps(prevI[c]);
                const __m256 curF = _mm256_cvtepi32_ps(curI[c]);
                const __m256 rf = _mm256_add_ps(_mm256_mul_ps(wp, prevF[c]),
                                                _mm256_mul_ps(wc, curF));
                blended[c] = Clamp255(_mm256_cvttps_epi32(rf));
            }

            const __m256i curOnly = _mm256_andnot_si256(prevM, curM);
            const __m256i leftOut = _mm256_and_si256(
                ovM, _mm256_cmpgt_epi32(_mm256_sub_epi32(sxV, featherV), xV));
            const __m256i rightOut = _mm256_and_si256(
                ovM, _mm256_cmpgt_epi32(xV, _mm256_add_epi32(sxV, featherV)));
            const __m256i band = _mm256_andnot_si256(
                _mm256_or_si256(leftOut, rightOut), ovM);
            const __m256i curSide = _mm256_or_si256(curOnly, rightOut);
            for (int c = 0; c < 3; c++) {
                __m256i r = prevI[c];
                r = _mm256_blendv_epi8(r, curI[c], curSide);
                r = _mm256_blendv_epi8(r, blended[c], band);
                Store8U8(&out[c][i], r);
            }
        }
        for (; x < W; x++) {
            FuseTailPixel(d, lut, x, y, out);
        }
    }
}

int64_t Checksum(const std::vector<uint8_t> out[3]) {
    int64_t sum = 0;
    for (int c = 0; c < 3; c++) {
        const std::vector<uint8_t>& v = out[c];
        for (size_t i = 0; i < v.size(); i += 4096) sum += v[i];
    }
    return sum;
}

double Median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    const size_t n = v.size();
    return n % 2 ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::printf("usage: fusion_avx2 <dataRoot> [iterations]\n");
        return 2;
    }
    const std::string root = argv[1];
    const int iters = argc >= 3 ? std::max(1, std::atoi(argv[2])) : 15;

#if defined(_MSC_VER)
    int regs[4] = {0, 0, 0, 0};
    __cpuid(regs, 7);
    if ((regs[1] & (1 << 5)) == 0) {
        std::fprintf(stderr, "CPU does not support AVX2\n");
        return 3;
    }
#endif

    std::vector<std::string> pairs;
    for (const char* name : {"pair_4_3", "pair_3_2", "pair_2_1"}) {
        std::ifstream probe(root + "/" + name + "/meta.txt");
        if (probe) pairs.push_back(name);
    }
    if (pairs.empty()) {
        std::fprintf(stderr, "no pair data under %s\n", root.c_str());
        return 4;
    }

    double totalScalar = 0.0;
    double totalAvx2 = 0.0;
    for (const std::string& name : pairs) {
        PairData d;
        if (!LoadPair(root + "/" + name, name, d)) return 5;
        const std::vector<float> lut = BuildWeightLut(d.feather);
        const size_t n = d.pixels();
        std::vector<uint8_t> outS[3], outA[3];
        for (int c = 0; c < 3; c++) {
            outS[c].resize(n);
            outA[c].resize(n);
        }

        for (int w = 0; w < 2; w++) FuseScalar(d, lut, outS);
        std::vector<double> ts;
        for (int it = 0; it < iters; it++) {
            const auto t0 = std::chrono::steady_clock::now();
            FuseScalar(d, lut, outS);
            const auto t1 = std::chrono::steady_clock::now();
            ts.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
        }
        for (int w = 0; w < 2; w++) FuseAvx2(d, lut, outA);
        std::vector<double> ta;
        for (int it = 0; it < iters; it++) {
            const auto t0 = std::chrono::steady_clock::now();
            FuseAvx2(d, lut, outA);
            const auto t1 = std::chrono::steady_clock::now();
            ta.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
        }

        size_t mismatch = 0;
        int maxDiff = 0;
        for (int c = 0; c < 3; c++) {
            for (size_t i = 0; i < n; i++) {
                const int dv = std::abs(static_cast<int>(outS[c][i]) - static_cast<int>(outA[c][i]));
                if (dv != 0) mismatch++;
                maxDiff = std::max(maxDiff, dv);
            }
        }

        const double ms = Median(ts);
        const double ma = Median(ta);
        totalScalar += ms;
        totalAvx2 += ma;
        std::printf("%s %dx%d  scalar=%.2f ms  avx2=%.2f ms  speedup=%.2fx  "
                    "mismatch=%zu maxdiff=%d  checksum=%lld\n",
                    name.c_str(), d.width, d.height, ms, ma, ms / ma,
                    mismatch, maxDiff, static_cast<long long>(Checksum(outA)));

        for (int c = 0; c < 3; c++) {
            char tag[2] = {static_cast<char>('b' + c), 0};
            WriteFile(root + "/" + name + "/ref_" + tag + ".u8", outS[c].data(), n);
        }
    }

    std::printf("TOTAL scalar=%.2f ms  avx2=%.2f ms  speedup=%.2fx\n",
                totalScalar, totalAvx2, totalScalar / totalAvx2);
    return 0;
}
