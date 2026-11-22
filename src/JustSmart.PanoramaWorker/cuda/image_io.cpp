// stb 实现单元：整个程序只在这里定义 STB_*_IMPLEMENTATION。
#include "image_io.h"

#define STB_IMAGE_IMPLEMENTATION
#define STB_IMAGE_WRITE_IMPLEMENTATION

#include "../stb_image/stb_image.h"
#include "../stb_image/stb_image_write.h"

#include <cstdio>

bool load_rgb(const std::string& path, int* width, int* height,
              std::vector<uint8_t>* rgb) {
    if (!width || !height || !rgb) return false;
    int w = 0, h = 0, channels = 0;
    stbi_uc* data = stbi_load(path.c_str(), &w, &h, &channels, 3);
    if (!data) {
        std::fprintf(stderr, "stbi_load 失败: %s (%s)\n", path.c_str(),
                     stbi_failure_reason());
        return false;
    }
    *width = w;
    *height = h;
    rgb->assign(data, data + static_cast<size_t>(w) * h * 3);
    stbi_image_free(data);
    return true;
}

bool load_gray(const std::string& path, int* width, int* height,
               std::vector<uint8_t>* gray) {
    if (!width || !height || !gray) return false;
    int w = 0, h = 0, channels = 0;
    stbi_uc* data = stbi_load(path.c_str(), &w, &h, &channels, 1);
    if (!data) {
        std::fprintf(stderr, "stbi_load 失败: %s (%s)\n", path.c_str(),
                     stbi_failure_reason());
        return false;
    }
    *width = w;
    *height = h;
    gray->assign(data, data + static_cast<size_t>(w) * h);
    stbi_image_free(data);
    return true;
}

bool save_rgb(const std::string& path, int width, int height,
              const uint8_t* rgb) {
    if (width <= 0 || height <= 0 || !rgb) return false;
    int ok = stbi_write_png(path.c_str(), width, height, 3, rgb, width * 3);
    if (!ok) std::fprintf(stderr, "stbi_write_png 失败: %s\n", path.c_str());
    return ok != 0;
}

bool save_gray(const std::string& path, int width, int height,
               const uint8_t* gray) {
    if (width <= 0 || height <= 0 || !gray) return false;
    int ok = stbi_write_png(path.c_str(), width, height, 1, gray, width);
    if (!ok) std::fprintf(stderr, "stbi_write_png 失败: %s\n", path.c_str());
    return ok != 0;
}
