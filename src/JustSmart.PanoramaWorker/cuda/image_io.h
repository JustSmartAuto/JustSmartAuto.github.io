// 基于 stb 的图像读写封装（纯 C++，不依赖 CUDA 运行时）。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

// 读取为 RGB 三通道（每像素 3 字节），失败返回 false。
bool load_rgb(const std::string& path, int* width, int* height,
              std::vector<uint8_t>* rgb);

// 读取为单通道灰度（每像素 1 字节），内部按 stb 的灰度转换。
bool load_gray(const std::string& path, int* width, int* height,
               std::vector<uint8_t>* gray);

// 保存 8bit RGB PNG。
bool save_rgb(const std::string& path, int width, int height,
              const uint8_t* rgb);

// 保存 8bit 单通道 PNG。
bool save_gray(const std::string& path, int width, int height,
               const uint8_t* gray);
