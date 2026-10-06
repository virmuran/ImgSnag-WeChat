# ImgSnag 手机版（Termux）

在手机上抓微信公众号文章的**原图**，存进相册目录。给红米 K80（HyperOS）实测写的。

跟电脑版的关系：**解析图片的规则是同一份代码**，所以 `/0` 原图档、去水印、
正文顺序合并这些能力完全一致——手机上抓到的图跟电脑上一样大、一样全。
区别只在界面：手机版没有窗口，走命令行和「分享菜单」。

> 需要装一个叫 **Termux** 的 App（免费、开源、在 F-Droid 或应用商店能下）。
> 它是安卓上的命令行环境，不用 root。

---

## 一、第一次安装

### 准备：装 Termux

推荐从 **F-Droid** 装（应用商店里的版本常过期）。装完打开，出现黑底终端界面就对了。

### 安装 ImgSnag

在 Termux 里，**一行一行**敲下面的命令（每行敲完按回车）：

```sh
pkg update -y && pkg install -y git
git clone https://github.com/virmuran/ImgSnag-WeChat.git
bash ImgSnag-WeChat/android/install.sh
```

第三条会自己装好 Python、requests，并把程序装到手机里。
中间如果弹**存储权限**的框，点**允许**。

装完会看到 `✅ 装好了`。

> **不想用 git 的话**：用 `ImgSnag_Termux.zip`（在电脑上生成的那份），
> 用微信/QQ 传给自己，存到手机 `Download` 目录，然后在 Termux 里跑
> ```
> pkg install -y unzip
> unzip ~/storage/shared/Download/ImgSnag_Termux.zip -d ~
> bash ~/android/install.sh
> ```

---

## 二、日常使用

### 方法一：分享（推荐，最省事）

微信里打开文章 → 右上角 **…** → **分享** → 在分享列表里找 **Termux**。

图会自动下完，存到：

```
手机存储 / Pictures / ImgSnagWeChat / 日期_时间_文章标题 /
        img_01.jpg  img_02.jpg  img_03.jpg …
```

相册里就能看到（`Pictures` 目录是系统相册会索引的地方）。

> 分享列表里第一次可能要找一下；找不到就往下滑，或者在「更多」里。
> 如果 Termux 压根没出现在分享列表里，用下面的方法二。

### 方法二：命令行

微信文章右上角 **…** → **复制链接**，回到 Termux：

```sh
imgsnag
```

它会自动读剪贴板（需要装 **Termux:API** App；不装的话把链接直接跟在后面）：

```sh
imgsnag https://mp.weixin.qq.com/s/xxxxxxxx
```

### 常用参数

| 命令 | 作用 |
|---|---|
| `imgsnag --list <链接>` | 只看会下哪些图，不真下（先探路用） |
| `imgsnag --out ~/storage/shared/DCIM <链接>` | 存到指定目录 |
| `imgsnag --scripts <链接>` | 提示「没找到图片」时加这个再试 |
| `imgsnag --insecure <链接>` | 连不上、报证书错误时用 |
| `imgsnag -h` | 全部参数 |

---

## 三、红米 K80 / HyperOS 要绕的两个坑

这两个坑不绕，表现都是「刚才还能用，过一会儿就抓不到图了」，很难自己想到原因。

**坑一：Termux 被系统杀掉**

小米的省电策略会清理后台。设置 → 应用设置 → 应用管理 → **Termux** → 省电策略选
**无限制**；再在最近任务里把 Termux **锁定**（长按卡片上的锁图标）。

**坑二：存储权限**

图片存在相册目录，需要 Termux 有「所有文件访问权限」。
设置 → 应用设置 → 应用管理 → **Termux** → 权限 → 文件和媒体 → 允许管理所有文件。

装完没弹授权框、图片跑到了 Termux 私有目录（相册里看不到）时，回来查这一项。

---

## 四、常见问题

**「没找到图片」**
先加 `--scripts` 再试一次。还不行，说明这篇文章要么真没图，要么需要登录才能看
（比如需要关注的号），手机上确实拿不到。

**相册里没有新图**
Android 会自己扫，但可能要等一会儿或重启相册 App。
想立刻生效：装 **Termux:API** App 后执行 `pkg install -y termux-api`，
之后 imgsnag 会自动通知系统刷新。

**图比电脑上小**
不会。两边用的是同一套提原图规则。如果确实觉得小，用 `--list` 看看地址里是不是
`/0` —— 是的话就是这篇的最大画质了。

**`pkg install` 卡住**
换网络（WiFi ↔ 流量）重试，或者先跑一次 `pkg update -y`。

---

## 五、更新与卸载

**更新**（电脑端的解析规则改了之后）：

```sh
cd ~/ImgSnag-WeChat && git pull
bash android/install.sh
```

**卸载**：

```sh
rm -rf ~/imgsnag ~/bin/imgsnag ~/bin/termux-url-opener ~/ImgSnag-WeChat
```

已下载的图片不受影响（它们在相册目录里，自己删就行）。

---

## 附：给维护者

```
android/
├── app/
│   ├── imgsnag.py            ← 手机端主程序（HTTP 抓取/下载/落盘/进度）
│   └── web_image_dl/         ← **自动生成的副本，不要手改**
├── sync_core.py              ← 把主项目的核心逻辑同步进 app/
├── install.sh                ← 一键安装
└── termux-url-opener         ← 「分享 → Termux」入口
```

`app/web_image_dl/` 下的 6 个文件是从项目根的 `web_image_dl/` **拷贝**过来的
（那 6 个零 Qt、零第三方依赖，所以能直接在手机上跑）。**主项目是唯一来源。**

改完微信解析规则后：

```sh
.venv/Scripts/python.exe android/sync_core.py          # 同步
.venv/Scripts/python.exe android/sync_core.py --check   # 只校验
```

忘记跑的话，`tests/test_imgsnag.py` 里有一条会红（逐字比对副本与源文件）。
新增站点适配器时，记得往 `sync_core.py` 的 `FILES` 里加一行。
