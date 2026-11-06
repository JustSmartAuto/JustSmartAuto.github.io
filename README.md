# LocalFileDropShell — 工控机文件/Shell/VNC 一体化远程运维

面向**工控机与调试机**的一体化远程运维工具。同一进程同时提供：

| 能力 | UI 层 | 说明 |
|---|---|---|
| **文件传送** | Tk 主界面（左窗格） | TightVNC RFB 通道优先 / aiohttp HTTP 回退 / 单工自动喷泉码；顶部“协议”下拉显式选择（默认 RFB File，LocalSend/FTP/HTTP/RSYNC 占位），选择持久化到 `settings.json`；左侧拖入后自动在资源管理器打开所属文件夹，右侧目录支持双击 / 右键“打开”、面包屑与“⬆ 上级”回退 |
| **拖放上传** | Tk 主界面（右窗格） | tkinterdnd2 + Win32 WM_DROPFILES 原生拖放 |
| **HTTP Shell** | pywebview 标签页（Shell GUI） | `/exec` 远程命令执行，pwsh → powershell → cmd 自动检测，GBK/UTF-8 自适应，超时 Kill |
| **rsync 增量同步** | Tk 主界面 / CLI | Adler-32 + MD5 双阶段块级 delta，push / pull 双向，自动回退全量 |
| **RFB 文件传输** | Tk 主界面 | TightVNC FTP 扩展消息，支持 offset 断点续传、MD5 块校验、目录列表 |
| **VNC 远程桌面** | pywebview 标签页（VNC） | 自动探测已有 TightVNC / RealVNC / UltraVNC 直接复用；无则可内嵌启动 + noVNC + WebSocket 代理；未安装 TightVNC 时用内嵌 MSI 静默提权安装 |
| **远程地址 / 收藏夹** | Tk 文件传输页顶栏 + 收藏夹页 | 输入 `ip[:端口]`（省略按 5900）点“连接”直连该远程 VNC；收藏备注名 / ip:port / 传输协议 / 文件传输默认路径 / 多个快捷路径，持久化到 `D:/JustSmart/LocalFileDropShell/favorites.json`（不可写回退 `%APPDATA%/LocalFileDropShell`） |
| **CLI 交互式 shell** | 纯控制台 | 同进程三角色（`gui` / `server` / `client`），内置 upload / download / rsync / rfb-* 等命令 |

**混合 UI 架构**：
- **主窗口（Tkinter + tkinterdnd2）**：负责文件/文件夹拖放、传输列表、进度条、日志输出、批量生成——Windows 95 风格（3D 凸起边框、Teal 标题栏、MS Sans Serif）
- **VNC 标签页（pywebview 内嵌）**：noVNC 前端，通过 WebSocket 代理桥接到本机 VNC Server
- **Shell CLI GUI 标签页（pywebview 内嵌）**：本地 Web Terminal，通过 `/exec` 路由发送命令，实时回显 stdout/stderr/exit_code
- **收藏夹标签页（Tk）**：每条收藏带“文件传输”/“远程桌面”快捷按钮，点击切页并自动填入远程地址与默认路径；另可配置多个快捷路径，每条路径一个按钮，一键切页并直达该远程目录，便于运维批量切换工控机
- 四个子界面共享同一 **aiohttp 服务端**（默认端口 10022），pywebview 标签页只做"壳"，全部业务逻辑在本地 HTTP

---

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│  Tk 根窗口 (root = Tk(), title="LocalFileDropShell")              │
│  ┌─ 菜单栏 ──────────────────────────────────────────────────┐   │
│  │ 文件  传输  Shell  VNC  帮助                                │   │
│  └────────────────────────────────────────────────────────────┘   │
│  ┌─ Tk Notebook 标签页 ─────────────────────────────────────┐   │
│  │ [ 文件传输 ] [ Shell CLI GUI ] [ VNC 远程桌面 ] [ ⭐ 收藏夹 ] │   │
│  │  ┌─ 文件传输 (Tk Frame) ──────────────────────────────┐   │   │
│  │  │ [远程地址 ip[:端口]] [连接] [远程目录] [刷新列表]   │   │   │
│  │  │ 本地列表 Treeview │ 远程列表 Treeview │ 传输按钮   │   │   │
│  │  │ (拖入 Win32 WM_DROPFILES 直接追加)   │ ↵ / → / ☰  │   │   │
│  │  │ 进度条 / 日志 / 批量生成                           │   │   │
│  │  └────────────────────────────────────────────────────┘   │   │
│  │  ┌─ Shell CLI GUI (Tk Frame 内嵌 pywebview 子窗口) ───┐   │   │
│  │  │ webview.create_window("Shell", "http://127.0.0.1:10022/shell") │   │
│  │  │ xterm.js / 简化 Web Terminal                         │   │   │
│  │  └────────────────────────────────────────────────────┘   │   │
│  │  ┌─ VNC 远程桌面 (Tk Frame 内嵌 pywebview 子窗口) ────┐   │   │
│  │  │ webview.create_window("VNC", "http://127.0.0.1:10022/vnc/vnc.html") │   │
│  │  │ noVNC (websockify → 127.0.0.1:5900 / 15900)        │   │   │
│  │  └────────────────────────────────────────────────────┘   │   │
│  └────────────────────────────────────────────────────────────┘   │
│  ┌─ 状态栏 ───────────────────────────────────────────────────┐   │
│  │ [传输状态] [TightVNC 通道] [HTTP 10022] [单工判定]         │   │
│  └────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
          │                        │
          │ TightVNC 优先探测       │ HTTP 10022 (回退)
          ▼                        ▼
┌─────────────────────┐  ┌─────────────────────────────────────────┐
│ TightVNC Server     │  │ LocalFileDropShell Server  (同进程)      │
│ (工控机已预装 5900) │  │ aiohttp                                   │
│ FC000102~FC000119   │  │ ├─ /exec            HTTP Shell           │
│   FTP 扩展消息       │  │ ├─ /upload /download /list               │
│  ─── 无需目标端启动本软件 ───    │ ├─ /rsync/blockset /delta /apply  │
└─────────────────────┘  │ ├─ /rfb/file/*       RFB FTP 封装        │
                         │ ├─ /vnc/             noVNC 静态页面       │
                         │ ├─ /websockify       WebSocket 代理       │
                         │ │   (浏览器 ↔ 127.0.0.1:5900+/15900+)     │
                         │ └─ /health                                │
                         └─────────────────────────────────────────┘
```

**Tk + pywebview 混合启动顺序**（`-role gui` 时）：

1. `cli.py` 解析参数 → 启动 aiohttp 服务端（异步）→ `await server.start()`
2. 创建 Tk 根窗口，应用 Win95 风格字体/颜色/边框（参考 `assets/02-windows-95.css` 的 token）
3. 启动 Tk 主循环 `root.mainloop()`
4. 用户切换到 Shell/VNC 标签页时 → 懒加载 pywebview 子窗口（`webview.create_window()`，不阻塞 Tk 主线程）
5. Tk 主窗口关闭时 → `await server.shutdown()` → `webview.destroy_window()` → 退出

传输层分支（文件/文件夹传送）：
1. 探测目标端 `TCP 5900` / `15900` 是否有 RFB 响应（握手 `RFB003.008\n`）
2. 成功 → 发 `FC000102` 验证 TightVNC FTP 扩展；后续文件传输全部走 RFB 通道
3. 失败 → 走自托管 HTTP（`/upload` / `/download` / `/list`）
4. 通道判定为单工 → 包 DCF2 容器（49B 自描述头 + SHA-256）+ decimen v3 Wire 帧（22B 自描述头）

---

## HTTP / API 路由

aiohttp 服务端默认监听 `0.0.0.0:10022`（环境变量 `HTTP_SHELL_PORT` 可覆盖）。

### `GET /health` 健康检查

```json
{"status":"running","shell":"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe","time":"2026-11-05 14:00:00","vnc_detected":15900}
```

### `POST /exec` HTTP Shell

```json
// 请求
{"command":"Get-ChildItem","timeout":30,"work_dir":"C:\\Users"}

// 响应
{"status":"success","stdout":"...","stderr":"","exit_code":0,"timeout":false,"command":"Get-ChildItem"}
```

Shell 自动检测优先级：`pwsh.exe` → `powershell.exe` → `cmd.exe`。Windows cmd 输出自动 UTF-8 校验 + GBK 回退转码（`isValidUTF8` / `windowsGbkToUTF8`）。超时默认 30 秒（环境变量 `HTTP_SHELL_TIMEOUT`），超时后 `Process.Kill()` 并返回 `exit_code=-1`、`timeout=true`。

### `POST /upload` / `POST /download` 文件上传下载

multipart/form-data：`file=@local.bin` + `path=/remote/dir/`（可选）。返回 `path`、`size`。

可选字段 `host` / `port`（TightVNC 端口，默认 5900）：
- 带 `host` → 服务端把文件经 RFB FTP **直接写入该远程主机**的 `dir/文件名`，不落本地磁盘（系统临时文件在传输后即删除）
- 不带 `host` → 保持原有行为（写服务端本机路径，并在本机 VNC Server 可用时同步推送）

`POST /download` 的 `host` 分支当前返回 501（RFB 下载协议尚未实现），本地路径下载不受影响。

```json
// POST /download（本地）
{"path":"C:/Users/Public/a.bin","offset":0}
```

### Rsync 增量同步接口

#### `POST /rsync/blockset` 获取块集合

```json
// 请求
{"path":"/remote/file.dat","seed":12345}

// 响应
{"status":"success","file_size":1048576,"block_size":1024,"block_count":1024,
 "remainder":0,"checksum_len":16,"seed":12345,
 "blocks":[
   {"index":0,"offset":0,"length":1024,"fast_sum":3221225472,"strong_sum":"base64-MD5..."},
   ...
 ]}
```

块大小公式：`max(700, ceil(sqrt(fileSize))) 向上取整到 8 的倍数`。

| 文件大小 | 块大小 | 块数量 |
|---|---|---|
| 100 KB | 700 B | 147 |
| 1 MB | 1,024 B | 1,024 |
| 10 MB | 2,048 B | 5,120 |
| 100 MB | 3,600 B | 28,445 |
| 1 GB | 8,192 B | 131,072 |

#### `POST /rsync/delta` 计算增量

```json
// push 模式（客户端发 src_path + dst_path，服务端用已有 dst 算相对于 src 的 delta）
{"src_path":"/local/file.dat","dst_path":"/remote/file.dat","seed":12345}

// pull 模式（客户端发 local_bs 本地 blockset，服务端用远程文件算相对于本地的 delta）
{"src_path":"/remote/file.dat","dst_path":"/local/file.dat","seed":12345,"direction":"pull","local_bs":{...}}
```

#### `POST /rsync/apply` 应用增量重建文件

```json
{"dst_path":"/remote/file.dat","src_path":"/remote/file.dat","chunks":[
  {"op":1,"length":256,"data":"base64..."},   // 1 = literal 字面数据
  {"op":2,"length":5},                        // 2 = block 引用远程第 5 块
  {"op":0}                                    // 0 = EOF
]}
```

Rsync 自动回退全量传输的条件：远程文件不存在 / blockset 获取失败 / delta 计算失败 / apply 失败 / MD5 验证不匹配 / 增量效率 < 20% 且文件 > 1MB。

### RFB FTP 文件传输接口（TightVNC FTP 扩展 HTTP 包装）

#### `POST /rfb/file/list` 列出目录

```json
// 本机 VNC（不传 host）
{"path":"C:/Users"}
// → {"path":"C:/Users","entries":[{"name":"Desktop","size":0,"mod_time":1730000000,"is_dir":true},...]}

// 指定远程目标（收藏夹“文件传输”快捷按钮即走这条）
{"path":"/data/inbox","host":"192.168.1.10","port":5900}
// → {"path":"/data/inbox","entries":[...],"target":"192.168.1.10:5900"}
```

指定 `host` 时只连该主机（失败返回 502，不回退本地目录）；未指定时沿用本机探测到的 VNC Server，失败回退本地目录列举。

#### `POST /rfb/file/upload` 上传（支持断点续传）

multipart/form-data：`file=@local.bin` + `path=/remote/dir/file.bin` + `offset=0`（可选，已传输字节数）。

#### `POST /rfb/file/download` 下载（支持断点续传）

```json
{"path":"/remote/file.dat","offset":0}
```

返回二进制流；Content-Length = `size - offset`。

#### `POST /rfb/file/md5` MD5 校验

```json
{"path":"/remote/file.dat","offset":0,"length":65536}
// → {"md5":"d41d8cd98f00b204e9800998ecf8427e","offset":0,"length":65536}
```

### VNC / Shell 静态页面接口

| 路径 | 用途 |
|---|---|
| `GET /vnc/` | noVNC 静态文件服务 |
| `GET /vnc/vnc.html` | noVNC 入口页面（pywebview VNC 标签页加载） |
| `GET /websockify` | VNC WebSocket 代理（浏览器 ↔ VNC Server） |
| `GET /shell/` | Shell CLI GUI 入口页面（pywebview Shell 标签页加载） |
| `GET /shell/index.html` | Web Terminal 页面 |

---

## CLI 三角色

统一入口：`uv run local-drop-shell`（别名 `uv run ldrop`）。

| 角色 | 默认 | UI 层 | 说明 | 入口参数 |
|---|---|---|---|---|
| `gui` | ✅ | Tk + pywebview 标签页 | Tk 主窗口（文件/拖放/传输/日志）+ 两个 pywebview 标签页（Shell、VNC） | `--role gui --host ... --folder ...` |
| `server` | | 无 | 纯 aiohttp 服务端（无 GUI，后台运行） | `--role server --host 0.0.0.0 --port 10022` |
| `client` | | 无 | 连接远程端（交互式 shell 或单次命令） | `--role client --url http://IP:PORT [-c "命令"]` |

### 交互式 shell 内置命令

| 命令 | 说明 |
|---|---|
| `exit` / `quit` / `q` | 退出客户端 |
| `cd <目录>` | 设置后续命令的工作目录 |
| `pwd` | 显示当前工作目录 |
| `clear` / `cls` | 清屏 |
| `!<命令>` | 执行**本地**命令（不发送到服务端） |
| `upload <本地路径> [远程路径]` | HTTP multipart 上传 |
| `download <远程路径> [本地路径]` | HTTP multipart 下载 |
| `rsync <本地路径> [远程路径]` | rsync 增量同步（push） |
| `rsync-pull <远程路径> [本地路径]` | rsync 增量拉取（pull） |
| `rfb-upload <本地路径> [远程路径]` | RFB FTP 断点续传上传 |
| `rfb-download <远程路径> [本地路径]` | RFB FTP 断点续传下载 |
| `rfb-list [目录]` | RFB FTP 列出远程目录 |
| `help` / `?` | 显示帮助 |

---

## VNC 探测策略

```
启动服务端
  │
  ├─ 扫描端口 5900-5909 与 15900-15909（netstat -aon | findstr）
  │    │
  │    └─ 找到监听 PID → tasklist /FI "PID eq N" → CSV → 匹配进程名
  │       tvnserver / vncserver / winvnc / uvnc / tightvnc / realvnc
  │       │
  │       ├─ 命中 → 兼容模式：WebSocket 代理直接连接该端口
  │       │        跳过注册表写入和 tvnserver 启动
  │       │        ✅ 零权限要求
  │       │
  │       └─ 未命中 → 先判断 TightVNC 是否已安装
  │          （检查 %ProgramFiles%\TightVNC\tvnserver.exe + 卸载注册表项，64/32 位视图）
  │          │
  │          ├─ 已安装 → 内嵌模式
  │          │  自动递增 RFB 端口避免冲突（默认 15900）
  │          │  释放 .cache/tvnc/ 二进制
  │          │  写注册表 SOFTWARE\\TightVNC\\Server
  │          │  启动 tvnserver -install / -start
  │          │  启动监控协程：tvnserver 退出则本进程退出
  │          │  ⚠️ 需要管理员权限
  │          │
  │          └─ 未安装 → 用内嵌 MSI 自动安装（静默 + UAC 提权）
  │             msiexec /i "assets/tightvnc-2.8.87-gpl-setup-64bit.msi" /qn /norestart
  │             ShellExecuteExW(lpVerb="runas") 弹 UAC 并等待 msiexec 退出
  │             用户取消 UAC / 安装失败 → 记录日志后继续启动，不阻塞服务
  │             已有其他 VNC Server 在运行（如 UltraVNC）→ 跳过安装，避免抢占 5900
  │
  └─ 启动 noVNC HTTP 服务 + WebSocket 代理 /websockify
```

**端口汇总**：

| 服务 | 默认端口 | 说明 |
|---|---|---|
| 主 HTTP 服务（Shell / upload / rsync / RFB / Shell GUI） | `10022` | 可通过 `--port` 或环境变量 `HTTP_SHELL_PORT` 覆盖 |
| VNC RFB（内嵌模式） | `15900` | 自动递增避免冲突 |
| noVNC HTTP | 与主服务复用 | `/vnc/` 路径 |
| Shell GUI HTTP | 与主服务复用 | `/shell/` 路径 |
| WebSocket 代理 | 与主服务复用 | `/websockify` 路径 |

---

## 协议参考

### 1. TightVNC RFB 文件传输扩展（复用通道）

来源：`tightvnc-2.8.90-gpl/ft-common/FTMessage.h`、`ft-client-lib/FileTransferInterface.h`、`file-lib/`。

所有扩展消息以 `0xFC000000` 掩码识别（RFB 扩展消息前缀 `0xFC` + 3 字节消息 ID）。消息体：

```
UINT32 messageId        // 4 字节小端
UINT8[] messageBody     // 依 messageId 不同
```

`StringUTF8`：`UINT32 size` + `UINT8 buffer[size]`；路径 Unix 风格 `/C:/folder`，文件夹末尾无斜杠。

核心消息对：

| 消息 ID | 名称 | 方向 | 用途 |
|---|---|---|---|
| `FC000102` | FILE_LIST_REQUEST | C→S | 枚举远程目录 |
| `FC000103` | FILE_LIST_REPLY | S→C | 压缩块（zlib 可选）：filesCount + 每条 fileSize/modTime/fileFlags/relFilename |
| `FC000104` | MD5_REQUEST | C→S | 请求远程文件指定偏移块的 MD5 |
| `FC000105` | MD5_REPLY | S→C | 16 字节 MD5 摘要 |
| `FC000106` | UPLOAD_START_REQUEST | C→S | 开始上传 |
| `FC000107` | UPLOAD_START_REPLY | S→C | 确认 |
| `FC000108` | UPLOAD_DATA_REQUEST | C→S | 携带 CompressedBlock |
| `FC000109` | UPLOAD_DATA_REPLY | S→C | 确认 |
| `FC00010A` | UPLOAD_END_REQUEST | C→S | 结束上传 |
| `FC00010B` | UPLOAD_END_REPLY | S→C | 确认 |
| `FC000111` | MKDIR_REQUEST | C→S | 远程创建目录 |
| `FC000113` | REMOVE_REQUEST | C→S | 远程删除文件 |
| `FC000119` | LAST_REQUEST_FAILED_REPLY | S→C | 兜底失败回复 |

`CompressedBlock` 结构：

```
UINT8 compressionLevel       // 0 = 不压缩，[1..9] = zlib 压缩
UINT32 compressedSize
UINT32 uncompressedSize
UINT8 compressedData[compressedSize]
```

### 2. Rsync 增量同步协议

来源：`http_shell_cli_vnc/http_shell_cli_vnc.go` 中的 rsync 实现（Adler-32 + MD5 双阶段）。

**双阶段校验和**：

| 阶段 | 校验和 | 长度 | 用途 |
|---|---|---|---|
| 第一阶段 | Adler-32 滚动校验和 | 4 字节 | 快速滑动窗口，O(1) 更新 |
| 第二阶段 | MD5 强校验和（混入 seed） | 16 字节 | 防碰撞强验证 |

**Delta Op**：

| Op | 含义 | 附加字段 |
|---|---|---|
| 0 | EOF | 无 |
| 1 | Literal（字面数据） | length + data |
| 2 | Block（引用远程块） | length（块索引） |

**自动回退**：远程文件不存在 / blockset / delta / apply 任一失败 / MD5 不匹配 / 增量效率 < 20% 且 > 1MB。

### 3. RFB FTP 扩展（TightVNC 风格）

来源：`http_shell_cli_vnc/http_shell_cli_vnc.go` 中的 RFB FTP 消息。HTTP 层 `/rfb/file/*` 接口封装此协议。

消息子类型：

| 子类型 | 值 | 用途 |
|---|---|---|
| CCSRST | 0x11 | 压缩支持检查 |
| CFLRST | 0x12 | 文件列表请求/响应 |
| CMDRST | 0x13 | 创建目录 |
| CFRRST | 0x14 | 删除文件 |
| CFMRST | 0x15 | 移动/重命名 |
| CFURST | 0x16 | 开始上传 |
| CUDRST | 0x17 | 上传数据块 |
| CUERST | 0x18 | 上传结束 |
| CFDRST | 0x19 | 开始下载 |
| CDDRST | 0x1A | 下载数据块 |
| CDSRST | 0x1B | 目录大小 |
| CM5RST | 0x1C | MD5 校验 |

### 4. 喷泉码（单工传输）

来源：`AirFountainZulu` / decimen v3（`qr/core/Wire.kt`、`qr/core/Container.kt`）。

**Wire 帧格式**（每帧自描述，无需握手，小端，22 字节头部）：

```
偏移  长度  字段
0     1     magic 0xD1
1     1     magic 0xC3
2     1     version = 3
3     1     flags       低 4 位必须理解
4     2     sessionId   每次发送随机生成
6     4     seq         喷泉码 PRNG 种子输入
10    2     k           源块数
12    2     blockLen    每帧载荷字节数
14    4     totalLen    受保护容器（DCF2）总长度
18    4     payloadFnv  整个容器的 FNV-1a，完成后校验
22    blockLen  block   喷泉码编码块
```

**DCF2 文件容器**（49 字节头部）：

```
偏移  长度  字段
0     4     magic "DCF2"
4     1     compression  0=none 1=gzip
5     2     nameLength
7     2     typeLength
9     4     fileLength
13    4     transmittedLength
17    32    sha256(原始文件)
49    nameLength  name UTF-8
49+nameLength  typeLength  type UTF-8
49+nameLength+typeLength  transmitted bytes
```

设计要点：每帧自描述，接收端可中途锁定；发送端重启产生新 `sessionId`，接收端自动重新开始；gzip 仅在确实变小时启用（≥ 768 字节且非已压缩格式），熵编码媒体跳过；SHA-256 完成校验前不向用户提供文件。

### 5. noVNC / WebSocket 代理

内嵌 noVNC 1.4.0（来自 `http_shell_cli_vnc/public_fs/novnc/`），WebSocket 代理桥接浏览器 ↔ VNC Server。

```
pywebview VNC 标签页
    ├─ http://127.0.0.1:10022/vnc/vnc.html
    │   └─ noVNC client
    └─ WebSocket → ws://127.0.0.1:10022/websockify
                      └─ TCP → 127.0.0.1:5900+ / 15900+ (VNC Server)
```

---

## 目录

| 路径 | 说明 |
|---|---|
| `assets/02-windows-95.css` | Windows 95 设计 token（Tk 主界面配色/字体参考） |
| `assets/drop3.ico` | 软件图标（Win95 风格，多尺寸帧） |
| `assets/screenshot1.png` | TightVNC File Transfer 界面原型图 |
| `assets/tightvnc-2.8.87-gpl-setup-64bit.msi` | 内嵌 TightVNC 安装包（未安装时静默提权安装，随 exe 打包） |
| `src/localdropshell/cli.py` | **入口**（三角色解析 + 启动 aiohttp + 分发到 gui / server / client） |
| `src/localdropshell/ui/tk_main.py` | **Tk 根窗口**：菜单栏 + Notebook 标签页 + 状态栏 |
| `src/localdropshell/ui/tk_transfer.py` | **文件传输标签页**：远程地址栏（ip[:端口]）/ 远程目录 / 协议下拉、远程面包屑导航（⬆ 上级 + 层级跳转）、本地/远程 Treeview、进度条、日志、批量；拖入本地后自动打开所在文件夹 |
| `src/localdropshell/ui/tk_favorites.py` | **收藏夹标签页**：备注名 / ip:port / 传输协议 / 文件传输默认路径 / 多个快捷路径（每条路径一个直达按钮），提供“文件传输”“远程桌面”“载入”“编辑”“删除”操作 |
| `src/localdropshell/settings.py` | 配置目录（`D:/JustSmart/LocalFileDropShell` → 回退 `%APPDATA%/LocalFileDropShell`）+ `favorites.json` / `settings.json`（传输协议）原子读写 + `ip[:端口]` 解析 |
| `src/localdropshell/webview_win.py` | pywebview 独立窗口进程：主线程 `webview.start()`，打包后由 exe `--webview-window <json>` 再启动一次 |
| `src/localdropshell/ui/tk_drop.py` | **Win32 拖放**：tkinterdnd2 + WM_DROPFILES 关闭 WebView2 内置拖放 |
| `src/localdropshell/ui/web_shell.py` | **Shell CLI GUI pywebview 子窗口**：xterm.js Web Terminal |
| `src/localdropshell/ui/web_vnc.py` | **VNC pywebview 子窗口**：noVNC + WebSocket 代理 |
| `src/localdropshell/shell.py` | ShellExecutor（pwsh → powershell → cmd，GBK/UTF-8 自适应，超时 Kill） |
| `src/localdropshell/tightvnc.py` | RFB 扩展客户端（FC000102~FC000119 消息对） |
| `src/localdropshell/rsync.py` | rsync Adler-32 + MD5 块级 delta |
| `src/localdropshell/rfbftp.py` | RFB FTP 消息（CCSRSST / CFLRST / CFURST / CM5RST / ...） |
| `src/localdropshell/vnc.py` | VNC 探测（端口 + 进程名）/ 内嵌 tvnserver 启动 / WebSocket 代理 / 内嵌 MSI 静默提权安装 |
| `src/localdropshell/fountain.py` | 喷泉码编解码（Wire 帧 + DCF2 容器） |
| `src/localdropshell/transfer.py` | 传输调度（TightVNC 优先 / HTTP 回退 / 单工判定 / 校验） |
| `src/localdropshell/server.py` | aiohttp 服务端路由注册（/exec /upload /download /rsync/* /rfb/* /vnc/ /shell/ /websockify /health） |
| `build_exe.py` | PyInstaller 打包（自动嵌入 `assets/`，含 TightVNC MSI；可选 `--with-vnc-bundle`），产物 `LocalFileDropShell_{时间戳}.exe` |

参考工程：
- **diffimg 图像差分**：pywebview + Tk + Windows 95 风格界面的组织方式（`src/odiffdiff/` 结构、`build_exe.py` venv 重执行 + 依赖探测 + 显式 collect-all）
- **http_shell_cli_vnc**：HTTP Shell / rsync / RFB FTP / VNC 探测与内嵌 / CLI 客户端交互式 shell（Go 实现，本项目用 Python 原生重写）
- **AirFountainZulu**：喷泉码 decimen v3（Wire.kt / Container.kt）
- **TightVNC 2.8.90**：RFB FTP 协议（ft-common / ft-client-lib / file-lib）

---

## 开发 / 运行 / 打包

```bash
uv sync                                                        # 安装依赖（Python 3.10+）
uv run local-drop-shell --role gui                              # GUI（Tk 主界面 + pywebview 标签页，默认）
uv run local-drop-shell --role server --host 0.0.0.0 --port 10022
uv run local-drop-shell --role client --url http://192.168.1.100:10022
uv run local-drop-shell --role client --url ... -c "Get-Process"
uv run python build_exe.py                                      # 普通打包 dist/LocalFileDropShell_{时间戳}.exe
uv run python build_exe.py --with-vnc-bundle                    # 额外内嵌 TightVNC + noVNC 前端
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HTTP_SHELL_PORT` | `10022` | 服务端监听端口 |
| `HTTP_SHELL_HOST` | `0.0.0.0` | 服务端监听地址 |
| `HTTP_SHELL_TIMEOUT` | `30` | 默认命令超时秒数 |
| `HTTP_SHELL_VNC_HOST` | `127.0.0.1` | 未在界面填远程地址时，服务端连接的 VNC 主机 |
| `LOCAL_DROP_CONFIG_DIR` | 空 | 覆盖收藏夹配置目录（调试 / 便携部署用） |

打包产物：单文件 exe（约 10~15 MB，`--with-vnc-bundle` 额外 ~10 MB），零运行时依赖。

---

## 设计决策

1. **混合 UI 架构（Tk + pywebview）**：文件/拖放/传输/日志这些 Win32 原生操作场景，tkinterdnd2 的 WM_DROPFILES 直接可控且无 WebView2 内置拖放冲突；而 VNC（noVNC + WebSocket）和 Shell CLI GUI（xterm.js）是现成的 Web 应用，pywebview 内嵌比 Tk 重造轮子更省力。两者通过 aiohttp 本地 HTTP 服务解耦
2. **TightVNC RFB 通道优先**：工控机一般预装 TightVNC Server（监听 5900），探测到后直接复用 `FC000102~FC000119` FTP 扩展消息，**目标端无需启动本软件**——大幅降低部署门槛
3. **HTTP / Shell / rsync / RFB / VNC 共享同一 aiohttp 服务端**：避免多端口、多进程；端口 10022 一条入口；pywebview 标签页和 CLI 客户端都连接同一服务端
4. **Rsync 自动回退**：增量效率 < 20% 或任一环节失败时自动退回全量传输，不会卡死
5. **VNC 探测优先，内嵌兜底**：优先复用已有 VNC Server（零权限），无则内嵌（需管理员）；两种模式透明切换
6. **CLI 单进程多角色**：同一个 `LocalFileDropShell.exe` 支持 gui / server / client 三种模式，部署最简
7. **单工喷泉码判定阈值**：TCP 连通但无法获得稳定响应（RTT > 2s / 丢包率 > 50%）→ 自动切换 DCF2 + decimen v3；连通稳定则走普通传输通道
8. **编码自适应**：Windows cmd 默认 GBK 输出，先 UTF-8 校验，失败则调 `MultiByteToWideChar(CP_ACP, ...)` 转 UTF-8；pwsh 直接 UTF-8
9. **路径格式统一**：Unix 风格 `/X:/...`，与 TightVNC FTP 协议保持一致
10. **Win95 风格跨 UI 层统一**：assets/02-windows-95.css 的 token（`--bg` `--chrome` `--title` `--font` 等）同时用于 Tk 主界面（配色/字体/边框）和 pywebview Shell/VNC 标签页（内嵌 Web 页内），视觉一致
11. **TightVNC 缺失自动安装**：`assets/tightvnc-2.8.87-gpl-setup-64bit.msi` 随 exe 内嵌（PyInstaller `--add-data`）；启动 gui/server 时若「安装目录 + 卸载注册表项」都查不到，则 `ShellExecuteExW(lpVerb="runas")` 提权执行 `msiexec /i <msi> /qn /norestart`，等待退出码后复查安装结果，失败/取消 UAC 只记录日志不阻塞启动（参考 `silent安装器/mosquitto_installer` 的内嵌安装器做法）
12. **远程目标由 UI 透传到服务端**：文件传输页顶栏的 `ip[:端口]` 解析后，作为 `host`/`port` 字段随 `/rfb/file/list`、`/upload` 请求下发；服务端 `_open_rfb_ftp(app, host, port)` 直连该 VNC，避免为每台工控机再起一个服务端进程
13. **收藏夹随配置目录走**：优先 `D:/JustSmart/LocalFileDropShell`（与运维其它工具统一归档），目录创建失败则回退 `%APPDATA%/LocalFileDropShell`；`favorites.json` 采用临时文件 + `os.replace` 原子写，避免断电损坏
14. **传输协议显式选择**：文件传输页顶部“协议”下拉（默认 RFB File，LocalSend / FTP / HTTP / RSYNC 为占位项），选择结果写入 `settings.json` 下次启动自动恢复，避免靠端口号猜测通道；占位协议仅在日志中提示，实际仍走 RFB File 通道
15. **pywebview 窗口放独立子进程**：pywebview 5.x 的 `webview.start()` 会校验 `threading.current_thread().name == 'MainThread'`，Tk 主界面已占用主线程，因此 VNC / Shell 窗口改由 `sys.executable --webview-window <json>` 再启动一个自身进程（打包后即同一 exe），子进程在主线程正常起 WebView2；PyInstaller 侧按 SvgOverlayPy 的做法 `--collect-all webview/pythonnet/clr_loader` + 显式 `hidden-import webview.platforms.winforms/edgechromium`

---

## 安全提示

本服务设计用于**离线内网工控机维护**，不提供身份认证。如需增强：
- 前端反向代理（Nginx）加 Basic Auth / IP 白名单
- 通过 VPN / 专线传输
- 对敏感命令和文件传输启用 HTTPS（aiohttp 支持 ssl_context 参数）
- VNC 远程桌面默认无密码，建议在受控网络中使用
- 嵌入式 Shell 执行需信任调用方，不要暴露到公网

---

## License

MIT
