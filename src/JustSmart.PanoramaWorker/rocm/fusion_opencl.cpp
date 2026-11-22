// AMD GPU（OpenCL 2.0 / AMD APP 运行时）加速的 Deghost 逐像素融合内核。
//
// 说明：本机 Radeon 630 为 gfx804，ROCm 6.4 HIP SDK 的 hipInfo 报告
// "no ROCm-capable device"，HIP 运行时不可用；AMD 驱动自带的 OpenCL 2.0
// 运行时（AMD Accelerated Parallel Processing）可以正常使用同一块 GPU，
// 因此这里通过 OpenCL 调用本机 AMD GPU，算法与 AVX2/标量版本逐像素一致。

#include <windows.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace {

typedef int32_t cl_int;
typedef uint32_t cl_uint;
typedef uint64_t cl_ulong;
typedef uint64_t cl_bitfield;
typedef void* cl_handle;
typedef cl_handle cl_platform_id;
typedef cl_handle cl_device_id;
typedef cl_handle cl_context;
typedef cl_handle cl_command_queue;
typedef cl_handle cl_program;
typedef cl_handle cl_kernel;
typedef cl_handle cl_mem;
typedef cl_handle cl_event;

constexpr cl_int CL_SUCCESS = 0;
constexpr cl_bitfield CL_DEVICE_TYPE_GPU = 1u << 2;
constexpr cl_uint CL_PLATFORM_NAME = 0x0902;
constexpr cl_uint CL_DEVICE_NAME = 0x102B;
constexpr cl_uint CL_DEVICE_MAX_MEM_ALLOC_SIZE = 0x1010;
constexpr cl_uint CL_PROGRAM_BUILD_LOG = 0x1183;
constexpr cl_bitfield CL_MEM_READ_ONLY = 1u << 2;
constexpr cl_bitfield CL_MEM_WRITE_ONLY = 1u << 1;
constexpr cl_bitfield CL_MEM_COPY_HOST_PTR = 1u << 5;
constexpr cl_bitfield CL_QUEUE_PROFILING_ENABLE = 1u << 1;
constexpr cl_uint CL_PROFILING_COMMAND_START = 0x1282;
constexpr cl_uint CL_PROFILING_COMMAND_END = 0x1283;
constexpr cl_uint CL_TRUE = 1;

struct OpenCl {
    HMODULE dll = nullptr;
    cl_int (*GetPlatformIDs)(cl_uint, cl_platform_id*, cl_uint*) = nullptr;
    cl_int (*GetPlatformInfo)(cl_platform_id, cl_uint, size_t, void*, size_t*) = nullptr;
    cl_int (*GetDeviceIDs)(cl_platform_id, cl_bitfield, cl_uint, cl_device_id*, cl_uint*) = nullptr;
    cl_int (*GetDeviceInfo)(cl_device_id, cl_uint, size_t, void*, size_t*) = nullptr;
    cl_context (*CreateContext)(const void*, cl_uint, const cl_device_id*, void*, void*, cl_int*) = nullptr;
    cl_command_queue (*CreateCommandQueue)(cl_context, cl_device_id, cl_bitfield, cl_int*) = nullptr;
    cl_program (*CreateProgramWithSource)(cl_context, cl_uint, const char**, const size_t*, cl_int*) = nullptr;
    cl_int (*BuildProgram)(cl_program, cl_uint, const cl_device_id*, const char*, void*, void*) = nullptr;
    cl_int (*GetProgramBuildInfo)(cl_program, cl_device_id, cl_uint, size_t, void*, size_t*) = nullptr;
    cl_kernel (*CreateKernel)(cl_program, const char*, cl_int*) = nullptr;
    cl_mem (*CreateBuffer)(cl_context, cl_bitfield, size_t, void*, cl_int*) = nullptr;
    cl_int (*EnqueueWriteBuffer)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, const void*, cl_uint, const cl_event*, cl_event*) = nullptr;
    cl_int (*EnqueueReadBuffer)(cl_command_queue, cl_mem, cl_uint, size_t, size_t, void*, cl_uint, const cl_event*, cl_event*) = nullptr;
    cl_int (*SetKernelArg)(cl_kernel, cl_uint, size_t, const void*) = nullptr;
    cl_int (*EnqueueNDRangeKernel)(cl_command_queue, cl_kernel, cl_uint, const size_t*, const size_t*, const size_t*, cl_uint, const cl_event*, cl_event*) = nullptr;
    cl_int (*Finish)(cl_command_queue) = nullptr;
    cl_int (*WaitForEvents)(cl_uint, const cl_event*) = nullptr;
    cl_int (*GetEventProfilingInfo)(cl_event, cl_uint, size_t, void*, size_t*) = nullptr;
    cl_int (*ReleaseEvent)(cl_event) = nullptr;
    cl_int (*ReleaseMemObject)(cl_mem) = nullptr;
    cl_int (*ReleaseKernel)(cl_kernel) = nullptr;
    cl_int (*ReleaseProgram)(cl_program) = nullptr;
    cl_int (*ReleaseCommandQueue)(cl_command_queue) = nullptr;
    cl_int (*ReleaseContext)(cl_context) = nullptr;

    bool Load() {
        dll = LoadLibraryA("OpenCL.dll");
        if (!dll) {
            std::fprintf(stderr, "OpenCL.dll not found\n");
            return false;
        }
#define LOAD(member, sym) do { member = reinterpret_cast<decltype(member)>(GetProcAddress(dll, sym)); \
        if (!member) { std::fprintf(stderr, "missing OpenCL symbol: %s\n", sym); return false; } } while (0)
        LOAD(GetPlatformIDs, "clGetPlatformIDs");
        LOAD(GetPlatformInfo, "clGetPlatformInfo");
        LOAD(GetDeviceIDs, "clGetDeviceIDs");
        LOAD(GetDeviceInfo, "clGetDeviceInfo");
        LOAD(CreateContext, "clCreateContext");
        LOAD(CreateCommandQueue, "clCreateCommandQueue");
        LOAD(CreateProgramWithSource, "clCreateProgramWithSource");
        LOAD(BuildProgram, "clBuildProgram");
        LOAD(GetProgramBuildInfo, "clGetProgramBuildInfo");
        LOAD(CreateKernel, "clCreateKernel");
        LOAD(CreateBuffer, "clCreateBuffer");
        LOAD(EnqueueWriteBuffer, "clEnqueueWriteBuffer");
        LOAD(EnqueueReadBuffer, "clEnqueueReadBuffer");
        LOAD(SetKernelArg, "clSetKernelArg");
        LOAD(EnqueueNDRangeKernel, "clEnqueueNDRangeKernel");
        LOAD(Finish, "clFinish");
        LOAD(WaitForEvents, "clWaitForEvents");
        LOAD(GetEventProfilingInfo, "clGetEventProfilingInfo");
        LOAD(ReleaseEvent, "clReleaseEvent");
        LOAD(ReleaseMemObject, "clReleaseMemObject");
        LOAD(ReleaseKernel, "clReleaseKernel");
        LOAD(ReleaseProgram, "clReleaseProgram");
        LOAD(ReleaseCommandQueue, "clReleaseCommandQueue");
        LOAD(ReleaseContext, "clReleaseContext");
#undef LOAD
        return true;
    }
};

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
    return in.gcount() == static_cast<std::streamsize>(bytes);
}

bool LoadPair(const std::string& dir, const std::string& name, PairData& d) {
    d.name = name;
    {
        std::ifstream meta(dir + "/meta.txt");
        if (!meta) return false;
        meta >> d.width >> d.height >> d.feather;
        meta >> d.gain[0] >> d.gain[1] >> d.gain[2];
        if (!meta) return false;
    }
    const size_t n = d.pixels();
    for (int c = 0; c < 3; c++) {
        d.prev[c].resize(n);
        d.cur[c].resize(n);
        const char tag[2] = {"bgr"[c], 0};
        if (!ReadFile(dir + "/prev_" + tag + ".u8", d.prev[c].data(), n)) return false;
        if (!ReadFile(dir + "/cur_" + tag + ".u8", d.cur[c].data(), n)) return false;
    }
    d.prevMask.resize(n);
    d.curMask.resize(n);
    d.overlap.resize(n);
    d.diff.resize(n);
    d.seam.resize(d.height);
    return ReadFile(dir + "/prevmask.u8", d.prevMask.data(), n)
        && ReadFile(dir + "/curmask.u8", d.curMask.data(), n)
        && ReadFile(dir + "/overlap.u8", d.overlap.data(), n)
        && ReadFile(dir + "/diff.u8", d.diff.data(), n)
        && ReadFile(dir + "/seam.i32", d.seam.data(), d.seam.size() * sizeof(int32_t));
}

const char* kKernelSource = R"CLC(
__kernel void fuse_pair(
    __global const uchar* prevB, __global const uchar* prevG, __global const uchar* prevR,
    __global const uchar* curB, __global const uchar* curG, __global const uchar* curR,
    __global const uchar* prevMask, __global const uchar* curMask,
    __global const uchar* overlap, __global const uchar* diff,
    __global const int* seam,
    __global uchar* outB, __global uchar* outG, __global uchar* outR,
    const int W, const int H, const int feather,
    const float gb, const float gg, const float gr)
{
    const int x = (int)get_global_id(0);
    const int y = (int)get_global_id(1);
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const int sx = seam[y];

    const uchar pb = prevB[i], pg = prevG[i], pr = prevR[i];
    const uchar cb = convert_uchar_sat_rte((float)curB[i] * gb);
    const uchar cg = convert_uchar_sat_rte((float)curG[i] * gg);
    const uchar cr = convert_uchar_sat_rte((float)curR[i] * gr);

    if (overlap[i] == (uchar)0) {
        if (curMask[i] != (uchar)0 && prevMask[i] == (uchar)0) {
            outB[i] = cb; outG[i] = cg; outR[i] = cr;
        } else {
            outB[i] = pb; outG[i] = pg; outR[i] = pr;
        }
        return;
    }
    if (x < sx - feather) {
        outB[i] = pb; outG[i] = pg; outR[i] = pr;
        return;
    }
    if (x > sx + feather) {
        outB[i] = cb; outG[i] = cg; outR[i] = cr;
        return;
    }

    const int dist = abs(x - sx);
    const int dd = min(dist, feather);
    float w = (feather <= 0) ? 0.0f
        : 0.5f * (1.0f - cos(3.14159265358979323846f * (float)dd / (float)feather));
    float wp = 1.0f - w;
    float wc = w;
    if (diff[i] > (uchar)14) {
        if (dist <= feather / 2) { wp = 1.0f; wc = 0.0f; }
        else { wp = 0.0f; wc = 1.0f; }
    }
    outB[i] = convert_uchar_sat(wp * (float)pb + wc * (float)cb);
    outG[i] = convert_uchar_sat(wp * (float)pg + wc * (float)cg);
    outR[i] = convert_uchar_sat(wp * (float)pr + wc * (float)cr);
}
)CLC";

void Check(OpenCl& cl, cl_int err, const char* what) {
    if (err != CL_SUCCESS) {
        std::fprintf(stderr, "%s failed: %d\n", what, err);
        std::exit(1);
    }
}

double Median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    const size_t n = v.size();
    return n % 2 ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}

std::string PlatformName(OpenCl& cl, cl_platform_id id) {
    char buf[256] = {0};
    cl.GetPlatformInfo(id, CL_PLATFORM_NAME, sizeof(buf), buf, nullptr);
    return buf;
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc < 2) {
        std::printf("usage: fusion_opencl <dataRoot> [iterations]\n");
        return 2;
    }
    const std::string root = argv[1];
    const int iters = argc >= 3 ? std::max(1, std::atoi(argv[2])) : 15;

    OpenCl cl;
    if (!cl.Load()) return 3;

    cl_uint platformCount = 0;
    Check(cl, cl.GetPlatformIDs(0, nullptr, &platformCount), "clGetPlatformIDs");
    std::vector<cl_platform_id> platforms(platformCount);
    Check(cl, cl.GetPlatformIDs(platformCount, platforms.data(), nullptr), "clGetPlatformIDs");
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
    Check(cl, cl.GetDeviceIDs(amd, CL_DEVICE_TYPE_GPU, 1, &device, nullptr), "clGetDeviceIDs");
    char devName[256] = {0};
    cl.GetDeviceInfo(device, CL_DEVICE_NAME, sizeof(devName), devName, nullptr);
    cl_ulong maxAlloc = 0;
    cl.GetDeviceInfo(device, CL_DEVICE_MAX_MEM_ALLOC_SIZE, sizeof(maxAlloc), &maxAlloc, nullptr);
    std::printf("device: %s  maxAlloc=%.0f MB\n", devName, maxAlloc / 1048576.0);

    cl_int err = CL_SUCCESS;
    cl_context context = cl.CreateContext(nullptr, 1, &device, nullptr, nullptr, &err);
    Check(cl, err, "clCreateContext");
    cl_command_queue queue = cl.CreateCommandQueue(context, device, CL_QUEUE_PROFILING_ENABLE, &err);
    Check(cl, err, "clCreateCommandQueue");

    const char* src = kKernelSource;
    cl_program program = cl.CreateProgramWithSource(context, 1, &src, nullptr, &err);
    Check(cl, err, "clCreateProgramWithSource");
    err = cl.BuildProgram(program, 1, &device, "-cl-std=CL2.0", nullptr, nullptr);
    if (err != CL_SUCCESS) {
        size_t logSize = 0;
        cl.GetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, 0, nullptr, &logSize);
        std::vector<char> log(logSize + 1, 0);
        cl.GetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, logSize, log.data(), nullptr);
        std::fprintf(stderr, "build log:\n%s\n", log.data());
        return 5;
    }
    cl_kernel kernel = cl.CreateKernel(program, "fuse_pair", &err);
    Check(cl, err, "clCreateKernel");

    std::vector<std::string> pairs;
    for (const char* name : {"pair_4_3", "pair_3_2", "pair_2_1"}) {
        std::ifstream probe(root + "/" + name + "/meta.txt");
        if (probe) pairs.push_back(name);
    }

    double totalKernel = 0.0;
    for (const std::string& name : pairs) {
        PairData d;
        if (!LoadPair(root + "/" + name, name, d)) return 6;
        const size_t n = d.pixels();
        const size_t bytes = n * sizeof(uint8_t);
        std::printf("%s %dx%d\n", name.c_str(), d.width, d.height);

        std::vector<cl_mem> inputs;
        auto makeInput = [&](const void* data, size_t size, cl_bitfield flags) {
            cl_int e = CL_SUCCESS;
            cl_mem m = cl.CreateBuffer(context, flags | CL_MEM_COPY_HOST_PTR, size,
                                       const_cast<void*>(data), &e);
            Check(cl, e, "clCreateBuffer");
            inputs.push_back(m);
            return m;
        };

        const auto t0 = std::chrono::steady_clock::now();
        cl_mem pB = makeInput(d.prev[0].data(), bytes, CL_MEM_READ_ONLY);
        cl_mem pG = makeInput(d.prev[1].data(), bytes, CL_MEM_READ_ONLY);
        cl_mem pR = makeInput(d.prev[2].data(), bytes, CL_MEM_READ_ONLY);
        cl_mem cB = makeInput(d.cur[0].data(), bytes, CL_MEM_READ_ONLY);
        cl_mem cG = makeInput(d.cur[1].data(), bytes, CL_MEM_READ_ONLY);
        cl_mem cR = makeInput(d.cur[2].data(), bytes, CL_MEM_READ_ONLY);
        cl_mem pM = makeInput(d.prevMask.data(), bytes, CL_MEM_READ_ONLY);
        cl_mem cM = makeInput(d.curMask.data(), bytes, CL_MEM_READ_ONLY);
        cl_mem ov = makeInput(d.overlap.data(), bytes, CL_MEM_READ_ONLY);
        cl_mem df = makeInput(d.diff.data(), bytes, CL_MEM_READ_ONLY);
        cl_mem sm = makeInput(d.seam.data(), d.seam.size() * sizeof(int32_t), CL_MEM_READ_ONLY);
        cl_mem oB = cl.CreateBuffer(context, CL_MEM_WRITE_ONLY, bytes, nullptr, &err);
        Check(cl, err, "clCreateBuffer");
        cl_mem oG = cl.CreateBuffer(context, CL_MEM_WRITE_ONLY, bytes, nullptr, &err);
        Check(cl, err, "clCreateBuffer");
        cl_mem oR = cl.CreateBuffer(context, CL_MEM_WRITE_ONLY, bytes, nullptr, &err);
        Check(cl, err, "clCreateBuffer");
        cl.Finish(queue);
        const auto t1 = std::chrono::steady_clock::now();
        const double uploadMs = std::chrono::duration<double, std::milli>(t1 - t0).count();

        cl_mem ins[11] = {pB, pG, pR, cB, cG, cR, pM, cM, ov, df, sm};
        cl_mem outs[3] = {oB, oG, oR};
        for (int k = 0; k < 11; k++) {
            Check(cl, cl.SetKernelArg(kernel, k, sizeof(cl_mem), &ins[k]), "clSetKernelArg");
        }
        for (int k = 0; k < 3; k++) {
            Check(cl, cl.SetKernelArg(kernel, 11 + k, sizeof(cl_mem), &outs[k]), "clSetKernelArg");
        }
        int W = d.width, H = d.height, feather = d.feather;
        cl.SetKernelArg(kernel, 14, sizeof(int), &W);
        cl.SetKernelArg(kernel, 15, sizeof(int), &H);
        cl.SetKernelArg(kernel, 16, sizeof(int), &feather);
        cl.SetKernelArg(kernel, 17, sizeof(float), &d.gain[0]);
        cl.SetKernelArg(kernel, 18, sizeof(float), &d.gain[1]);
        cl.SetKernelArg(kernel, 19, sizeof(float), &d.gain[2]);

        const size_t global[2] = {static_cast<size_t>(W), static_cast<size_t>(H)};
        const size_t local[2] = {16, 16};
        auto launch = [&](cl_event* ev) {
            return cl.EnqueueNDRangeKernel(queue, kernel, 2, nullptr, global, local, 0, nullptr, ev);
        };
        for (int w = 0; w < 2; w++) {
            Check(cl, launch(nullptr), "clEnqueueNDRangeKernel");
        }
        cl.Finish(queue);

        std::vector<double> times;
        for (int it = 0; it < iters; it++) {
            cl_event ev = nullptr;
            Check(cl, launch(&ev), "clEnqueueNDRangeKernel");
            Check(cl, cl.WaitForEvents(1, &ev), "clWaitForEvents");
            cl_ulong start = 0, end = 0;
            cl.GetEventProfilingInfo(ev, CL_PROFILING_COMMAND_START, sizeof(start), &start, nullptr);
            cl.GetEventProfilingInfo(ev, CL_PROFILING_COMMAND_END, sizeof(end), &end, nullptr);
            times.push_back((end - start) / 1e6);
            cl.ReleaseEvent(ev);
        }
        const double kernelMs = Median(times);
        totalKernel += kernelMs;

        // 回读并和 AVX2 的标量参考结果对比
        std::vector<uint8_t> host[3];
        const auto t2 = std::chrono::steady_clock::now();
        size_t mismatch = 0;
        int maxDiff = 0;
        for (int c = 0; c < 3; c++) {
            host[c].resize(n);
            Check(cl, cl.EnqueueReadBuffer(queue, outs[c], CL_TRUE, 0, bytes, host[c].data(),
                                           0, nullptr, nullptr), "clEnqueueReadBuffer");
            std::vector<uint8_t> ref;
            const char tag[2] = {"bgr"[c], 0};
            std::ifstream in(root + "/" + name + "/ref_" + tag + ".u8", std::ios::binary);
            if (in) {
                ref.resize(n);
                in.read(reinterpret_cast<char*>(ref.data()), static_cast<std::streamsize>(n));
                for (size_t i = 0; i < n; i++) {
                    const int dv = std::abs(static_cast<int>(host[c][i]) - static_cast<int>(ref[i]));
                    if (dv != 0) mismatch++;
                    if (dv > maxDiff) maxDiff = dv;
                }
            }
        }
        const auto t3 = std::chrono::steady_clock::now();
        const double downloadMs = std::chrono::duration<double, std::milli>(t3 - t2).count();

        std::printf("  kernel=%.3f ms  upload=%.1f ms  download=%.1f ms  "
                    "vs.avx2 mismatch=%zu maxdiff=%d\n",
                    kernelMs, uploadMs, downloadMs, mismatch, maxDiff);

        for (cl_mem m : inputs) cl.ReleaseMemObject(m);
        for (cl_mem m : outs) cl.ReleaseMemObject(m);
    }

    std::printf("TOTAL GPU kernel=%.3f ms\n", totalKernel);
    cl.ReleaseKernel(kernel);
    cl.ReleaseProgram(program);
    cl.ReleaseCommandQueue(queue);
    cl.ReleaseContext(context);
    return 0;
}
