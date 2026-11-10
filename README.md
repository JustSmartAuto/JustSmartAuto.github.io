# VirtualLanPy

启动后自动请求 UAC 管理员权限，并使用 EasyTier 公共服务器加入虚拟局域网。网络名和密码均固定为 `justsmart`，DHCP 网段为 `10.168.10.0/24`，无需填写配置。

## 开发运行

```bash
uv sync
uv run python main.py
```

## 打包

```bash
uv run python build_exe.py
```

生成 `dist/VirtualLanPy_时间戳.exe`。单个 EXE 内嵌 easytier-core、easytier-cli、wintun.dll、Packet.dll、WinDivert64.sys、图标及 Tk DND 组件，可直接复制到 Windows 工控机运行。打包完成后会使用当前用户证书库中的 `JustSmart VirtualLanPy Code Signing` 证书自动签名。

## 运行说明

- 使用默认 EasyTier 监听器，并通过 TCP、UDP 连接 `public.easytier.cn:11010`。
- 启动时清理 VirtualLanPy 自己残留的提权 core 进程，避免旧实例占用 11010 等端口；不会清理 EasyTier GUI 的进程。
- 提权后自动将内嵌证书导入当前用户的“受信任的根证书颁发机构”和“受信任的发布者”。
- 配置目录优先使用 `D:/JustSmart/VirtualLanPy`，不可用时回退到 `%APPDATA%/VirtualLanPy`；EasyTier 日志保存在其中的 `logs/`。
- 界面显示本机虚拟 IP、peer 虚拟 IP、节点数量和连接详情。CLI 状态 API 不可用时，自动改用 Windows 网卡检测本机虚拟 IP。

## EasyTier GUI 测试配置

在 EasyTier GUI 中新建配置，将以下内容保存为 `justsmart.toml` 后加载。网络名、密钥、DHCP 网段和公共中继与 VirtualLanPy 保持一致：

```toml
instance_name = "justsmart"
dhcp = true
ipv4 = "10.168.10.0/24"
listeners = [
    "tcp://0.0.0.0:11010",
    "udp://0.0.0.0:11010",
    "wg://0.0.0.0:11011",
]
rpc_portal = "127.0.0.1:15888"

[network_identity]
network_name = "justsmart"
network_secret = "justsmart"

[[peer]]
uri = "tcp://public.easytier.cn:11010"

[[peer]]
uri = "udp://public.easytier.cn:11010"

[flags]
```

如果 GUI 不提供 TOML 导入，可在界面中手动填写相同的网络名、密钥、IPv4/DHCP 网段及两个公共节点。
