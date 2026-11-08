# InternetProxyPy — 基于 Python 的工控机互联网代理

局域网内的一批工控机（或任意设备），其中**只有一台连接了互联网**。本项目让这台
工控机充当**代理网关（服务端）**，其余设备运行**客户端**，通过 TCP / UDP 代理的
方式共享这唯一的互联网出口。整体原理与 VPN 类似：客户端把流量交给服务端，
由服务端代为收发并回传结果，从而让"无外网"的局域网设备也能上网。

纯 Python 实现，客户端 / 服务端同属一套代码，按角色启动。

## 特性

- **TCP / UDP 双协议代理**：覆盖绝大多数上网场景（HTTP(S)、DNS 在内的 UDP 请求等）。
- **免认证**：配套给封闭局域网内的工控机使用，不设用户名密码，连接即用。
- **规则代理 / 全局代理**：默认**规则代理**——目标为局域网 / 回环 / 链路本地 IP
  时直连不走网关，域名与其余流量仍经网关；UDP 53 的 DNS 请求始终经网关转发，
  避免局域网 DNS 无互联网出口导致解析失败；**全局代理**则所有流量一律走网关。
  模式与角色一起持久化在配置文件中。
- **系统透明代理（虚拟网卡，默认开启）**：客户端自动创建 wintun 虚拟网卡
  （InternetProxyPy，198.18.0.1）并接管系统 TCP/UDP 流量，**任何应用无需配置
  SOCKS5 即可上网**，DNS 也随系统流量一起走代理。核心为内嵌的
  [tun2socks](https://github.com/xjasonlyu/tun2socks) + wintun 驱动，需要管理员
  权限（exe 已申请 UAC）；缺依赖或无权限时自动回退纯 SOCKS5 模式并写日志。
  停止代理时自动清理虚拟网卡路由。
- **默认客户端模式**：双击 exe 直接以客户端运行，网关 IP 未配置时回退
  `127.0.0.1`（本机开启服务端联调）；在设置里选择运行角色即可切到服务端。
- **默认端口 10082**：SOCKS5 等代理端口默认使用高端口 **10082**，避开常用服务
  端口冲突，也减少被随机扫描的概率。
- **静默启动**：界面启动时立即静默最小化到托盘，用户无感知——代理开机即工作，
  不打扰任何前台操作；需要时从托盘唤出主窗口查看日志或修改设置。
- **开机自启（可选）**：设置页勾选后注册计划任务（最高权限，登录时**不弹 UAC**），
  并写入 `config.json` 的 `autostart`；非管理员环境下退回注册表启动项。登录后
  程序随系统启动，并自动把已安装的 nginx / v2ray 服务拉起，重启现场无需手动操作。
- **客户端 VMess 上行（可选）**：客户端可不再连本软件网关，改为由内嵌 v2ray
  以 **VMess**（TCP / WS，可加 TLS）连接 v2ray 服务端，复用成熟的加密与传输方案；
  本机 SOCKS5 与透明代理端口保持不变，局域网其它设备无感知。详见下文。
- **托盘后台运行**：基于 Tkinter + tkinterdnd2（`TkDND2` 扩展）创建系统托盘图标，
  代理在工作期间常驻后台，不弹出多余的窗口干扰生产现场。
- **配置持久化**：将可上网工控机（网关）的 IP 与代理端口写入本地配置文件，
  下次启动自动读取，无需重复输入。配置目录默认
  `D:/JustSmart/InternetProxyPy`（不存在时自动创建）；该分区/路径不可用时
  回退到 `%APPDATA%/InternetProxyPy`。注意目录显示为
  `D:\JustSmart\InternetProxyPy`（Windows 反斜杠）。
- **Windows 95 风格界面**：后台日志输出与设置界面采用
  [02-windows-95.css](assets/02-windows-95.css) 的设计令牌（`--bg`、`--chrome`、
  `--title` 等），经典灰石窗口风格。
- **应用图标**：托盘与窗口使用 [assets/cat.ico](assets/cat.ico)。

## 本软件与 SSH 隧道（ssh -L/-D）对比

| 维度 | InternetProxyPy | SSH 隧道（`ssh -L` / `ssh -D`） |
|---|---|---|
| 部署前提 | 仅需在网关机跑一个免认证代理进程 | 网关需运行 SSH 服务，且每台客户端要有 SSH 账号/密钥 |
| 认证方式 | 免认证（面向封闭局域网） | 依赖用户名密码或密钥，需维护凭据 |
| 协议覆盖 | TCP + UDP 双协议（HTTP(S)、DNS 等 UDP 场景可用） | `-L` 单端口 TCP 转发；`-D` SOCKS5 但 UDP 需额外配置且常不可用 |
| 多客户端 | 一个服务端并发服务整个局域网，免配置 | 每个客户端各自建立 SSH 连接，连接数随设备线性增长 |
| 配置方式 | 图形界面/配置文件持久化，双击 exe 即用 | 命令行参数或 ssh_config，工控机现场操作门槛高 |
| 常驻运行 | 托盘静默后台常驻，开机即用、随系统运行 | 需额外手段（autossh、计划任务）保活，断线需重连逻辑 |
| 日志排查 | 内置日志页实时输出，现场可直接查看 | 依赖客户端 `-v` 日志，分散在各台机器上 |
| 运维成本 | 零（复制 exe → 双击 → 填网关 IP） | 密钥分发、账号权限、sshd 安全加固等持续维护 |
| 安全边界 | 仅适合可信封闭局域网（免认证即无防护） | SSH 本身加密+认证，适合不可信链路 |

一句话总结：**SSH 隧道是通用远程工具顺带做转发，本软件是为"封闭局域网批量工控机共享一个外网出口"场景专门做的零配置常驻代理**。如果链路不可信或需要加密与访问控制，应选 SSH/VPN；如果目标是现场免运维、开机即用，本软件更合适。

## 安装器（nginx / v2ray）

主界面新增「安装器」标签页，并在**首次启动时自动请求管理员权限（UAC）静默安装**：

| 组件 | 来源 | 安装位置 | 监听 | 说明 |
|---|---|---|---|---|
| v2ray | `assets/v2ray-windows-64.zip` | `D:/JustSmart/InternetProxyPy/v2ray` | `0.0.0.0:11080` | SOCKS5 服务端，账号 `justsmart` / 密码 `justsmart`，开启 UDP |
| v2ray | 同上 | 同上 | `0.0.0.0:11081` | **VMess 入站**，UUID 首次安装时生成并写入 `installer.json` |
| nginx | `assets/nginx-1.31.8.zip` | `D:/JustSmart/InternetProxyPy/nginx` | `0.0.0.0:18080` | 静态站点，`/status` 为健康检查 |

- 安装时写入 `nginx/conf/nginx.conf` 与 `v2ray/config.json`，并按参考文档放行防火墙端口；
- 安装记录写入 `D:/JustSmart/InternetProxyPy/installer.json`；自动安装只尝试一次，
  取消 UAC 后可在「安装器」页手动重试；
- 「安装器」页提供 一键安装/修复、启动服务、停止服务、打开目录、刷新状态、复制 UUID，
  并实时显示两个组件的安装与运行状态；
- 端口取 11080 / 11081 / 18080 而非 1080 / 8080，避免与常见代理、Web 服务冲突。

参考实现见 [setup_v2ray_server_at_windows.md](reference/setup_v2ray_server_at_windows.md)。

## v2ray 模式教程（VMess 服务端 / 客户端）

本软件内置两条代理通道，按现场情况二选一：

| 通道 | 客户端 → 服务端 | 适用 |
|---|---|---|
| 内置隧道（默认） | 自研 TCP/UDP 协议，免认证 | 封闭局域网、追求零配置 |
| **v2ray 模式** | 内嵌 v2ray 以 **VMess** 连接 v2ray 服务端 | 需要成熟加密 / 传输伪装（WS、TLS） |

> 配好服务端后，客户端**只填服务端 IP、选好模式即可**：服务端会提供一个只读
> 发现页（`http://<服务端IP>:18081/`），保存时自动取回 UUID、端口、传输等参数，
> 不用再手工抄写。

```
工控机 B（客户端，v2ray 模式）                        工控机 A（服务端 + v2ray 服务端）
浏览器 / PLC 软件 ─┐
                  ├─▶ 本机 SOCKS5 0.0.0.0:10082
tun2socks（透明代理）┘        │
                              └── VMess(TCP/WS/TLS) ──▶ 0.0.0.0:11081 ──▶ freedom ──▶ 互联网
局域网其它设备 ──▶ B:10082（SOCKS5，免认证）
```

启用 v2ray 模式后，客户端**不再连接本软件的 10082 服务端**，网关 IP 字段不参与转发；
本机 SOCKS5 端口仍是 10082，透明代理与局域网其它设备的使用方式完全不变。

### 一、服务端（连了互联网的那台工控机）

1. **启动软件并选角色**：设置页角色选「服务端」，端口保持 `10082`，点「保存」。
   服务端本身不需要开透明代理。
2. **安装 v2ray**：打开「安装器」页。首次启动会自动弹 UAC 静默安装 nginx / v2ray；
   若当时取消了 UAC，点「一键安装/修复」重试（需要管理员权限）。
3. **确认服务已启动**：「安装器」页状态应显示
   ```
   nginx  已安装  运行中
   v2ray  已安装  运行中
   VMess  11081  运行中
   ```
   未运行时点「启动服务」。
4. **确认发现页可用**：服务端角色启动后会在 `0.0.0.0:18081` 起一个只读网页，
   在任意一台局域网机器上打开：
   - `http://<服务端IP>:18081/` —— 人类可读，列出 VMess 参数、客户端操作步骤；
   - `http://<服务端IP>:18081/v2ray.json` —— 机器可读，供客户端自动取参。

   页面里的 UUID 与 `installer.json` 的 `vmess_uuid` 一致（也可在「安装器」页
   「复制 UUID」，但客户端已无需手抄）。
5. **核对防火墙**（安装时已自动放行；发现页端口 18081 在服务端角色启动时按需放行）：
   ```powershell
   netsh advfirewall firewall show rule name=InternetProxyPy-v2ray-VMess
   netsh advfirewall firewall show rule name=InternetProxyPy-discovery
   ```
6. **自检监听**：
   ```powershell
   netstat -ano | findstr ":11081 :18081"
   ```
   出现 `LISTENING` 表示 VMess 入站与发现页均已就绪；`v2ray.exe` 由安装器在
   `D:/JustSmart/InternetProxyPy/v2ray` 下以 `config.json` 启动。

服务端端口一览：

| 端口 | 协议 | 用途 |
|---|---|---|
| `10082` | TCP/UDP | 本软件内置隧道的服务端（v2ray 模式可不使用） |
| `11080` | TCP/UDP | v2ray SOCKS5（`justsmart` / `justsmart`），给浏览器等直连用 |
| `11081` | TCP | v2ray **VMess 入站**，供本软件客户端连接 |
| `18081` | TCP | 服务端**发现页** `/` 与 `/v2ray.json`（服务端角色运行时开启） |
| `18080` | TCP | nginx 静态站点，`/status` 健康检查 |

> 用第三方 v2ray 服务端时：在其配置里加一个 vmess 入站即可，`clients[].id`
> 填你的 UUID、`alterId` 为 `0`、`security` 用 `auto`，端口与客户端填写保持一致；
> 这种情况下没有发现页，客户端需手工填写 UUID 等参数。

### 二、客户端（没有外网的其它工控机）

1. **启动软件**（默认客户端角色），切到「设置」页，勾选
   「**通过 v2ray 服务端（VMess）代理互联网**」。
2. **只填服务端 IP，选好模式**：「服务端」填服务端 IP（或域名）；「代理模式」选
   **规则代理**（局域网设备直连，推荐）或 **全局代理**（全部走 VMess）。
   UUID、端口、传输等保持空白即可。
3. **保存即自动取参**：点「保存」时程序访问 `http://<IP>:18081/v2ray.json`，
   自动填入 VMess 端口、UUID、传输、WS 路径、TLS，然后重启代理引擎。日志页出现：
   ```
   正在从 http://192.168.1.10:18081/ 获取服务端配置 ...
   已获取服务端配置: VMess 192.168.1.10:11081 UUID 3f2b6c1e-…（保存后生效）
   VMess 上行: 192.168.1.10:11081（TCP）→ 本机 SOCKS5 0.0.0.0:10082
   [tun] 系统透明代理已接管（虚拟网卡）
   ```
   也可先点「**从服务端获取配置**」按钮，确认参数无误后再保存。
4. **参数对照**（自动获取的值，可手动覆盖，用于第三方 v2ray 服务端）：

   | 字段 | 来源 |
   |---|---|
   | 服务端 | 你填写的 IP / 域名 |
   | 端口 | 发现页 `vmess.port`，本软件默认 `11081` |
   | UUID | 发现页 `vmess.uuid` |
   | 传输 / WS 路径 | 发现页 `vmess.network` / `vmess.path`，默认 `TCP` / `/vmess` |
   | TLS | 发现页 `vmess.tls`，默认关闭 |

5. **验证**：直接打开浏览器即可上网（透明代理已接管系统流量）；
   局域网其它设备把代理设为「客户端 IP:10082」（SOCKS5，免认证）也能上网，
   必要时在客户端放行入站 10082。
6. **可选**：勾选「开机自启(含 nginx/v2ray)」，会注册最高权限计划任务，
   重启后自动拉起程序和已安装的 nginx / v2ray。

客户端生成的 v2ray 配置保存在 `D:/JustSmart/InternetProxyPy/v2ray/client.json`，
每次启动按设置页重新生成；规则模式下私网网段直连，全局模式全部走 VMess。

### 三、互通排查

| 现象（日志页原文） | 原因 / 处理 |
|---|---|
| `未安装 v2ray，无法使用 VMess 上行` | 客户端本机也要安装 v2ray：安装器页「一键安装/修复」 |
| `未填写 v2ray 服务端地址` | 设置页「服务端」为空 |
| `获取配置失败: 无法访问 http://…:18081/` | 服务端未运行、角色不是「服务端」，或 18081 被防火墙/安全软件拦截；确认后重试，或手工填 UUID |
| `保存失败: UUID 无效，且未能从服务端获取配置` | 同上；也可能是第三方 v2ray 服务端（无发现页），需手工填写 UUID |
| `v2ray 退出（码 …）` | 看 `v2ray/client-error.log`；多为服务端端口 / UUID / 传输不一致 |
| `v2ray 启动超时：本机 SOCKS5 10082 未就绪` | 本地端口被占用：换端口或结束残留进程 |
| `VMess 上行不可用，回退到内置隧道` | 上面任一失败都会回退；按提示修正后重新保存 |
| 能 ping 通服务端但连不上 | 服务端 11081 未放行防火墙，或 v2ray 未启动 |
| 客户端能上网、局域网设备不能 | 局域网设备要填「客户端 IP:10082」，并在客户端放行该端口 |
| 改完设置不生效 | 保存会自动重载；仍异常时先「停止代理」再「开始代理」 |

安全提示：VMess 只保证传输加密与完整性，UUID 等同密码，请勿外泄；软件本身
面向可信局域网，若需暴露到公网，建议启用 WS + TLS 并配合端口收敛。

## 现场踩坑记录（v2ray + wintun 联调）

以下为工控机实测 v2ray + wintun 客户端联调时遇到的问题，按现象归档。

| 现象 | 根因 | 处理 |
|---|---|---|
| 服务端 `access.log` 出现 `invalid user > Not Found`，客户端隧道能连但 HTTPS 全部失败 | 两端系统时钟相差超过 VMess 约 90 秒的容差 | 以服务端时间为准校时（可用服务端 `18081` 的 HTTP `Date` 响应头），再重启客户端；无外网的工控机建议固定从服务端同步时间 |
| wintun 已 Up、用 IP 直连能返回 200，但域名解析全部超时 | 工控机原有 LAN DNS 已失效，而 wintun 网卡本身不下发 DNS | 把活动网卡 DNS 改为 `223.5.5.5`、`8.8.8.8`，并给 `InternetProxyPy` 网卡补 `223.5.5.5`；改前记录原值 |
| 远程执行 `schtasks /RL HIGHEST` 报 `Access is denied` | HTTP Shell 会话是 Medium 完整性，无法创建高权限任务 | 用 `Start-Process -Verb RunAs` 触发 UAC，弹窗出现在工控机控制台，需要现场点“是” |
| `--headless` 启动后看不到任何输出 | exe 为 windowed 构建，没有控制台 | 用进程、监听端口、网卡、路由和 `v2ray/client-error.log` 判断状态 |
| 手写的 `config.json` 不生效，程序仍按默认配置启动 | 文件带 UTF-8 BOM，`json.loads` 抛 `ValueError` 后被静默忽略 | PowerShell 用 `[IO.File]::WriteAllText` 配 `UTF8Encoding($false)` 落盘，不要用 `Set-Content -Encoding UTF8` |
| 客户端起不来，日志提示 `本机 SOCKS5 10082 未就绪` | 旧 v2ray 或上次残留进程占用端口 | 先确认并结束残留 v2ray，再启动客户端 |
| 工控机上同时存在监听 11080/11081 的 v2ray | 之前按服务端方式安装过，客户端不会自动停掉它 | 先确认来源，不需要时手动停止，避免与 10082 混淆 |
| `curl` 访问 HTTPS 报 `schannel: 0x80092013` | 证书吊销列表离线，无法校验证书 | 加 `--ssl-no-revoke` 先验证连通性；DNS 与路由正常后通常会恢复 |
| `ping` 服务端不通 | 代理只承载 TCP/UDP，不含 ICMP | 正常现象，用 `Test-NetConnection`、HTTP 请求验证 |

透明代理是否真正生效，按这四项判断：

1. 网卡 `InternetProxyPy` 状态 Up，地址 `198.18.0.1/24`；
2. `0.0.0.0/1` 与 `128.0.0.0/1` 路由指向 `198.18.0.1`；
3. `10082` 由客户端 v2ray 子进程监听；
4. 域名解析成功，HTTP 与 HTTPS 均返回 200。

另外，v2ray 模式下**服务端本机不监听 10082 是正常的**（10082 只用于客户端本地 SOCKS5）。
配套的排查脚本与步骤见 [proxy-joint-debug](.trae/skills/proxy-joint-debug/SKILL.md)，
远程执行方式见 [http-shell-ipc-powershell](.trae/skills/http-shell-ipc-powershell/SKILL.md)。

## 与参考实现对比：3proxy 与 wintun

`reference/` 下放了两份上游实现用于对照：**3proxy**（通用代理服务器，与本项目解决
同一类问题）和 **wintun**（Windows L3 TUN 驱动，本项目透明代理的底座）。这里只做
架构对照，没有复制它们的代码。

### 3proxy 1.0.3

`reference/3proxy-1.0.3-x64/` 是发行包（`3proxy.exe`、插件 DLL、`cfg/3proxy.cfg.sample`
与 man 文档），源码在 `reference/3proxy-master.zip` 的 `src/`。

实现要点：

- **配置即服务**：`3proxy.cfg` 每行声明一个监听服务（`socks`、`proxy`、`pop3p`、
  `smtpp`、`udppm` 等），一个进程同时跑多种协议；支持注册为 Windows 服务、
  `nscache` DNS 缓存、`timeouts` 超时组、ACL 与 `auth` 认证。
- **单进程 select/poll 事件循环**：`src/common.c` 的 `mypoll()` 用 `select()` 模拟
  `poll()`（Windows 上同样走 select），所有连接在同一线程轮转，超时统一取
  `conf.timeouts[]`；建连也走 `connectwithpoll()` 等可写。
- **每连接一个 `struct clientparam`**（`src/structures.h`）：携带双端 socket、读写
  缓冲、ACL/认证回调、限速与流量统计钩子；`FilePlugin.dll`、`TrafficPlugin.dll`、
  `WindowsAuthentication.dll` 通过函数指针挂载。
- **SOCKS5 在 `src/socks.c`**：实现 CONNECT / BIND / UDP ASSOCIATE，请求行缓冲
  1024 字节（`BUFSIZE`），UDP 报文缓冲 16384 字节（`UDPBUFSIZE`）。UDP ASSOCIATE
  可用 `-Ni` 向客户端通告一个"内部 NAT 地址"，客户端把数据报发到该地址，再由
  `udpsockmap()` 按 TCP 控制连接归位。
- **面向公网**：认证、ACL、计费、限速是它的卖点，因此配置项多、学习成本高。

与本项目的差异：

| 维度 | InternetProxyPy | 3proxy |
|---|---|---|
| 形态 | Python 单包，客户端 / 网关两种角色，PyInstaller 打成单 exe | C 守护进程 / Windows 服务，配置文件驱动 |
| 客户端接入 | 默认透明代理（wintun + tun2socks），也可直接用 SOCKS5 | 只提供标准 SOCKS/HTTP 等，需应用显式配置 |
| 网关侧协议 | 自定义"长度前缀地址 + 原始数据"隧道，网关不对外暴露 SOCKS5 | 直接对客户端提供 SOCKS5 等标准协议 |
| 认证 / ACL | 无（封闭局域网免配置） | 认证、ACL、插件、计费齐备 |
| 并发模型 | 每 TCP 连接、每 UDP 会话各一个线程，逻辑直观但千级连接时线程开销大 | 单线程 poll 复用，更适合高并发 |
| UDP | 客户端为每个（会话，目标）维护上行 socket，回包按真实源地址回填 SOCKS5 头 | `-Ni` 通告内部地址 + `udpsockmap` 归位 |
| 规模 | 11 个模块、约 1500 行 Python，现场改起来快 | 20 余年 C 代码，跨平台但阅读门槛高 |

### wintun

`reference/wintun/` 是完整源码：`api/`（用户态 DLL）与 `driver/`（内核驱动），
支持 Windows 7–11，驱动需签名、创建网卡需管理员权限。

实现要点：

- **适配器**：[api/adapter.c](reference/wintun/api/adapter.c) 提供
  `WintunCreateAdapter(name, type, guid)`，按名称/GUID 幂等复用；适配器信息落在
  注册表命名空间（`api/registry.c`、`api/namespace.c`），所以**进程退出后网卡仍然
  存在**。
- **会话与环**：[api/session.c](reference/wintun/api/session.c) 的
  `WintunStartSession()` 建立会话，内核与用户态通过环形缓冲区交换 IP 包，容量可在
  128 KiB – 64 MiB 之间选择；收包 `WintunReceivePacket()` →
  `WintunReleaseReceivePacket()`，发包 `WintunAllocateSendPacket()` →
  `WintunSendPacket()`，无包时等 `WintunGetReadWaitEvent()`。
- **驱动侧**：[driver/wintun.c](reference/wintun/driver/wintun.c) 处理 IRP，把 IP 包
  放进 ring，本身不解析、不转发任何协议——它只搬包。

与本项目的关系：

- 本项目**不直接调用 wintun API**：`assets/bin/wintun.dll` 由内嵌的 tun2socks 加载
  （`--device tun://InternetProxyPy`），TCP/IP 协议栈还原、DNS、UDP 会话都交给
  tun2socks；我们只负责起进程、配地址、加路由（[tun.py](src/internet_proxy/tun.py)）。
- 正因为适配器是持久的，才出现过"重名网卡被改名为 `InternetProxyPy 2`、
  `198.18.0.1` 已被占用（错误 5010）"的问题；现在启动前用 `_cleanup_adapters()`
  清残留、用 `_adapter_index()` 按 `ifIndex` 配置，绕开名字漂移。
- 路由策略（`0.0.0.0/1` + `128.0.0.0/1` 覆盖路由、网关 `/32` 直连防回环）属于
  Windows 路由表操作，wintun 完全不涉及，是本项目自己补的一层。
- 若绕过 tun2socks 直接用 wintun：可用 `ctypes` 调 `WintunCreateAdapter` /
  `WintunStartSession` 省掉一个外部进程，但必须在 Python 里实现 TCP/IP 栈，
  代价远大于收益，因此没有采用。

### 可以直接借鉴的点

- **3proxy 的 `-Ni` 内部 NAT 地址**：让应用把 UDP 统一发往一个中继端口，可显著
  减少客户端 UDP socket 数量，缓解 `WSAENOBUFS (10055)`；代价是要在客户端维护
  "控制连接 ↔ 数据报来源"的映射。
- **3proxy 的 poll 模型**：若以后要顶上千并发，可把"每连接一线程"改为单线程
  `selectors` 多路复用。
- **3proxy 的 ACL**：即使保持免认证，也可以只允许配置中的局域网网段连接，
  降低被误用风险。
- **3proxy 的 `nscache`**：网关侧加一层 DNS 缓存，减少上游查询、加快客户端解析。
- **wintun 的 `WintunEnumAdapters`**：比现在用 PowerShell `Get-NetAdapter` 轮询更快，
  也不受中文系统编码影响，可作为后续优化项。

## 版本号规则

版本号采用 **日期时间格式 `YY.M.D.H`**（年.月.日.24 小时制小时，均不补零），
每次发布取**发布时间**生成新版本号，例如 `26.11.7.12` 表示 2026 年 11 月 7 日
12 时发布的版本。版本号需同步维护以下位置：

- [pyproject.toml](pyproject.toml) 的 `project.version`
- [src/internet_proxy/__init__.py](src/internet_proxy/__init__.py) 的 `__version__`
- `uv.lock` 中本包（internetproxypy）的 `version`

## 工作原理

```
┌──────────────┐        局域网 (LAN)        ┌────────────────┐      ┌────────┐
│ 工控机 B      │──TCP/UDP──▶ 工控机 A      │  互联网出口      │──────▶│  外网   │
│ (客户端)      │            (代理服务端)     │                 │       │        │
│ 桌面/给网无关  │            │               │                 │       │        │
└──────────────┘            │               └────────────────┘       └────────┘
┌──────────────┐            │
│ 工控机 C      │──TCP/UDP──▶
│ (客户端)      │
└──────────────┘
```

- **服务端（网关工控机）**：开启了互联网连接，监听 SOCKS5 等 TCP / UDP 端口
  （默认 **10082**），将收到的请求转发到真实外网，并把应答原路返回。
- **客户端（其余工控机）**：解析配置中的网关 IP 与端口，把本机需要外联的 TCP
  连接 / UDP 报文封装后发送给服务端处理。
- **配置文件**：目录解析顺序──默认 `D:/JustSmart/InternetProxyPy`（自动创建）
  → 失败时回退 %APPDATA%/InternetProxyPy。首次运行写入网关 IP 与代理端口
  （默认 **10082**），后续直接复用；改动设置文件随即生效，代理的角色
  （服务端 / 客户端）也持久化在同一配置文件中。

## 界面

托盘图标即入口；程序启动时**静默最小化到托盘**，主窗口隐藏、用户无感知，代理
直接在后台开始工作。右键托盘菜单可随时唤出主窗口（**日志** 与 **设置** 两个
标签页）。界面整体采用 Windows 95 主题（设计令牌来自 `assets/02-windows-95.css`）：

- 日志区：以滚动输出的窗口呈现客户端 / 服务端运行日志，方便现场排查。
- 设置区：填写网关工控机 IP、代理端口（SOCKS5 等，默认 **10082**）、运行角色；
  保存即写入配置文件，托盘中的代理可以在后台不间断地继续工作。

## 快速开始

**双击打包出的 exe** 即以客户端模式启动（静默到托盘）；配置文件里没有网关 IP
时回退 **127.0.0.1** —— 也就是本机（服务端角色时即网关本身）。

```bash
# 依赖（Python 3.x）；tkinter 为标准库
pip install tkinterdnd2

# 托盘后台（GUI）方式运行——启动即静默到托盘
python main.py            # 或安装后运行 internet-proxy

# 无界面前台运行（调试 / 服务方式）
python main.py --role server --headless      # 服务端，监听 10082
python main.py --role client --headless      # 客户端，SOCKS5 10082
```

- 服务端（可上网工控机）监听 `0.0.0.0:10082`（TCP 隧道 + UDP 地址头）。
- 客户端在本机提供标准 **SOCKS5（10082）**：应用把代理指向客户端 IP:10082，
  其 CONNECT 经 TCP 隧道送网关，UDP 报文以地址头前缀经网关 UDP 通道转发。
- 配置文件持久化在 `D:/JustSmart/InternetProxyPy/config.json`
  （回退规则见特性一节）。

把局域网内其它设备的默认网关 / DNS 指向运行客户端的机器（或在客户端机器上开启
本地转发），即可让它们共享服务端那张唯一的外网网卡。

**使用边界（重要）**：

- **ping 不通属正常现象**：ping 使用 ICMP 协议，SOCKS5 只承载 TCP/UDP，
  本软件不支持也不应支持 ICMP 转发；验证连通性请用 TCP（如浏览器访问网页）。
- **应用无需配置（默认透明代理）**：虚拟网卡自动接管系统流量，浏览器等
  直接上网，DNS 也走代理。若在设置里关闭"系统透明代理"，则需给应用显式
  配置 SOCKS5（客户端 IP:10082）并勾选"远程 DNS / 代理解析 DNS"，
  否则 DNS 仍由本机直接发出，会出现"DNS 异常"。
- UDP ASSOCIATE 应答中的 BND.ADDR 为客户端本机地址，应用的 UDP 报文
  （如 DNS）应发往该地址与端口。

## 目录结构

```
InternetProxyPy/
├── assets/
│   ├── cat.ico                 # 托盘 / 窗口图标
│   ├── cat.jpg / clash.jpg     # 图标源图（圆角裁切生成 ico）
│   └── 02-windows-95.css       # Win95 设计令牌（界面风格来源）
├── build_exe.py                # PyInstaller 单文件打包
├── pyproject.toml              # 依赖（tkinterdnd2）与入口脚本
├── README.md
└── index.html                  # 项目主页（GitHub Pages，internet-proxy 分支）

配置目录（运行时）:
D:/JustSmart/InternetProxyPy/          # 默认，自动创建
%APPDATA%/InternetProxyPy/            # 上述路径不可用时的回退
```

## 许可证

见 [LICENSE](LICENSE)。
