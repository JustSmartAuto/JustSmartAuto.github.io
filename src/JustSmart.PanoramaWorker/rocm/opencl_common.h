// OpenCL 动态加载与公共常量（AMD APP 运行时，无需链接 OpenCL.lib）。
#pragma once

#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

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
constexpr cl_uint CL_PROGRAM_BUILD_LOG = 0x1183;
constexpr cl_bitfield CL_MEM_READ_WRITE = 1u << 0;
constexpr cl_bitfield CL_MEM_WRITE_ONLY = 1u << 1;
constexpr cl_bitfield CL_MEM_READ_ONLY = 1u << 2;
constexpr cl_bitfield CL_MEM_COPY_HOST_PTR = 1u << 5;
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
        LOAD(ReleaseMemObject, "clReleaseMemObject");
        LOAD(ReleaseKernel, "clReleaseKernel");
        LOAD(ReleaseProgram, "clReleaseProgram");
        LOAD(ReleaseCommandQueue, "clReleaseCommandQueue");
        LOAD(ReleaseContext, "clReleaseContext");
#undef LOAD
        return true;
    }
};

inline void Check(OpenCl& cl, cl_int err, const char* what) {
    if (err != CL_SUCCESS) {
        std::fprintf(stderr, "%s failed: %d\n", what, err);
        std::exit(1);
    }
}

inline std::string PlatformName(OpenCl& cl, cl_platform_id id) {
    char buf[256] = {0};
    cl.GetPlatformInfo(id, CL_PLATFORM_NAME, sizeof(buf), buf, nullptr);
    return buf;
}

inline size_t RoundUp(size_t v, size_t m) { return ((v + m - 1) / m) * m; }
