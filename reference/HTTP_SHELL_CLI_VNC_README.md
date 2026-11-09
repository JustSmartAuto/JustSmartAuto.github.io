# HTTP Shell CLI + VNC - 远程 Shell & 桌面工具

基于 HTTP 协议的远程 Shell 执行系统，集成 noVNC 远程桌面功能。通过 Go 语言编写的单文件可执行程序，在工控机侧启动 HTTP 服务端接收命令和提供 VNC 远程桌面，在开发机侧启动客户端实现类似 SSH 的交互式体验。

**当前版本 v2.4**：在 v2.3 基础上增强 VNC 兼容性，支持自动检测并使用系统中已运行的其他 VNC Server。

---

## 功能特性

- **零依赖部署**：单文件可执行程序，无需安装运行时
- **双角色一体**：同一个程序既可当服务端也可当客户端
- **Shell 自动检测**：按 `pwsh → powershell → cmd` 优先级检测 Windows Shell
- **编码自适应**：自动处理 Windows GBK/UTF-8 编码问题
- **命令超时控制**：超时后主动 Kill 进程
- **CORS 跨域支持**：允许浏览器端直接调用 API
- **文件上传/下载**：通过 multipart/form-data 实现文件传输
- **rsync 双向增量同步**：
  - `rsync` 命令：本地→远程增量同步（上传）
  - `rsync-pull` 命令：远程→本地增量拉取（下载）
  - 基于 Adler-32 + MD5 双阶段校验的块级 delta 同步
  - 传输失败自动回退到全量传输
- **RFB 文件传输**：支持断点续传（offset 参数）、MD5 校验、目录列表
- **服务端日志**：自动记录操作日志到 `http_shell_cli_{yyyyMMddHHmmssfff}.log`
- **VNC 远程桌面**（Windows 服务端）：
  - **兼容已有 VNC Server**：自动检测 RealVNC/TightVNC/UltraVNC 等（端口 5900-5909, 15900-15909），直接复用
  - 内嵌 TightVNC Server，无其他 VNC 时自动释放并运行
  - 内嵌 noVNC 1.4.0，浏览器直接访问
  - WebSocket 代理桥接，无需额外配置
  - 自动检测并递增端口避免冲突

---

## 项目结构

```
http_shell_cli_vnc/
├── http_shell_cli_vnc.go      # 主程序（服务端+客户端+rsync+RFB+VNC）
├── http_shell_cli_windows.go  # Windows 平台专用（GBK转码、VT颜色、隐藏窗口）
├── http_shell_cli_unix.go     # Unix/Linux/macOS 平台空实现
├── public_fs/
│   ├── public_fs.go           # embed 静态文件定义
│   ├── novnc/                 # noVNC 1.4.0 前端文件
│   └── tvnc/                  # TightVNC Server 二进制
│       ├── tvnserver.exe
│       ├── hookldr.exe
│       ├── screenhooks32.dll
│       ├── screenhooks64.dll
│       └── tvnviewer.exe
├── vnc_proxy/
│   ├── proxy.go               # WebSocket VNC 代理
│   └── peer.go                # VNC 连接对等体
├── go.mod                     # Go 模块定义
├── go.sum                     # Go 依赖校验
├── build.ps1                  # PowerShell 打包脚本（带时间戳）
├── build.bat                  # CMD 打包脚本（带时间戳）
└── README.md                  # 本文档
```

---

## 快速开始

### 1. 初始化并编译

```bash
# 进入项目目录
cd http_shell_cli_vnc

# 下载依赖
go mod tidy

# 编译当前平台版本
go build -o http_shell_cli_vnc.exe .

# 或使用打包脚本（自动添加时间戳后缀）
.\build.ps1        # PowerShell
.\build.bat        # CMD
```

打包脚本会生成带时间戳的可执行文件，例如：`http_shell_cli_vnc_20260519_132801.exe`

### 2. 启动服务端（工控机）

```bash
# 交互式选择角色
.\http_shell_cli_vnc.exe

# 或直接指定角色
.\http_shell_cli_vnc.exe -role server -host 0.0.0.0 -port 10022
```

Windows 服务端启动时会自动：
1. **检测是否已有 VNC Server 运行**（兼容模式）
   - 扫描端口 5900-5909 和 15900-15909
   - 如果检测到 RealVNC/TightVNC/UltraVNC 等，直接复用，跳过注册表和 tvnserver 启动
2. 无其他 VNC 时，进入内嵌模式：
   - 检测并递增 RFB 端口（默认 15900）避免冲突
   - 释放内嵌的 TightVNC Server 到 `.cache/tvnc/`
   - 写入注册表配置（需要管理员权限）
   - 启动 tvnserver 服务
3. 启动 noVNC HTTP 服务和 WebSocket 代理

### 3. 连接客户端（开发机）

```bash
# 交互式模式（类似 SSH）
.\http_shell_cli_vnc.exe -role client -url http://192.168.1.100:10022

# 单次执行模式
.\http_shell_cli_vnc.exe -role client -url http://192.168.1.100:10022 -c "Get-Location"
```

### 4. 访问 VNC 远程桌面

在浏览器中打开：
```
http://192.168.1.100:10022/vnc/vnc.html
```

无需密码，直接连接即可看到远程桌面。

---

## API 接口

### POST /exec

执行 Shell 命令。

**请求体：**
```json
{
  "command": "Get-ChildItem",
  "timeout": 30,
  "work_dir": "C:\\Users"
}
```

**响应：**
```json
{
  "status": "success",
  "stdout": "...",
  "stderr": "",
  "exit_code": 0,
  "timeout": false,
  "command": "Get-ChildItem"
}
```

### POST /upload

上传文件到服务端。

**参数：**
- `file` (file, 必填)：要上传的文件
- `path` (string, 可选)：服务端保存路径

**curl 示例：**
```bash
curl -X POST http://192.168.1.100:10022/upload \
  -F "file=@local.txt" \
  -F "path=C:/remote/dir/"
```

### POST /download

从服务端下载文件。

**参数：**
- `path` (string, 必填)：服务端文件路径

**curl 示例：**
```bash
curl -X POST http://192.168.1.100:10022/download \
  -F "path=C:/remote/dir/file.txt" \
  -o local.txt
```

### Rsync 增量同步接口

#### POST /rsync/blockset

获取文件块集合。

**请求体：**
```json
{
  "path": "/remote/file.dat",
  "seed": 12345
}
```

#### POST /rsync/delta

计算增量数据。

**Push 模式（默认）：**
```json
{
  "src_path": "/local/file.dat",
  "dst_path": "/remote/file.dat",
  "seed": 12345
}
```

**Pull 模式：**
```json
{
  "src_path": "/remote/file.dat",
  "dst_path": "/local/file.dat",
  "seed": 12345,
  "direction": "pull",
  "local_bs": { ... }
}
```

#### POST /rsync/apply

应用增量数据重建文件。

**请求体：**
```json
{
  "dst_path": "/remote/file.dat",
  "src_path": "/remote/file.dat",
  "chunks": [
    {"op": 1, "length": 256, "data": "..."},
    {"op": 2, "length": 5},
    {"op": 0}
  ]
}
```

### RFB 文件传输接口

#### POST /rfb/file/list

列出远程目录内容。

#### POST /rfb/file/download

下载文件（支持断点续传）。

**请求体：**
```json
{
  "path": "/remote/file.dat",
  "offset": 0
}
```

#### POST /rfb/file/upload

上传文件（支持断点续传）。

**参数：**
- `file` (file, 必填)
- `path` (string, 必填)
- `offset` (string, 可选)

#### POST /rfb/file/md5

计算文件 MD5 校验值。

### VNC 远程桌面接口

#### GET /vnc/

noVNC 静态文件服务。访问 `/vnc/vnc.html` 打开远程桌面页面。

#### GET /websockify

VNC WebSocket 代理端点。noVNC 通过此端点与 TightVNC Server 通信。

### GET /health

检查服务状态。

---

## 客户端内置命令

| 命令 | 说明 |
|------|------|
| `exit`, `quit`, `q` | 退出客户端 |
| `cd <目录>` | 设置后续命令的工作目录 |
| `pwd` | 显示当前工作目录 |
| `clear`, `cls` | 清屏 |
| `!<命令>` | 执行本地命令（不发送到服务端） |
| `upload <本地路径> [远程路径]` | 上传文件 |
| `download <远程路径> [本地路径]` | 下载文件 |
| `rsync <本地路径> [远程路径]` | rsync 增量同步（本地→远程） |
| `rsync-pull <远程路径> [本地路径]` | rsync 增量拉取（远程→本地） |
| `rfb-upload <本地路径> [远程路径]` | RFB 断点续传上传 |
| `rfb-download <远程路径> [本地路径]` | RFB 断点续传下载 |
| `rfb-list [目录]` | RFB 列出远程目录 |
| `help`, `?` | 显示帮助 |

---

## VNC 远程桌面说明

### 工作原理

```
浏览器(noVNC)  <--WebSocket-->  /websockify  <--TCP-->  VNC Server(127.0.0.1:5900+ 或 15900+)
```

1. 服务端启动时，优先检测系统中是否已有 VNC Server（端口 5900-5909, 15900-15909）
2. 如果检测到已有 VNC Server，直接进入兼容模式，WebSocket 代理连接到已有服务
3. 如果没有检测到，释放内嵌的 TightVNC Server 到 `.cache/tvnc/`，写入注册表，启动 tvnserver
4. HTTP 服务提供 noVNC 静态页面（`/vnc/`）
5. WebSocket 代理（`/websockify`）桥接浏览器与 VNC Server

### 端口说明

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| HTTP Shell | 10022 | 主 HTTP 服务端口（可配置） |
| VNC RFB | 15900 | TightVNC Server 端口（自动递增） |
| VNC HTTP | 9091 | noVNC 页面端口（与主服务复用） |

### 注意事项

- **仅 Windows 服务端支持 VNC**：Linux/macOS 服务端不启动 VNC 相关组件
- **兼容已有 VNC Server**：支持 RealVNC、TightVNC、UltraVNC 等，无需管理员权限即可复用
- **内嵌模式需要管理员权限**：首次运行内嵌 TightVNC 需要管理员权限写入注册表和安装服务
- **端口冲突自动处理**：如果 15900 被占用，自动递增到可用端口
- **VNC 服务监控**：如果内嵌 tvnserver 进程意外退出，程序会自动退出

---

## Rsync 增量同步原理

### 块大小计算

```
块大小 = max(700, ceil(sqrt(文件大小)) 向上取整到 8 的倍数)
```

| 文件大小 | 块大小 | 块数量 |
|---------|--------|--------|
| 100 KB | 700 B | 147 |
| 1 MB | 1,024 B | 1,024 |
| 10 MB | 2,048 B | 5,120 |
| 100 MB | 3,600 B | 28,445 |
| 1 GB | 8,192 B | 131,072 |

### 双阶段校验和

| 阶段 | 校验和 | 长度 | 用途 |
|------|--------|------|------|
| 第一阶段 | Adler-32 滚动校验和 | 4 字节 | 快速滚动比较，O(1) 滑动窗口更新 |
| 第二阶段 | MD5 强校验和 | 16 字节 | 强验证，混入随机种子防止哈希碰撞 |

### 自动回退机制

rsync 在以下情况自动回退到全量传输：
- 远程文件不存在
- blockset 获取失败 / delta 计算失败 / apply 失败
- MD5 验证不匹配
- 增量效率 < 20%（且文件 > 1MB）

---

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HTTP_SHELL_PORT` | `10022` | 服务端监听端口 |
| `HTTP_SHELL_HOST` | `0.0.0.0` | 服务端监听地址 |
| `HTTP_SHELL_TIMEOUT` | `30` | 默认命令超时秒数 |

---

## 更新日志

### v2.4
- 增强 VNC 兼容性：自动检测并使用系统中已运行的 VNC Server
- 支持 RealVNC、TightVNC、UltraVNC 等常见 VNC 服务端
- 无需管理员权限即可在已有 VNC 环境下运行

### v2.3
- 新增 VNC 远程桌面功能（TightVNC Server + noVNC + WebSocket 代理）
- 服务端启动时自动初始化 VNC 服务（Windows）
- 打包脚本添加时间戳后缀

### v2.2
- 新增服务端日志功能，自动记录到时间戳命名的 `.log` 文件

### v2.1
- 新增 rsync 双向增量同步
- 新增 RFB 文件传输协议（含断点续传）

---

## 适用场景

- 离线内网工控机远程维护
- 无法安装 SSH 的封闭系统
- 临时性的命令执行需求
- 跨平台（Windows/Linux）统一工具
- 大文件增量同步（rsync 模式）
- 不稳定网络下的文件传输（RFB 断点续传模式）
- **需要图形化远程桌面的工控机维护**（VNC 模式）

## 不适用场景

- 公网暴露的生产环境
- 高并发批量命令执行
- 需要交互式 TUI 程序（如 vim）

---

## 安全提示

本服务设计用于**离线内网环境**，不提供身份认证。如需增强安全性，建议：
- 在前端增加反向代理（如 Nginx）配置 Basic Auth 或 IP 白名单
- 通过 VPN 或专线传输
- 对敏感文件传输启用 TLS（HTTPS）
- VNC 远程桌面默认无密码，建议在受控网络中使用

---

## 打包说明

### 使用脚本打包

```powershell
# PowerShell
.\build.ps1

# CMD
.\build.bat
```

输出示例：`http_shell_cli_vnc_20260519_132801.exe`（约 12MB）

### 手动打包

```bash
# 整理依赖
go mod tidy

# 编译（带时间戳）
go build -ldflags "-s -w" -o http_shell_cli_vnc_$(date +%Y%m%d_%H%M%S).exe .
```

### 交叉编译

```bash
# 交叉编译 Windows 版本（在 Linux/macOS 上）
GOOS=windows GOARCH=amd64 go build -ldflags "-s -w" -o http_shell_cli_vnc.exe .

# 交叉编译 Linux 版本
GOOS=linux GOARCH=amd64 go build -ldflags "-s -w" -o http_shell_cli_vnc .
```

---

## 依赖说明

| 依赖 | 用途 |
|------|------|
| `golang.org/x/net/websocket` | noVNC WebSocket 代理 |
| `golang.org/x/sys/windows/registry` | Windows 注册表操作（VNC 配置） |
| `golang.org/x/text/encoding/simplifiedchinese` | Windows GBK 编码转换 |
| `github.com/evangwt/go-bufcopy` | VNC 代理数据拷贝 |
| `github.com/pkg/errors` | 错误处理增强 |

---

## License

MIT License

## 源码
**go.mod**
```go-mod
module http_shell_cli_vnc

go 1.22

require (
	github.com/evangwt/go-bufcopy v0.1.1
	github.com/pkg/errors v0.9.1
	golang.org/x/net v0.25.0
	golang.org/x/sys v0.20.0
	golang.org/x/text v0.15.0
)

```

**go.sum**
```go-sum
github.com/evangwt/go-bufcopy v0.1.1 h1:dJDsc/n+rDQj1jEQueddij8Lok4XSy3jBEjCNztqdDE=
github.com/evangwt/go-bufcopy v0.1.1/go.mod h1:0rJeA3+mLjd/WmJmDjZdNU1dZZAxlzW2+VDgGrxFzL8=
github.com/pkg/errors v0.9.1 h1:FEBLx1zS214owpjy7qsBeixbURkuhQAwrK5UwLGTwt4=
github.com/pkg/errors v0.9.1/go.mod h1:bwawxfHBFNV+L2hUp1rHADufV3IMtnDRdf1r5NINEl0=
golang.org/x/net v0.25.0 h1:d/OCCoBEUq33pjydKrGQhw7IlUPI2Oylr+8qLx49kac=
golang.org/x/net v0.25.0/go.mod h1:JkAGAh7GEvH74S6FOH42FLoXpXbE/aqXSrIQjXgsiwM=
golang.org/x/sys v0.20.0 h1:Od9JTbYCk261bKm4M/mw7AklTlFYIa0bIp9BgSm1S8Y=
golang.org/x/sys v0.20.0/go.mod h1:/VUhepiaJMQUp4+oa/7Zr1D23ma6VTLIYjOOTFZPUcA=
golang.org/x/text v0.15.0 h1:h1V/4gjBv8v9cjcR6+AR5+/cIYK5N/WAgiv4xlsEtAk=
golang.org/x/text v0.15.0/go.mod h1:18ZOQIKpY8NJVqYksKHtTdi31H5itFRjB5/qKTNYzSU=

```

**http_shell_cli_unix.go**
```go
//go:build !windows

package main

import (
	"os/exec"
	"syscall"
)

// enableWindowsVT Unix 平台空实现
func enableWindowsVT() {}

// setHideWindow Unix 平台空实现
func setHideWindow(cmd interface{}) {
	if c, ok := cmd.(*exec.Cmd); ok {
		c.SysProcAttr = &syscall.SysProcAttr{}
	}
}

// windowsGbkToUTF8 Unix 平台直接返回原字符串
func windowsGbkToUTF8(data []byte) string {
	return string(data)
}

// IsWindows 返回 false 表示当前不是 Windows 平台
func IsWindows() bool {
	return false
}

```

**http_shell_cli_vnc.go**
```go
package main

import (
	"bufio"
	"bytes"
	"crypto/md5"
	"embed"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"math"
	"mime"
	"mime/multipart"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/net/websocket"
	"golang.org/x/sys/windows/registry"

	"http_shell_cli_vnc/public_fs"
	"http_shell_cli_vnc/vnc_proxy"
)

// ==================== 配置常量 ====================
const (
	DefaultPort    = 10022
	DefaultHost    = "0.0.0.0"
	DefaultTimeout = 30
	UserAgent      = "HTTP-Shell-CLI/2.3"

	// Rsync 协议常量
	RSYNC_BLOCK_MIN = 700
	RSYNC_TAB_SIZE  = 65536
)

// ==================== VNC 配置 ====================
type TightVNCSettings struct {
	QueryTimeout                uint32
	QueryAcceptOnTimeout        uint32
	LocalInputPriorityTimeout   uint32
	LocalInputPriority          uint32
	BlockRemoteInput            uint32
	BlockLocalInput             uint32
	ExtraPorts                  string
	IpAccessControl             string
	RfbPort                     uint32
	HttpPort                    uint32
	DisconnectAction            uint32
	AcceptRfbConnections        uint32
	UseVncAuthentication        uint32
	UseControlAuthentication    uint32
	RepeatControlAuthentication uint32
	LoopbackOnly                uint32
	AcceptHttpConnections       uint32
	LogLevel                    uint32
	EnableFileTransfers         uint32
	RemoveWallpaper             uint32
	UseD3D                      uint32
	UseMirrorDriver             uint32
	EnableUrlParams             uint32
	AlwaysShared                uint32
	NeverShared                 uint32
	DisconnectClients           uint32
	PollingInterval             uint32
	AllowLoopback               uint32
	VideoRecognitionInterval    uint32
	GrabTransparentWindows      uint32
	SaveLogToAllUsersPath       uint32
	RunControlInterface         uint32
	ConnectToRdp                uint32
	IdleTimeout                 uint32
	VideoClasses                string
	VideoRects                  string
}

var vncSettings = TightVNCSettings{
	QueryTimeout:                0x1e,
	QueryAcceptOnTimeout:        0x0,
	LocalInputPriorityTimeout:   0x3,
	LocalInputPriority:          0x0,
	BlockRemoteInput:            0x0,
	BlockLocalInput:             0x0,
	ExtraPorts:                  "",
	IpAccessControl:             "",
	RfbPort:                     15900,
	HttpPort:                    15901,
	DisconnectAction:            0x0,
	AcceptRfbConnections:        0x1,
	UseVncAuthentication:        0x0,
	UseControlAuthentication:    0x0,
	RepeatControlAuthentication: 0x0,
	LoopbackOnly:                0x0,
	AcceptHttpConnections:       0x0,
	LogLevel:                    0x0,
	EnableFileTransfers:         0x1,
	RemoveWallpaper:             0x1,
	UseD3D:                      0x1,
	UseMirrorDriver:             0x1,
	EnableUrlParams:             0x1,
	AlwaysShared:                0x0,
	NeverShared:                 0x0,
	DisconnectClients:           0x1,
	PollingInterval:             0x3e8,
	AllowLoopback:               0x0,
	VideoRecognitionInterval:    0xbb8,
	GrabTransparentWindows:      0x1,
	SaveLogToAllUsersPath:       0x0,
	RunControlInterface:         0x1,
	ConnectToRdp:                0x0,
	IdleTimeout:                 0x0,
	VideoClasses:                "",
	VideoRects:                  "",
}

var vncHttpPort = 9091 // noVNC HTTP 服务端口（与主服务复用）

// ==================== 日志功能 ====================
var (
	logFile   *os.File
	logMu     sync.Mutex
	logOpened bool
)

func openLogFile() {
	logMu.Lock()
	defer logMu.Unlock()
	if logOpened {
		return
	}
	timestamp := time.Now().Format("20060102150405.000")
	// 去掉毫秒中的小数点
	timestamp = strings.ReplaceAll(timestamp, ".", "")
	logName := fmt.Sprintf("http_shell_cli_%s.log", timestamp)
	f, err := os.OpenFile(logName, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[日志] 打开日志文件失败: %v\n", err)
		return
	}
	logFile = f
	logOpened = true
}

func logPrintf(format string, v ...interface{}) {
	logMu.Lock()
	defer logMu.Unlock()
	if !logOpened || logFile == nil {
		return
	}
	timestamp := time.Now().Format("2006-01-02 15:04:05.000")
	msg := fmt.Sprintf(format, v...)
	fmt.Fprintf(logFile, "[%s] %s\n", timestamp, msg)
	logFile.Sync()
}

func logClose() {
	logMu.Lock()
	defer logMu.Unlock()
	if logFile != nil {
		logFile.Close()
		logFile = nil
	}
	logOpened = false
}

// ==================== 颜色输出 ====================
type Colors struct {
	Green  string
	Red    string
	Yellow string
	Blue   string
	Cyan   string
	Gray   string
	Reset  string
	Bold   string
}

var colors Colors

func initColors() {
	if runtime.GOOS == "windows" {
		enableWindowsVT()
	}
	colors = Colors{
		Green:  "\033[92m",
		Red:    "\033[91m",
		Yellow: "\033[93m",
		Blue:   "\033[94m",
		Cyan:   "\033[96m",
		Gray:   "\033[90m",
		Reset:  "\033[0m",
		Bold:   "\033[1m",
	}
}

// ==================== Shell 执行引擎 (服务端) ====================
type ShellExecutor struct {
	ShellCmd string
}

func NewShellExecutor() *ShellExecutor {
	shell := detectShell()
	fmt.Printf("[server] 检测到Shell: %s\n", shell)
	return &ShellExecutor{ShellCmd: shell}
}

func detectShell() string {
	if runtime.GOOS == "windows" {
		for _, cmd := range []string{"pwsh.exe", "powershell.exe", "cmd.exe"} {
			if path, err := exec.LookPath(cmd); err == nil {
				return path
			}
		}
		return "cmd.exe"
	}
	for _, cmd := range []string{"bash", "sh", "zsh"} {
		if path, err := exec.LookPath(cmd); err == nil {
			return path
		}
	}
	return "/bin/sh"
}

func (se *ShellExecutor) isPowerShell() bool {
	s := strings.ToLower(se.ShellCmd)
	return strings.Contains(s, "powershell") || strings.Contains(s, "pwsh")
}

func (se *ShellExecutor) isCmd() bool {
	return strings.Contains(strings.ToLower(se.ShellCmd), "cmd.exe")
}

func (se *ShellExecutor) buildCommand(command string) []string {
	if se.isPowerShell() {
		return []string{se.ShellCmd, "-NoProfile", "-Command", command}
	} else if se.isCmd() {
		return []string{se.ShellCmd, "/C", command}
	}
	return []string{se.ShellCmd, "-c", command}
}

func (se *ShellExecutor) getEncoding() string {
	if runtime.GOOS == "windows" {
		if se.isCmd() {
			return "gbk"
		}
		return "utf-8"
	}
	return "utf-8"
}

type ExecResult struct {
	Status   string `json:"status"`
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
	ExitCode int    `json:"exit_code"`
	Timeout  bool   `json:"timeout"`
	Command  string `json:"command"`
}

func (se *ShellExecutor) Execute(command string, timeoutSec int, workDir string) ExecResult {
	if strings.TrimSpace(command) == "" {
		return ExecResult{
			Status:   "error",
			Stderr:   "空命令",
			ExitCode: -1,
			Command:  command,
		}
	}

	cmdline := se.buildCommand(command)
	stdout, stderr, exitCode, timedOut := executeWithTimeout(cmdline, timeoutSec, workDir)

	encoding := se.getEncoding()
	stdout = decodeOutput([]byte(stdout), encoding)
	stderr = decodeOutput([]byte(stderr), encoding)

	status := "success"
	if exitCode != 0 {
		status = "error"
	}

	return ExecResult{
		Status:   status,
		Stdout:   stdout,
		Stderr:   stderr,
		ExitCode: exitCode,
		Timeout:  timedOut,
		Command:  command,
	}
}

func executeWithTimeout(cmdline []string, timeoutSec int, workDir string) (stdout, stderr string, exitCode int, timedOut bool) {
	cmd := exec.Command(cmdline[0], cmdline[1:]...)
	if workDir != "" {
		if info, err := os.Stat(workDir); err == nil && info.IsDir() {
			cmd.Dir = workDir
		}
	}
	setHideWindow(cmd)

	var stdoutBuf, stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	done := make(chan error, 1)
	if err := cmd.Start(); err != nil {
		return "", err.Error(), -1, false
	}

	go func() { done <- cmd.Wait() }()

	select {
	case err := <-done:
		if err != nil {
			if exitError, ok := err.(*exec.ExitError); ok {
				exitCode = exitError.ExitCode()
			} else {
				exitCode = -1
				stderr = err.Error()
			}
		}
	case <-time.After(time.Duration(timeoutSec) * time.Second):
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
		<-done
		timedOut = true
		exitCode = -1
		stderr = fmt.Sprintf("命令执行超时（>%d秒）", timeoutSec)
	}

	return stdoutBuf.String(), stderrBuf.String(), exitCode, timedOut
}

func decodeOutput(data []byte, encoding string) string {
	if encoding == "gbk" && len(data) > 0 {
		if isValidUTF8(data) {
			return string(data)
		}
		return gbkToUTF8(data)
	}
	return string(data)
}

func isValidUTF8(data []byte) bool {
	for i := 0; i < len(data); {
		if data[i] < 0x80 {
			i++
			continue
		}
		if i+1 >= len(data) {
			return false
		}
		if data[i] < 0xE0 {
			if data[i] < 0xC2 || data[i+1] < 0x80 || data[i+1] > 0xBF {
				return false
			}
			i += 2
		} else if data[i] < 0xF0 {
			if i+2 >= len(data) {
				return false
			}
			i += 3
		} else {
			return false
		}
	}
	return true
}

func gbkToUTF8(data []byte) string {
	if runtime.GOOS != "windows" || len(data) == 0 {
		return string(data)
	}
	return windowsGbkToUTF8(data)
}


// ==================== VNC 辅助函数 ====================

func getLocalIPv4Address() string {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return ""
	}
	for _, addr := range addrs {
		ipNet, isIpNet := addr.(*net.IPNet)
		if isIpNet && !ipNet.IP.IsLoopback() {
			ipv4 := ipNet.IP.To4()
			if ipv4 != nil && !strings.HasPrefix(ipv4.String(), "169.254") {
				return ipv4.String()
			}
		}
	}
	return ""
}

func extraEmbedFs(EmbedFs embed.FS, fsDir, targetDir string) error {
	if err := os.MkdirAll(targetDir, 0755); err != nil {
		return err
	}

	err := fs.WalkDir(EmbedFs, fsDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !d.IsDir() {
			data, err := fs.ReadFile(EmbedFs, path)
			if err != nil {
				return err
			}

			targetPath := filepath.Join(targetDir, path)
			if err := os.MkdirAll(filepath.Dir(targetPath), 0755); err != nil {
				return err
			}
			if err := os.WriteFile(targetPath, data, 0644); err != nil {
				return err
			}
		}
		return nil
	})

	if err != nil {
		return err
	}
	return nil
}

func initVncReg() error {
	key, _, err := registry.CreateKey(registry.LOCAL_MACHINE, `SOFTWARE\TightVNC\Server`, registry.ALL_ACCESS)
	if err != nil {
		return fmt.Errorf("Error creating/opening key: %v\n", err)
	}
	defer func(key registry.Key) {
		_ = key.Close()
	}(key)

	if err := setRegistryValues(key, vncSettings); err != nil {
		return fmt.Errorf("Error setting registry values: %v\n", err)
	}

	return nil
}

func setRegistryValues(key registry.Key, settings interface{}) error {
	val := reflect.ValueOf(settings)
	typ := val.Type()
	var errs []string

	for i := 0; i < val.NumField(); i++ {
		field := val.Field(i)
		name := typ.Field(i).Name

		switch field.Kind() {
		case reflect.Uint32:
			if err := key.SetDWordValue(name, uint32(field.Uint())); err != nil {
				return err
			}
		case reflect.String:
			if err := key.SetStringValue(name, field.String()); err != nil {
				return err
			}
		default:
			errs = append(errs, fmt.Sprintf("unsupported field type %v", field.Kind()))
		}
	}

	if len(errs) > 0 {
		return fmt.Errorf("unsupported field type %s", strings.Join(errs, ",unsupported field type "))
	}
	return nil
}

func findWindowsPortProcessPID(port string) string {
	cmd := exec.Command("cmd", "/C", "netstat -aon | findstr", fmt.Sprintf(":%s", port))
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	output, err := cmd.Output()
	if err != nil {
		return ""
	}

	lines := strings.Split(string(output), "\n")
	var pid string
	for _, line := range lines {
		if strings.Contains(line, fmt.Sprintf(":%s", port)) {
			parts := strings.Fields(line)
			pid = parts[len(parts)-1]
			break
		}
	}
	return pid
}

func killProcessUsingPort(port string, force bool) error {
	pid := findWindowsPortProcessPID(port)
	if len(pid) < 1 {
		return fmt.Errorf("not find port exe,:%s", port)
	}
	arg := []string{"/PID", pid}
	if force {
		arg = []string{"/F", "/PID", pid}
	}
	killCmd := exec.Command("taskkill", arg...)
	killCmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := killCmd.Run(); err != nil {
		return err
	}
	return nil
}

// findExistingVNCServer 检测系统中是否已有 VNC Server 在运行
// 扫描常见 VNC 端口：5900-5909, 15900-15909
// 返回检测到的端口号，如果没有检测到则返回 0
func findExistingVNCServer() int {
	commonPorts := []int{}
	for p := 5900; p <= 5909; p++ {
		commonPorts = append(commonPorts, p)
	}
	for p := 15900; p <= 15909; p++ {
		commonPorts = append(commonPorts, p)
	}

	for _, port := range commonPorts {
		pid := findWindowsPortProcessPID(fmt.Sprintf("%d", port))
		if pid != "" {
			// 进一步确认该进程是否是 VNC 相关进程
			if isVNCProcess(pid) {
				return port
			}
		}
	}
	return 0
}

// isVNCProcess 根据 PID 判断进程是否是 VNC Server
func isVNCProcess(pid string) bool {
	cmd := exec.Command("tasklist", "/FI", fmt.Sprintf("PID eq %s", pid), "/FO", "CSV", "/NH")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	output, err := cmd.Output()
	if err != nil {
		return false
	}
	lower := strings.ToLower(string(output))
	vncKeywords := []string{"tvnserver", "vncserver", "winvnc", "uvnc", "tightvnc", "realvnc", "vnc"}
	for _, kw := range vncKeywords {
		if strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}

// ==================== Rsync 协议核心实现 ====================

// Block 描述一个文件块
type Block struct {
	Index     uint32 `json:"index"`
	Offset    int64  `json:"offset"`
	Length    int32  `json:"length"`
	FastSum   uint32 `json:"fast_sum"`
	StrongSum []byte `json:"strong_sum"`
}

// BlockSet 一个文件的块集合
type BlockSet struct {
	FileSize  int64   `json:"file_size"`
	BlockSize int32   `json:"block_size"`
	Remainder int32   `json:"remainder"`
	Count     uint32  `json:"count"`
	Blocks    []Block `json:"blocks"`
	Checksum  int     `json:"checksum"`
}

// BlockHashEntry 哈希表条目
type BlockHashEntry struct {
	Block *Block
	Next  *BlockHashEntry
}

// BlockHashTable 65536 桶的链式哈希表
type BlockHashTable struct {
	Buckets [RSYNC_TAB_SIZE]*BlockHashEntry
	Pool    []BlockHashEntry
	poolIdx int
}

// NewBlockHashTable 创建哈希表
func NewBlockHashTable(capacity int) *BlockHashTable {
	return &BlockHashTable{
		Pool: make([]BlockHashEntry, capacity),
	}
}

// Insert 插入块
func (ht *BlockHashTable) Insert(blk *Block) {
	idx := blk.FastSum % RSYNC_TAB_SIZE
	entry := &ht.Pool[ht.poolIdx]
	ht.poolIdx++
	entry.Block = blk
	entry.Next = ht.Buckets[idx]
	ht.Buckets[idx] = entry
}

// Lookup 查找匹配块
func (ht *BlockHashTable) Lookup(fastSum uint32, strongSum []byte, length int32) *Block {
	idx := fastSum % RSYNC_TAB_SIZE
	for e := ht.Buckets[idx]; e != nil; e = e.Next {
		if e.Block.FastSum == fastSum && e.Block.Length == length {
			if len(strongSum) == 0 {
				return e.Block
			}
			// 比较强校验和
			if len(strongSum) == len(e.Block.StrongSum) {
				match := true
				for i := range strongSum {
					if strongSum[i] != e.Block.StrongSum[i] {
						match = false
						break
					}
				}
				if match {
					return e.Block
				}
			}
		}
	}
	return nil
}

// ComputeBlockSize 计算块大小: max(700, ceil(sqrt(fileSize)) 向上取整到8的倍数)
func ComputeBlockSize(fileSize int64) int32 {
	if fileSize < 0 {
		return RSYNC_BLOCK_MIN
	}
	if fileSize >= int64(RSYNC_BLOCK_MIN*RSYNC_BLOCK_MIN) {
		sz := int32(math.Ceil(math.Sqrt(float64(fileSize))))
		if sz%8 != 0 {
			sz += 8 - (sz % 8)
		}
		return sz
	}
	return RSYNC_BLOCK_MIN
}

// Adler32Rolling 滚动 Adler-32 校验和状态
type Adler32Rolling struct {
	s1 uint32
	s2 uint32
}

// Init 初始化 Adler-32
func (a *Adler32Rolling) Init(data []byte) uint32 {
	a.s1 = 0
	a.s2 = 0
	for i := 0; i < len(data); i++ {
		a.s1 += uint32(int8(data[i]))
		a.s2 += a.s1
	}
	return (a.s1 & 0xFFFF) | (a.s2 << 16)
}

// Roll 滚动更新: 移除最左字节，添加新字节
func (a *Adler32Rolling) Roll(outByte byte, inByte byte, blockSize int32) uint32 {
	a.s1 -= uint32(int8(outByte))
	a.s1 += uint32(int8(inByte))
	a.s2 -= uint32(int8(outByte)) * uint32(blockSize)
	a.s2 += a.s1
	return (a.s1 & 0xFFFF) | (a.s2 << 16)
}

// FastChecksum 计算快速校验和
func FastChecksum(data []byte) uint32 {
	var a Adler32Rolling
	return a.Init(data)
}

// StrongChecksum 计算强校验和 (MD5)
func StrongChecksum(data []byte, seed int32) []byte {
	h := md5.New()
	h.Write(data)
	if seed != 0 {
		binary.Write(h, binary.LittleEndian, seed)
	}
	return h.Sum(nil)
}

// BuildBlockSet 为文件构建块集合
func BuildBlockSet(filePath string, seed int32) (*BlockSet, error) {
	info, err := os.Stat(filePath)
	if err != nil {
		return nil, err
	}
	if info.IsDir() {
		return nil, fmt.Errorf("is directory")
	}

	fileSize := info.Size()
	blockSize := ComputeBlockSize(fileSize)

	var count uint32
	var remainder int32
	if fileSize == 0 {
		count = 0
		remainder = 0
	} else {
		count = uint32(fileSize / int64(blockSize))
		remainder = int32(fileSize % int64(blockSize))
		if remainder > 0 {
			count++
		}
	}

	bs := &BlockSet{
		FileSize:  fileSize,
		BlockSize: blockSize,
		Remainder: remainder,
		Count:     count,
		Blocks:    make([]Block, count),
		Checksum:  16, // MD5 = 16 bytes
	}

	if count == 0 {
		return bs, nil
	}

	file, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	buf := make([]byte, blockSize)
	for i := uint32(0); i < count; i++ {
		off := int64(i) * int64(blockSize)
		length := blockSize
		if i == count-1 && remainder > 0 {
			length = remainder
		}
		n, err := file.ReadAt(buf[:length], off)
		if err != nil && err != io.EOF {
			return nil, err
		}
		data := buf[:n]
		bs.Blocks[i] = Block{
			Index:     i,
			Offset:    off,
			Length:    int32(n),
			FastSum:   FastChecksum(data),
			StrongSum: StrongChecksum(data, seed),
		}
	}

	return bs, nil
}

// BuildBlockHashTable 从 BlockSet 构建哈希表
func BuildBlockHashTable(bs *BlockSet) *BlockHashTable {
	ht := NewBlockHashTable(len(bs.Blocks))
	for i := range bs.Blocks {
		ht.Insert(&bs.Blocks[i])
	}
	return ht
}

// DeltaOp 类型常量
const (
	DeltaOpLiteral = 1
	DeltaOpBlock   = 2
	DeltaOpEOF     = 0
)

// DeltaChunk Delta 数据块
type DeltaChunk struct {
	Op     uint8  `json:"op"`
	Length uint32 `json:"length"`
	Data   []byte `json:"data,omitempty"`
}

// ComputeDelta 计算增量数据
func ComputeDelta(filePath string, bs *BlockSet, ht *BlockHashTable, seed int32) ([]DeltaChunk, error) {
	info, err := os.Stat(filePath)
	if err != nil {
		return nil, err
	}
	if info.IsDir() {
		return nil, fmt.Errorf("is directory")
	}
	if info.Size() == 0 {
		return []DeltaChunk{{Op: DeltaOpEOF}}, nil
	}

	file, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	data, err := io.ReadAll(file)
	if err != nil {
		return nil, err
	}

	var chunks []DeltaChunk
	blockSize := bs.BlockSize
	if blockSize == 0 {
		// 目标为空，发送全部字面数据
		if len(data) > 0 {
			chunks = append(chunks, DeltaChunk{Op: DeltaOpLiteral, Length: uint32(len(data)), Data: append([]byte(nil), data...)})
		}
		chunks = append(chunks, DeltaChunk{Op: DeltaOpEOF})
		return chunks, nil
	}

	var literalStart int
	var adler Adler32Rolling
	needReinit := true

	for i := 0; i <= len(data)-int(blockSize); {
		var fastSum uint32
		if needReinit {
			fastSum = adler.Init(data[i : i+int(blockSize)])
			needReinit = false
		} else {
			fastSum = adler.Roll(data[i-1], data[i+int(blockSize)-1], blockSize)
		}

		// 查找匹配块
		var matched *Block
		if i+int(blockSize) <= len(data) {
			strong := StrongChecksum(data[i:i+int(blockSize)], seed)
			matched = ht.Lookup(fastSum, strong, blockSize)
		}

		if matched != nil {
			// 输出之前的字面数据
			if literalStart < i {
				lit := data[literalStart:i]
				chunks = append(chunks, DeltaChunk{Op: DeltaOpLiteral, Length: uint32(len(lit)), Data: append([]byte(nil), lit...)})
			}
			chunks = append(chunks, DeltaChunk{Op: DeltaOpBlock, Length: matched.Index})
			i += int(matched.Length)
			literalStart = i
			needReinit = true // 下一个窗口需要重新初始化
		} else {
			i++
		}
	}

	// 剩余数据作为字面数据
	if literalStart < len(data) {
		lit := data[literalStart:]
		chunks = append(chunks, DeltaChunk{Op: DeltaOpLiteral, Length: uint32(len(lit)), Data: append([]byte(nil), lit...)})
	}

	chunks = append(chunks, DeltaChunk{Op: DeltaOpEOF})
	return chunks, nil
}

// ApplyDelta 应用增量数据重建文件
func ApplyDelta(dstPath string, srcPath string, chunks []DeltaChunk) error {
	tmpPath := dstPath + ".tmp"

	// 如果源文件和目标文件相同，需要特殊处理（Windows 文件锁定）
	sameFile := srcPath != "" && filepath.Clean(dstPath) == filepath.Clean(srcPath)

	// 预读取源文件数据到内存（避免Windows文件锁定问题）
	var srcData []byte
	var srcBlockSize int32
	if srcPath != "" {
		data, err := os.ReadFile(srcPath)
		if err == nil {
			srcData = data
			srcBlockSize = ComputeBlockSize(int64(len(data)))
		}
	}

	out, err := os.Create(tmpPath)
	if err != nil {
		return err
	}
	defer func() {
		out.Close()
		if err != nil {
			os.Remove(tmpPath)
		}
	}()

	for _, chunk := range chunks {
		switch chunk.Op {
		case DeltaOpLiteral:
			if _, err := out.Write(chunk.Data); err != nil {
				return err
			}
		case DeltaOpBlock:
			if len(srcData) == 0 {
				return fmt.Errorf("source file required for block reference")
			}
			if srcBlockSize == 0 {
				return fmt.Errorf("cannot determine block size")
			}
			off := int64(chunk.Length) * int64(srcBlockSize)
			if off >= int64(len(srcData)) {
				return fmt.Errorf("block reference out of range: index=%d, offset=%d, srcLen=%d", chunk.Length, off, len(srcData))
			}
			end := off + int64(srcBlockSize)
			if end > int64(len(srcData)) {
				end = int64(len(srcData))
			}
			if _, err := out.Write(srcData[off:end]); err != nil {
				return err
			}
		case DeltaOpEOF:
			// 结束
		}
	}

	out.Close()

	// Windows: 如果源文件和目标文件相同，先删除目标文件再重命名
	if sameFile {
		if err := os.Remove(dstPath); err != nil {
			// 如果删除失败，尝试直接覆盖
			return copyFile(tmpPath, dstPath)
		}
	}

	if err := os.Rename(tmpPath, dstPath); err != nil {
		return copyFile(tmpPath, dstPath)
	}
	return nil
}

// copyFile 复制文件内容（Windows 回退方案）
func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	if err := os.WriteFile(dst, data, 0644); err != nil {
		return err
	}
	os.Remove(src)
	return nil
}

// RsyncFileInfo rsync 文件信息
type RsyncFileInfo struct {
	Path     string `json:"path"`
	Size     int64  `json:"size"`
	ModTime  int64  `json:"mod_time"`
	Mode     uint32 `json:"mode"`
	Checksum string `json:"checksum,omitempty"`
}

// RsyncSession rsync 会话状态
type RsyncSession struct {
	Seed      int32
	BlockSets map[string]*BlockSet
	mu        sync.RWMutex
}

func NewRsyncSession() *RsyncSession {
	return &RsyncSession{
		Seed:      int32(time.Now().UnixNano()),
		BlockSets: make(map[string]*BlockSet),
	}
}

func (rs *RsyncSession) GetBlockSet(path string) *BlockSet {
	rs.mu.RLock()
	defer rs.mu.RUnlock()
	return rs.BlockSets[path]
}

func (rs *RsyncSession) SetBlockSet(path string, bs *BlockSet) {
	rs.mu.Lock()
	defer rs.mu.Unlock()
	rs.BlockSets[path] = bs
}

// ==================== RFB 协议核心实现 ====================

// RFB 协议常量
const (
	RFBVersionMajor = 3
	RFBVersionMinor = 8

	// 安全类型
	RFBSecurityNone = 1

	// 客户端消息类型
	RFBClientSetPixelFormat     = 0
	RFBClientSetEncodings       = 2
	RFBClientFramebufferUpdate  = 3
	RFBClientKeyEvent           = 4
	RFBClientPointerEvent       = 5
	RFBClientCutText            = 6
	RFBClientFileTransfer       = 7  // UltraVNC 扩展
	RFBClientTightFileTransfer  = 252 // TightVNC 扩展

	// 服务器消息类型
	RFBServerFramebufferUpdate = 0
	RFBServerSetColourMap      = 1
	RFBServerBell              = 2
	RFBServerServerCutText     = 3
	RFBServerFileTransfer      = 7  // UltraVNC 扩展
	RFBServerTightFileTransfer = 252 // TightVNC 扩展

	// Tight 文件传输消息子类型
	RFBFTCCSRST = 0x11 // 压缩支持检查
	RFBFTCFLRST = 0x12 // 文件列表请求/响应
	RFBFTCMDRST = 0x13 // 创建目录
	RFBFTCFRRST = 0x14 // 删除文件
	RFBFTCFMRST = 0x15 // 移动/重命名
	RFBFTCFURST = 0x16 // 开始上传
	RFBFTCUDRST = 0x17 // 上传数据块
	RFBFTCUERST = 0x18 // 上传结束
	RFBFTCFDRST = 0x19 // 开始下载
	RFBFTCDDRST = 0x1A // 下载数据块
	RFBFTCDSRST = 0x1B // 目录大小
	RFBFTCM5RST = 0x1C // MD5 校验
)

// RFBFileTransfer 文件传输信息
type RFBFileTransfer struct {
	ContentType  uint8  `json:"content_type"`
	ContentParam int32  `json:"content_param"`
	Size         int64  `json:"size"`
	Length       uint32 `json:"length"`
	Data         []byte `json:"data,omitempty"`
}

// RFBFileInfo 文件信息
type RFBFileInfo struct {
	Name    string `json:"name"`
	Size    int64  `json:"size"`
	ModTime int64  `json:"mod_time"`
	IsDir   bool   `json:"is_dir"`
	Mode    uint32 `json:"mode"`
}

// RFBSession RFB 会话状态
type RFBSession struct {
	Version     string
	Width       uint16
	Height      uint16
	PixelFormat PixelFormat
	DesktopName string
	Transfers   map[string]*RFBTransferState
	mu          sync.RWMutex
}

// PixelFormat RFB 像素格式
type PixelFormat struct {
	BitsPerPixel uint8
	Depth        uint8
	BigEndian    uint8
	TrueColour   uint8
	RedMax       uint16
	GreenMax     uint16
	BlueMax      uint16
	RedShift     uint8
	GreenShift   uint8
	BlueShift    uint8
}

// RFBTransferState 文件传输状态
type RFBTransferState struct {
	FilePath  string
	File      *os.File
	Offset    int64
	TotalSize int64
	Direction string // "upload" or "download"
	IsActive  bool
}

func NewRFBSession() *RFBSession {
	return &RFBSession{
		Version:     fmt.Sprintf("RFB %03d.%03d\n", RFBVersionMajor, RFBVersionMinor),
		Width:       800,
		Height:      600,
		PixelFormat: DefaultPixelFormat(),
		DesktopName: "HTTP Shell CLI RFB",
		Transfers:   make(map[string]*RFBTransferState),
	}
}

func DefaultPixelFormat() PixelFormat {
	return PixelFormat{
		BitsPerPixel: 32,
		Depth:        24,
		BigEndian:    0,
		TrueColour:   1,
		RedMax:       255,
		GreenMax:     255,
		BlueMax:      255,
		RedShift:     16,
		GreenShift:   8,
		BlueShift:    0,
	}
}

// RFBReadFileTransfer 从 reader 读取文件传输消息
func RFBReadFileTransfer(r io.Reader) (*RFBFileTransfer, error) {
	var ft RFBFileTransfer
	if err := binary.Read(r, binary.BigEndian, &ft.ContentType); err != nil {
		return nil, err
	}
	if err := binary.Read(r, binary.BigEndian, &ft.ContentParam); err != nil {
		return nil, err
	}
	if err := binary.Read(r, binary.BigEndian, &ft.Size); err != nil {
		return nil, err
	}
	if err := binary.Read(r, binary.BigEndian, &ft.Length); err != nil {
		return nil, err
	}
	if ft.Length > 0 {
		ft.Data = make([]byte, ft.Length)
		if _, err := io.ReadFull(r, ft.Data); err != nil {
			return nil, err
		}
	}
	return &ft, nil
}

// RFBWriteFileTransfer 向 writer 写入文件传输消息
func RFBWriteFileTransfer(w io.Writer, ft *RFBFileTransfer) error {
	if err := binary.Write(w, binary.BigEndian, ft.ContentType); err != nil {
		return err
	}
	if err := binary.Write(w, binary.BigEndian, ft.ContentParam); err != nil {
		return err
	}
	if err := binary.Write(w, binary.BigEndian, ft.Size); err != nil {
		return err
	}
	if err := binary.Write(w, binary.BigEndian, ft.Length); err != nil {
		return err
	}
	if ft.Length > 0 && len(ft.Data) > 0 {
		if _, err := w.Write(ft.Data); err != nil {
			return err
		}
	}
	return nil
}

// RFBListDirectory 列出目录内容
func RFBListDirectory(dirPath string) ([]RFBFileInfo, error) {
	entries, err := os.ReadDir(dirPath)
	if err != nil {
		return nil, err
	}

	var files []RFBFileInfo
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			continue
		}
		files = append(files, RFBFileInfo{
			Name:    entry.Name(),
			Size:    info.Size(),
			ModTime: info.ModTime().Unix(),
			IsDir:   entry.IsDir(),
			Mode:    uint32(info.Mode()),
		})
	}
	return files, nil
}

// RFBSecurePath 安全检查路径，防止目录遍历
func RFBSecurePath(root, path string) (string, error) {
	cleanPath := filepath.Clean(path)
	cleanRoot := filepath.Clean(root)

	fullPath := filepath.Join(cleanRoot, cleanPath)
	absPath, err := filepath.Abs(fullPath)
	if err != nil {
		return "", err
	}
	absRoot, err := filepath.Abs(cleanRoot)
	if err != nil {
		return "", err
	}

	if !strings.HasPrefix(absPath, absRoot) {
		return "", fmt.Errorf("path traversal detected: %s", path)
	}
	return absPath, nil
}

// ==================== HTTP 服务端 ====================

const usageHTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>HTTP Shell Proxy</title>
<style>
body { font-family: monospace; max-width: 900px; margin: 40px auto; padding: 20px; background: #1e1e1e; color: #d4d4d4; }
h1 { color: #4ec9b0; border-bottom: 2px solid #4ec9b0; padding-bottom: 10px; }
h2 { color: #569cd6; margin-top: 30px; }
code { background: #2d2d2d; padding: 2px 8px; border-radius: 4px; color: #ce9178; }
pre { background: #2d2d2d; padding: 15px; border-radius: 8px; overflow-x: auto; border-left: 4px solid #4ec9b0; }
table { border-collapse: collapse; width: 100%; margin: 15px 0; }
th, td { border: 1px solid #444; padding: 10px; text-align: left; }
th { background: #2d2d2d; color: #4ec9b0; }
.method { color: #4ec9b0; font-weight: bold; }
.url { color: #ce9178; }
.status-ok { color: #4ec9b0; }
.status-err { color: #f44747; }
</style>
</head>
<body>
<h1>HTTP Shell Proxy 服务</h1>
<p>通过 HTTP 接口远程执行 Shell 命令，支持文件上传下载、rsync 增量同步、RFB 文件传输</p>

<h2>API 接口</h2>

<h3><span class="method">POST</span> <span class="url">/exec</span></h3>
<p>执行 Shell 命令</p>

<h3><span class="method">POST</span> <span class="url">/upload</span></h3>
<p>上传文件（multipart/form-data）</p>

<h3><span class="method">POST</span> <span class="url">/download</span></h3>
<p>下载文件（multipart/form-data）</p>

<h3><span class="method">POST</span> <span class="url">/rsync/blockset</span></h3>
<p>获取文件块集合（用于 rsync 增量同步）</p>
<pre>POST /rsync/blockset
Content-Type: application/json
{"path": "/path/to/file", "seed": 12345}</pre>

<h3><span class="method">POST</span> <span class="url">/rsync/delta</span></h3>
<p>计算增量数据</p>
<pre>POST /rsync/delta
Content-Type: application/json
{"src_path": "/local/file", "dst_path": "/remote/file", "seed": 12345}</pre>

<h3><span class="method">POST</span> <span class="url">/rsync/apply</span></h3>
<p>应用增量数据重建文件</p>

<h3><span class="method">POST</span> <span class="url">/rfb/file/list</span></h3>
<p>RFB 文件传输：列出目录</p>
<pre>POST /rfb/file/list
Content-Type: application/json
{"path": "/remote/dir"}</pre>

<h3><span class="method">POST</span> <span class="url">/rfb/file/download</span></h3>
<p>RFB 文件传输：下载文件（支持断点续传 offset）</p>
<pre>POST /rfb/file/download
Content-Type: application/json
{"path": "/remote/file", "offset": 0}</pre>

<h3><span class="method">POST</span> <span class="url">/rfb/file/upload</span></h3>
<p>RFB 文件传输：上传文件（支持断点续传 offset）</p>
<pre>POST /rfb/file/upload
Content-Type: multipart/form-data
file=@local.bin&path=/remote/file&offset=0</pre>

<h3><span class="method">GET</span> <span class="url">/vnc</span></h3>
<p>noVNC 远程桌面页面</p>

<h3><span class="method">GET</span> <span class="url">/health</span></h3>
<p>检查服务状态</p>

<hr>
<p style="color:#666;">HTTP Shell Proxy | 离线环境专用 | 无认证模式</p>
</body>
</html>
`

type HealthResponse struct {
	Status string `json:"status"`
	Shell  string `json:"shell"`
	Time   string `json:"time"`
}

type ExecRequest struct {
	Command string `json:"command"`
	Timeout int    `json:"timeout"`
	WorkDir string `json:"work_dir"`
}

func runServer(host string, port int, defaultTimeout int) {
	openLogFile()
	defer logClose()

	executor := NewShellExecutor()
	rsyncSession := NewRsyncSession()
	_ = rsyncSession

	logPrintf("[server] 服务启动 | host=%s port=%d timeout=%d", host, port, defaultTimeout)

	// ========== VNC 服务启动 ==========
	var vncProxy *vnc_proxy.Proxy
	if IsWindows() {
		fmt.Println("[server] 正在初始化 VNC 服务...")
		logPrintf("[server] 正在初始化 VNC 服务...")

		// 先检测系统中是否已有其他 VNC Server 在运行（兼容模式）
		existingVncPort := findExistingVNCServer()
		if existingVncPort > 0 {
			vncSettings.RfbPort = uint32(existingVncPort)
			vncProxy = NewVNCProxy()
			fmt.Printf("[server] 检测到已有 VNC Server，使用兼容模式 | RFB端口: %d | noVNC端口: %d\n", vncSettings.RfbPort, vncHttpPort)
			logPrintf("[server] 检测到已有 VNC Server，使用兼容模式 | RFB端口: %d | noVNC端口: %d", vncSettings.RfbPort, vncHttpPort)
		} else {
			// 自动递增端口，避免冲突
			for findWindowsPortProcessPID(fmt.Sprintf("%d", vncSettings.RfbPort)) != "" {
				vncSettings.RfbPort++
				vncSettings.HttpPort++
			}

			// 如果端口被占用，先停止之前的 VNC 服务
			if vncSettings.RfbPort > 15900 {
				_ = exec.Command(".cache/tvnc/tvnserver.exe", "-stop").Run()
				time.Sleep(time.Millisecond * 500)
				_ = killProcessUsingPort(fmt.Sprintf("%d", vncSettings.RfbPort), true)
				_ = killProcessUsingPort(fmt.Sprintf("%d", vncHttpPort), true)
				time.Sleep(time.Second)
			}

			_ = os.RemoveAll(".cache")
			if err := extraEmbedFs(public_fs.EmbedVNC, "tvnc", ".cache"); err != nil {
				fmt.Fprintf(os.Stderr, "[server] 提取 VNC 文件失败: %v\n", err)
				logPrintf("[server] 提取 VNC 文件失败: %v", err)
			} else {
				if err := initVncReg(); err != nil {
					fmt.Fprintf(os.Stderr, "[server] 初始化 VNC 注册表失败: %v\n", err)
					logPrintf("[server] 初始化 VNC 注册表失败: %v", err)
					fmt.Fprintln(os.Stderr, "[server] 提示: 可能需要管理员权限，或系统中已运行其他 VNC Server")
				} else {
					go func() {
						_ = exec.Command(".cache/tvnc/tvnserver.exe", "-install", "-silent").Run()
						for {
							time.Sleep(time.Second)
							if findWindowsPortProcessPID(fmt.Sprintf("%d", vncSettings.RfbPort)) != "" {
								break
							}
							_ = exec.Command(".cache/tvnc/tvnserver.exe", "-start").Run()
						}
						for {
							time.Sleep(time.Second)
							if findWindowsPortProcessPID(fmt.Sprintf("%d", vncSettings.RfbPort)) == "" {
								fmt.Println("[server] VNC 服务已停止，退出程序")
								logPrintf("[server] VNC 服务已停止，退出程序")
								os.Exit(0)
							}
						}
					}()
				}
			}

			vncProxy = NewVNCProxy()
			fmt.Printf("[server] VNC 服务已配置 | RFB端口: %d | noVNC端口: %d\n", vncSettings.RfbPort, vncHttpPort)
			logPrintf("[server] VNC 服务已配置 | RFB端口: %d | noVNC端口: %d", vncSettings.RfbPort, vncHttpPort)
		}
	}

	mux := http.NewServeMux()

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/" || r.URL.Path == "/index" {
			if r.Method == "GET" {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.Write([]byte(usageHTML))
				return
			}
		}
		if r.Method == "OPTIONS" {
			w.Header().Set("Access-Control-Allow-Origin", "*")
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
			w.WriteHeader(200)
			return
		}
		http.NotFound(w, r)
	})

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		resp := HealthResponse{
			Status: "running",
			Shell:  executor.ShellCmd,
			Time:   time.Now().Format("2006-01-02 15:04:05"),
		}
		json.NewEncoder(w).Encode(resp)
	})

	// noVNC 静态文件服务
	noVncHandler := http.FileServer(http.FS(public_fs.EmbedFiles))
	mux.HandleFunc("/vnc/", func(w http.ResponseWriter, r *http.Request) {
		r.URL.Path = "novnc" + strings.TrimPrefix(r.URL.Path, "/vnc")
		noVncHandler.ServeHTTP(w, r)
	})

	// VNC WebSocket 代理
	if vncProxy != nil {
		mux.HandleFunc("/websockify", func(w http.ResponseWriter, r *http.Request) {
			h := websocket.Handler(vncProxy.ServeWS)
			h.ServeHTTP(w, r)
		})
	}

	mux.HandleFunc("/exec", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req ExecRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		command := strings.TrimSpace(req.Command)
		if command == "" {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'command' field"})
			return
		}

		timeout := req.Timeout
		if timeout <= 0 {
			timeout = defaultTimeout
		}

		clientIP := r.RemoteAddr
		userAgent := r.UserAgent()
		if userAgent == "" {
			userAgent = "-"
		}
		nowStr := time.Now().Format("2006-01-02 15:04:05")
		fmt.Printf("[server] [%s] [%s] 执行命令: %s\n", nowStr, clientIP, truncate(command, 80))
		logPrintf("[server] [%s] [%s] 执行命令: %s", nowStr, clientIP, truncate(command, 80))
		startTime := time.Now()
		result := executor.Execute(command, timeout, req.WorkDir)
		elapsed := time.Since(startTime)

		statusIcon := "✓"
		if result.Status != "success" {
			statusIcon = "✗"
		}
		nowStr = time.Now().Format("2006-01-02 15:04:05")
		fmt.Printf("[server] [%s] [%s] 执行结果: %s | 退出码: %d | 耗时: %v\n",
			nowStr, clientIP, statusIcon, result.ExitCode, elapsed)
		logPrintf("[server] [%s] [%s] 执行结果: %s | 退出码: %d | 耗时: %v | stdout=%d stderr=%d",
			nowStr, clientIP, statusIcon, result.ExitCode, elapsed, len(result.Stdout), len(result.Stderr))

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(200)
		json.NewEncoder(w).Encode(result)
	})

	// ===== /upload =====
	mux.HandleFunc("/upload", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		if err := r.ParseMultipartForm(100 << 20); err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid multipart form: " + err.Error()})
			return
		}
		defer r.MultipartForm.RemoveAll()

		file, header, err := r.FormFile("file")
		if err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'file' field: " + err.Error()})
			return
		}
		defer file.Close()

		saveDir := r.FormValue("path")
		if saveDir == "" {
			saveDir = "."
		}

		isDirIntent := strings.HasSuffix(saveDir, "/") || strings.HasSuffix(saveDir, "\\")
		cleanPath := strings.TrimRight(saveDir, "/\\")

		var savePath string
		info, err := os.Stat(cleanPath)
		if err == nil && info.IsDir() {
			savePath = filepath.Join(cleanPath, header.Filename)
		} else if isDirIntent {
			if err := os.MkdirAll(cleanPath, 0755); err != nil {
				w.Header().Set("Content-Type", "application/json; charset=utf-8")
				w.WriteHeader(500)
				json.NewEncoder(w).Encode(map[string]string{"error": "Cannot create directory: " + err.Error()})
				return
			}
			savePath = filepath.Join(cleanPath, header.Filename)
		} else {
			parent := filepath.Dir(cleanPath)
			if parent != "." && parent != "/" {
				if err := os.MkdirAll(parent, 0755); err != nil {
					w.Header().Set("Content-Type", "application/json; charset=utf-8")
					w.WriteHeader(500)
					json.NewEncoder(w).Encode(map[string]string{"error": "Cannot create directory: " + err.Error()})
					return
				}
			}
			savePath = cleanPath
		}

		out, err := os.Create(savePath)
		if err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": "Cannot create file: " + err.Error()})
			return
		}
		defer out.Close()

		size, err := io.Copy(out, file)
		if err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": "Failed to save file: " + err.Error()})
			return
		}

		clientIP := r.RemoteAddr
		nowStr := time.Now().Format("2006-01-02 15:04:05")
		fmt.Printf("[server] [%s] [%s] 上传文件: %s -> %s (%d bytes)\n",
			nowStr, clientIP, header.Filename, savePath, size)
		logPrintf("[server] [%s] [%s] 上传文件: %s -> %s (%d bytes)",
			nowStr, clientIP, header.Filename, savePath, size)

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(200)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":   "success",
			"message":  "文件上传成功",
			"saved_as": savePath,
			"size":     size,
		})
	})

	// ===== /download =====
	mux.HandleFunc("/download", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var filePath string
		contentType := r.Header.Get("Content-Type")
		if strings.Contains(contentType, "multipart/form-data") {
			if err := r.ParseMultipartForm(10 << 20); err != nil {
				w.Header().Set("Content-Type", "application/json; charset=utf-8")
				w.WriteHeader(400)
				json.NewEncoder(w).Encode(map[string]string{"error": "Invalid multipart form: " + err.Error()})
				return
			}
			defer r.MultipartForm.RemoveAll()
			filePath = r.FormValue("path")
		} else {
			if err := r.ParseForm(); err != nil {
				w.Header().Set("Content-Type", "application/json; charset=utf-8")
				w.WriteHeader(400)
				json.NewEncoder(w).Encode(map[string]string{"error": "Invalid form: " + err.Error()})
				return
			}
			filePath = r.PostFormValue("path")
		}

		filePath = strings.TrimSpace(filePath)
		if filePath == "" {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'path' field"})
			return
		}

		info, err := os.Stat(filePath)
		if err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(404)
			json.NewEncoder(w).Encode(map[string]string{"error": "File not found: " + err.Error()})
			return
		}
		if info.IsDir() {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Path is a directory"})
			return
		}

		f, err := os.Open(filePath)
		if err != nil {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": "Cannot open file: " + err.Error()})
			return
		}
		defer f.Close()

		filename := filepath.Base(filePath)
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=\"%s\"", filename))
		w.Header().Set("Content-Length", strconv.FormatInt(info.Size(), 10))

		clientIP := r.RemoteAddr
		nowStr := time.Now().Format("2006-01-02 15:04:05")
		fmt.Printf("[server] [%s] [%s] 下载文件: %s (%d bytes)\n",
			nowStr, clientIP, filePath, info.Size())
		logPrintf("[server] [%s] [%s] 下载文件: %s (%d bytes)",
			nowStr, clientIP, filePath, info.Size())

		io.Copy(w, f)
	})

	// ===== /rsync/blockset =====
	mux.HandleFunc("/rsync/blockset", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req struct {
			Path string `json:"path"`
			Seed int32  `json:"seed"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		if req.Path == "" {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'path' field"})
			return
		}

		bs, err := BuildBlockSet(req.Path, req.Seed)
		if err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		rsyncSession.SetBlockSet(req.Path, bs)

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":       "success",
			"file_size":    bs.FileSize,
			"block_size":   bs.BlockSize,
			"block_count":  bs.Count,
			"remainder":    bs.Remainder,
			"checksum_len": bs.Checksum,
			"blocks":       bs.Blocks,
			"seed":         req.Seed,
		})
	})

	// ===== /rsync/delta =====
	mux.HandleFunc("/rsync/delta", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req struct {
			SrcPath     string    `json:"src_path"`
			DstPath     string    `json:"dst_path"`
			Seed        int32     `json:"seed"`
			Direction   string    `json:"direction"`
			LocalBS     *BlockSet `json:"local_bs,omitempty"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		if req.SrcPath == "" || req.DstPath == "" {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing path fields"})
			return
		}

		var chunks []DeltaChunk
		var err error

		if req.Direction == "pull" && req.LocalBS != nil {
			// Pull 模式：客户端发送本地 blockset，服务端用远程文件(src_path)计算相对于本地文件的 delta
			// 即：服务端计算 "远程文件" 相对于 "本地 blockset" 的差异
			ht := BuildBlockHashTable(req.LocalBS)
			chunks, err = ComputeDelta(req.SrcPath, req.LocalBS, ht, req.Seed)
		} else {
			// Push 模式（默认）：服务端计算 "源文件" 相对于 "目标文件" 的 delta
			bs, err2 := BuildBlockSet(req.DstPath, req.Seed)
			if err2 != nil {
				w.WriteHeader(500)
				json.NewEncoder(w).Encode(map[string]string{"error": err2.Error()})
				return
			}
			ht := BuildBlockHashTable(bs)
			chunks, err = ComputeDelta(req.SrcPath, bs, ht, req.Seed)
		}

		if err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "success",
			"chunks": chunks,
			"seed":   req.Seed,
		})
	})

	// ===== /rsync/apply =====
	mux.HandleFunc("/rsync/apply", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req struct {
			DstPath string       `json:"dst_path"`
			SrcPath string       `json:"src_path"`
			Chunks  []DeltaChunk `json:"chunks"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		if req.DstPath == "" {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'dst_path' field"})
			return
		}

		if err := ApplyDelta(req.DstPath, req.SrcPath, req.Chunks); err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":  "success",
			"message": "增量应用成功",
			"path":    req.DstPath,
		})
	})

	// ===== /rfb/file/list =====
	mux.HandleFunc("/rfb/file/list", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req struct {
			Path string `json:"path"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		if req.Path == "" {
			req.Path = "."
		}

		files, err := RFBListDirectory(req.Path)
		if err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "success",
			"files":  files,
		})
	})

	// ===== /rfb/file/download =====
	mux.HandleFunc("/rfb/file/download", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req struct {
			Path   string `json:"path"`
			Offset int64  `json:"offset"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		if req.Path == "" {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'path' field"})
			return
		}

		info, err := os.Stat(req.Path)
		if err != nil {
			w.WriteHeader(404)
			json.NewEncoder(w).Encode(map[string]string{"error": "File not found"})
			return
		}
		if info.IsDir() {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Path is a directory"})
			return
		}

		f, err := os.Open(req.Path)
		if err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		defer f.Close()

		if req.Offset > 0 {
			if _, err := f.Seek(req.Offset, io.SeekStart); err != nil {
				w.WriteHeader(500)
				json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
				return
			}
		}

		// 分块传输，每块 64KB
		const chunkSize = 64 * 1024
		remaining := info.Size() - req.Offset
		if remaining < 0 {
			remaining = 0
		}

		filename := filepath.Base(req.Path)
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=\"%s\"", filename))
		w.Header().Set("X-File-Size", strconv.FormatInt(info.Size(), 10))
		w.Header().Set("X-File-Offset", strconv.FormatInt(req.Offset, 10))
		w.Header().Set("X-File-Remaining", strconv.FormatInt(remaining, 10))

		buf := make([]byte, chunkSize)
		for remaining > 0 {
			toRead := chunkSize
			if int64(toRead) > remaining {
				toRead = int(remaining)
			}
			n, err := f.Read(buf[:toRead])
			if err != nil && err != io.EOF {
				break
			}
			if n > 0 {
				w.Write(buf[:n])
				remaining -= int64(n)
			}
			if err == io.EOF {
				break
			}
		}
	})

	// ===== /rfb/file/upload =====
	mux.HandleFunc("/rfb/file/upload", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		if err := r.ParseMultipartForm(100 << 20); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid multipart form: " + err.Error()})
			return
		}
		defer r.MultipartForm.RemoveAll()

		file, header, err := r.FormFile("file")
		if err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Missing 'file' field"})
			return
		}
		defer file.Close()

		savePath := r.FormValue("path")
		if savePath == "" {
			savePath = header.Filename
		}

		offsetStr := r.FormValue("offset")
		offset := int64(0)
		if offsetStr != "" {
			offset, _ = strconv.ParseInt(offsetStr, 10, 64)
		}

		var out *os.File
		if offset > 0 {
			out, err = os.OpenFile(savePath, os.O_WRONLY|os.O_CREATE, 0644)
			if err != nil {
				w.WriteHeader(500)
				json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
				return
			}
			out.Seek(offset, io.SeekStart)
		} else {
			out, err = os.Create(savePath)
			if err != nil {
				w.WriteHeader(500)
				json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
				return
			}
		}
		defer out.Close()

		size, err := io.Copy(out, file)
		if err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":   "success",
			"message":  "文件上传成功",
			"saved_as": savePath,
			"size":     size,
			"offset":   offset,
		})
	})

	// ===== /rfb/file/md5 =====
	mux.HandleFunc("/rfb/file/md5", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(200)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			json.NewEncoder(w).Encode(map[string]string{"error": "Method Not Allowed"})
			return
		}

		var req struct {
			Path string `json:"path"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "Invalid JSON"})
			return
		}

		f, err := os.Open(req.Path)
		if err != nil {
			w.WriteHeader(404)
			json.NewEncoder(w).Encode(map[string]string{"error": "File not found"})
			return
		}
		defer f.Close()

		h := md5.New()
		if _, err := io.Copy(h, f); err != nil {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "success",
			"md5":    hex.EncodeToString(h.Sum(nil)),
			"path":   req.Path,
		})
	})

	addr := fmt.Sprintf("%s:%d", host, port)
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[server] 启动失败: %v\n", err)
		logPrintf("[server] 启动失败: %v", err)
		os.Exit(1)
	}

	logPrintf("[server] 服务监听地址: http://%s", addr)

	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("  HTTP Shell Proxy 服务端已启动")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Printf("  监听地址: http://%s\n", addr)
	fmt.Printf("  执行接口: POST http://%s/exec\n", addr)
	fmt.Printf("  文件上传: POST http://%s/upload\n", addr)
	fmt.Printf("  文件下载: POST http://%s/download\n", addr)
	fmt.Printf("  Rsync块集: POST http://%s/rsync/blockset\n", addr)
	fmt.Printf("  Rsync增量: POST http://%s/rsync/delta\n", addr)
	fmt.Printf("  Rsync应用: POST http://%s/rsync/apply\n", addr)
	fmt.Printf("  RFB列表:   POST http://%s/rfb/file/list\n", addr)
	fmt.Printf("  RFB下载:   POST http://%s/rfb/file/download\n", addr)
	fmt.Printf("  RFB上传:   POST http://%s/rfb/file/upload\n", addr)
	fmt.Printf("  RFB校验:   POST http://%s/rfb/file/md5\n", addr)
	if IsWindows() {
		fmt.Printf("  VNC页面:   GET  http://%s/vnc/vnc.html\n", addr)
		fmt.Printf("  VNC代理:   WS   http://%s/websockify\n", addr)
	}
	fmt.Printf("  状态检查: GET  http://%s/health\n", addr)
	fmt.Printf("  使用说明: GET  http://%s/\n", addr)
	fmt.Printf("  默认超时: %d秒\n", defaultTimeout)
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("  按 Ctrl+C 停止服务")
	fmt.Println(strings.Repeat("=", 60))

	server := &http.Server{Handler: mux}
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		fmt.Fprintf(os.Stderr, "[server] 服务异常: %v\n", err)
		logPrintf("[server] 服务异常: %v", err)
		os.Exit(1)
	}
}

func NewVNCProxy() *vnc_proxy.Proxy {
	return vnc_proxy.New(&vnc_proxy.Config{
		TokenHandler: func(r *http.Request) (addr string, err error) {
			defer func() {
				if p := recover(); p != nil {
					debug.PrintStack()
				}
			}()
			return fmt.Sprintf("%s:%d", getLocalIPv4Address(), vncSettings.RfbPort), nil
		},
	})
}

func truncate(s string, maxLen int) string {
	if len(s) > maxLen {
		return s[:maxLen] + "..."
	}
	return s
}

// ==================== HTTP 客户端 ====================
type ShellClient struct {
	BaseURL        string
	Timeout        int
	WorkDir        string
	SessionHistory []string
}

func NewShellClient(baseURL string, timeout int) *ShellClient {
	return &ShellClient{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Timeout: timeout,
	}
}

func (sc *ShellClient) request(path string, data interface{}, method string) (map[string]interface{}, error) {
	url := sc.BaseURL + path
	var body io.Reader
	if data != nil {
		b, err := json.Marshal(data)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(b)
	}

	req, err := http.NewRequest(method, url, body)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("User-Agent", UserAgent)

	client := &http.Client{Timeout: time.Duration(sc.Timeout+5) * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("连接失败: %v", err),
			"exit_code": -1,
		}, nil
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    string(respBody),
			"exit_code": resp.StatusCode,
		}, nil
	}
	return result, nil
}

func (sc *ShellClient) requestRaw(path string, data interface{}, method string) (*http.Response, error) {
	url := sc.BaseURL + path
	var body io.Reader
	if data != nil {
		b, err := json.Marshal(data)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(b)
	}

	req, err := http.NewRequest(method, url, body)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("User-Agent", UserAgent)

	client := &http.Client{Timeout: time.Duration(sc.Timeout+30) * time.Second}
	return client.Do(req)
}

func (sc *ShellClient) checkHealth() map[string]interface{} {
	result, _ := sc.request("/health", nil, "GET")
	return result
}

func (sc *ShellClient) execute(command string, workDir string) map[string]interface{} {
	data := map[string]interface{}{
		"command": command,
		"timeout": sc.Timeout,
	}
	if workDir != "" {
		data["work_dir"] = workDir
	}
	result, _ := sc.request("/exec", data, "POST")
	return result
}

func (sc *ShellClient) upload(localPath, remotePath string) map[string]interface{} {
	file, err := os.Open(localPath)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("打开本地文件失败: %v", err),
			"exit_code": -1,
		}
	}
	defer file.Close()

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)

	filename := filepath.Base(localPath)
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建表单失败: %v", err),
			"exit_code": -1,
		}
	}
	if _, err := io.Copy(part, file); err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("读取文件失败: %v", err),
			"exit_code": -1,
		}
	}

	if remotePath != "" {
		writer.WriteField("path", remotePath)
	}
	writer.Close()

	url := sc.BaseURL + "/upload"
	req, err := http.NewRequest("POST", url, &body)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建请求失败: %v", err),
			"exit_code": -1,
		}
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("User-Agent", UserAgent)

	client := &http.Client{Timeout: time.Duration(sc.Timeout+30) * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("上传失败: %v", err),
			"exit_code": -1,
		}
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    string(respBody),
			"exit_code": resp.StatusCode,
		}
	}
	return result
}

func (sc *ShellClient) download(remotePath, localPath string) map[string]interface{} {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	writer.WriteField("path", remotePath)
	writer.Close()

	url := sc.BaseURL + "/download"
	req, err := http.NewRequest("POST", url, &body)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建请求失败: %v", err),
			"exit_code": -1,
		}
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("User-Agent", UserAgent)

	client := &http.Client{Timeout: time.Duration(sc.Timeout+30) * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("下载失败: %v", err),
			"exit_code": -1,
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		respBody, _ := io.ReadAll(resp.Body)
		var result map[string]interface{}
		if err := json.Unmarshal(respBody, &result); err != nil {
			return map[string]interface{}{
				"status":    "error",
				"stderr":    string(respBody),
				"exit_code": resp.StatusCode,
			}
		}
		return result
	}

	if localPath == "" {
		_, params, _ := mime.ParseMediaType(resp.Header.Get("Content-Disposition"))
		if params["filename"] != "" {
			localPath = params["filename"]
		} else {
			localPath = filepath.Base(remotePath)
		}
	}

	out, err := os.Create(localPath)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建本地文件失败: %v", err),
			"exit_code": -1,
		}
	}
	defer out.Close()

	size, err := io.Copy(out, resp.Body)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("保存文件失败: %v", err),
			"exit_code": -1,
		}
	}

	return map[string]interface{}{
		"status":   "success",
		"message":  "文件下载成功",
		"saved_as": localPath,
		"size":     size,
	}
}

// ===== rsync 客户端方法 =====

func (sc *ShellClient) rsyncBlockSet(remotePath string, seed int32) map[string]interface{} {
	data := map[string]interface{}{
		"path": remotePath,
		"seed": seed,
	}
	result, _ := sc.request("/rsync/blockset", data, "POST")
	return result
}

func (sc *ShellClient) rsyncDelta(srcPath, dstPath string, seed int32) map[string]interface{} {
	data := map[string]interface{}{
		"src_path": srcPath,
		"dst_path": dstPath,
		"seed":     seed,
	}
	result, _ := sc.request("/rsync/delta", data, "POST")
	return result
}

func (sc *ShellClient) rsyncApply(dstPath, srcPath string, chunks []DeltaChunk) map[string]interface{} {
	data := map[string]interface{}{
		"dst_path": dstPath,
		"src_path": srcPath,
		"chunks":   chunks,
	}
	result, _ := sc.request("/rsync/apply", data, "POST")
	return result
}

// RsyncSync 执行完整的 rsync 增量同步（客户端→服务端，上传）
func (sc *ShellClient) RsyncSync(localPath, remotePath string) map[string]interface{} {
	localInfo, err := os.Stat(localPath)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("本地文件不存在: %v", err),
			"exit_code": -1,
		}
	}

	// 1. 检查远程文件是否存在
	remoteInfoResult := sc.rsyncBlockSet(remotePath, 0)
	remoteExists := false
	if status, _ := remoteInfoResult["status"].(string); status == "success" {
		remoteExists = true
	}

	// 如果远程文件不存在，直接上传
	if !remoteExists {
		fmt.Printf("%s远程文件不存在，直接上传...%s\n", colors.Gray, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	// 2. 生成随机种子
	seed := int32(time.Now().UnixNano())

	// 3. 请求远程文件的 blockset
	result := sc.rsyncBlockSet(remotePath, seed)
	if status, _ := result["status"].(string); status != "success" {
		// blockset 获取失败，回退到直接上传
		fmt.Printf("%s获取远程块集失败，回退到直接上传...%s\n", colors.Yellow, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	// 4. 解析远程 blockset
	remoteBS := parseBlockSetFromResult(result)
	if remoteBS == nil {
		fmt.Printf("%s解析远程块集失败，回退到直接上传...%s\n", colors.Yellow, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	// 如果文件大小和时间都相同，跳过同步
	if remoteBS.FileSize == localInfo.Size() {
		// 快速路径：尝试比较MD5
		localMD5 := fileMD5(localPath)
		remoteMD5Result := sc.rfbMD5(remotePath)
		if remoteMD5, ok := remoteMD5Result["md5"].(string); ok && remoteMD5 == localMD5 {
			return map[string]interface{}{
				"status":  "success",
				"message": "文件已是最新，跳过同步",
				"path":    remotePath,
			}
		}
	}

	// 5. 在本地计算 delta（本地文件 vs 远程 blockset）
	ht := BuildBlockHashTable(remoteBS)
	chunks, err := ComputeDelta(localPath, remoteBS, ht, seed)
	if err != nil {
		fmt.Printf("%s计算增量失败，回退到直接上传: %v%s\n", colors.Yellow, err, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	// 6. 统计 delta 效率
	literalSize := uint64(0)
	blockCount := 0
	for _, c := range chunks {
		if c.Op == DeltaOpLiteral {
			literalSize += uint64(len(c.Data))
		} else if c.Op == DeltaOpBlock {
			blockCount++
		}
	}
	efficiency := float64(0)
	if localInfo.Size() > 0 {
		efficiency = float64(localInfo.Size()-int64(literalSize)) / float64(localInfo.Size()) * 100
	}
	fmt.Printf("%s增量分析: %d 个匹配块, %d 字节字面数据, 节省 %.1f%%%s\n",
		colors.Gray, blockCount, literalSize, efficiency, colors.Reset)

	// 如果增量太大（超过80%），直接上传更高效
	if efficiency < 20 && localInfo.Size() > 1024*1024 {
		fmt.Printf("%s增量效率太低，直接上传...%s\n", colors.Yellow, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	// 7. 发送 delta 到服务端应用
	applyResult := sc.rsyncApply(remotePath, remotePath, chunks)
	if status, _ := applyResult["status"].(string); status != "success" {
		// 应用失败，回退到直接上传
		fmt.Printf("%s应用增量失败，回退到直接上传...%s\n", colors.Yellow, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	// 8. 验证：比较MD5
	localMD5 := fileMD5(localPath)
	remoteMD5Result := sc.rfbMD5(remotePath)
	if remoteMD5, ok := remoteMD5Result["md5"].(string); ok && !strings.EqualFold(remoteMD5, localMD5) {
		fmt.Printf("%sMD5 不匹配，重新上传...%s\n", colors.Yellow, colors.Reset)
		return sc.upload(localPath, remotePath)
	}

	return map[string]interface{}{
		"status":   "success",
		"message":  fmt.Sprintf("rsync 增量同步成功 (节省 %.1f%%)", efficiency),
		"path":     remotePath,
		"efficiency": efficiency,
		"literal":  literalSize,
		"blocks":   blockCount,
	}
}

// RsyncPull 执行完整的 rsync 增量同步（服务端→客户端，下载）
func (sc *ShellClient) RsyncPull(remotePath, localPath string) map[string]interface{} {
	if localPath == "" {
		localPath = filepath.Base(remotePath)
	}

	// 1. 获取远程文件信息
	seed := int32(time.Now().UnixNano())
	remoteResult := sc.rsyncBlockSet(remotePath, seed)
	if status, _ := remoteResult["status"].(string); status != "success" {
		// 远程文件不存在或无法访问，尝试普通下载
		fmt.Printf("%s远程块集获取失败，回退到直接下载...%s\n", colors.Yellow, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	remoteBS := parseBlockSetFromResult(remoteResult)
	if remoteBS == nil {
		fmt.Printf("%s解析远程块集失败，回退到直接下载...%s\n", colors.Yellow, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	// 2. 检查本地文件是否存在
	localInfo, err := os.Stat(localPath)
	localExists := err == nil && !localInfo.IsDir()

	// 如果本地文件不存在，直接下载
	if !localExists {
		fmt.Printf("%s本地文件不存在，直接下载...%s\n", colors.Gray, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	// 如果文件大小和时间都相同，跳过同步
	if remoteBS.FileSize == localInfo.Size() {
		localMD5 := fileMD5(localPath)
		remoteMD5Result := sc.rfbMD5(remotePath)
		if remoteMD5, ok := remoteMD5Result["md5"].(string); ok && remoteMD5 == localMD5 {
			return map[string]interface{}{
				"status":  "success",
				"message": "文件已是最新，跳过同步",
				"path":    localPath,
			}
		}
	}

	// 3. 构建本地 blockset
	localBS, err := BuildBlockSet(localPath, seed)
	if err != nil {
		fmt.Printf("%s构建本地块集失败，回退到直接下载: %v%s\n", colors.Yellow, err, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	// 4. 在本地计算 delta（远程文件 vs 本地 blockset）
	// 需要服务端计算远程文件相对于本地 blockset 的 delta
	// 由于 delta 计算在服务端做，我们需要发送本地 blockset 到服务端
	// 但 blockset 可能很大，所以采用另一种策略：
	// 服务端计算远程文件的 blockset，客户端下载 blockset，客户端在本地计算 delta，然后请求服务端发送字面数据
	
	// 策略：客户端用远程 blockset 计算本地文件的 delta
	// 这意味着：客户端告诉服务端"我有这些块"，服务端发送"你需要这些字面数据"
	// 但我们的架构是：服务端计算 delta，客户端应用 delta
	// 对于 pull 场景，需要反过来：
	// 1. 客户端发送本地 blockset 到服务端（或服务端已有）
	// 2. 服务端用远程文件和本地 blockset 计算 delta
	// 3. 服务端发送 delta 给客户端
	// 4. 客户端应用 delta

	// 简化实现：客户端下载远程 blockset，在本地计算"远程文件相对于本地文件"的 delta
	// 然后请求服务端发送 delta 中的字面数据
	// 但这样需要服务端支持发送指定块的数据

	// 更实际的实现：使用 /rsync/delta 接口，但交换 src/dst 的角色
	// 服务端计算 delta(remote_file, local_blockset)
	// 但服务端没有本地文件...

	// 最终策略：客户端发送本地 blockset 到服务端，服务端计算 delta 并返回
	deltaResult := sc.rsyncDeltaWithLocalBlockSet(remotePath, localPath, localBS, seed)
	if status, _ := deltaResult["status"].(string); status != "success" {
		fmt.Printf("%s增量计算失败，回退到直接下载...%s\n", colors.Yellow, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	chunks := parseDeltaChunksFromResult(deltaResult)
	if chunks == nil {
		fmt.Printf("%s解析增量数据失败，回退到直接下载...%s\n", colors.Yellow, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	// 统计 delta 效率
	literalSize := uint64(0)
	blockCount := 0
	for _, c := range chunks {
		if c.Op == DeltaOpLiteral {
			literalSize += uint64(len(c.Data))
		} else if c.Op == DeltaOpBlock {
			blockCount++
		}
	}
	efficiency := float64(0)
	if remoteBS.FileSize > 0 {
		efficiency = float64(remoteBS.FileSize-int64(literalSize)) / float64(remoteBS.FileSize) * 100
	}
	fmt.Printf("%s增量分析: %d 个匹配块, %d 字节字面数据, 节省 %.1f%%%s\n",
		colors.Gray, blockCount, literalSize, efficiency, colors.Reset)

	// 如果增量太大，直接下载
	if efficiency < 20 && remoteBS.FileSize > 1024*1024 {
		fmt.Printf("%s增量效率太低，直接下载...%s\n", colors.Yellow, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	// 5. 在本地应用 delta
	err = ApplyDelta(localPath, localPath, chunks)
	if err != nil {
		fmt.Printf("%s应用增量失败，回退到直接下载: %v%s\n", colors.Yellow, err, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	// 6. 验证 MD5
	localMD5 := fileMD5(localPath)
	remoteMD5Result := sc.rfbMD5(remotePath)
	if remoteMD5, ok := remoteMD5Result["md5"].(string); ok && !strings.EqualFold(remoteMD5, localMD5) {
		fmt.Printf("%sMD5 不匹配，重新下载...%s\n", colors.Yellow, colors.Reset)
		return sc.download(remotePath, localPath)
	}

	return map[string]interface{}{
		"status":     "success",
		"message":    fmt.Sprintf("rsync 增量拉取成功 (节省 %.1f%%)", efficiency),
		"path":       localPath,
		"efficiency": efficiency,
		"literal":    literalSize,
		"blocks":     blockCount,
	}
}

// rsyncDeltaWithLocalBlockSet 发送本地 blockset 到服务端，让服务端计算 delta
func (sc *ShellClient) rsyncDeltaWithLocalBlockSet(remotePath, localPath string, localBS *BlockSet, seed int32) map[string]interface{} {
	// 将本地 blockset 序列化后发送到服务端
	data := map[string]interface{}{
		"src_path":   remotePath,
		"dst_path":   localPath,
		"seed":       seed,
		"local_bs":   localBS,
		"direction":  "pull",
	}
	result, _ := sc.request("/rsync/delta", data, "POST")
	return result
}

// parseBlockSetFromResult 从 API 结果解析 BlockSet
func parseBlockSetFromResult(result map[string]interface{}) *BlockSet {
	if result == nil {
		return nil
	}
	bs := &BlockSet{}
	if v, ok := result["file_size"].(float64); ok {
		bs.FileSize = int64(v)
	}
	if v, ok := result["block_size"].(float64); ok {
		bs.BlockSize = int32(v)
	}
	if v, ok := result["block_count"].(float64); ok {
		bs.Count = uint32(v)
	}
	if v, ok := result["remainder"].(float64); ok {
		bs.Remainder = int32(v)
	}
	if v, ok := result["checksum_len"].(float64); ok {
		bs.Checksum = int(v)
	}
	if blocks, ok := result["blocks"].([]interface{}); ok {
		bs.Blocks = make([]Block, len(blocks))
		for i, b := range blocks {
			if m, ok := b.(map[string]interface{}); ok {
				blk := &bs.Blocks[i]
				if v, ok := m["index"].(float64); ok {
					blk.Index = uint32(v)
				}
				if v, ok := m["offset"].(float64); ok {
					blk.Offset = int64(v)
				}
				if v, ok := m["length"].(float64); ok {
					blk.Length = int32(v)
				}
				if v, ok := m["fast_sum"].(float64); ok {
					blk.FastSum = uint32(v)
				}
				if s, ok := m["strong_sum"].(string); ok {
					blk.StrongSum, _ = base64.StdEncoding.DecodeString(s)
				} else if data, ok := m["strong_sum"].([]interface{}); ok {
					for _, d := range data {
						if fv, ok := d.(float64); ok {
							blk.StrongSum = append(blk.StrongSum, byte(fv))
						}
					}
				}
			}
		}
	}
	return bs
}

// parseDeltaChunksFromResult 从 API 结果解析 DeltaChunk
func parseDeltaChunksFromResult(result map[string]interface{}) []DeltaChunk {
	if result == nil {
		return nil
	}
	chunksRaw, ok := result["chunks"].([]interface{})
	if !ok {
		return nil
	}
	var chunks []DeltaChunk
	for _, c := range chunksRaw {
		if m, ok := c.(map[string]interface{}); ok {
			chunk := DeltaChunk{}
			if v, ok := m["op"].(float64); ok {
				chunk.Op = uint8(v)
			}
			if v, ok := m["length"].(float64); ok {
				chunk.Length = uint32(v)
			}
			if data, ok := m["data"].([]interface{}); ok {
				for _, d := range data {
					if fv, ok := d.(float64); ok {
						chunk.Data = append(chunk.Data, byte(fv))
					}
				}
			} else if s, ok := m["data"].(string); ok {
				chunk.Data, _ = base64.StdEncoding.DecodeString(s)
			}
			chunks = append(chunks, chunk)
		}
	}
	return chunks
}

// fileMD5 计算文件 MD5
func fileMD5(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := md5.New()
	io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil))
}

// ===== rfb 客户端方法 =====

func (sc *ShellClient) rfbListDir(path string) map[string]interface{} {
	data := map[string]interface{}{"path": path}
	result, _ := sc.request("/rfb/file/list", data, "POST")
	return result
}

func (sc *ShellClient) rfbDownload(remotePath, localPath string, offset int64) map[string]interface{} {
	data := map[string]interface{}{
		"path":   remotePath,
		"offset": offset,
	}

	url := sc.BaseURL + "/rfb/file/download"
	bodyBytes, _ := json.Marshal(data)
	req, err := http.NewRequest("POST", url, bytes.NewReader(bodyBytes))
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建请求失败: %v", err),
			"exit_code": -1,
		}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", UserAgent)

	client := &http.Client{Timeout: time.Duration(sc.Timeout+30) * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("下载失败: %v", err),
			"exit_code": -1,
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		respBody, _ := io.ReadAll(resp.Body)
		var result map[string]interface{}
		if err := json.Unmarshal(respBody, &result); err != nil {
			return map[string]interface{}{
				"status":    "error",
				"stderr":    string(respBody),
				"exit_code": resp.StatusCode,
			}
		}
		return result
	}

	if localPath == "" {
		_, params, _ := mime.ParseMediaType(resp.Header.Get("Content-Disposition"))
		if params["filename"] != "" {
			localPath = params["filename"]
		} else {
			localPath = filepath.Base(remotePath)
		}
	}

	// 断点续传模式
	flag := os.O_CREATE | os.O_WRONLY
	if offset > 0 {
		flag |= os.O_APPEND
	} else {
		flag |= os.O_TRUNC
	}
	out, err := os.OpenFile(localPath, flag, 0644)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建本地文件失败: %v", err),
			"exit_code": -1,
		}
	}
	defer out.Close()

	if offset > 0 {
		out.Seek(offset, io.SeekStart)
	}

	size, err := io.Copy(out, resp.Body)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("保存文件失败: %v", err),
			"exit_code": -1,
		}
	}

	return map[string]interface{}{
		"status":   "success",
		"message":  "文件下载成功",
		"saved_as": localPath,
		"size":     size,
		"offset":   offset,
	}
}

func (sc *ShellClient) rfbUpload(localPath, remotePath string, offset int64) map[string]interface{} {
	file, err := os.Open(localPath)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("打开本地文件失败: %v", err),
			"exit_code": -1,
		}
	}
	defer file.Close()

	if offset > 0 {
		file.Seek(offset, io.SeekStart)
	}

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)

	filename := filepath.Base(localPath)
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建表单失败: %v", err),
			"exit_code": -1,
		}
	}
	if _, err := io.Copy(part, file); err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("读取文件失败: %v", err),
			"exit_code": -1,
		}
	}

	if remotePath != "" {
		writer.WriteField("path", remotePath)
	}
	if offset > 0 {
		writer.WriteField("offset", strconv.FormatInt(offset, 10))
	}
	writer.Close()

	url := sc.BaseURL + "/rfb/file/upload"
	req, err := http.NewRequest("POST", url, &body)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("创建请求失败: %v", err),
			"exit_code": -1,
		}
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("User-Agent", UserAgent)

	client := &http.Client{Timeout: time.Duration(sc.Timeout+30) * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("上传失败: %v", err),
			"exit_code": -1,
		}
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    string(respBody),
			"exit_code": resp.StatusCode,
		}
	}
	return result
}

func (sc *ShellClient) rfbMD5(path string) map[string]interface{} {
	data := map[string]interface{}{"path": path}
	result, _ := sc.request("/rfb/file/md5", data, "POST")
	return result
}

// RFBResumeUpload 带断点续传的 RFB 上传
func (sc *ShellClient) RFBResumeUpload(localPath, remotePath string) map[string]interface{} {
	// 1. 检查远程文件是否存在，获取已传输大小
	remoteMD5 := sc.rfbMD5(remotePath)
	localInfo, err := os.Stat(localPath)
	if err != nil {
		return map[string]interface{}{
			"status":    "error",
			"stderr":    fmt.Sprintf("本地文件不存在: %v", err),
			"exit_code": -1,
		}
	}

	if status, _ := remoteMD5["status"].(string); status == "success" {
		// 远程文件存在，计算本地 MD5
		f, err := os.Open(localPath)
		if err == nil {
			h := md5.New()
			io.Copy(h, f)
			f.Close()
			localHash := hex.EncodeToString(h.Sum(nil))
			if remoteHash, ok := remoteMD5["md5"].(string); ok && remoteHash == localHash {
				return map[string]interface{}{
					"status":  "success",
					"message": "文件已存在且 MD5 相同，跳过上传",
					"path":    remotePath,
				}
			}
		}
	}

	// 2. 检查远程文件大小，确定续传偏移
	var offset int64
	listResult := sc.rfbListDir(filepath.Dir(remotePath))
	if files, ok := listResult["files"].([]interface{}); ok {
		baseName := filepath.Base(remotePath)
		for _, f := range files {
			if m, ok := f.(map[string]interface{}); ok {
				if name, _ := m["name"].(string); name == baseName {
					if sz, ok := m["size"].(float64); ok {
						offset = int64(sz)
					}
					break
				}
			}
		}
	}

	if offset >= localInfo.Size() {
		return map[string]interface{}{
			"status":  "success",
			"message": "文件已传输完成",
			"path":    remotePath,
		}
	}

	// 3. 从 offset 开始上传
	return sc.rfbUpload(localPath, remotePath, offset)
}

// RFBResumeDownload 带断点续传的 RFB 下载
func (sc *ShellClient) RFBResumeDownload(remotePath, localPath string) map[string]interface{} {
	if localPath == "" {
		localPath = filepath.Base(remotePath)
	}

	var offset int64
	if info, err := os.Stat(localPath); err == nil {
		offset = info.Size()
	}

	return sc.rfbDownload(remotePath, localPath, offset)
}

// ==================== 交互式 Shell (客户端) ====================
type InteractiveShell struct {
	client      *ShellClient
	running     bool
	promptCount int
}

func NewInteractiveShell(client *ShellClient) *InteractiveShell {
	return &InteractiveShell{
		client:  client,
		running: true,
	}
}

func (is *InteractiveShell) getPrompt() string {
	is.promptCount++
	dirHint := ""
	if is.client.WorkDir != "" {
		dirHint = fmt.Sprintf("[%s]", is.client.WorkDir)
	}
	return fmt.Sprintf("%s[%d]%s%sPS>%s%s ", colors.Cyan, is.promptCount, colors.Reset, colors.Green, colors.Reset, dirHint)
}

func (is *InteractiveShell) printResult(result map[string]interface{}) {
	status, _ := result["status"].(string)
	stdout, _ := result["stdout"].(string)
	stderr, _ := result["stderr"].(string)
	exitCode := 0
	if v, ok := result["exit_code"].(float64); ok {
		exitCode = int(v)
	}
	timeout, _ := result["timeout"].(bool)

	if stdout != "" {
		fmt.Println(strings.TrimRight(stdout, "\r\n"))
	}
	if stderr != "" {
		fmt.Printf("%s%s%s\n", colors.Red, strings.TrimRight(stderr, "\r\n"), colors.Reset)
	}
	if timeout {
		fmt.Printf("%s[超时]%s\n", colors.Yellow, colors.Reset)
	} else if status != "success" && stderr == "" {
		fmt.Printf("%s[退出码: %d]%s\n", colors.Red, exitCode, colors.Reset)
	}
}

func (is *InteractiveShell) printFileResult(result map[string]interface{}) {
	status, _ := result["status"].(string)
	message, _ := result["message"].(string)
	savedAs, _ := result["saved_as"].(string)
	stderr, _ := result["stderr"].(string)
	size := int64(0)
	if v, ok := result["size"].(float64); ok {
		size = int64(v)
	}

	if status == "success" {
		fmt.Printf("%s✓ %s%s\n", colors.Green, message, colors.Reset)
		if savedAs != "" {
			fmt.Printf("  保存路径: %s\n", savedAs)
		}
		if size > 0 {
			fmt.Printf("  文件大小: %d bytes\n", size)
		}
	} else {
		if stderr != "" {
			fmt.Printf("%s✗ %s%s\n", colors.Red, stderr, colors.Reset)
		} else {
			fmt.Printf("%s✗ 操作失败%s\n", colors.Red, colors.Reset)
		}
	}
}

func (is *InteractiveShell) handleBuiltin(cmdLine string) bool {
	parts := strings.Fields(cmdLine)
	if len(parts) == 0 {
		return true
	}
	cmd := strings.ToLower(parts[0])
	arg := ""
	if len(parts) > 1 {
		arg = strings.TrimSpace(cmdLine[len(parts[0]):])
	}

	switch cmd {
	case "exit", "quit", "q":
		fmt.Printf("%s再见！%s\n", colors.Yellow, colors.Reset)
		is.running = false
		return true
	case "cd":
		if arg != "" {
			is.client.WorkDir = strings.Trim(arg, `"'`)
			fmt.Printf("%s工作目录: %s%s\n", colors.Gray, is.client.WorkDir, colors.Reset)
		} else {
			is.client.WorkDir = ""
			fmt.Printf("%s工作目录已重置%s\n", colors.Gray, colors.Reset)
		}
		return true
	case "pwd":
		if is.client.WorkDir != "" {
			fmt.Println(is.client.WorkDir)
		} else {
			fmt.Println("(默认目录)")
		}
		return true
	case "clear", "cls":
		if runtime.GOOS == "windows" {
			cmd := exec.Command("cmd", "/c", "cls")
			cmd.Stdout = os.Stdout
			cmd.Run()
		} else {
			fmt.Print("\033[H\033[2J")
		}
		return true
	case "help", "?":
		is.printHelp()
		return true
	case "upload":
		if len(parts) < 2 {
			fmt.Printf("%s用法: upload <本地路径> [远程路径]%s\n", colors.Yellow, colors.Reset)
			return true
		}
		localPath := parts[1]
		remotePath := ""
		if len(parts) > 2 {
			remotePath = parts[2]
		}
		fmt.Printf("%s正在上传 %s ...%s\n", colors.Gray, localPath, colors.Reset)
		result := is.client.upload(localPath, remotePath)
		is.printFileResult(result)
		return true
	case "download":
		if len(parts) < 2 {
			fmt.Printf("%s用法: download <远程路径> [本地路径]%s\n", colors.Yellow, colors.Reset)
			return true
		}
		remotePath := parts[1]
		localPath := ""
		if len(parts) > 2 {
			localPath = parts[2]
		}
		fmt.Printf("%s正在下载 %s ...%s\n", colors.Gray, remotePath, colors.Reset)
		result := is.client.download(remotePath, localPath)
		is.printFileResult(result)
		return true
	case "rsync":
		if len(parts) < 2 {
			fmt.Printf("%s用法: rsync <本地路径> [远程路径]%s\n", colors.Yellow, colors.Reset)
			return true
		}
		localPath := parts[1]
		remotePath := ""
		if len(parts) > 2 {
			remotePath = parts[2]
		} else {
			remotePath = filepath.Base(localPath)
		}
		fmt.Printf("%s正在 rsync 同步 %s -> %s ...%s\n", colors.Gray, localPath, remotePath, colors.Reset)
		result := is.client.RsyncSync(localPath, remotePath)
		is.printFileResult(result)
		return true
	case "rsync-pull":
		if len(parts) < 2 {
			fmt.Printf("%s用法: rsync-pull <远程路径> [本地路径]%s\n", colors.Yellow, colors.Reset)
			return true
		}
		remotePath := parts[1]
		localPath := ""
		if len(parts) > 2 {
			localPath = parts[2]
		}
		fmt.Printf("%s正在 rsync 拉取 %s -> %s ...%s\n", colors.Gray, remotePath, localPath, colors.Reset)
		result := is.client.RsyncPull(remotePath, localPath)
		is.printFileResult(result)
		return true
	case "rfb-upload":
		if len(parts) < 2 {
			fmt.Printf("%s用法: rfb-upload <本地路径> [远程路径]%s\n", colors.Yellow, colors.Reset)
			return true
		}
		localPath := parts[1]
		remotePath := ""
		if len(parts) > 2 {
			remotePath = parts[2]
		} else {
			remotePath = filepath.Base(localPath)
		}
		fmt.Printf("%s正在 RFB 上传 %s -> %s (支持断点续传)...%s\n", colors.Gray, localPath, remotePath, colors.Reset)
		result := is.client.RFBResumeUpload(localPath, remotePath)
		is.printFileResult(result)
		return true
	case "rfb-download":
		if len(parts) < 2 {
			fmt.Printf("%s用法: rfb-download <远程路径> [本地路径]%s\n", colors.Yellow, colors.Reset)
			return true
		}
		remotePath := parts[1]
		localPath := ""
		if len(parts) > 2 {
			localPath = parts[2]
		}
		fmt.Printf("%s正在 RFB 下载 %s (支持断点续传)...%s\n", colors.Gray, remotePath, colors.Reset)
		result := is.client.RFBResumeDownload(remotePath, localPath)
		is.printFileResult(result)
		return true
	case "rfb-list":
		path := "."
		if len(parts) > 1 {
			path = parts[1]
		}
		fmt.Printf("%s正在列出目录 %s ...%s\n", colors.Gray, path, colors.Reset)
		result := is.client.rfbListDir(path)
		if status, _ := result["status"].(string); status == "success" {
			if files, ok := result["files"].([]interface{}); ok {
				fmt.Printf("%-40s %10s %s\n", "名称", "大小", "类型")
				fmt.Println(strings.Repeat("-", 60))
				for _, f := range files {
					if m, ok := f.(map[string]interface{}); ok {
						name, _ := m["name"].(string)
						sz, _ := m["size"].(float64)
						isDir, _ := m["is_dir"].(bool)
						typ := "FILE"
						if isDir {
							typ = "DIR"
						}
						fmt.Printf("%-40s %10.0f %s\n", name, sz, typ)
					}
				}
			}
		} else {
			is.printFileResult(result)
		}
		return true
	}

	if strings.HasPrefix(cmdLine, "!") {
		localCmd := strings.TrimSpace(cmdLine[1:])
		if localCmd != "" {
			cmd := exec.Command("cmd", "/C", localCmd)
			if runtime.GOOS != "windows" {
				cmd = exec.Command("sh", "-c", localCmd)
			}
			output, err := cmd.CombinedOutput()
			if err != nil {
				fmt.Printf("%s本地命令失败: %v%s\n", colors.Red, err, colors.Reset)
			}
			if len(output) > 0 {
				fmt.Println(strings.TrimRight(string(output), "\r\n"))
			}
		}
		return true
	}

	return false
}

func (is *InteractiveShell) printHelp() {
	helpText := fmt.Sprintf(`
%sHTTP Shell Client - 交互式远程Shell%s

%s内置命令:%s
  exit, quit, q     退出客户端
  cd <目录>         设置后续命令的工作目录
  pwd               显示当前工作目录
  clear, cls        清屏
  !<命令>           执行本地命令（不发送到服务端）
  upload <本地路径> [远程路径]     上传文件
  download <远程路径> [本地路径]   下载文件
  rsync <本地路径> [远程路径]      rsync 增量同步（本地→远程）
  rsync-pull <远程路径> [本地路径] rsync 增量拉取（远程→本地）
  rfb-upload <本地路径> [远程路径] RFB 断点续传上传
  rfb-download <远程路径> [本地路径] RFB 断点续传下载
  rfb-list [目录]                 RFB 列出远程目录
  help, ?           显示此帮助

%s使用示例:%s
  Get-Location              查看当前目录
  Get-ChildItem             列出文件
  mkdir hello               创建目录
  ipconfig                  查看网络配置

%s提示: 所有命令在远程工控机上执行，结果通过HTTP返回。%s
`, colors.Bold, colors.Reset, colors.Cyan, colors.Reset, colors.Cyan, colors.Reset, colors.Gray, colors.Reset)
	fmt.Println(helpText)
}

func (is *InteractiveShell) printBanner() {
	banner := fmt.Sprintf(`
%s%s╔══════════════════════════════════════════════════════════════╗
║           HTTP Shell Client - 远程PowerShell控制台            ║
╚══════════════════════════════════════════════════════════════╝%s

服务端: %s%s%s
输入 %shelp%s 查看帮助，%sexit%s 退出
`, colors.Bold, colors.Cyan, colors.Reset, colors.Yellow, is.client.BaseURL, colors.Reset, colors.Green, colors.Reset, colors.Green, colors.Reset)
	fmt.Println(banner)
}

func (is *InteractiveShell) run() {
	is.printBanner()

	health := is.client.checkHealth()
	if status, ok := health["status"].(string); ok && status == "running" {
		shell, _ := health["shell"].(string)
		fmt.Printf("%s✓ 服务端连接成功%s | Shell: %s%s%s\n\n", colors.Green, colors.Reset, colors.Yellow, shell, colors.Reset)
	} else {
		stderr, _ := health["stderr"].(string)
		if stderr == "" {
			stderr = "未知错误"
		}
		fmt.Printf("%s✗ 服务端连接失败: %s%s\n\n", colors.Red, stderr, colors.Reset)
		fmt.Printf("%s仍可使用本地命令 (!开头)，远程命令将失败。%s\n\n", colors.Yellow, colors.Reset)
	}

	reader := bufio.NewReader(os.Stdin)
	for is.running {
		fmt.Print(is.getPrompt())
		cmdLine, err := reader.ReadString('\n')
		if err != nil {
			fmt.Printf("\n%s再见！%s\n", colors.Yellow, colors.Reset)
			break
		}
		cmdLine = strings.TrimSpace(cmdLine)
		if cmdLine == "" {
			continue
		}
		if is.handleBuiltin(cmdLine) {
			continue
		}
		result := is.client.execute(cmdLine, is.client.WorkDir)
		is.printResult(result)
	}
}

func runOnce(client *ShellClient, command string) int {
	result := client.execute(command, client.WorkDir)
	stdout, _ := result["stdout"].(string)
	stderr, _ := result["stderr"].(string)
	exitCode := 0
	if v, ok := result["exit_code"].(float64); ok {
		exitCode = int(v)
	}
	if stdout != "" {
		fmt.Println(strings.TrimRight(stdout, "\r\n"))
	}
	if stderr != "" {
		fmt.Fprintln(os.Stderr, strings.TrimRight(stderr, "\r\n"))
	}
	return exitCode
}

// ==================== 角色选择 ====================
func selectRole() string {
	fmt.Println("=" + strings.Repeat("=", 58))
	fmt.Println("  HTTP Shell Proxy - 请选择运行角色")
	fmt.Println("=" + strings.Repeat("=", 58))
	fmt.Println("  1) server  - 启动服务端（工控机运行）")
	fmt.Println("  2) client  - 启动客户端（开发机运行）")
	fmt.Println("=" + strings.Repeat("=", 58))

	reader := bufio.NewReader(os.Stdin)
	for {
		fmt.Print("请输入选项 (1/2): ")
		input, err := reader.ReadString('\n')
		if err != nil {
			fmt.Println("读取输入失败")
			os.Exit(1)
		}
		input = strings.TrimSpace(input)
		switch input {
		case "1", "server", "s":
			return "server"
		case "2", "client", "c":
			return "client"
		default:
			fmt.Println("无效选项，请重新输入")
		}
	}
}

// ==================== 主程序 ====================
func main() {
	initColors()

	var role string
	flag.StringVar(&role, "role", "", "运行角色: server 或 client")

	var serverHost string
	var serverPort int
	var serverTimeout int
	flag.StringVar(&serverHost, "host", getEnv("HTTP_SHELL_HOST", DefaultHost), "服务端监听地址")
	flag.IntVar(&serverPort, "port", getEnvInt("HTTP_SHELL_PORT", DefaultPort), "服务端监听端口")
	flag.IntVar(&serverTimeout, "timeout", getEnvInt("HTTP_SHELL_TIMEOUT", DefaultTimeout), "默认命令超时秒数")

	var clientURL string
	var clientCommand string
	var clientWorkDir string
	flag.StringVar(&clientURL, "url", "", "服务端URL (客户端模式)")
	flag.StringVar(&clientCommand, "c", "", "单次执行命令 (客户端模式)")
	flag.StringVar(&clientWorkDir, "w", "", "工作目录 (客户端模式)")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "用法: %s [选项]\n\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "模式选择:\n")
		fmt.Fprintf(os.Stderr, "  -role server          启动服务端\n")
		fmt.Fprintf(os.Stderr, "  -role client          启动客户端\n")
		fmt.Fprintf(os.Stderr, "  (不指定-role时进入交互式选择)\n\n")
		fmt.Fprintf(os.Stderr, "服务端选项:\n")
		fmt.Fprintf(os.Stderr, "  -host string          监听地址 (默认: %s)\n", DefaultHost)
		fmt.Fprintf(os.Stderr, "  -port int             监听端口 (默认: %d)\n", DefaultPort)
		fmt.Fprintf(os.Stderr, "  -timeout int          默认超时秒数 (默认: %d)\n\n", DefaultTimeout)
		fmt.Fprintf(os.Stderr, "客户端选项:\n")
		fmt.Fprintf(os.Stderr, "  -url string           服务端URL\n")
		fmt.Fprintf(os.Stderr, "  -c string             单次执行命令\n")
		fmt.Fprintf(os.Stderr, "  -w string             工作目录\n")
		fmt.Fprintf(os.Stderr, "\n示例:\n")
		fmt.Fprintf(os.Stderr, "  %s -role server -port 10022\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "  %s -role client -url http://192.168.1.100:10022\n", os.Args[0])
		fmt.Fprintf(os.Stderr, "  %s -role client -url http://192.168.1.100:10022 -c \"Get-Location\"\n", os.Args[0])
	}

	flag.Parse()

	if role == "" {
		args := flag.Args()
		if len(args) > 0 {
			if args[0] == "server" || args[0] == "client" {
				role = args[0]
				flag.CommandLine.Parse(args[1:])
			} else if strings.HasPrefix(args[0], "http://") || strings.HasPrefix(args[0], "https://") {
				role = "client"
				clientURL = args[0]
				if len(args) > 2 && args[1] == "-c" {
					clientCommand = args[2]
				}
			}
		}
	}

	if role == "" {
		role = selectRole()
	}

	switch role {
	case "server", "s":
		runServer(serverHost, serverPort, serverTimeout)
	case "client", "c":
		if clientURL == "" {
			clientURL = promptForServerURL()
		}
		client := NewShellClient(clientURL, serverTimeout)
		if clientWorkDir != "" {
			client.WorkDir = clientWorkDir
		}
		if clientCommand != "" {
			exitCode := runOnce(client, clientCommand)
			os.Exit(exitCode)
		} else {
			shell := NewInteractiveShell(client)
			shell.run()
		}
	default:
		fmt.Fprintf(os.Stderr, "错误: 无效的角色 '%s'，请使用 server 或 client\n", role)
		os.Exit(1)
	}
}

func promptForServerURL() string {
	reader := bufio.NewReader(os.Stdin)
	for {
		fmt.Print("请输入服务端地址 (例如 http://192.168.100.55:10022): ")
		input, err := reader.ReadString('\n')
		if err != nil {
			fmt.Fprintf(os.Stderr, "读取输入失败: %v\n", err)
			os.Exit(1)
		}
		input = strings.TrimSpace(input)
		if input == "" {
			fmt.Println("地址不能为空，请重新输入")
			continue
		}
		if !strings.HasPrefix(input, "http://") && !strings.HasPrefix(input, "https://") {
			input = "http://" + input
		}

		fmt.Printf("正在连接 %s ...\n", input)
		testClient := NewShellClient(input, 5)
		health := testClient.checkHealth()
		if status, ok := health["status"].(string); ok && status == "running" {
			shell, _ := health["shell"].(string)
			fmt.Printf("%s✓ 连接成功%s | Shell: %s%s%s\n", colors.Green, colors.Reset, colors.Yellow, shell, colors.Reset)
			return input
		}
		stderr, _ := health["stderr"].(string)
		if stderr == "" {
			stderr = "无法连接到服务端"
		}
		fmt.Printf("%s✗ 连接失败: %s%s\n", colors.Red, stderr, colors.Reset)
		fmt.Print("是否重新输入? (y/n): ")
		retry, _ := reader.ReadString('\n')
		retry = strings.TrimSpace(strings.ToLower(retry))
		if retry == "n" || retry == "no" || retry == "q" || retry == "quit" {
			os.Exit(1)
		}
	}
}

func getEnv(key, defaultValue string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultValue
}

func getEnvInt(key string, defaultValue int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return defaultValue
}
```

**http_shell_cli_windows.go**
```go
//go:build windows

package main

import (
	"syscall"
	"unsafe"

	"golang.org/x/text/encoding/simplifiedchinese"
	"golang.org/x/text/transform"
)

// enableWindowsVT 启用 Windows 虚拟终端处理（支持 ANSI 颜色）
func enableWindowsVT() {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	getConsoleMode := kernel32.NewProc("GetConsoleMode")
	setConsoleMode := kernel32.NewProc("SetConsoleMode")

	handle, err := syscall.GetStdHandle(syscall.STD_OUTPUT_HANDLE)
	if err != nil {
		return
	}
	var mode uint32
	ret, _, _ := getConsoleMode.Call(uintptr(handle), uintptr(unsafe.Pointer(&mode)))
	if ret == 0 {
		return
	}
	mode |= 0x0004 // ENABLE_VIRTUAL_TERMINAL_PROCESSING
	ret, _, _ = setConsoleMode.Call(uintptr(handle), uintptr(mode))
	_ = ret
}

// setHideWindow 设置进程隐藏窗口（Windows 专用）
func setHideWindow(cmd interface{}) {
	// 在 Go 中通过反射设置 SysProcAttr 比较麻烦
	// 这里使用类型断言
	if c, ok := cmd.(interface{ SetSysProcAttr(*syscall.SysProcAttr) }); ok {
		c.SetSysProcAttr(&syscall.SysProcAttr{
			HideWindow: true,
		})
	}
}

// windowsGbkToUTF8 将 GBK 编码转换为 UTF-8
func windowsGbkToUTF8(data []byte) string {
	reader := transform.NewReader(
		&byteReader{data: data},
		simplifiedchinese.GBK.NewDecoder(),
	)
	result := make([]byte, 0, len(data)*2)
	buf := make([]byte, 4096)
	for {
		n, err := reader.Read(buf)
		if n > 0 {
			result = append(result, buf[:n]...)
		}
		if err != nil {
			break
		}
	}
	return string(result)
}

type byteReader struct {
	data []byte
	off  int
}

func (r *byteReader) Read(p []byte) (int, error) {
	if r.off >= len(r.data) {
		return 0, syscall.ERROR_HANDLE_EOF
	}
	n := copy(p, r.data[r.off:])
	r.off += n
	return n, nil
}

// IsWindows 返回 true 表示当前是 Windows 平台
func IsWindows() bool {
	return true
}

```

**public_fs/public_fs.go**
```go
package public_fs

import "embed"

//go:embed novnc
var EmbedFiles embed.FS

//go:embed tvnc
var EmbedVNC embed.FS

```

**vnc_proxy/peer.go**
```go
package vnc_proxy

import (
	"net"
	"time"

	"github.com/evangwt/go-bufcopy"
	"github.com/pkg/errors"
	"golang.org/x/net/websocket"
)

const (
	defaultDialTimeout = 5 * time.Second
)

var (
	bcopy = bufcopy.New()
)

// peer represents a vnc proxy peer
type peer struct {
	source *websocket.Conn
	target net.Conn
}

func NewPeer(ws *websocket.Conn, addr string, dialTimeout time.Duration) (*peer, error) {
	if ws == nil {
		return nil, errors.New("websocket connection is nil")
	}

	if len(addr) == 0 {
		return nil, errors.New("addr is empty")
	}

	if dialTimeout <= 0 {
		dialTimeout = defaultDialTimeout
	}
	c, err := net.DialTimeout("tcp", addr, dialTimeout)
	if err != nil {
		return nil, errors.Wrap(err, "cannot connect to vnc backend")
	}

	err = c.(*net.TCPConn).SetKeepAlive(true)
	if err != nil {
		return nil, errors.Wrap(err, "enable vnc backend connection keepalive failed")
	}

	err = c.(*net.TCPConn).SetKeepAlivePeriod(30 * time.Second)
	if err != nil {
		return nil, errors.Wrap(err, "set vnc backend connection keepalive period failed")
	}

	return &peer{
		source: ws,
		target: c,
	}, nil
}

// ReadSource copy source stream to target connection
func (p *peer) ReadSource() error {
	if _, err := bcopy.Copy(p.target, p.source); err != nil {
		return errors.Wrapf(err, "copy source(%v) => target(%v) failed", p.source.RemoteAddr(), p.target.RemoteAddr())
	}
	return nil
}

// ReadTarget copys target stream to source connection
func (p *peer) ReadTarget() error {
	if _, err := bcopy.Copy(p.source, p.target); err != nil {
		return errors.Wrapf(err, "copy target(%v) => source(%v) failed", p.target.RemoteAddr(), p.source.RemoteAddr())
	}
	return nil
}

// Close close the websocket connection and the vnc backend connection
func (p *peer) Close() {
	p.source.Close()
	p.target.Close()
}

```

**vnc_proxy/proxy.go**
```go
package vnc_proxy

import (
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/websocket"
)

type TokenHandler func(r *http.Request) (addr string, err error)

// Config represents vnc proxy config
type Config struct {
	DialTimeout time.Duration
	TokenHandler
}

// Proxy represents vnc proxy
type Proxy struct {
	dialTimeout  time.Duration
	peers        map[*peer]struct{}
	l            sync.RWMutex
	tokenHandler TokenHandler
}

// New returns a vnc proxy
func New(conf *Config) *Proxy {
	if conf.TokenHandler == nil {
		conf.TokenHandler = func(r *http.Request) (addr string, err error) {
			return ":5901", nil
		}
	}

	return &Proxy{
		dialTimeout:  conf.DialTimeout,
		peers:        make(map[*peer]struct{}),
		l:            sync.RWMutex{},
		tokenHandler: conf.TokenHandler,
	}
}

// ServeWS provides websocket handler
func (p *Proxy) ServeWS(ws *websocket.Conn) {
	log.Println("ServeWS")
	ws.PayloadType = websocket.BinaryFrame

	r := ws.Request()
	log.Printf("request url: %v\n", r.URL)

	addr, err := p.tokenHandler(r)
	if err != nil {
		log.Printf("get vnc backend failed: %v\n", err)
		return
	}

	peer, err := NewPeer(ws, addr, p.dialTimeout)
	if err != nil {
		log.Printf("new vnc peer failed: %v\n", err)
		return
	}

	p.addPeer(peer)
	defer func() {
		log.Println("close peer")
		p.deletePeer(peer)
	}()

	go func() {
		if err := peer.ReadTarget(); err != nil {
			if strings.Contains(err.Error(), "use of closed network connection") {
				return
			}
			log.Println(err)
			return
		}
	}()

	if err = peer.ReadSource(); err != nil {
		if strings.Contains(err.Error(), "use of closed network connection") {
			return
		}
		log.Println(err)
		return
	}
}

func (p *Proxy) addPeer(peer *peer) {
	p.l.Lock()
	p.peers[peer] = struct{}{}
	p.l.Unlock()
}

func (p *Proxy) deletePeer(peer *peer) {
	p.l.Lock()
	delete(p.peers, peer)
	peer.Close()
	p.l.Unlock()
}

func (p *Proxy) Peers() map[*peer]struct{} {
	p.l.RLock()
	defer p.l.RUnlock()
	return p.peers
}

```