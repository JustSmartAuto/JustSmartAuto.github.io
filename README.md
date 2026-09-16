# JustSmartAuto Inc. 官方博客

广州市捷思智能科技有限公司（JustSmartAuto Inc.）静态博客，纯 HTML/CSS，无构建依赖。

## 目录结构

```
├── index.html                    # 首页（公司简介 + 文章列表 + 联系方式）
├── assets/
│   └── style.css                 # 站点样式
└── posts/
    ├── sunroof-gearbox-part1.html    # 某马达产线项目（一）马达/胶盖/热压热铆
    ├── sunroof-gearbox-part2.html    # 某马达产线项目（二）铁齿轮/胶齿轮
    ├── sunroof-gearbox-part3.html    # 某马达产线项目（三）牙箱/介子/涡轮杆/齿轮点油
    ├── vidi-classify-vs-segment.html # 专题：ViDi EL Classify vs EL Segment
    └── linescan-vs-areas-scan.html   # 专题：面阵 vs 线扫选型
```

## 发布新文章

1. 在 `posts/` 下复制任一现有文章，改 `<title>`、`<meta name="description">`、
   正文 `<h1>` 和 `<p class="meta">`（日期 / 作者 / 分类）。
2. 在 `index.html` 的 `<div class="post-list">` 中按同样格式添加一个 `<a class="post-item">`。

## 内容边界（重要）

- **不公开任何产品实物图片 / 客户产线照片**：方案报告中的检测效果图、产品照片一律不上博客、不进仓库。
- 博客仅保留**技术细节**作为公司技术实力展示：检测需求描述、视野、精度、节拍、
  相机/镜头/光源选型、算法方案与结论等文字和参数表格。
- 涉及客户名称或项目敏感信息时，统一以「某马达产线视觉项目」等泛化名称表述。

## 本地预览

直接双击 `index.html` 即可；或运行本地服务器（推荐，路径行为与线上一致）：

```bash
python -m http.server 8000
# 打开 http://localhost:8000
```

## 部署到 GitHub Pages

### 方式一：GitHub 网页操作（最简单）

1. 在 GitHub 新建仓库，例如 `justsmartauto.github.io`（想用 `https://<用户名>.github.io` 作为主站的话必须用此命名；用其他名字则地址为 `https://<用户名>.github.io/<仓库名>`）。
2. 把本目录内容推送到仓库：

```bash
git init
git add .
git commit -m "Initial blog"
git branch -M main
git remote add origin https://github.com/<用户名>/<仓库名>.git
git push -u origin main
```

3. 在仓库页面进入 **Settings → Pages**。
4. Source 选择 **Deploy from a branch**，Branch 选 `main` / `/(root)`，点 **Save**。
5. 等待约 1 分钟，访问 `https://<用户名>.github.io`（或 `/<仓库名>`）即可。

### 方式二：GitHub Actions 自动部署（可选，便于后续 CI）

`.github/workflows/deploy.yml`：

```yaml
name: Deploy to GitHub Pages
on:
  push:
    branches: [main]
permissions:
  pages: write
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/upload-pages-artifact@v3
        with:
          path: .
      - uses: actions/deploy-pages@v4
```

然后在 **Settings → Pages** 中 Source 选 **GitHub Actions**。

### 绑定自定义域名（www.justsmartauto.com）

1. 域名 DNS 添加记录：`CNAME www → <用户名>.github.io`（Apex 域加 4 条 A 记录指向 GitHub Pages IP）。
2. 仓库中添加 `CNAME` 文件，内容为一行：`www.justsmartauto.com`。
3. 在 **Settings → Pages → Custom domain** 填入域名并勾选 **Enforce HTTPS**。
