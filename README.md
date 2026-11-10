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

生成 `dist/VirtualLanPy_时间戳.exe`。单个 EXE 内嵌 easytier-core、easytier-cli、wintun.dll、Packet.dll、WinDivert64.sys、图标及 Tk DND 组件，可直接复制到 Windows 工控机运行。

## EasyTier GUI 测试配置

在 EasyTier GUI 中新建配置，将以下内容保存为 `justsmart.toml` 后加载。网络名、密钥、DHCP 网段和公共中继与 VirtualLanPy 保持一致：

```toml
instance_name = "justsmart"
dhcp = true
ipv4 = "10.168.10.0/24"
listeners = []
rpc_portal = "127.0.0.1:15888"

[network_identity]
network_name = "justsmart"
network_secret = "justsmart"

[[peer]]
uri = "tcp://public.easytier.cn:11010"

[[peer]]
uri = "udp://public.easytier.cn:11010"

[flags]
multi_thread = true
```

`listeners = []` 表示不监听入站端口，对应命令行的 `--no-listener`。如果 GUI 不提供 TOML 导入，可在界面中手动填写相同的网络名、密钥、IPv4/DHCP 网段及两个公共节点。
