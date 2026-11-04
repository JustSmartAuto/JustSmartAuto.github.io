# OdiffDiff — 图像差分 Diff 器

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
# 产物：dist/OdiffDiff_<时间戳>.exe
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

## 许可证

MIT
