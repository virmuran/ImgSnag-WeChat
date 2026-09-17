<!-- markdownlint-disable -->

<div align="center">

<img alt="ImgSnag" src="ImgSnag.png" width="128" height="128" />

# ImgSnag · 微信公众号版

<br>
<div>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.13-%233776AB?logo=python">
    <img alt="platform" src="https://img.shields.io/badge/platform-Windows-lightgrey">
    <img alt="deps" src="https://img.shields.io/badge/%E4%BE%9D%E8%B5%96-requests%20%2B%20PySide6-Essentials-blue">
</div>
<div>
    <img alt="version" src="https://img.shields.io/badge/version-1.1.0-green">
    <img alt="stars" src="https://img.shields.io/github/stars/virmuran/ImgSnag-WeChat?style=social">
</div>
<br>

**一键提取微信公众号文章正文图片，批量下载无水印原图**

只为微信公众号做一件事：纯 requests 抓取，不需要浏览器内核，安装包 30 MB

</div>

## 为什么只支持微信公众号

这不是能力不足，是刻意的取舍。

早期的 ImgSnag 是通用网页图片下载器，支持微信公众号 / 抖音 / 微博 / 森空岛 / 小黑盒 / 堆糖等平台。问题出在**抗风险能力**上：

- **抖音**：一次改版就让提取策略彻底失效，维护一份随时可能废掉的适配代码，投入产出比极低
- **森空岛、微博**：图片在服务器端就压了水印，抓下来也是一张带水印的图，等于没抓到
- **其他站点**：流量小、结构多变，为了少数需求维护多套策略，最后每套都是半成品

而微信公众号结构多年稳定，文章是静态 HTML（不需要 JS 渲染），正文本图本身不带水印。于是这个项目收缩到**只做微信公众号**：

| | 旧版通用 ImgSnag | 本版 |
|---|---|---|
| 支持平台 | 7 个 | 微信公众号 |
| 依赖 | PySide6 + requests + playwright | PySide6-Essentials + requests |
| 需下载浏览器内核 | 是（Chromium ≈ 300 MB） | 否 |
| 安装包 | 592 MB（解压后） | 30.5 MB（安装包）/ 32.3 MB（便携包） |

少即是多——单一平台可以做得足够深，也不怕它哪天变。

## 功能特性

- 📐 **原图画质（默认开启）**：微信同一张图提供多个画质档位，正文 HTML 里给的通常是压缩档（地址带 `/640`），程序自动改写成原图档（`/0`）再下载。实测同一张图 `1080×1423 / 101.6 KB` → `1280×1687 / 128.8 KB`，大图差距更明显；原图取不到时自动回退压缩档，不会整张失败
- 🎯 **三种图源自动叠加**：`<img data-src>` 懒加载图、`picture_page_info_list` JS 变量里的**无水印原图**、旧文章的 `<img src>`，哪种结构都能覆盖
- 🔍 **补充 Script 扫描**：个别文章把图片藏在 `<script>` 里，解析到 0 张图时勾选此项再解析一次即可兜底
- 🧹 **CDN 变体智能合并**：同一张图微信会从多个 CDN 节点、多个尺寸发出（`mmbiz_jpg` / `sz_mmbiz_jpg` / `/0` / `/640`），按图片身份去重，不会出现一堆重复图
- 👤 **头像与装饰自动排除**：识别作者头像字段与微信 UI 装饰图，再用内容哈希滤掉重复图
- 🚫 **右键屏蔽尺寸（可学习）**：遇到漏网的噪音图，右键「屏蔽此尺寸」，写入配置后**下次解析自动过滤**，还会自动归类为封面 / 头像 / UI 装饰
- 🖼 **大图预览 + 缩略图条**：左侧看大图，右侧勾选，支持按名称 / 文件大小 / 分辨率排序
- 🔄 **统一另存格式**：原格式 / JPG / PNG / WebP 任选，一键转好再落盘
- 📝 **本地下载历史**：SQLite 记录每次解析与下载，可一键「再次解析」、打开保存目录、删除记录
- 🔒 **纯本地运行**：不联网上报、不收集任何信息，所有数据留在你自己的电脑上

<!-- markdownlint-disable -->

<details><summary>点我看截图</summary>

<p align="center">
  <em>截图待补充 — 欢迎提交 PR！</em>
</p>

</details>

<!-- markdownlint-restore -->

## 下载与安装

> 本仓库只提交**源码与打包配置**。发行产物（安装包 / 便携包）请前往 [Releases](https://github.com/virmuran/ImgSnag-WeChat/releases)。
> 若还没有可用发行版，按下面「从源码运行」即可。

| 文件 | 适合谁 | 说明 |
|---|---|---|
| `ImgSnagWeChat_vX.X.X_setup.exe` | 普通用户 | 安装向导（简体中文），下一步装完，开始菜单 / 桌面快捷方式、控制面板卸载 |
| `ImgSnagWeChat_vX.X.X_portable.zip` | 便携党 | 解压即用、免安装、无需管理员权限，适合 U 盘或受管控的公司电脑 |

两种发行都不需要你安装 Python。**用户数据保存在用户目录**（见下），安装版卸载**不会**删除你的下载历史与过滤配置。

用户数据位置：

| 内容 | 位置 |
|---|---|
| 下载历史（SQLite） | `~/.imgsnag_wechat/history.db` |
| 屏蔽尺寸配置 | `%APPDATA%\ImgSnagWeChat\blocked_sizes.json` |

## 从源码运行

```bash
git clone https://github.com/virmuran/ImgSnag-WeChat.git
cd ImgSnag-WeChat

python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Windows
.venv\Scripts\python main.py
```

需要 Python 3.11+。依赖只有两个：`PySide6-Essentials`（Qt 基础模块）和 `requests`。

> 为什么不是 `PySide6`？完整元包会连带 `PySide6-Addons`（Qt3D / QtMultimedia / QtWebEngine 等）一起下载，本项目一个都用不到。实测换用 Essentials 后，装依赖从「十几分钟还没下完」变成 **36 秒**。

## 使用说明

### 1. 解析

把**公众号文章链接**（`https://mp.weixin.qq.com/s/...`）粘进输入框，点「解析图片」或按 `Ctrl+Enter`。粘贴的内容里混着分享文字也没关系，会自动把链接揪出来。

解析不了的少数情况，可以改用 HTML 源码模式：浏览器按 `F12` → 元素面板 → 右键 `<html>` → 复制 → **复制 outerHTML**，然后粘进同一个输入框点「解析图片」。点「粘贴链接」按钮会直接读取剪贴板内容。

### 2. 挑选

- **原图画质**（默认开启）：请求原图档（`/0`）而不是文章里的压缩档（`/640`）。解析时状态栏会显示本次合计体积；原图取不到会自动回退压缩档
- **过滤小图与装饰**（默认开启）：按已知噪音尺寸（公众号头像、UI 装饰、封面横幅）加兜底规则（最长边 ≤ 200px、透明小 PNG）自动隐藏，取消勾选可全部显示
- **右键任意缩略图 → 屏蔽此尺寸**：把当前这张图的尺寸加入黑名单，下次解析直接过滤
- 顶部可切换排序（默认顺序 / 名称 / 尺寸 / 分辨率），「全选 / 取消全选」批量勾选

### 3. 下载

选好保存格式与目录，点「下载选中图片」。状态栏会显示进度与结果统计（共几张、几张有效、几张重复）。

### 4. 历史

「历史」页记录每次解析与下载（已解析 / 已下载 / 部分成功 / 失败），每条都能**再次解析**、**打开保存目录**、**删除**，也可以一键清空。

## 常见问题

**Q：解析到 0 张图？**
先勾选「补充Script扫描」重新解析；仍为空的话，多半是文章需要登录/验证才能查看，或文章里确实没有图片。

**Q：下载的图还是只有几百 KB？**
先确认「原图画质」是勾选的。微信对同一张图提供多个画质档位，正文 HTML 里给的一般是压缩档（地址里的 `/640`），本程序会自动改写成原图档（`/0`）。

修复前后差多少，用一篇真实的 8 图文章实测（`tools/e2e_article_download.py`）：

| | 旧版（`/640`） | 当前版本（`/0`） | 提升 |
|---|---|---|---|
| 单图体积 | 93.4 – 175.3 KB | 363.8 – 748.9 KB | 3.17 – 4.29 倍 |
| 单图分辨率 | 1080×1440 | 2155×2873 ~ 2560×3413 | 3.98 – 5.62 倍像素 |
| **全篇 8 张合计** | **1131 KB（约 1.1 MB）** | **4372 KB（约 4.4 MB）** | **3.86 倍** |

这就是"ImgSnag 只下来几百 KB、手动保存却有好几 MB"的全部原因——不是微信不给原图，是旧版从来没要过原图档。

`/0` 已经**探明是天花板**，没有更高的档位可挖：

- `/1080`、`/1280`、`/2000`、`/3000` 等档位一律被 CDN 拒绝（HTTP 400），合法档位只有 `/0` 和 `/640`
- 去掉尺寸段等同于 `/0`
- 把 CDN 前缀换成 `mmbiz` / `mmbiz_png` 等，只会拿到 0.2 KB 的"未经允许不可引用"占位图（这种垃圾小图会被程序按最小体积阈值滤掉）

所以如果换成原图档后体积仍然不大，那就是微信对**这张图本身**只提供到该画质——`/0` 就是上限，没有更高的档位可挖了。

顺带澄清一个容易误判的点：`picture_page_info_list` 里每个 item 有两条 `cdn_url`，是**两个分别上传的文件**（文件 ID 不同），一条无水印、一条带水印。实测两者的 `/0` 档体积基本相当，差值绝大多数在 2% 以内（8 图文章里水印版 4 张略大 0.4%–1.8%、4 张略小；另一篇 5 图文章里最大的一张水印版大 13%）。也就是说**并不存在"几 MB 的带水印原图"**——取无水印版不会损失画质，换带水印版也换不来更大的图。本项目只取无水印版是纯收益，没有任何画质代价。

想实测某篇文章差多少，或者自己复核上面的数字：

```bash
.venv\Scripts\python.exe tools\e2e_article_download.py <文章链接>          # 端到端跑生产链路，新旧画质对比
.venv\Scripts\python.exe tools\probe_wechat_quality.py <文章链接>          # 只测档位差距，不落盘
.venv\Scripts\python.exe tools\probe_max_quality.py <图片链接或文章链接>    # 穷举写法找画质上限
```

**Q：能下载别的网站的图片吗？**
不能，这是刻意设计的。贴非 `mp.weixin.qq.com` 的链接会直接提示不支持。理由见[开头](#为什么只支持微信公众号)。

**Q：下载的图有水印吗？**
微信**正文图**本身不带水印，本项目还会优先取 `picture_page_info_list` 里的无水印原图。若混进带水印的**封面图**，它会被「过滤小图与装饰」按封面尺寸拦掉，也可右键手动屏蔽该尺寸。

**Q：安装时提示「错误 5：拒绝访问」？**
少数受管控的公司电脑会拦 Temp 目录写入。右键「以管理员身份运行」，或直接改用**便携版**（不写临时目录、无需管理员）。

**Q：卸载会删掉我的历史记录吗？**
不会。用户数据在用户目录，卸载程序不碰它，覆盖升级也不会丢。

## 打包发版

```bash
# 双击 build.bat 也行（venv 不存在会自动创建并装依赖）
.venv\Scripts\python.exe build_release.py
```

一条命令跑完：**PyInstaller onedir → Inno Setup 安装包 → 便携 zip（LZMA）**，产物在 `installer/` 与 `dist/`。

- 采用 **onedir** 而非 onefile：不再往 `%TEMP%` 解压，启动更快、杀软误报更少，也不怕公司电脑 Temp 目录被拦
- 版本号**唯一手写在 `version.py`**，构建脚本自动同步到 `.iss`；脚本内置闸门，格式不合法或该号已打过包都会中断打包
- `build.bat` 支持传参，`--skip-build` 可跳过 PyInstaller 只重做安装包

## 项目结构

```
ImgSnag-WeChat/
├── main.py                    # 入口：QApplication + 主窗口
├── version.py                 # 版本号唯一来源（发版脚本会同步到 .iss）
├── web_image_dl/
│   ├── app.py                 # 主窗口：解析页 + 历史页，侧边栏切换
│   ├── extractor.py           # 微信图片提取器（本项目核心）
│   ├── worker.py              # QThread 下载线程 + 图片元信息
│   ├── widgets.py             # 缩略图卡片 / 大图预览 / 侧边栏按钮
│   ├── history_manager.py     # SQLite 下载历史
│   └── blocked_config.py      # 屏蔽尺寸配置（JSON 热读写）
├── tests/test_extractor.py    # 提取器与画质策略回归测试（44 项）
├── tools/                     # 诊断脚本（排查微信改版用，不参与打包）
│   ├── e2e_article_download.py     # 端到端跑生产下载链路，对比新旧画质（推荐先用这个）
│   ├── probe_wechat_quality.py     # 量化某篇文章压缩档 vs 原图的差距
│   ├── probe_max_quality.py        # 穷举地址写法，确认 /0 是否画质上限（含分辨率）
│   ├── inspect_article_html.py     # 统计文章里图片地址的尺寸段与参数分布
│   ├── inspect_picture_page_info.py# 摊开 picture_page_info_list 原文
│   └── inspect_ppi_items.py        # 对比每个 item 的无水印版与水印版
├── ImgSnagWeChat.spec         # PyInstaller onedir 配置
├── ImgSnagWeChat.iss          # Inno Setup 安装包脚本
├── build_release.py           # 一键发版
└── build.bat                  # 双击发版入口
```

### 提取逻辑简述

`extractor.py` 是全部价值所在，四种来源合并后统一去重、再提升到原图档：

1. `picture_page_info_list` JS 变量 → 取每个 item 的第一条 `cdn_url`（**无水印版**；同 item 内嵌套的 `watermark_info.cdn_url` 是带水印的另一次上传，文件 ID 都不同）
2. `<img data-src>` → 正文图懒加载地址
3. `<img src>` → 兼容旧文章与封面
4. 可选：全量扫描 `<script>` 内的 `mmbiz.qpic.cn` 链接

然后依次做四件事：洗掉 JS/HTML 转义残留（`\x26amp;amp;`）与 `#imgIndex` 锚点 → 剔除 `round_head_img`（作者头像）→ 按图片身份（URL 里的 FILEID）合并 CDN 变体 → **改写尺寸段为 `/0` 取原图**。

尺寸档位是画质的关键：路径里的 `/640` 不是"宽 640 像素"，而是微信的一个**压缩档位**（实测 640 档给到 1080×1423），`/0` 才是原图档（同图 1280×1687）。这是"下载下来只有几百 KB"的根因。

## 致谢

### 开源库

- GUI 框架：[PySide6](https://wiki.qt.io/Qt_for_Python)（Essentials）
- HTTP 请求：[requests](https://github.com/psf/requests)
- 打包工具：[PyInstaller](https://github.com/pyinstaller/pyinstaller)
- 安装包制作：[Inno Setup](https://jrsoftware.org/isinfo.php)

### 参与开发

欢迎提交 Issue 和 Pull Request。改动提取逻辑（`extractor.py`）时，请一并说明对应的微信页面结构特征，方便回归验证。

改动后先跑回归测试：

```bash
.venv\Scripts\python.exe tests\test_extractor.py
```

发现某篇文章解析结果不对时，用 `tools/` 里的诊断脚本先看清页面结构再动手，别凭猜测改正则。

## 更新日志

> 唯一版本记录。用户向说明见 [GitHub Releases](https://github.com/virmuran/ImgSnag-WeChat/releases)。

### v1.1.0 (2026-09-17)

- 📐 **新增「原图画质」（默认开启）**：修正"下载下来只有几百 KB"的画质问题。根因是微信正文图地址里带画质档位——路径中的 `/640` 是压缩档、`/0` 才是原图档，旧版直接拿文章里的地址下载，拿到的一直是压缩档。现统一改写为 `/0`。用一篇真实的 8 图文章端到端实测：`1080×1440` → `2560×3413`，全篇合计 `1.1 MB` → `4.4 MB`（**3.86 倍**）
- 🛡 **原图失败自动回退**：原图档取不到（404 / 超时 / 返回过小）时自动退回压缩档，不会整张失败；状态栏会统计有多少张走了回退
- 🧹 **剥离噪声参数**：`tp=webp`（强制转有损图）、`wx_lazy`、`wxfrom`、`wx_co`、`#imgIndex` 锚点等一律清掉
- 🐛 **修复 JS/HTML 转义未清洗**：`picture_page_info_list` 里的地址原文是 `.../640?wx_fmt=gif\x26amp;amp;from=appmsg`，带转义残留会让请求拿到错误响应（现统一还原为 `&`）
- 🐛 **修复 `picture_page_info_list` 只认单引号**：真实结构确认为单引号 JS 字面量，但放宽到单/双引号都兼容，避免微信改写法后静默失效；同时确认每个 item 的两条 `cdn_url` 是**两个不同文件**（无水印版 + 带水印版，FILEID 不同），只取无水印那条
- 📊 解析完成后状态栏显示本次合计体积与回退张数
- ✅ 新增 `tests/test_extractor.py`（44 项回归测试）：地址改写、非 mmbiz 地址不误伤、CDN 变体合并、头像排除、无水印/水印版区分、转义清洗、下载回退与候选顺序
- 🔧 新增 `tools/` 诊断脚本 6 个，用于微信改版后快速定位（端到端画质对比、量化档位差距、穷举画质上限、统计地址结构、摊开 JS 变量）

### v1.0.0 (2026-09-17)

- 🎉 首个版本：从通用版 ImgSnag 收缩为**微信公众号专精版**
- `extractor.py` 只保留微信策略，移除抖音 / 微博 / 森空岛 / 小黑盒 / 堆糖 / C站等平台适配
- 移除 Playwright 与浏览器模式，改纯 requests 抓取；依赖仅剩 `PySide6-Essentials` + `requests`
- 数据目录与旧版隔离：历史库 `~/.imgsnag_wechat/`、屏蔽尺寸 `%APPDATA%/ImgSnagWeChat/`
- 链接入口增加校验：非 `mp.weixin.qq.com` 直接提示不支持，不做静默兜底
- 打包链路落地（复刻 ChemCal）：PyInstaller onedir + Inno Setup + 便携 zip，`build.bat` 一键发版

## 免责声明

本项目仅供个人学习与合法用途，用于下载你自己有权访问和保存的内容。请遵守微信平台服务条款与《著作权法》相关规定，**不要**用于批量抓取、商业转载或任何侵犯他人权益的行为。使用本工具产生的一切后果由使用者自行承担。

## 许可

本仓库**尚未声明开源许可证**。在补充许可证之前，默认保留所有权利。
