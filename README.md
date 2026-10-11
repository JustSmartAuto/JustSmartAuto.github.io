# Decimen Optical Transfer：基于喷泉码的二维码文件传输

只需一块**屏幕和一个摄像头**，就能在两台设备之间传送文件。一端把文件显示为
源源不断的动态二维码流，另一端用摄像头对准屏幕即可还原文件。**两台设备之间
无需网络连接，无需安装应用，无需配对，除了摄像头也不需要任何权限。**
数据以光为媒介传输。

## 立即体验

### **→ [decimen.app](https://decimen.app/)**

在两台设备上打开即可使用——无需安装任何东西。首次访问后可离线使用，还可以
作为应用安装到 iOS 和 Android 的主屏幕上。

支持最大 64 MB 的文件（或粘贴的文本片段），保留文件名和媒体类型，只在有
收益时才 gzip 压缩，提供下载前先做 SHA-256 校验——接收到的视频可以直接在
页面内播放。码流还可以[导出为循环播放的动画](docs/user/sending.md#export-animation)
（APNG，或供视频编辑器使用的 PNG 序列）：任何对准该文件播放画面——网页、
直播流、视频课程——的摄像头，都能像实时发送端一样接收它。界面支持十二种
语言（English、español、português、français、Deutsch、italiano、русский、
हिन्दी、简体中文、日本語、한국어、العربية——在需要的地方使用从右到左的
布局）；机器翻译的版本会在页面上注明，直到母语者
[审校](docs/technical/localization.md)为止。当前实测
**<!-- speed:begin -->418.5 KB/s 持续速率<!-- speed:end -->**（屏幕到摄像头）
——[有据可查的纪录](#实测速度)。

<p align="center">
  <img src="docs/receiving.jpg" width="420"
       alt="手机通过光接收文件：130.5 KB/s 有效吞吐，正在解码发送端的动态二维码流（进度过半）" />
</p>
<p align="center"><em>传输进行中：手机正以 130 KB/s 的速率“从空气中”拉取文件。</em></p>

两种模式均未加密：发送屏幕上显示的任何内容，都能被对准它的任何摄像头读取。
这个方案给你的是“无网络”，而不是“保密”——参见[隐私说明](docs/user/privacy.md)。

## 实测速度

<!-- benchmarks:begin -->
![持续速率纪录](https://img.shields.io/badge/dynamic/json?label=sustained&color=blue&query=%24.sustained.badge&url=https%3A%2F%2Fraw.githubusercontent.com%2Fbashalarmistalt%2Fdecimen-optical-transfer%2Fmain%2Fbenchmarks%2Frecords.json)

每组设备组合只保留一次纪录跑分。持续速率为整个传输的有效吞吐；峰值为同一
次跑分中最佳的 ≥1 秒窗口。每行都链接到产生该纪录的完整诊断跑分报告
（[测量方法](docs/technical/diagnostics.md)）。

| 设备组合 | 持续速率 | 峰值速率 | 传输量 | 码字数 | 设备 | 时间 | 记录 |
|---|---|---|---|---|---|---|---|
| 桌面 → 手机 | **418.5 KB/s** | **601.5 KB/s** | 1.0 MB 用时 2.5 s | 4 | Odyssey G9 49″ → iPhone 17 Pro Max | 2026-08-09 (v0.4.0) | [run](benchmarks/runs/2026-08-09T04-12-41-run.json) |
| 手机 → 手机 | **199.2 KB/s** | **340.8 KB/s** | 1.0 MB 用时 5.1 s | 2 | iPhone 17 Pro Max → iPhone 17 Pro Max | 2026-08-09 (v0.4.0) | [run](benchmarks/runs/2026-08-09T04-49-39-run.json) |
<!-- benchmarks:end -->

## 文档

**使用指南** — [快速上手](docs/user/quick-start.md) ·
[发送](docs/user/sending.md) · [接收](docs/user/receiving.md) ·
[故障排除](docs/user/troubleshooting.md) ·
[安装与离线使用](docs/user/install-and-offline.md) ·
[隐私](docs/user/privacy.md)

**实现原理** — [架构](docs/technical/architecture.md) ·
[协议](docs/technical/protocol.md) ·
[平台差异](docs/technical/platform-quirks.md) ·
[构建与发布](docs/technical/build-and-release.md) ·
[本地化](docs/technical/localization.md)

协议简述：屏幕到摄像头的链路没有反向信道，因此发送端持续发送喷泉码帧
（[Luby transform](https://en.wikipedia.org/wiki/Luby_transform_code)）——
接收端只需以任意顺序收集 *任意* 约 K·1.15 个不同的帧，即可解出文件。
丢帧只会损失时间，绝不会损失正确性。

## 自己动手运行

```bash
npm install
npm run dev               # HTTPS 开发服务器，带 HMR
npm run serve             # 构建并提供生产包
npm run demo              # 演示模式：只能发送内置的示例负载
npm run diagnostics       # 开发服务器 + 在终端输出每次传输的报告
npm run benchmark         # 诊断 + 发送端锁定为标准 1 MB 负载
npm run benchmark:promote # 将你最好的一次跑分宣布为纪录（更新上表）
npm test                  # 线格式黄金测试向量与单元测试
npm run build             # 托管站点 → dist/
npm run build:standalone  # 两个自包含页面 → dist-standalone/
npm run build:all         # 全部构建
```

在发送端设备打开 `https://localhost:5173/send/`，接收端手机打开终端打印的
`Network` 地址（首次需接受自签名证书）。完整流程见
[快速上手](docs/user/quick-start.md)。

## 类似项目

本项目的构思是独立完成的。后来发现已有几位开发者有过类似想法，他们的实现
都值得一看：

- [mohankumarelec/airgapped-qr-code-transfer](https://github.com/mohankumarelec/airgapped-qr-code-transfer)：
  基于浏览器的二维码文件传输，带压缩和顺序分块。是在本项目公开演示之后才
  发现的——趋同演化的活例子。
- [divan/txqr](https://github.com/divan/txqr)（2018）：Go 语言实现的动态
  二维码加喷泉码，附带两篇讲解“喷泉码为何优于顺序循环”的精彩文章。
- [sz3/libcimbar](https://github.com/sz3/libcimbar)：完全超越二维码，为
  这类信道量身定制了高密度彩色编码。

## 项目作者/维护者

由 [Evan Crawley (Bash Alarmist)](https://www.linkedin.com/in/evan-crawley) 构建，使用了
[node-qrcode](https://github.com/soldair/node-qrcode) 和
[decimen-codec](https://github.com/bashalarmistalt/decimen-codec)——一个基于
[zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) 的定制构建。

## 许可证

自 v0.4.0 起采用 [AGPL-3.0-or-later](LICENSE)。v0.3.0 及更早的版本以 MIT
许可证发布，并继续按原条款提供。

部分代码以 MIT 许可贡献；内嵌的
[decimen-codec](https://github.com/bashalarmistalt/decimen-codec) 解码器
（AGPL-3.0-or-later）以 Apache-2.0 引入了
[zxing-cpp](https://github.com/zxing-cpp/zxing-cpp)——详见
[NOTICE](NOTICE)。
