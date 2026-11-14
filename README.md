# RemoteRipplePy

Python/Tkinter 编写的远程桌面客户端，连接 VNC（RFB 协议）服务器，提供远程桌面
查看与操作、基于 TightVNC 扩展的文件传输，以及最近会话管理。仅支持 Windows。

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

## 许可证

[MIT](LICENSE) © 2026 JustSmartAuto
