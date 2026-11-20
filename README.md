# ImageRotatePy

基于 Python 3.13、Pillow 和 TkinterDnD2 的桌面图片旋转工具。采用 Tk Win95 风格界面，支持拖入图片或文件夹进行批量处理。

## 功能

- 顺时针旋转 90°、逆时针旋转 90°、旋转 180°。
- 真正旋转图片像素，而非仅修改 EXIF 方向标记。
- 处理前按原 EXIF 方向校正，保存后将方向标记重置为正常方向。
- 直接覆盖原图片，并在首次处理前备份为 `文件名.扩展名.bak`。
- 已有 `.bak` 文件不会被覆盖。
- 支持拖入或选择多个图片、文件夹；文件夹会递归扫描子目录。
- 处理结果保存在各图片的原目录。
- 支持 JPG、JPEG、PNG、BMP、TIFF 和 WebP。

> 注意：JPEG 是有损格式，旋转后重新编码可能产生轻微画质变化。处理前请先确认备份文件已生成。

## 环境要求

- Windows
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## 安装与运行

在项目目录执行：

```bash
uv sync
uv run imagerotatepy
```

也可以运行模块：

```bash
uv run python -m imagerotatepy.app
```

启动后，将图片或文件夹拖入窗口，点击所需旋转角度并确认即可。也可以点击“添加图片”或“添加文件夹”。

## 打包 EXE

```bash
uv sync
uv run python build_exe.py
```

打包结果为单文件 Windows 应用，输出到 `dist/ImageRotatePy_<时间戳>.exe`。应用图标及 TkinterDnD2 拖放资源会一并打包。

## 项目结构

```text
├── assets/rotate.ico          # 应用图标
├── src/imagerotatepy/
│   ├── app.py                 # 应用入口
│   ├── rotate.py              # 图片收集、旋转、备份与保存
│   ├── theme.py               # Win95 风格主题
│   └── ui.py                  # Tk 主界面与拖放交互
├── build_exe.py               # PyInstaller 打包脚本
├── pyproject.toml             # uv 项目与依赖配置
└── rotate.ico                 # 原始圆角图标
```

## 备份示例

处理 `photo.jpg` 后：

```text
photo.jpg       # 已旋转的图片
photo.jpg.bak   # 首次处理前的原始图片
```
