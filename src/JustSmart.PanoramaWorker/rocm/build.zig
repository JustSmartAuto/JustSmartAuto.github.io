const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const exe = b.addExecutable(.{
        .name = "fusion_opencl",
        .root_module = b.createModule(.{
            .target = target,
            .optimize = optimize,
        }),
    });

    exe.root_module.addCSourceFile(.{
        .file = b.path("fusion_opencl.cpp"),
        .flags = &.{ "-std=c++17", "-O3" },
    });
    exe.root_module.link_libcpp = true;

    b.installArtifact(exe);
}
