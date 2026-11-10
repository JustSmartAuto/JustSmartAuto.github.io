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
