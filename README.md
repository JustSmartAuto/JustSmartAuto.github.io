# SvgOverlayPy — SVG 叠加图生成器（图片 + 同名 SVG）

把检测相机输出的「底图 + 标注 SVG」合成为一张**高分辨率叠加图**：按原图 **3 倍 DPI**
栅格化 SVG 文字与图形，输出 `{文件名}_叠加图.jpg`（JPEG 品质 95），方便肉眼核对
焊缝检测的 NG 标注。

- 界面采用 Windows 95 风格（设计 token 来自 `02-windows-95.css`，logo `assets/overlay_icon.png`）
- 渲染基于 pywebview（Windows 上为 WebView2）：合成在页面 `<canvas>` 中完成，
  SVG 文字按 3 倍尺寸栅格化，比 Pillow 直接缩放清晰得多
- 对标参考工程：`DataRouterPy`（工程结构 / uv / PyInstaller 打包方式）

## 效果图

![界面效果图](assets/screenshot1.png)

## 数据规则

- 支持**直接拖入文件夹**到窗口（也支持「打开文件夹…」或命令行 `--folder`）
- 图片格式不限（Pillow 支持即可：BMP / JPG / PNG / TIF / WEBP …）
- **必须存在同名 SVG 才是一对有效数据**：`_NG_01246.bmp` ↔ `_NG_01246.svg`
  （主名匹配不区分大小写；扩展名 `.svg`）
- 没有 SVG 配对的图片计入「缺 SVG」，不参与生成
- 已生成的 `*_叠加图.jpg` 会被自动跳过，不会重复叠加

## 生成规则

1. 底图按 EXIF 方向摆正后以**全分辨率**送入浏览器（浏览器原生格式直接传原始字节，无二次压缩）
2. SVG 重写 `width/height` 为 3 倍尺寸、剥离引用底图的 `<image>` 节点、缺 `viewBox` 时自动补齐
3. canvas 以 `原图宽高 × 3` 合成：白底 → 底图 → SVG，`toDataURL('image/jpeg', 0.95)` 回传
4. 先写 `.part` 临时文件再原子重命名，输出与原图同目录：`{文件名}_叠加图.jpg`（同名覆盖）
5. 例：`438×832` 的 BMP → `1314×2496` 的 `_叠加图.jpg`

## 界面（对标 screenshot1.png）

- **数据文件**列表：文件名 / 格式 / 尺寸 / 输出状态（已有 / 生成中 / 完成 / 失败），点击行切换预览
- **叠加预览**：原图 + SVG 叠加效果实时预览，「叠加层」复选框可单独查看底图
- **开始生成 / 取消**：批量生成，进度条与逐条日志实时反馈
- **打开输出目录**：在资源管理器中打开当前文件夹
- 菜单：文件（打开 / 重新扫描 / 打开输出目录 / 退出）、帮助（使用说明 / 关于）

## 开发 / 运行 / 打包

```bash
uv sync                              # 安装依赖（pywebview / Pillow，dev 组含 PyInstaller）
uv run svg-overlay                   # 启动应用
uv run svg-overlay --folder D:/pics  # 启动时直接载入文件夹
uv run svg-overlay -v                # 调试日志 + 开发者工具
uv run python build_exe.py           # PyInstaller 打包 dist/SvgOverlayPy_{yyyyMMddHHmmss}.exe
```

- 依赖 Python 3.13+；Windows 下需要 WebView2 Runtime（Win10/11 一般已内置）
- 拖放通过 Win32 `WM_DROPFILES` 实现（关闭了 WebView2 内置拖放），可拿到完整文件路径

## 目录

| 路径 | 说明 |
|---|---|
| `assets/02-windows-95.css` | Windows 95 设计 token |
| `assets/overlay_icon.png` | 软件 logo（圆角图标） |
| `assets/index.html` / `app.js` / `app.css` | pywebview 界面（菜单 / 列表 / 预览 / 日志） |
| `assets/_NG_01246.bmp` + `.svg` | 示例数据对 |
| `src/svgoverlay/pairs.py` | 文件夹扫描：图片 ↔ 同名 SVG 配对 |
| `src/svgoverlay/overlay.py` | 载荷准备：底图 dataURL、SVG 重写（3× / 剥离底图引用） |
| `src/svgoverlay/api.py` | pywebview JS API：扫描 / 预览 / 批量生成 / 进度推送 |
| `src/svgoverlay/win32ui.py` | Win32：WM_DROPFILES 拖放、窗口图标 |
| `src/svgoverlay/app.py` | 入口（参数解析 / 事件挂接） |
| `index.html` | 项目介绍页（GitHub Pages 可直接用） |
