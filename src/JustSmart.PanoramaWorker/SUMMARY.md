# seal_circle_detector — 印章圆检测算子

## 项目概述

基于**卡尺法（Caliper Method）**的工业级印章圆检测算子，使用纯 C++17 实现，Zig 作为构建工具，stb_image 作为图像 I/O。支持从 JPG/PNG/BMP 等格式图像中自动检测印章圆形轮廓，输出圆心坐标与半径。

---

## 目录结构

| 文件/目录 | 说明 |
|-----------|------|
| `seal_circle_detector.cpp` | 主程序：卡尺检测 + RANSAC 圆拟合 + 可视化 |
| `build.zig` | Zig 构建脚本，编译 C++ 源文件为可执行文件 |
| `stb_image.h` / `stb_image_write.h` | 单头文件图像读写库（stb） |
| `__init__.py` / `setup.py` | Python 包封装（纯 Python 备用实现入口） |
| `stb_image.py` / `stb_image_write.py` | 纯 Python 标准库图像 I/O 实现 |
| `tech_summary.md` / `tech_summary.html` | 技术文档（Markdown + HTML 双格式） |
| `print_pdf.py` | 使用 Playwright 将 HTML 技术文档打印为 PDF |
| `test.bmp` / `pkg_test.png` | 测试图像 |
| `zig-out/bin/` | 编译输出目录 |

---

## 核心算法

### 1. 卡尺边缘检测（Caliper Edge Detection）

沿圆周均匀放置 360 个径向卡尺（每 1° 一个），在每个卡尺的径向范围内：

1. **双线性插值采样**灰度值，构建 1D 灰度轮廓
2. **中心差分求导**，计算边缘响应强度
3. 根据**极性过滤**（黑到白 / 白到黑 / 任意），选取最强边缘点
4. 将局部坐标**反算**回原始图像坐标系

### 2. 圆拟合

- **RANSAC**：随机采样 3 点几何拟合圆，迭代 2000 次，选取内点最多的模型
- **Kasa 最小二乘**：用所有内点精修圆参数（代数方法，稳定高效）

### 3. 图像预处理流程

```
输入图像 (RGB/RGBA/Gray)
    ↓
RGB → 灰度转换 (0.299R + 0.587G + 0.114B)
    ↓
3×3 高斯模糊降噪
    ↓
卡尺边缘检测 → 边缘点集
    ↓
RANSAC + Kasa 拟合 → 圆心 (cx, cy) + 半径 r
    ↓
可视化输出 (绿色圆环 + 红色十字标记圆心)
```

---

## 编译与运行

### 编译

```bash
zig build
```

输出可执行文件：`zig-out/bin/seal_circle_detector`

### 运行

```bash
# 使用默认参数（图像中心为圆心估计，半径为 min(w,h)*0.4）
./zig-out/bin/seal_circle_detector test.bmp

# 指定初始估计参数
./zig-out/bin/seal_circle_detector seal.jpg 400 300 200
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `image_path` | 输入图像路径（jpg/png/bmp） | 必填 |
| `center_x` | 圆心初始估计 X | 图像宽度 / 2 |
| `center_y` | 圆心初始估计 Y | 图像高度 / 2 |
| `radius` | 半径初始估计 | min(w, h) × 0.4 |

---

## 运行示例

```
加载图像: test.bmp (1046x1038, 3 channels)
初始估计: 圆心=(523.0, 519.0), 半径=415.2
开始印章圆检测...
  检测到 136 个边缘点

检测结果:
  圆心: (438.55, 374.35)
  半径: 382.89
  内点: 60
  结果已保存: test_result.png
```

---

## 关键技术参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `num_calipers` | 360 | 沿圆周卡尺数量（每 1° 一个） |
| `caliper_height` | max(40, radius×0.10) | 径向搜索范围（像素） |
| `threshold` | 15.0 | 边缘强度阈值 |
| `polarity` | 0 | 边缘极性（0=任意, 1=黑到白, -1=白到黑） |
| `RANSAC iterations` | 2000 | 最大迭代次数 |
| `RANSAC threshold` | 5.0 | 内点距离阈值（像素） |

---

## 依赖

- **Zig**（构建工具，兼作 C++ 编译器）
- **stb_image** / **stb_image_write**（单头文件图像 I/O，已内嵌）
- 无 OpenCV、无外部 C++ 标准库依赖

---

## 许可证

MIT License
