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
- **其他**：深色主题、诊断模式（详细日志）、启动时检查更新

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

## 目录结构

```
src/remoteripple/
├── __main__.py      # 命令行入口、日志初始化
├── app.py           # 主窗口、会话管理、设置、更新检查
├── viewer.py        # 远程桌面页（画面渲染、输入事件）
├── filetransfer.py  # 文件传输页（本地/远程双栏、拖放）
├── vnc.py           # VNC/RFB 客户端（协议握手、编码解码、键鼠）
├── rfbftp.py        # TightVNC RFB 文件传输会话
├── models.py        # Host/Session 数据模型、地址解析
└── storage.py       # 配置存储、DPAPI 密码加密
```

## 配置与日志

配置保存在用户配置目录（`%APPDATA%/GlavSoft/RemoteRipplePy`）：

- `store.json` — 主机、会话与设置（密码以 `dpapi:` 前缀加密存储）
- `logs/remoteripple.log` — 诊断日志（可通过菜单“文件 → 打开日志目录”打开）

## 许可证

[MIT](LICENSE) © 2026 JustSmartAuto
