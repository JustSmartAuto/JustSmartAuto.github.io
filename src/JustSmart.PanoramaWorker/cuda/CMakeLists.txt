cmake_minimum_required(VERSION 3.20)
project(panorama_cuda LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
    # 目标卡：RTX 3080 Ti（GA102，sm_86）；可按需改成 75 / 89 等。
    set(CMAKE_CUDA_ARCHITECTURES 86)
endif()

find_package(CUDAToolkit REQUIRED)

add_executable(panorama_cuda
    main.cu
    sift_cuda.cu
    fusion_cuda.cu
    binarize_cuda.cu
    image_io.cpp
)

target_include_directories(panorama_cuda PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/../stb_image
)

if(MSVC)
    target_compile_options(panorama_cuda PRIVATE
        $<$<COMPILE_LANGUAGE:CUDA>:--use_fast_math>
        $<$<COMPILE_LANGUAGE:CXX>:/O2>
    )
else()
    target_compile_options(panorama_cuda PRIVATE
        $<$<COMPILE_LANGUAGE:CUDA>:--use_fast_math>
        $<$<COMPILE_LANGUAGE:CXX>:-O3>
    )
endif()
