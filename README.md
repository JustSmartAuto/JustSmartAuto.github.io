# ImageDiffPy — 图像差分 Diff 器

基于 **Python Tk GUI** + **odiff.exe（Zig SIMD 加速）** 的像素级图像差分对比工具。
左右并排 + Diff 结果三栏同屏对比，支持拖拽加载、Ctrl+滚轮缩放、自动保存结果。

## 功能

- 🖱️ **拖拽加载**：把图片拖入 Base / Compare 面板即可（或点击按钮选择）
- ⚡ **odiff.exe 引擎**：SIMD 加速，毫秒级 diff（支持 SSE2 / AVX2 / AVX512）
- 🔍 **三栏同屏**：Base / Compare / Diff 结果并排，Ctrl+滚轮缩放，左键拖拽平移
- ⚙️ **参数可调**：阈值 (0-1) / 忽略抗锯齿 / Diff Mask 透明底 / 白底叠加 / 差异色
- 💾 **自动保存**：Diff 完成后自动输出到 `D:/JustSmart/ImageDiffPy/Diff/`（回退 APPDATA），命名 `{左图}_diff_{右图}.jpg`（品质 95）
- 📂 **一键打开**结果目录

## 项目结构

```
├── src/odiffdiff/
│   ├── app.py        # 入口
│   ├── ui.py         # 三栏 Tk 主界面（TkinterDnD 拖放 + Win95 主题）
│   ├── odiff.py      # odiff.exe 子进程封装 + parsable-stdout 解析
│   └── theme.py      # Win95 主题（teal 桌面 / 凸起按钮 / navy 标题栏）
├── assets/
│   ├── odiff-windows-x64.exe   # odiff 引擎（运行必需）
│   ├── odiff-icon.png          # 方形圆角 app icon (512×512)
│   └── 02-windows-95.css       # Win95 设计 token
├── legacy/           # 旧版 WPF + Python 存档
├── docs/             # PatInspect 算法文档（PDF 转 Markdown）
├── odiff-main/       # odiff 上游源码（Zig CLI + npm bindings）
├── build_exe.py      # PyInstaller onefile 打包
└── pyproject.toml
```

## 运行

```powershell
# 开发模式
pip install pillow tkinterdnd2
$env:PYTHONPATH = "src"
python -m odiffdiff.app

# 打包（单文件 exe）
pip install pyinstaller pillow
python build_exe.py
# 产物：dist/ImageDiffPy_<时间戳>.exe
```

**注意**：tkinterdnd2 依赖 Tcl/Tk DLL 位数与 Python 一致。如果启动报 "Bad exe format"，拖放功能将自动降级为仅按钮选择，不影响核心 diff 功能。

## Windows 长路径

应用启动时会尝试写注册表 `LongPathsEnabled=1`（需管理员）。应用层面也通过文件名自动截断 + MD5 hash 后缀兜底，确保输出文件名 ≤ 120 字符。

## 技术栈

| 组件 | 技术 |
|------|------|
| UI | Python 3.10+ / Tkinter / TkinterDnD2 |
| 图像处理 | Pillow |
| Diff 引擎 | odiff.exe（Zig + SIMD，[dmtrKovalenko/odiff](https://github.com/dmtrKovalenko/odiff)） |
| 打包 | PyInstaller --onefile --windowed |

## odiff.exe CLI

```
odiff <base> <compare> [diff_output.png] [options]

Options:
  -t, --threshold <0.0-1.0>   差异阈值（默认 0.1）
  --aa, --antialiasing        忽略抗锯齿像素
  --diff-color <#rrggbb>      差异高亮色
  --diff-mask                 仅输出变更像素（透明底）
  --diff-overlay <0.5>        白底叠加
  --parsable-stdout           机器可读输出（count;percentage）
```

## 原理对比：PatInspect vs odiff

本项目的差分引擎 odiff 与 Cognex VisionPro 的 PatInspect（**图案检验**，亦译“图案检测”）都在做逐像素差异检测，但定位和算法模型不同。PatInspect 的完整算法说明见 [docs/PatInspect.md](docs/PatInspect.md)。

### 相同点

- **逐像素比较**：都以一张参考图（PatInspect 的训练图案 / odiff 的 Base）为基准，找出与待检图（运行图像 / Compare）的差异。
- **输出差异图**：把差异像素高亮成结果图，便于后续用 Blob / Histogram 等工具或人眼复核。
- **有灵敏度参数**：PatInspect 用 `A × σ + B` 阈值，odiff 用 0~1 的 `threshold`。
- **可限定比较区域**：PatInspect 通过训练区域 / Pose 限定，odiff 通过 `--ignore` 忽略区域。
- **要求几何对齐**：PatInspect 依赖 PMAlign / Fixture 提供 Pose，odiff 要求两图像素级同尺寸对齐（尺寸不同时按重叠区域比较）。

### 不同点

| 维度 | PatInspect（VisionPro） | odiff（本项目） |
| --- | --- | --- |
| 参考模型 | 统计训练：N 张良品图像逐像素求均值 `p̄(i,j)` 与标准差 `σ(i,j)` | 无训练模型，直接比较 Base / Compare 两张图 |
| 阈值 | 每像素自适应：`Threshold(i,j) = A·σ(i,j) + B` | 全局标量：`max_delta = 35215 × threshold²` |
| 光照鲁棒性 | 内置直方图均衡、均值标准差、Match Tails、Robust Line Fit、局部校正等归一化 | 无归一化，依赖两图采集 / 渲染条件一致 |
| 颜色度量 | 对像素值及其标准差建模，可配合 Sobel 边缘 | 转 YIQ，按 0.5053 / 0.299 / 0.1957 加权平方差 |
| 抗锯齿 | 不专门处理 | 可选用 3×3 邻域同色兄弟像素检测忽略抗锯齿 |
| 差异后处理 | 原始差分超过阈值后减去 offset，输出 thresholded difference | 只标记差异像素并计数，不做二次阈值化 |
| 几何对齐 | PMAlign + PatMax 定位，支持 Pose 传递 | 不做配准；尺寸不同时按重叠区域比较，多余非透明像素计为差异 |
| 加速方式 | 工业运行时（未开源） | Zig SIMD：通用向量 + AVX512 汇编内核 / NEON / RVV，毫秒级 |
| 典型场景 | 产线在线缺陷检测（容忍来料与光照波动） | 截图 / 渲染结果视觉回归（要求像素稳定） |

### 对本项目的启示

- odiff 的定位是“确定性像素回归”，不是“容忍生产波动的缺陷检测”；Base / Compare 应在同一设备、同一参数下采集。
- 若用于工业图像，需先做几何对齐与光照归一化（类似 PMAlign + PatInspect normalization），否则正常波动会被判成缺陷。
- PatInspect 的自适应阈值思路（按局部标准差定阈值）可作为后续增强：高噪声区域放宽、稳定区域收紧。

## 术语对照

**PatInspect** 中的 *Pat* 取自 Pattern（图案），*Inspect* 意为检验，因此准确中文译名为 **“图案检验”**（部分资料译“图案检测”）。Cognex 官方中文资料通常保留英文名，功能上对应“瑕疵 / 缺陷检测”；本文档统一译为“图案检验”。相关术语对照如下（对应 [docs/PatInspect.md](docs/PatInspect.md)）：

| 英文 | 中文 | 备注 |
| --- | --- | --- |
| PatInspect | 图案检验 | 亦译“图案检测”，基于训练图案的缺陷检测工具 |
| trained pattern | 训练图案 | 多张良品图像逐像素统计得到的基准图案 |
| training image | 训练图像 | 用于建立训练图案的良品图像 |
| run-time image | 运行图像 | 实际待检图像，亦称“待检图像” |
| standard deviation image | 标准差图像 | 表示训练图案各像素的允许波动 |
| pseudo-standard deviation image | 伪标准差图像 | 单张训练图时由 Sobel Edge 生成 |
| threshold image | 阈值图像 | 每像素的判定阈值 `A·σ + B` |
| match image | 匹配图像 | 运行图像中与训练区域对应的部分 |
| raw difference image | 原始差分图像 | 训练图案与匹配图像的绝对差 |
| thresholded difference image | 阈值化差分图像 | 超过阈值的差异输出，供 Blob / Histogram 分析 |
| image normalization | 图像归一化 | 补偿运行图像的光照变化 |
| identity | 恒等（不归一化） | 不执行任何归一化 |
| histogram equalization | 直方图均衡化 | 使直方图与训练图案匹配 |
| mean and standard deviation | 均值与标准差 | 匹配直方图的均值与标准差 |
| match tails | 尾部匹配 | 对齐直方图左右尾部 |
| robust line fit | 稳健直线拟合 | 抗大缺陷的拟合方法，耗时更长 |
| local correction | 局部校正 | 按矩形邻域分别归一化 |
| enhanced local correction | 增强局部校正 | 效果更好但更慢 |
| scale coefficient (A) | 比例系数 (A) | 控制边缘对比度 |
| offset coefficient (B) | 偏移系数 (B) | 控制整体亮度 / 判定偏移 |
| Pose | 位姿 | 描述物体位置、旋转、缩放的映射 |
| PMAlign | PMAlign 图案匹配 | 基于 PatMax 的定位工具，为 PatInspect 提供位姿 |
| interpolation mode | 插值模式 | Bilinear（双线性）/ Compatibility（兼容） |
| defect pixel | 缺陷像素 | 超过阈值、被判定为异常的像素 |
| Sobel Edge | Sobel 边缘检测 | 生成伪标准差图像时使用 |

## 许可证

MIT
