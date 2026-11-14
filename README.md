# RemoteRipplePy

Python/Tkinter 编写的远程桌面客户端，连接 VNC（RFB 协议）服务器，提供远程桌面
查看与操作、基于 TightVNC 扩展的文件传输，以及最近会话管理。仅支持 Windows。

当前版本：`26.11.14.15`（版本号规则：`年.月.日.小时`）

## 功能

- **远程桌面**：键鼠控制、滚轮、自动缩放/手动缩放（10%–200%）、全屏、
  截图保存（PNG）、Ctrl+Alt+Del、只读模式、剪贴板双向同步
- **文件传输**：独立的 TightVNC RFB 文件传输会话，支持目录浏览、上传
  （文件/目录）、下载、新建目录、重命名、删除；支持将本地文件或目录
  直接拖入远程文件列表（tkinterdnd2）
- **画面编码**：自动 / ZRLE / Hextile / 原始
- **会话管理**：最近会话列表（搜索、重命名、修改密码、删除）、主机别名、
  连接缩略图、配置导出（JSON，不含密码）
- **密码保护**：VNC 密码使用当前 Windows 用户的 DPAPI 加密保存
- **界面**：Claude 风格暖色主题（暖米白背景 + 涟漪橙红高亮），深色模式，
  全窗口应用圆角涟漪图标
- **其他**：诊断模式（详细日志）、启动时检查更新

## 环境要求

- Windows 10/11
- Python 3.13（>=3.13，<3.14）

## 安装

```bash
pip install -e .
```

依赖：`pythonnet`、`tkinterdnd2`、`pillow`（见 `requirements.txt`）。

## 使用

```bash
remoteripplepy                      # 启动主界面
remoteripplepy 192.168.1.10:5900    # 启动后直接连接指定主机
python -m remoteripple [target]     # 等价写法
```

目标地址支持 `host`、`host:5900`、`host::5900`、`[IPv6]:5900` 等格式，
默认端口 5900。

## 打包

```bash
python build_exe.py                # 单文件版 exe（默认）
python build_exe.py --onedir       # 目录版
python build_exe.py --console      # 保留控制台便于排查
python build_exe.py --name 自定义名称
```

脚本自动创建 `.venv` 并安装依赖，产物输出至 `dist/`，exe 内嵌
`assets/remoteripple.ico` 图标。

## 目录结构

```
src/remoteripple/
├── __main__.py      # 命令行入口、日志初始化
├── app.py           # 主窗口、会话管理、设置、更新检查
├── viewer.py        # 远程桌面页（画面渲染、输入事件）
├── filetransfer.py  # 文件传输页（本地/远程双栏、拖放）
├── vnc.py           # VNC/RFB 客户端（协议握手、编码解码、键鼠）
├── rfbftp.py        # TightVNC RFB 文件传输会话
├── theme.py         # Claude 风格 ttk 主题
├── models.py        # Host/Session 数据模型、地址解析
└── storage.py       # 配置存储、DPAPI 密码加密
```

`assets/` 存放应用图标 `remoteripple.ico` 与 Claude 风格设计规范
`claude-style.css`。

## 配置与日志

配置按以下优先级保存（自动新建）：

1. 环境变量 `REMOTERIPPLEPY_CONFIG_DIR`（如已设置）
2. `D:/JustSmart/RemoteRipplePy`
3. 回退 `%APPDATA%/JustSmart/RemoteRipplePy`

目录内容：

- `store.json` — 主机、会话与设置（密码以 `dpapi:` 前缀加密存储）
- `logs/remoteripple.log` — 诊断日志（可通过菜单“文件 → 打开日志目录”打开）

## 排查经验：VNC 连接黑屏的定位与结论

本项目曾出现"连接成功但画面全黑"的问题（TightVNC Viewer 同一服务器显示正常）。
最终定位过程与结论记录如下，供排查类似协议类问题参考。

### 现象

- 连接握手、认证均成功（`connected` 事件触发），但画面始终黑屏，无报错弹窗
- TightVNC Viewer 连接同一测试机（192.168.10.64:5900）显示正常
- 客户端日志无异常，线程看似"卡住"等待数据

### 定位方法

1. **写最小探针脚本**：绕开客户端，用约 60 行标准库代码（`socket` + `struct`
   + `zlib`）直接完成 RFB 3.8 握手 → SetPixelFormat → SetEncodings →
   FramebufferUpdateRequest，逐字节解析服务器响应
2. **对比正常/异常路径**：探针能解出真实画面，客户端不能 → 问题必然在客户端
   解析代码，而非服务器或网络
3. **十六进制转储 + 字节计数器**：在客户端每个 `_recv` 上打印
   `已消费字节数 + 原始 hex`，与探针逐字节比对，确定流错位的精确偏移
4. **对拍 RFC 规范**：不靠记忆，逐字段核对 RFC 6143 §7.6/§7.7 的消息结构

### 根因结论

**RFC 6143 §7.6.1 的 FramebufferUpdate 头是
`[message-type 1B][padding 1B][number-of-rectangles 2B]`——padding 只有
1 字节。** 原实现误按 3 字节读取，多消耗的 2 字节被当作矩形数的一部分，
导致从服务器第一个更新开始整个字节流永久错位（矩形数被读成 0 或 1024 之类的
垃圾值），画面自然全黑且不报错。

### 顺带发现并修复的同类问题

| 问题 | 位置 | 规范依据 |
| --- | --- | --- |
| Hextile 背景/前景/子矩形颜色只读 3 字节，32bpp 下应为 4 字节 | `vnc.py` | RFC 6143 §7.7.4（PIXEL = bytesPerPixel） |
| ZRLE 调色板 RLE 子编码 130-255 的调色板数误读为 `subencoding` | `vnc.py` | §7.7.5：paletteSize = subencoding − 128 |
| ZRLE plain RLE（128）把长度字节读在 CPIXEL 之前 | `vnc.py` | §7.7.5：像素在前、长度字节在后（255 为续接） |
| ZRLE packed palette 位宽错误（16 项调色板用了 8 bit） | `vnc.py` | §7.7.5：2→1bit、3-4→2bit、5-16→4bit，行按字节对齐 |
| 0×0 光标伪矩形（隐藏光标）被当作非法矩形报错 | `vnc.py` | §7.8.1 |
| "自动"编码偏好把 Raw 排在最前，浪费带宽 | `vnc.py` | §7.5.2：列表顺序即偏好提示 |

### 经验总结

- **协议类黑屏/卡死，先怀疑"多读/少读 N 字节"**：TCP 流式协议一旦错位，
  所有后续解析都是垃圾，且往往不抛异常（长度校验恰好"合法"时）
- **探针 + 字节计数器是最快的定位手段**：全链路打点
  `consumed=N want=X got=Y: hex`，一次运行即可看出在哪个字段开始偏离
- **不要凭记忆实现协议字段**：padding 字节数、像素字节数、字节序这三类细节
  最容易错，必须逐条对 RFC 原文核对
- **同一服务器、同一参数，Tight(16) 与 None(1) 安全类型可能走不同服务端路径**
  （如 Tight 模式下 ServerInit 附带交互能力声明、桌面尺寸变化后等待客户端
  重新全量请求），客户端两条路径都要测

## 许可证

[MIT](LICENSE) © 2026 JustSmartAuto
