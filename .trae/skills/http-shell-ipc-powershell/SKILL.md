---
name: http-shell-ipc-powershell
description: Run PowerShell on an industrial PC through the bundled HTTP Shell CLI/VNC exe, with file transfer and a noVNC desktop. Use when asked to connect to 工控机 or run remote PowerShell. Not for SSH.
---

# 工控机远程 PowerShell（HTTP Shell）

用项目自带的单文件 `http_shell_cli_vnc_*.exe` 连接工控机。同一个程序在工控机上跑 `server`、在本机跑 `client`，不需要 SSH。服务端按 `pwsh → powershell → cmd` 自动选择 Shell。

## 0. 先确认

- 工控机地址和端口（默认 `10022`）。用户没给地址就先问，不要猜、不要扫端口。
- 客户端程序在 `assets\` 下，文件名带时间戳，始终取最新的：

```powershell
$Root = "d:\JustStupid\httpshell远程操作工控机_2605201359\InternetProxyPy"
$exe = (Get-ChildItem "$Root\assets\http_shell_cli_vnc_*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
```

每次 Shell 调用都是新的 PowerShell 会话，`$exe` 不会跨命令保留；需要时在同一段命令里重新解析。

## 1. 健康检查（每次会话先做一次）

```powershell
Invoke-RestMethod -Uri "http://192.168.1.100:10022/health" -TimeoutSec 5
```

返回 `status = running` 才能继续；`shell` 字段是服务端实际 Shell 的路径。

连不通时按顺序核对，不要反复重试：
1. 工控机上服务端是否已启动：`& $exe -role server -host 0.0.0.0 -port 10022`
2. IP、端口是否正确，工控机防火墙是否放行该端口。
3. 仍不通就停止，告诉用户需要现场确认。

## 2. 执行命令（默认单次模式）

```powershell
$exe = (Get-ChildItem "d:\JustStupid\httpshell远程操作工控机_2605201359\InternetProxyPy\assets\http_shell_cli_vnc_*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
& $exe -role client -url "http://192.168.1.100:10022" -timeout 60 -c "Get-Process | Select-Object -First 5"
```

- 远端 stdout 直接输出到 stdout、stderr 输出到 stderr，进程退出码等于远端命令退出码，可用 `$LASTEXITCODE` 判断。
- `-timeout` 同时控制客户端请求超时和远端命令超时，默认 30 秒。安装、编译、启停服务等慢操作必须显式放大。
- `-w "C:\app"` 指定远端工作目录。

### 引号规则（最容易出错）

远端命令含 `$env:`、`$true` 等变量时，外层用单引号，防止本地 PowerShell 先展开：

```powershell
& $exe -role client -url "http://192.168.1.100:10022" -c 'Get-ChildItem $env:SystemRoot | Select-Object -First 3'
```

远端命令自身需要单引号时，外层改用双引号并转义 `$`（例如 `` `$env:Path ``）。命令跨多行、引号嵌套或脚本较长时，不要硬拼字符串，改用第 3 节。

## 3. 复杂 / 多行脚本（推荐）

配套脚本直接调用 `/exec` 接口，用 UTF-8 JSON 传输命令，避开所有嵌套引号问题：

```powershell
$ps1 = "d:\JustStupid\httpshell远程操作工控机_2605201359\InternetProxyPy\.trae\skills\http-shell-ipc-powershell\scripts\Invoke-RemotePowerShell.ps1"

# 单条命令
& $ps1 -Url "http://192.168.1.100:10022" -Command "Get-Service | Where-Object Status -eq 'Running'" -Timeout 60

# 多行脚本：先写成 UTF-8 的 .ps1，再整体发送
& $ps1 -Url "http://192.168.1.100:10022" -ScriptFile .\remote_task.ps1 -WorkDir "C:\app" -Timeout 600

# 只做健康检查
& $ps1 -Url "http://192.168.1.100:10022" -Health
```

脚本输出远端 stdout/stderr，并以远端退出码退出，适合需要判断成功失败的自动化任务。

## 4. 交互式模式（连续操作或文件传输）

```powershell
& $exe -role client -url "http://192.168.1.100:10022"
```

内置命令：`cd`、`pwd`、`upload <本地> [远程]`、`download <远程> [本地]`、`rsync` / `rsync-pull`（增量同步）、`rfb-upload` / `rfb-download` / `rfb-list`（断点续传）、`!<命令>`（仅本地执行）、`exit`。

这是给人用的交互终端；Agent 自动化时优先用单次模式或第 3 节脚本模式。

## 5. 文件传输（自动化）

HTTP 接口，适合脚本调用：

```powershell
curl.exe -X POST "http://192.168.1.100:10022/upload" -F "file=@.\local.txt" -F "path=C:/remote/dir/"
curl.exe -X POST "http://192.168.1.100:10022/download" -F "path=C:/remote/dir/file.txt" -o .\file.txt
```

大文件或弱网优先用交互模式里的 `rsync` / `rfb-*` 命令。

## 6. 远程桌面

浏览器打开 `http://192.168.1.100:10022/vnc/vnc.html`，默认无密码。仅 Windows 工控机支持；服务端会自动复用已有的 RealVNC/TightVNC/UltraVNC，没有则启用内嵌 TightVNC（首次需要管理员权限）。

## 7. 常见问题

- `连接失败` 或健康检查无响应：服务端未启动、IP 错、防火墙拦截。确认一次后上报。
- 退出码 `-1` 且 stderr 含“命令执行超时”：加大 `-timeout`，或把长任务放到远端后台执行后轮询结果。
- 中文乱码：PowerShell 输出为 UTF-8，cmd 输出由服务端自动按 GBK 转码；仍乱码时让远端命令先设 `[Console]::OutputEncoding = [Text.Encoding]::UTF8`。
- 输出过大：在远端先筛选或 `Out-File`，再下载结果文件，不要直接回传海量文本。

## 8. 安全边界

该服务没有身份认证，只用于内网 / 离线环境。不要暴露到公网；不要把密码等凭据写进命令或日志；传输敏感文件前先确认网络环境。
