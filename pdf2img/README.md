# PDF转图片工具

一个基于 PyMuPDF 与 Tkinter 的 PDF 转图片小工具，支持拖拽导入 PDF 文件，按 600 ppi 将每一页导出为 JPEG 图片，输出到 PDF 所在目录。

## 功能特性

- 拖拽或点击选择 PDF 文件
- 按 600 ppi 高精度渲染每一页
- 导出为 95% 质量的 JPEG（`文件名_pageN.jpg`）
- 多文件依次转换，实时显示进度日志
- 单文件打包为独立 EXE（内置图标）

## 环境要求

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)

## 使用方法

```bash
# 安装依赖
uv sync

# 运行
uv run src/pdf2img/main.py

# 打包为 EXE
uv run pyinstaller pdf2img.spec
```

## 项目结构

```
pdf2img/
├── index.html                 # 项目主页
├── pdf2img.spec               # PyInstaller 打包配置
├── logo.ico                   # 程序图标
├── rthook-pymupdf.py          # PyMuPDF 运行时钩子
├── hook-tkinterdnd2.py        # tkinterdnd2 打包钩子
├── scripts/
│   ├── extract_icon.py        # 图标提取脚本
│   └── hello.py               # uv init 生成的示例脚本
└── src/
    └── pdf2img/
        └── main.py            # 程序入口
```

## License

MIT
