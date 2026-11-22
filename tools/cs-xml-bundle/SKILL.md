---
name: cs-xml-bundle
description: 将项目里的可读文本文件（源码与配置）打包成单个 XML 并按原字节还原。用于用户要求 打包项目/导出源码包/生成 bundle/从 XML 解包还原项目 时；不用于 Markdown 文档转换或整盘二进制备份。
---

# C# 项目 ⇄ XML 源码包

把任意项目目录的可读文本文件打成一个 XML（便于贴给 AI 或归档），再按相对路径逐字节还原。Go 单文件实现，仅标准库。

## 文件清单

以下路径均相对本 `SKILL.md` 所在目录：

- `scripts/bundle_cs_to_xml.go` — 打包源码
- `scripts/extract_xml_to_files.go` — 解包源码
- `bin/bundle_cs_to_xml.exe` — 打包程序（Windows amd64，已编译，可直接用）
- `bin/extract_xml_to_files.exe` — 解包程序（Windows amd64，已编译，可直接用）

## 打包

交互式（用户手动操作，支持把文件夹拖进控制台，自动去引号）：

```powershell
& "<技能目录>\bin\bundle_cs_to_xml.exe"
```

命令行（Agent 用这条，避免交互等待）：

```powershell
& "<技能目录>\bin\bundle_cs_to_xml.exe" -src "D:\path\MyProject" -out "D:\temp\MyProject_bundle.xml"
```

- 不传 `-out` 时默认输出到当前目录，文件名 `<项目名>_bundle_yyyyMMdd_HHmmss.xml`，时间戳后缀用于区分多次打包。
- 默认跳过目录 `.git .vs .svn .hg .idea .vscode bin obj packages node_modules TestResults`，`-all` 可关闭。
- 按内容而非扩展名判定文本：无 NUL 且控制字符比例低即视为文本；含 NUL 的二进制文件跳过并在结尾列出。
- `-max-mb` 控制单文件上限，默认 64MB。

## 解包

```powershell
& "<技能目录>\bin\extract_xml_to_files.exe" -xml "D:\temp\MyProject_bundle.xml" -out "D:\temp\MyProject_extracted"
```

- 默认输出 `<XML所在目录>/<项目名>_extracted_yyyyMMdd_HHmmss`，带时间戳不覆盖旧目录。
- 已存在且内容不同的文件默认跳过并计数，需要覆盖时加 `-force`。
- 解包后逐个核对包内 SHA256，末尾报告不一致文件；拒绝绝对路径、盘符与 `..` 越界路径。

## 编码规则（改动脚本前必须了解）

| XML 中 encoding | 含义 | 还原方式 |
|---|---|---|
| `utf-8` | UTF-8 无 BOM 文本 | 直接写回 |
| `utf-8-bom` | UTF-8 带 BOM | 写回时补 `EF BB BF` |
| `utf-16le` / `utf-16be` | UTF-16 文本，XML 内以 UTF-8 呈现 | 重新编码并补 BOM |
| `base64` | GBK/Big5 等非 UTF-8 文本，原字节 base64 | 解码原样写回 |

每个 `<file>` 节点属性为 `path encoding size sha256`，正文即文件内容（XML 转义）。末尾 `<summary files skipped bytes/>` 记录统计。

## 验证流程（每次打包/改代码后执行）

1. 打包目标项目到临时目录。
2. 解包到另一个临时目录。
3. 用 PowerShell 对比解包文件与原文件 SHA256，要求 `mismatch=0`：

```powershell
$src="原项目"; $dst="解包目录"
Get-ChildItem $dst -Recurse -File | ForEach-Object {
  $rel=$_.FullName.Substring($dst.Length+1)
  if((Get-FileHash $_.FullName).Hash -ne (Get-FileHash (Join-Path $src $rel)).Hash){ "DIFF $rel" }
}
```

## 修改 Go 源码后

两个文件各自独立 `main` 包，必须分别编译（不能 `go build ./...` 一次出两个）：

```powershell
go build -o bin/bundle_cs_to_xml.exe scripts/bundle_cs_to_xml.go
go build -o bin/extract_xml_to_files.exe scripts/extract_xml_to_files.go
```

非 Windows 平台无预编译 exe，用 `go run scripts/bundle_cs_to_xml.go -src ...` 代替。
