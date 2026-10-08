# -*- coding: utf-8 -*-
"""ImgSnag 安卓版 —— 界面后端（本地 HTTP 服务 + 会话模型）

────────────────────────────────────────────────────────────────────────
为什么界面做成"内嵌网页"而不是原生控件
────────────────────────────────────────────────────────────────────────
手机端真正麻烦的三件事 —— 图片网格、点开看大图、双指缩放 ——
浏览器天生就做好了：布局、懒加载、图片解码与内存回收、手势、深浅色。
换成原生控件手写要几百行，而且**开发机上没有任何安卓模拟器**，
那几百行一个字都验不了（这个项目的规律：验不了的地方就是出 bug 的地方）。

网页界面则能在电脑上原样跑起来、看到效果、截图比对。于是：

    WebView  ── 加载 ──▶  本模块起的本地服务（只绑 127.0.0.1）
              页面与接口**同源** → 不跨域、不需要 JS↔Java 桥

────────────────────────────────────────────────────────────────────────
分工与握手点
────────────────────────────────────────────────────────────────────────
    imgsnag_android.py   接平台：urllib 网络、安卓证书合并、报告文本
    imgsnag.py           抓取主流程的公共零件（下载、去重、命名、体积）
    web_image_dl/        站点知识（与桌面版/Termux 版同一份，别手改）
    imgsnag_web.py       本文件：会话模型 + HTTP 接口 + 相册对接
    webui.py             页面（HTML/CSS/JS，单文件）

**与 Java 的握手点只有三个**，测试逐条钉住：
    ① 相册入库：本模块调 `Gallery.publishDir(ctx, 目录, 相册子目录)`，拿回成功张数
    ② 分享投递：Java 调 `push_share(文本)`
    ③ 启动：Java 调 `start(ctx, 版本号, files_dir=应用私有目录)`，拿回端口
除这三处之外两边互不依赖 —— Java 越薄，能出错的地方越少。
（页面里那个「从剪贴板」按钮、以及**装更新包**走的是 WebView 的 JS 桥，不经过本模块。）

────────────────────────────────────────────────────────────────────────
应用内更新（安卓版）
────────────────────────────────────────────────────────────────────────
流程：检查版本（比版本号）→ 用户点「更新」→ 本模块下载 APK 到
`<files_dir>/update/`（**必须**落在 Java 侧 FileProvider 声明过的那个目录里，
否则装的时候会报"文件不存在"，而且只在真机上暴露）→ 校验大小与 sha256 →
网页调 Java 桥 `install(路径)` 交给系统安装器。

为什么下载放 Python 而不是 Java：这套逻辑能在电脑上跑测试（假网络 + 假文件），
而 Java 那侧一行都验不了。Java 只留"最后一步"：把文件交给系统安装器。

失败一定要**说人话**并**留着网页下载这条路**：手机网络下 GitHub 的下载 CDN
未必连得上（桌面版实测过连不上的情况），那时用户至少还能点「去网页下载」。

────────────────────────────────────────────────────────────────────────
偏好：小图过滤与保存画质
────────────────────────────────────────────────────────────────────────
存在应用目录下的 `web_settings.json`（原子写）。两个开关：

**保存画质** —— 开＝保存时取原图档（清晰、费流量），关＝取压缩档。
微信那张图本来就有两个档位，这里只是决定"点保存时用哪个"，不额外下载。

**跳过小图** —— 表情、二维码、分割线这类小图是相册里最烦的垃圾。
解析完成后会有一个**体检**：逐张把缩略图档拿一遍，量体积，小于阈值的
在界面上**默认不勾选**（并打一个「小」标）。

体检**不产生额外流量**：缩略图本来就要下给网格看，这里只是提前下、
顺手量一下。关掉这个开关则完全跳过体检（不想要过滤的人零成本）。

⚠ 小图只是"默认不勾"，**不是静默丢弃**：用户手动勾上的照存不误。
在"用户的选择"和"机器的判断"之间，永远前者优先 —— 悄悄少存几张，
比多存几张难查得多。

────────────────────────────────────────────────────────────────────────
画质档位：缩略图与"保存时"用不同档
────────────────────────────────────────────────────────────────────────
微信同一张图有两个地址：`/0` 原图档（实测最大差 4 倍体积）与 `/640` 压缩档。
适配器的 `candidates()` 是**首选项在前**，于是这里：
    缩略图 用候选表**末位**（最小的档）—— 一屏几十张也不心疼流量
    保存时 用候选表**首位**（原图档）  —— 挑中的才下原图
比桌面版更省：桌面版是先把每张按原图拉进内存、再挑哪些落盘。

所以"体检"量的就是缩略图档的体积 —— 那张图本来就要下给网格看，
量一下不花额外流量；而"关掉过滤"的用户连体检都不跑。

格式不用地址猜、**用文件头判**（`sniff_ext`）—— 地址猜格式在这个项目上
已经出过一次"整类 png 被存成 .jpg"，内容骗不了人。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import imgsnag                                                       # noqa: E402
import imgsnag_android                                               # noqa: E402
import webui                                                         # noqa: E402
from web_image_dl import updater                                     # noqa: E402
from web_image_dl.history_manager import HistoryManager              # noqa: E402
from web_image_dl.naming import folder_name                          # noqa: E402
from web_image_dl.sites import get_adapter, supported_names          # noqa: E402

APP_LABEL = 'ImgSnag 安卓版'

#: 相册里的子目录名（最终路径 `Pictures/ImgSnagWeChat/<文章文件夹>`）。
#: 取值来自同步进来的 `imgsnag.DEFAULT_SUBDIR` —— 只有一处定义。
#: Java 侧 `Gallery.ALBUM` 必须与它一致，测试钉住这条；不一致的表现是
#: "图抓到了但相册里没有"，而且两边都不报错（本项目踩过一次的坑）。
ALBUM_SUBDIR = imgsnag.DEFAULT_SUBDIR

#: 相册入库的 Java 类。**全限定名** —— Chaquopy 的 `java.jclass()` 只认这个写法。
GALLERY_CLASS = 'com.virmuran.imgsnag.Gallery'

#: 应用私有目录下的布局
SESSION_DIR = 'sessions'
THUMB_DIR = 'thumb'
FULL_DIR = 'full'
OUT_DIR = 'out'
HISTORY_DB = 'history.db'

#: 保留几份会话缓存（含缩略图）。图本身在相册里，这里只留到"能再打开一次"的程度。
MAX_SESSIONS = 5

#: 同时最多几张图在下载（浏览器连接多，不限会一次开几十个）
MAX_CONCURRENT_DOWNLOADS = 4

_LOG_MAX = 200

#: 偏好文件（放在应用目录里，跟会话缓存同处）。
SETTINGS_FILE = 'web_settings.json'

#: 默认偏好。**键名与默认值只写在这一处** —— 界面、路由、测试都从这里取，
#: 免得"界面以为是开的、后端以为是关的"这种对不上还两边都不报错的情况。
DEFAULT_SETTINGS = {
    # True＝保存时取原图档（清晰、费流量）；False＝取压缩档（省流量）
    'original': True,
    # True＝解析后体检一遍，小图默认不勾选
    'skip_tiny': True,
    # 小于这个体积（KB）算小图。微信的表情/二维码通常只有几 KB，
    # 正文配图几十到几百 KB —— 12 KB 这条线实测能分开这两类。
    # ⚠ 别调到 `imgsnag.MIN_IMAGE_BYTES`（500 字节）以下：比它更小的响应
    # 在下载那一步就被当失败丢了（CDN 出错时返回的占位图就那么大），
    # 根本到不了体检这一步，阈值调得再低也筛不出东西来。
    'tiny_kb': 12,
}

_MIME = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp',
}

#: 版本更新检测的状态文件。**故意与偏好分开** —— 偏好是"用户的意思"，
#: 这个只是"机器上次问到的答案"，混在一起会让 normalize_settings 的意图变模糊。
UPDATE_FILE = 'update_check.json'

#: 问哪个接口。安卓安装包挂在**滚动的预发布位** `apk-latest` 上：
#: 每次云构建都先删掉整个 release 再重建，所以资产上的创建时间就是那次构建的时间。
#: ⚠ 不能沿用桌面版那个 `/releases/latest` —— 它**只返回正式发布**，
#: 看不见这个预发布位，结果是安卓版永远显示"已是最新"，而且不报任何错。
#: 仓库名从同步来的 updater 里取，两处不会漂移。
APK_RELEASE_TAG = 'apk-latest'
APK_API_URL = (f'https://api.github.com/repos/{updater.GITHUB_REPO}'
               f'/releases/tags/{APK_RELEASE_TAG}')
APK_PAGE_URL = (f'https://github.com/{updater.GITHUB_REPO}'
                f'/releases/tag/{APK_RELEASE_TAG}')

#: 判定"有新版本"的宽限期（毫秒）。手机时钟与 GitHub 的服务器时钟总有几秒差，
#: 不留余量的话"刚装完就提示有新版本"会反复出现 —— 用户只能学会无视它。
UPDATE_SLACK_MS = 60 * 1000

#: "本机安装时间"合理的下限（毫秒）= 2020-01-01。比这更早的一律当作"问不到"。
#: 防的是这种错：Java 侧若用 int 去存毫秒时间戳，值会被截断成**负数**，
#: 而负数在布尔判断里是"真" —— 结果就是每次检查都报"有新版本"，
#: 比老实说"不知道"糟得多。手机时钟跑飞了也被这一条兜住。
MIN_INSTALL_MS = 1577836800000

#: 应用内更新的安装包目录名。**必须是 Java 侧 FileProvider 声明过的那个子目录**
#: （`res/xml/file_paths.xml` 里的 `<files-path path="update/">`，
#: 与 `MainActivity.UPDATE_DIR` 同名）。三处不一致的表现是「下载完成、点安装却
#: 报文件不存在」，而且只在真机上才暴露 —— 测试把这三处钉在一起。
UPDATE_DIR_NAME = 'update'

#: 下载安装包时每次读多少（64 KB：进度条够顺，又不至于把时间耗在 syscall 上）
APK_CHUNK = 64 * 1024

#: 下载安装包的超时（秒）。安装包有二十多兆，比问接口宽松得多 ——
#: 手机上慢网常见，超时给短了表现是"总是下载失败"，用户只能反复重试。
APK_TIMEOUT = 120

#: 下载安装包的 User-Agent。GitHub 的资产下载地址对空 UA 不友好，
#: 而且带上一眼能看出是谁在下（对端日志里好认）。
APK_USER_AGENT = 'ImgSnag-Android'


# ============================================================================
#  小工具
# ============================================================================

def session_id(source: str) -> str:
    """由文章地址推出会话目录名。

    用地址而不是时间戳：同一篇文章重新解析时**复用同一份缓存**；
    历史库里一行也就能直接对应到缓存目录 —— 不必给共享的数据库加字段。
    """
    return hashlib.md5((source or '').encode('utf-8')).hexdigest()[:12]


def sniff_ext(data: bytes, fallback: str = '.jpg') -> str:
    """从文件头判断图片真实格式，决定存盘扩展名与 Content-Type。"""
    if not data:
        return fallback
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if data[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if data[:4] == b'GIF8':
        return '.gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return fallback


def mime_of(ext: str) -> str:
    return _MIME.get((ext or '').lower(), 'application/octet-stream')


def write_atomic(path: str, data: bytes) -> None:
    """先写临时文件再 os.replace —— 中途被杀不会留下半截图片。"""
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def clear_dir(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def read_head(path: str, n: int = 16) -> bytes:
    try:
        with open(path, 'rb') as f:
            return f.read(n)
    except OSError:
        return b''


def as_int(value, default=0) -> int:
    """把 JSON 里的数字/数字字符串取成 int（前端传来的东西不能信）。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_iso(iso: str):
    """ISO 时间串 → 本地时区的 datetime；认不出来返回 None。

    GitHub 给的是 UTC（形如 `2026-10-06T07:12:33Z`），**必须转本地**再显示：
    不转的话"今天 07:12 构建的"其实是北京 15:12，用户会以为那不是自己刚推的那次。
    （`fromisoformat` 从 Python 3.11 起才认末尾的 `Z`，这里用 3.12/3.13，没问题。）
    """
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt.astimezone() if dt.tzinfo else dt


def iso_ms(iso: str) -> int:
    """ISO 时间 → 毫秒时间戳（认不出来返回 0）。

    与 `pretty_time` 共用同一个解析 —— 显示的时间和拿来比大小的时间
    必须来自同一处，否则会出现"界面说 15:12、判断却按 07:12"这种对不上的怪事。
    """
    dt = _parse_iso(iso)
    return int(dt.timestamp() * 1000) if dt else 0


def pretty_time(iso: str) -> str:
    """把 ISO 时间转成人话：今天 14:03 / 昨天 09:12 / 10-04 20:31"""
    dt = _parse_iso(iso)
    if dt is None:
        return iso or ''
    now = datetime.now()
    if dt.date() == now.date():
        return f'今天 {dt:%H:%M}'
    if (now.date() - dt.date()).days == 1:
        return f'昨天 {dt:%H:%M}'
    if dt.year == now.year:
        return f'{dt:%m-%d %H:%M}'
    return f'{dt:%Y-%m-%d}'


def title_from_path(path: str) -> str:
    """从 `Pictures/ImgSnagWeChat/2026-10-06_1133_标题` 里取回标题。

    历史库里没有标题列，而文件夹名本来就是 `日期_时分秒_标题` ——
    标题就在里面，比给共享的数据库加一列划算。
    """
    name = (path or '').replace('\\', '/').rstrip('/').split('/')[-1]
    parts = name.split('_', 2)
    return parts[2] if len(parts) == 3 else ''


def pick_source(text: str, html: str = '') -> tuple:
    """从「剪贴板里同时拿到的纯文本与 HTML」里决定拿哪个去解析。

    手机剪贴板常常是**两份内容**：纯文本（可能只是标题或一坨空白）
    加一份 HTML（浏览器复制富文本时才有）。三种情况，顺序即优先级：

      1. 纯文本里有链接   → 用链接。最省流量，抓到的内容也最全
      2. 纯文本本身就是 HTML（`<` 开头）→ 当源码用（用户自己粘的源码）
      3. 两者都不像       → HTML 非空就用 HTML 当源码（浏览器里复制的正文）
                           否则把纯文本原样交出去，让适配器自己去猜

    ⚠ 传进来的 `html` **不能拿去 `extract_url`**：HTML 里的第一个链接
    通常是 `<img src>`，那是图片地址不是文章地址 —— 拿它去解析只会
    抓回一张图。所以这里只把 HTML 整体当源码。

    `html=''` 时，本函数与 `imgsnag.resolve_source` 行为完全一致
    （测试逐条对照过），所以它只是"多带了 HTML 这一路"，不是另一套规则。
    """
    text = (text or '').strip()
    url = imgsnag.extract_url(text)
    if url:
        return url, True
    if text.startswith('<'):
        return text, False
    if (html or '').strip():
        return html, False
    return text, True


def load_settings(path: str) -> dict:
    """读偏好。文件不在、坏了、多了不认识的键 —— 一律退回默认，绝不抛。"""
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(path, encoding='utf-8') as f:
            got = json.load(f)
    except (OSError, ValueError):
        return data
    if isinstance(got, dict):
        for key in DEFAULT_SETTINGS:
            if key in got:
                data[key] = got[key]
    return data


def normalize_settings(raw) -> dict:
    """把外部传进来的偏好收敛成「只认识的键 + 正确的类型」。

    前端传来的东西不能信：`{'original': 'false'}` 这种字符串真值会让
    开关怎么点都是开的。布尔键只认真正的 bool，数字键转成非负整数。
    """
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, default in DEFAULT_SETTINGS.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default, bool):
            # ⚠ 只认**真正的**布尔值。写成 `bool(value)` 是个坑：
            # 字符串 'false' 也是真值 → 表现是"开关怎么点都是开的"，
            # 而且点一下看着生效了、刷新又弹回去。类型不对就保持原值。
            if isinstance(value, bool):
                out[key] = value
        else:
            try:
                out[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue
    return out


def _dl_idle() -> dict:
    """下载状态的初始值（也用于取消/失败后的复位）。

    状态机：idle → downloading → done
                            ↘ error（带人话原因）
    字段给界面用：`done`/`total` 是字节数（total 为 0 时显示"不确定进度"），
    `path` **只在 done 时才有值** —— 界面上「安装」按钮就靠它判断能不能点。
    """
    return {'phase': 'idle', 'done': 0, 'total': 0, 'error': '',
            'path': '', 'name': '', 'version': ''}


def _net_error_text(exc: Exception) -> str:
    """把网络异常翻成手机用户看得懂的一句话。

    安卓上没有控制台可看，用户唯一的线索就是这句话；所以宁可比"网络错误"
    具体一点，也别把 urllib 的英文原文直接扔出去。
    """
    import socket                                                   # noqa: PLC0415
    if isinstance(exc, urllib.error.HTTPError):
        return f'下载地址返回 HTTP {exc.code}（安装包可能已被替换或删除，稍后再试）'
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, socket.timeout):
            return '下载超时了，换个网络（或连上 Wi-Fi）再试一次'
        text = str(reason)
        if 'CERTIFICATE' in text.upper() or 'certificate' in text:
            return '证书校验没过（像是网络被中转），换个网络再试一次'
        return f'连不上下载地址：{text}'
    if isinstance(exc, socket.timeout):
        return '下载超时了，换个网络（或连上 Wi-Fi）再试一次'
    return str(exc) or type(exc).__name__


@dataclass
class Item:
    """一张待处理的图：两个档位的地址都留着，用哪档由调用方决定。"""
    i: int
    cands: list = field(default_factory=list)      # 首选项在前（原图档在前）

    @property
    def original(self) -> str:
        return self.cands[0] if self.cands else ''

    def thumb_candidates(self) -> list:
        """缩略图用最小档优先，失败才退回大档（顺序颠倒即可）。"""
        return list(reversed(self.cands))

    def to_json(self) -> dict:
        return {'i': self.i, 'cands': list(self.cands)}

    @staticmethod
    def from_json(d) -> 'Item':
        d = d or {}
        return Item(i=as_int(d.get('i')), cands=[str(u) for u in (d.get('cands') or [])])


class NotFound(Exception):
    """不是本次会话的图 / 序号越界。"""


class Failed(Exception):
    """图确实下不下来。"""


# ============================================================================
#  会话引擎
# ============================================================================

class _Cancelled(Exception):
    """用户取消了下载（下载线程用它把自己干净地收尾）。"""


def _silent_remove(path: str):
    """删掉半截文件；删不掉也不报（它只会占一点空间，下次会被覆盖）。"""
    try:
        os.remove(path)
    except OSError:
        pass


class Engine:
    """一次「解析 → 挑图 → 保存」的过程，以及它在磁盘上的缓存。

    状态机：idle → parsing → ready → saving → saved
                      ↘ error（任何一步失败都落到这里，并带上人话说明）

    `saved` 之后仍然可以再保存（改选几张、补存漏掉的），所以 save() 同时接受
    ready 与 saved 两个状态 —— 不允许"只能保存一次"，那是数据丢失式的设计。
    """

    def __init__(self, root=None, get=None, publish=None, when=None, version='',
                 files_dir=None, apk_opener=None):
        self.root = root or os.path.expanduser('~')
        os.makedirs(self.root, exist_ok=True)
        #: 注入点：测试用假网络、假相册；None = 走真实实现
        self.get = get
        self.publish = publish
        #: 注入点：给文件夹名一个「假装是别的时刻」的机会；不注入就用当前时间。
        #: 写成 `lambda: None` 而不是留着 None —— 调用处是 `self.when()`，
        #: 留着 None 会炸在 "'NoneType' object is not callable"（测试当场抓到过）。
        self.when = when or (lambda: None)
        self.version = version

        #: 应用内更新的安装包落在这个目录。安卓侧由 Java 把应用私有目录显式传进来
        #: （FileProvider 就声明在那里）；桌面/测试环境退回数据目录 —— 这条回退
        #: 只是为了能在电脑上跑起来，安卓上的下载目录永远以传进来的为准。
        self.update_dir = os.path.join(files_dir or self.root, UPDATE_DIR_NAME)
        #: 注入点：下载安装包用的 opener（测试注入假的，不让它真去下二十兆）
        self.apk_opener = apk_opener
        #: 下载状态。**名称必须与任何方法名区分开** —— 早先 `save` 就撞过一次：
        #: 实例属性把同名方法覆盖掉，调用时当场 "'dict' object is not callable"。
        self.dl = _dl_idle()
        #: 用户取消下载的标志。置上之后下载线程自己收尾（删半截文件）。
        self._dl_cancel = False

        self.lock = threading.RLock()
        self._sem = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self._locks: dict = {}
        self.hist = HistoryManager(db_path=os.path.join(self.root, HISTORY_DB))

        self.settings_path = os.path.join(self.root, SETTINGS_FILE)
        self.settings = load_settings(self.settings_path)

        #: 最近一次「检查更新」的结果。启动时从盘上读回来 —— 节流必须跨启动生效，
        #: 否则每打开一次 App 就问一遍 GitHub（未认证请求按 IP 限流 60 次/小时）。
        self.upd = self._load_update()

        self._reset()

    # ── 状态 ────────────────────────────────────────────────────────────────

    def _reset(self):
        """回到"没有会话"的状态。调用方需持有 self.lock（构造时除外）。"""
        self.phase = 'idle'
        self.message = ''
        self.title = ''
        self.folder = ''
        self.source = ''
        self.is_url = True
        self.sid = ''
        self.items: list = []
        self.progress = {'done': 0, 'total': 0}
        #: 保存进度。**名字必须与 save() 方法区分开** —— 早先这里叫 `self.save`，
        #: 于是把同名方法覆盖成了字典，`engine.save(...)` 当场报
        #: "'dict' object is not callable"（测试抓到的）。加实例属性前先看一眼
        #: 有没有同名方法：Python 不会为此报警，只会在这个方法被调时炸。
        self.save_progress = {'done': 0, 'total': 0}
        self.entry_id = 0
        self.pending_share = ''
        self.logs: list = []
        #: 小图体检的结果：{序号: 缩略图档字节数}。0 表示这张没拿到（不是小图）。
        self.sizes: dict = {}
        self.inspected = 0
        self.checking = False
        self.checked = False
        self._headers = imgsnag.build_headers('')
        self._locks = {}

    def log_line(self, text: str):
        with self.lock:
            self.logs.append(f'{datetime.now():%H:%M:%S} {text}')
            if len(self.logs) > _LOG_MAX:
                del self.logs[:len(self.logs) - _LOG_MAX]

    def state(self) -> dict:
        with self.lock:
            return {
                'phase': self.phase,
                'message': self.message,
                'title': self.title,
                'folder': self.folder,
                'source': self.source,
                'sid': self.sid,
                'count': len(self.items),
                'progress': dict(self.progress),
                'save': dict(self.save_progress),
                'log': list(self.logs),
                'sites': supported_names(),
                'album': ALBUM_SUBDIR,
                'label': APP_LABEL,
                'version': self.version,
                'settings': dict(self.settings),
                #: 小图清单（序号，1 起）。阈值跟着设置走，用户一改立刻生效。
                'tiny': self.tiny_indexes(),
                #: 体检进度；`total` 为 0 或 `running` 为假且 `done` 为 0 时界面不显示
                'check': {'done': self.inspected, 'total': len(self.items),
                          'running': self.checking},
                #: 应用内更新的下载进度。挂在通用状态里，页面就不用为它单开一个轮询。
                'dl': dict(self.dl),
            }

    def tiny_indexes(self) -> list:
        """"会被自动跳过"的图序号（1 起，升序）。

        两条判据都在这儿，页面只管照着用：
          · 过滤关着 → 空表。用户说了不在乎小图，界面就不该再打标、再改勾选。
          · 阈值跟着设置走，所以改阈值立刻生效，不必重新解析。

        `0 < n`：没体检到（下不下来）的不算小图 —— 那是"坏图"，
        跟"图小"是两回事，混在一起会让用户以为过滤逻辑坏了。
        """
        if not self.settings.get('skip_tiny'):
            return []
        limit = as_int(self.settings.get('tiny_kb'), 0) * 1024
        if limit <= 0:
            return []
        with self.lock:
            return sorted(i for i, n in self.sizes.items() if 0 < n < limit)

    # ── 路径 ────────────────────────────────────────────────────────────────

    def session_dir(self, sid: str) -> str:
        return os.path.join(self.root, SESSION_DIR, sid or '_')

    def _session_json(self, sid: str) -> str:
        return os.path.join(self.session_dir(sid), 'session.json')

    def _net(self):
        """真实网络函数：urllib + 安卓证书合并（电脑上就是普通 urllib）。"""
        if self.get is None:
            self.get = imgsnag_android.make_get(
                imgsnag_android.ssl_context(cache_dir=self.root))
        return self.get

    # ── ① 解析 ──────────────────────────────────────────────────────────────

    def parse(self, text: str, html: str = '') -> bool:
        """开始解析（链接 / 分享文本 / 网页源码）。正在解析或保存时拒绝。

        `html` 是「从剪贴板」那条路额外带过来的 HTML（浏览器复制的富文本）。
        只有一个输入时传空串即可，行为与从前完全一样。
        """
        with self.lock:
            if self.phase in ('parsing', 'saving'):
                return False
            self._reset()
            self.phase = 'parsing'
            self.message = '正在打开文章…'
        self.log_line(f'开始解析：{(text or "")[:120]}')
        threading.Thread(target=self._parse_worker, args=(text or '', html or ''),
                         daemon=True).start()
        return True

    def _parse_worker(self, text: str, paste_html: str = ''):
        try:
            # ⚠ 参数叫 paste_html 而不是 html：下面 `html` 是**抓回来的网页正文**，
            # 同名会把剪贴板那份悄悄覆盖掉（覆盖之后还不报错，只是行为诡异）。
            source, is_url = pick_source(text, paste_html)
            if not source:
                return self._fail('没能从这段内容里找到链接。\n'
                                  '可以复制文章链接后再试一次。')

            adapter = get_adapter(source, is_url=is_url)
            if adapter is None:
                return self._fail(f'这个链接不认识。\n目前支持：{supported_names()}\n'
                                  f'（认出来的是 {source[:80]}）')

            self.is_url = is_url
            if is_url:
                self.source = source
                try:
                    html = imgsnag.fetch_html(source, get=self._net())
                except Exception as e:                              # noqa: BLE001
                    return self._fail('打开文章失败：'
                                      + imgsnag_android.polish(str(e)))
                self._headers = imgsnag.build_headers(source)
            else:
                # 粘的是源码：历史里不能把整页 HTML 塞进 URL 列，压成一个短标记
                self.source = 'html:' + hashlib.md5(source.encode('utf-8')).hexdigest()[:12]
                html = source
                self._headers = imgsnag.build_headers('')

            title = adapter.extract_title(html)
            urls = adapter.extract(html, include_scripts=False)
            if not urls and is_url:
                self.log_line('正文里没直接看到图，补扫 <script> 再找一遍…')
                urls = adapter.extract(html, include_scripts=True)
            if not urls:
                return self._fail('这篇文章里没找到图片。\n'
                                  '可能原因：文章确实没图 / 需要登录才能看 / '
                                  '图藏在脚本里（已自动补扫过一次）。')

            items = [Item(i=i, cands=list(adapter.candidates(u)) or [u])
                     for i, u in enumerate(urls, 1)]
            sid = session_id(self.source)
            title_ok = title or adapter.fallback_title
            folder = folder_name(title, when=self.when(),
                                 fallback=adapter.fallback_title)
            self._write_session(sid, title, folder, items, adapter)

            with self.lock:
                self.items = items
                self.sid = sid
                self.title = title_ok
                self.folder = folder
                self.phase = 'ready'
                self.message = f'找到 {len(items)} 张图'
            self.log_line(f'标题：{title_ok}')
            self.log_line(f'共 {len(items)} 张；缩略图按需加载，选中保存时才取原图')

            # 历史先落一条"已解析"，保存成功后同一条会被更新成"已下载"
            try:
                self.entry_id = self.hist.add(self.source, total_images=len(items),
                                              status='scanned', note=title_ok)
            except Exception as e:                                  # noqa: BLE001
                self.log_line(f'历史写入失败：{e}')
            self._prune()
            # 网格已经在画了，接着把"哪几张是小图"量出来（零额外流量）
            self._maybe_inspect()

        except Exception as e:                                      # noqa: BLE001
            traceback.print_exc()
            self._fail(f'{type(e).__name__}: {e}')

    def _maybe_inspect(self):
        """需要的话起一轮小图体检。守卫都在这儿，调用处不必判。"""
        with self.lock:
            if not self.settings.get('skip_tiny'):
                return
            if self.phase != 'ready' or not self.items:
                return
            if self.checking or self.checked:
                return
            self.checking = True
            sid = self.sid
        threading.Thread(target=self._inspect_worker, args=(sid,), daemon=True).start()

    def _inspect_worker(self, sid: str):
        """逐张把缩略图档量一遍体积 —— 这就是网格要显示的那批图。

        并发跑（缩略图是一次几十毫秒的小请求，串行会让人干等十几秒），
        但整体仍受 `_sem` 限流，不会把带宽全占了。

        每张都回头看一眼 `self.sid` 还是不是起点那个：用户在体检途中
        重新解析了另一篇，这条线程就该安静退出，而不是继续给旧会话干活。
        """
        box = queue.Queue()
        for i in range(1, len(self.items) + 1):
            box.put(i)

        def one():
            while True:
                try:
                    i = box.get_nowait()
                except queue.Empty:
                    return
                if self.sid != sid:
                    return
                size = 0
                try:
                    path, _ = self._fetch_item(sid, 't', i)
                    size = os.path.getsize(path)
                except Exception:                                   # noqa: BLE001
                    size = 0
                with self.lock:
                    self.sizes[i] = size
                    self.inspected += 1

        threads = [threading.Thread(target=one, daemon=True)
                   for _ in range(min(MAX_CONCURRENT_DOWNLOADS, len(self.items)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with self.lock:
            if self.sid != sid:
                return                          # 会话换了：别去改新会话的状态
            self.checking = False
            self.checked = True
            tiny = len(self.tiny_indexes())
        self.log_line(f'体检完成：{tiny} 张偏小，默认没勾' if tiny
                      else '体检完成：没有小图')

    def _fail(self, message: str):
        with self.lock:
            self.phase = 'error'
            self.message = imgsnag_android.polish(message)
        self.log_line('失败：' + (message or '').split('\n')[0])

    def _write_session(self, sid: str, title: str, folder: str, items, adapter):
        """落一份 session.json —— 历史里"点开这一次"全靠它恢复。

        **必须存下每张图的地址**：不然"打开上次那篇"就只剩一排空壳，
        选了也下不动（这是本文件第一版的 bug，写在这里免得再犯）。
        """
        base = self.session_dir(sid)
        os.makedirs(base, exist_ok=True)
        clear_dir(os.path.join(base, THUMB_DIR))
        clear_dir(os.path.join(base, FULL_DIR))
        data = {
            'source': self.source,
            'is_url': self.is_url,
            'title': title,
            'folder': folder,
            'adapter': adapter.name,
            'when': datetime.now().isoformat(),
            'items': [it.to_json() for it in items],
        }
        write_atomic(self._session_json(sid),
                     json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _prune(self):
        """只留最近 MAX_SESSIONS 份会话缓存（缩略图占地，图本身在相册里）。"""
        base = os.path.join(self.root, SESSION_DIR)
        try:
            subs = [(os.path.getmtime(os.path.join(base, n)), os.path.join(base, n))
                    for n in os.listdir(base)]
        except OSError:
            return
        subs.sort(reverse=True)
        for _, path in subs[MAX_SESSIONS:]:
            clear_dir(path)

    # ── ② 图片（按需下载 + 磁盘缓存）─────────────────────────────────────────

    def _item(self, sid: str, i: int) -> Item:
        with self.lock:
            if not sid or sid != self.sid:
                raise NotFound('会话已切换')
            if not (1 <= i <= len(self.items)):
                raise NotFound('没有这张图')
            return self.items[i - 1]

    def _lock_for(self, key):
        with self.lock:
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    def _fetch_item(self, sid: str, kind: str, i: int) -> tuple:
        """确保某张图的某个档位已在本机，返回 (路径, 扩展名)。

        `kind`：`t` 缩略图（最小档优先）/ `f` 原图（原图档优先）。
        同一张图被多个请求同时要时，只有第一个真的去下，其余等它。
        """
        it = self._item(sid, i)
        sub = THUMB_DIR if kind == 't' else FULL_DIR
        folder = os.path.join(self.session_dir(sid), sub)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{i}.img')

        if os.path.exists(path):
            return path, sniff_ext(read_head(path))

        with self._lock_for((sid, kind, i)):
            if os.path.exists(path):
                return path, sniff_ext(read_head(path))
            cands = it.thumb_candidates() if kind == 't' else list(it.cands)
            if kind == 'f' and len(self.items) > 1:
                self.log_line(f'取原图 {i}/{len(self.items)}…')
            try:
                self._sem.acquire()
                try:
                    used, data = imgsnag.download_one(cands, self._headers, self._net())
                finally:
                    self._sem.release()
            except Exception as e:                                  # noqa: BLE001
                self.log_line(f'第 {i} 张出错：{type(e).__name__}: {e}')
                used, data = None, None
            if not data:
                raise Failed(f'第 {i} 张没下下来')
            write_atomic(path, data)
            if used and used != it.original:
                self.log_line(f'第 {i} 张用了压缩档（原图档取不到）')
            return path, sniff_ext(data)

    def image(self, sid: str, kind: str, i: int) -> tuple:
        """给 HTTP 层用：返回 (路径, mime)。失败抛 NotFound / Failed。"""
        path, ext = self._fetch_item(sid, kind, i)
        return path, mime_of(ext)

    # ── ③ 保存到相册 ────────────────────────────────────────────────────────

    def save(self, idxs, original=None) -> bool:
        """把选中的图下载、去重，交给 Java 写进相册。

        `original`：None＝按设置（默认原图档）；True/False 可当场覆盖
        （界面上"存这张"就用了这个口子，不受设置影响）。
        """
        with self.lock:
            if self.phase not in ('ready', 'saved'):
                return False
            want = sorted({as_int(x) for x in (idxs or [])})
            want = [x for x in want if 1 <= x <= len(self.items)]
            if not want:
                return False
            quality = bool(self.settings.get('original')
                           if original is None else original)
            self.save_progress = {'done': 0, 'total': len(want)}
            self.phase = 'saving'
            self.message = f'正在保存 0/{len(want)}…'
        self.log_line(f'开始保存 {len(want)} 张（{"原图" if quality else "压缩档"}）')
        threading.Thread(target=self._save_worker, args=(want, quality),
                         daemon=True).start()
        return True

    def _save_worker(self, idxs, original=True):
        sid, folder = self.sid, self.folder
        # 画质就是"取哪一档"：原图档走 'f'，压缩档走 't'（＝网格那张缩略图，
        # 通常已经在磁盘上了，等于零额外下载）。
        kind = 'f' if original else 't'
        out = os.path.join(self.session_dir(sid), OUT_DIR)
        clear_dir(out)
        os.makedirs(out, exist_ok=True)

        seen, kept, dup, fail = set(), 0, 0, 0
        try:
            for n, i in enumerate(idxs, 1):
                try:
                    path, ext = self._fetch_item(sid, kind, i)
                    with open(path, 'rb') as f:
                        data = f.read()
                except (Failed, NotFound, OSError) as e:
                    fail += 1
                    self.log_line(f'第 {i} 张跳过：{e}')
                else:
                    h = hashlib.md5(data).hexdigest()
                    if h in seen:
                        dup += 1
                        self.log_line(f'第 {i} 张与前面某张重复，跳过')
                    else:
                        seen.add(h)
                        kept += 1
                        ext = sniff_ext(data, ext)
                        write_atomic(os.path.join(out, f'img_{kept:02d}{ext}'), data)
                with self.lock:
                    self.save_progress['done'] = n
                    self.message = f'正在保存 {n}/{len(idxs)}…'
        except Exception as e:                                      # noqa: BLE001
            traceback.print_exc()
            clear_dir(out)
            self._fail(f'保存出错：{type(e).__name__}: {e}')
            return

        if not kept:
            return self._finish(out, 0, dup, fail, folder, original)

        try:
            published = int(self._publish(out, folder) or 0)
        except Exception as e:                                      # noqa: BLE001
            traceback.print_exc()
            clear_dir(out)
            return self._fail(f'写进相册失败：{type(e).__name__}: {e}')
        self._finish(out, published, dup, fail, folder, original)

    def _publish(self, out_dir: str, folder: str) -> int:
        """交给 Java 写系统相册。

        **只有这一处需要 Context**（MediaStore 是安卓特有的），所以 Java 在
        `start()` 里把 Application 上下文交过来，这里转手传给 Gallery。
        电脑上跑（本地开发/测试）没有 Java —— 这时必须由调用方注入 publish。
        """
        if self.publish is None:
            raise RuntimeError('没有接入相册（电脑上跑时应当注入一个假的）')
        return self.publish(out_dir, folder)

    def _finish(self, out: str, published: int, dup: int, fail: int, folder: str,
                original: bool = True):
        clear_dir(out)
        # 原图缓存不留在磁盘上 —— 图已经在相册里了，留着白占几十兆
        clear_dir(os.path.join(self.session_dir(self.sid), FULL_DIR))

        path = f'Pictures/{ALBUM_SUBDIR}/{folder}'
        if published:
            msg = f'已存入相册：{path}（{published} 张）'
            if not original:
                msg += '\n（按设置存的是压缩档，想存原图去首页打开开关）'
            if dup:
                msg += f'\n另有 {dup} 张与前面的重复，已跳过'
            if fail:
                msg += f'\n{fail} 张没下下来'
        elif dup and not fail:
            msg = f'选中的图都已经在相册里了（{dup} 张全部重复）'
        else:
            msg = '没有可保存的图片：都没下下来，换个网络再试'

        status = 'done' if (published and not fail) else ('partial' if published else 'failed')
        try:
            if self.entry_id:
                self.hist.update(self.entry_id, success_images=published,
                                 save_path=path if published else '',
                                 status=status, note=self.title)
            else:
                self.entry_id = self.hist.add(self.source, total_images=len(self.items),
                                              success_images=published,
                                              save_path=path if published else '',
                                              status=status, note=self.title)
        except Exception as e:                                      # noqa: BLE001
            self.log_line(f'历史写入失败：{e}')

        with self.lock:
            self.phase = 'saved'
            self.message = msg
        self.log_line(msg.replace('\n', ' '))

    # ── ④ 历史 ──────────────────────────────────────────────────────────────

    def history(self) -> list:
        out = []
        for e in self.hist.get_all(limit=200):
            sid = session_id(e.source_url)
            out.append({
                'id': e.id,
                'url': e.source_url,
                'title': e.note or title_from_path(e.save_path) or '（无标题）',
                'time': pretty_time(e.parsed_at),
                'total': e.total_images,
                'success': e.success_images,
                'status': e.status,
                'status_text': e.status_text(),
                'path': e.save_path,
                'has_cache': os.path.exists(self._session_json(sid)),
            })
        return out

    def open_history(self, entry_id) -> dict:
        """把某条历史恢复成"当前会话"，之后就能正常挑图 / 补存 / 重新保存。"""
        target = as_int(entry_id)
        row = next((e for e in self.hist.get_all(limit=200) if e.id == target), None)
        if row is None:
            return {'ok': False, 'reason': 'missing', 'message': '这条记录已经不在了'}

        sid = session_id(row.source_url)
        json_path = self._session_json(sid)
        if not os.path.exists(json_path):
            return {'ok': False, 'reason': 'nocache', 'source': row.source_url,
                    'message': '这次的图片缓存已经清掉了，可以重新解析一次'}
        try:
            with open(json_path, encoding='utf-8') as f:
                data = json.load(f)
            items = [Item.from_json(d) for d in (data.get('items') or [])]
        except Exception as e:                                      # noqa: BLE001
            return {'ok': False, 'reason': 'error', 'message': f'读缓存失败：{e}'}
        if not items:
            return {'ok': False, 'reason': 'nocache', 'source': row.source_url,
                    'message': '缓存不完整，重新解析一次吧'}

        folder = (data.get('folder')
                  or (row.save_path or '').replace('\\', '/').rstrip('/').split('/')[-1]
                  or title_from_path(row.save_path)
                  or '图片')
        with self.lock:
            if self.phase in ('parsing', 'saving'):
                return {'ok': False, 'reason': 'busy', 'message': '正在忙，稍等一下'}
            self._reset()
            self.source = data.get('source') or row.source_url
            self.is_url = bool(data.get('is_url', True))
            self.sid = sid
            self.title = data.get('title') or title_from_path(row.save_path) or '（无标题）'
            self.folder = folder
            self.items = items
            self.entry_id = row.id
            self.phase = 'ready'
            self.message = f'已载入上次保存的 {len(items)} 张图'
            if self.is_url:
                self._headers = imgsnag.build_headers(self.source)
        self.log_line(f'载入历史会话 {sid}（{len(items)} 张）')
        return {'ok': True, 'count': len(items), 'title': self.title}

    def delete_history(self, entry_id) -> bool:
        try:
            self.hist.delete(as_int(entry_id))
            return True
        except Exception:                                           # noqa: BLE001
            return False

    # ── ⑤ 偏好 ──────────────────────────────────────────────────────────────

    def set_settings(self, patch) -> dict:
        """改偏好并落盘，返回改完之后的完整快照。

        存不下来（磁盘满/权限）只记一行日志、不抛：偏好丢了顶多是下次
        回到默认值，为此把当前这次保存搞失败完全不划算。
        """
        clean = normalize_settings(patch)
        with self.lock:
            self.settings.update(clean)
            snap = dict(self.settings)
        try:
            write_atomic(self.settings_path,
                         json.dumps(snap, ensure_ascii=False).encode('utf-8'))
        except OSError as e:
            self.log_line(f'偏好没能存下来（下次会回到默认值）：{e}')
        if clean:
            self.log_line('偏好已更新：'
                          + '、'.join(f'{k}={v}' for k, v in clean.items()))
        # 刚把"跳过小图"打开、而这次会话还没体检过 —— 补一轮
        self._maybe_inspect()
        return snap

    # ── ⑥ 版本更新 ──────────────────────────────────────────────────────────

    def _load_update(self) -> dict:
        try:
            with open(os.path.join(self.root, UPDATE_FILE), encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}                                  # 没有 / 坏了都当"还没问过"

    def _remember_update(self, out: dict):
        try:
            write_atomic(os.path.join(self.root, UPDATE_FILE),
                         json.dumps(out, ensure_ascii=False).encode('utf-8'))
        except OSError:
            pass                              # 存不下只是下次多问一遍，不影响功能

    def _update_fetch(self, url, timeout=None):
        """给 `updater.check_for_updates` 用的取数函数。

        签名必须与它的注入契约 `fetch(url, timeout) -> dict` **完全一致** ——
        它按位置传两个参数。桌面版在这条上踩过（v1.8.0）：测试全用注入的假
        fetch，真联网那条路从没被走到，用户一点就报
        `takes from 0 to 1 positional arguments but 2 were given`。
        """
        resp = self._net()(
            url,
            headers={'Accept': 'application/vnd.github+json',
                     'User-Agent': f'ImgSnag-Android/{self.version or "dev"}'},
            timeout=timeout or updater.DEFAULT_TIMEOUT,
        )
        if not 200 <= resp.status < 300:
            raise RuntimeError(f'HTTP {resp.status}')
        return json.loads(resp.text or '{}') or {}

    def check_update(self, installed_at: int = 0, force: bool = False) -> dict:
        """问 GitHub：`apk-latest` 上那个安装包是不是比本机装的更新。

        **判据是版本号**（从资产名里抠出来，形如
        `ImgSnag_1.11.0_android_arm64.apk`）—— 滚动发布位的 tag 里没有版本号，
        但文件名里有，这是当初只能"比构建时间"之后补上的更准的一条路。

        构建时间降级为**兜底**，只在两种情况下用：
          · 两边有一边的版本号认不出来（改过命名规则、老包没有版本号）
          · 版本号相同 —— tag 与版本号都没变但重新构建过（迭代期很常见），
            这时"构建时间比本机安装时间新"就是唯一能发现更新的线索
        比较逻辑用 `updater.compare_versions`（三端**同一份**实现），
        不在这儿另写一套。

        `installed_at` 由 Java 侧给（`PackageManager` 的 lastUpdateTime）。
        拿不到时（浏览器里调试、桥不在）返回 `unknown=True`：界面只报远端信息，
        **不下"有没有新版"的结论** —— 猜一个结论比不给结论更糟。
        """
        now = time.time()
        # 明显不合理的安装时间一律当"问不到"（负数、时间戳被截断、时钟跑飞）。
        # 不设防的话 `installed_at` 为负数在布尔判断里是真，会算出"永远有新版本"。
        installed_at = as_int(installed_at, 0)
        if installed_at < MIN_INSTALL_MS:
            installed_at = 0
        with self.lock:
            cached = dict(self.upd)
        if not force and cached and (now - cached.get('_at', 0)) < updater.CHECK_INTERVAL:
            cached['cached'] = True
            return cached

        info = updater.check_for_updates(fetch=self._update_fetch, api_url=APK_API_URL,
                                         current=self.version)
        apk = self._pick_apk(info.assets)
        remote_ms = iso_ms(apk.created_at) if apk else 0
        remote_ver = (apk.version if apk else '') or ''
        local_ver = self.version or ''

        # 能不能用版本号下结论：两边都得是规范三段式（`1.11.0`）。
        # 认不出来就退回老办法（比构建时间），而不是硬算出一个错的结论。
        by_version = bool(remote_ver and local_ver
                          and updater.is_valid_version(remote_ver)
                          and updater.is_valid_version(local_ver))
        cmp_version = (updater.compare_versions(local_ver, remote_ver)
                       if by_version else 0)
        by_time = bool(remote_ms and installed_at
                       and remote_ms > installed_at + UPDATE_SLACK_MS)
        if by_version and cmp_version != 0:
            has_update = cmp_version < 0                 # 版本号说了算
        else:
            # 版本号认不出来，或两边版本号相同（同版本号重新构建过）→ 看时间
            has_update = by_time

        out = {
            'ok': bool(info.ok and apk),
            'cached': False,
            'error': info.error or ('' if apk else 'GitHub 上还没有安卓安装包'),
            'current': self.version,
            'remote_version': remote_ver,
            'remote_time': pretty_time(apk.created_at) if apk else '',
            'remote_ms': remote_ms,
            'installed_ms': installed_at,
            'has_update': has_update,
            #: 只有远端信息拿得到、本机信息拿不到时才是 unknown（见 docstring）
            'unknown': bool(apk and not installed_at and not by_version),
            'page_url': info.page_url or APK_PAGE_URL,
            #: 应用内更新要用的三样：地址、字节数、sha256（GitHub 现在直接给摘要）
            'apk_name': apk.name if apk else '',
            'apk_url': apk.url if apk else '',
            'apk_size': int(apk.size or 0) if apk else 0,
            'apk_sha256': (apk.digest if apk else '') or '',
            '_at': now,
        }
        with self.lock:
            self.upd = out
        if out['ok']:
            self.log_line(f'更新检查：远端 {out["remote_version"] or "?"}'
                          f'（{out["remote_time"]}），'
                          + ('有新版本' if out['has_update'] else '已是最新'))
        else:
            self.log_line(f'更新检查失败：{out["error"]}')
        self._remember_update(out)
        return out

    @staticmethod
    def _pick_apk(assets) -> object:
        """从资产里挑出手机该装的那个 APK。

        优先名字里带 arm64 的（本 App 只带 arm64-v8a）—— 将来若同时发布多个 ABI，
        挑错了的表现是"下载成功但装不上，报与 CPU 不兼容"。
        """
        apks = [a for a in (assets or []) if str(a.name).lower().endswith('.apk')]
        for a in apks:
            if 'arm64' in str(a.name).lower():
                return a
        return apks[0] if apks else None

    # ── ② 应用内更新：下载 ──────────────────────────────────────────────────

    def download_state(self) -> dict:
        """当前下载状态（网页轮询用；也挂在 state() 的 `dl` 上）。"""
        with self.lock:
            return dict(self.dl)

    def start_download(self, installed_at: int = 0, force: bool = False) -> dict:
        """开始下载安装包（后台线程）。返回此刻的下载状态。

        下载地址等三样（地址/字节数/sha256）取自最近一次检查结果；
        没有（还没查过、或缓存被清）就先同步查一次 —— 用户点「更新」时
        不该再让他自己去点一次「检查更新」。
        """
        with self.lock:
            if self.dl.get('phase') == 'downloading':
                return dict(self.dl)
            info = dict(self.upd or {})

        if force or not info.get('apk_url'):
            info = self.check_update(installed_at=installed_at, force=True)

        url = info.get('apk_url') or ''
        if not url:
            with self.lock:
                self.dl = _dl_idle()
                self.dl.update({'phase': 'error',
                                'error': info.get('error') or '拿不到安装包的下载地址'})
                return dict(self.dl)

        with self.lock:
            self._dl_cancel = False
            self.dl = _dl_idle()
            self.dl.update({
                'phase': 'downloading',
                'total': as_int(info.get('apk_size'), 0),
                'name': os.path.basename(str(info.get('apk_name') or 'ImgSnag.apk')),
                'version': str(info.get('remote_version') or ''),
            })
        args = (url, as_int(info.get('apk_size'), 0), str(info.get('apk_sha256') or ''),
                self.dl['name'], self.dl['version'])
        threading.Thread(target=self._download_worker, args=args,
                         name='update-download', daemon=True).start()
        self.log_line(f'开始下载更新包 {self.dl["name"]}')
        return self.download_state()

    def cancel_download(self) -> dict:
        """取消下载：置标志位，由下载线程自己收尾（半截文件一并删掉）。

        不在这里直接杀线程 —— 那样半截文件会留在盘上，下次进来还得判它是不是完整的。
        """
        with self.lock:
            if self.dl.get('phase') == 'downloading':
                self._dl_cancel = True
        return self.download_state()

    def _download_worker(self, url: str, size: int, sha256: str,
                         name: str, version: str):
        """把安装包下到 `<update_dir>/<资产名>`：边下边算 sha256，下完校验再落位。

        校验这一环不能省：企业网络出口会把长响应截断（桌面版 ChemCal 遇到过
        在 50 MiB 处被截断），截断的 APK 装上会直接报"解析包时出现问题" ——
        那时候用户完全想不到是网络的事。
        """
        dest = os.path.join(self.update_dir, name or 'ImgSnag.apk')
        tmp = dest + '.part'
        try:
            os.makedirs(self.update_dir, exist_ok=True)
            opener = self.apk_opener or imgsnag_android.build_opener(cache_dir=self.root)
            req = urllib.request.Request(url, headers={
                'User-Agent': f'{APK_USER_AGENT}/{version or "dev"}',
                'Accept': 'application/octet-stream',
            })
            digest = hashlib.sha256()
            got = 0
            with opener.open(req, timeout=APK_TIMEOUT) as resp, open(tmp, 'wb') as f:
                if not size:
                    size = as_int(resp.headers.get('Content-Length'), 0)
                self._dl_set(total=size)
                while True:
                    if self._dl_cancel:
                        raise _Cancelled()
                    chunk = resp.read(APK_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    self._dl_set(done=got)

            if self._dl_cancel:
                raise _Cancelled()
            if size and got != size:
                raise RuntimeError(f'下载不完整（只拿到 {imgsnag.human_size(got)}，'
                                   f'应有 {imgsnag.human_size(size)}），请重试')
            want = (sha256 or '').strip().lower()
            if want.startswith('sha256:'):
                want = want.split(':', 1)[1]
            if want and digest.hexdigest() != want:
                raise RuntimeError('下载的文件校验不通过（传输途中被改过），已丢弃，请重试')

            os.replace(tmp, dest)                       # 原子落位：半截文件不会冒充成品
            self._prune_old_apks(dest)
        except _Cancelled:
            _silent_remove(tmp)
            with self.lock:
                self.dl = _dl_idle()
            self.log_line('更新包下载已取消')
            return
        except Exception as e:                          # noqa: BLE001
            _silent_remove(tmp)
            text = _net_error_text(e)
            with self.lock:
                self.dl = _dl_idle()
                self.dl.update({'phase': 'error', 'error': text})
            self.log_line(f'❌ 更新包下载失败：{text}')
            return

        with self.lock:
            self.dl = _dl_idle()
            self.dl.update({'phase': 'done', 'done': got, 'total': got,
                            'path': dest, 'name': name, 'version': version})
        self.log_line(f'更新包已下载：{name}（{imgsnag.human_size(got)}），可以安装了')

    def _prune_old_apks(self, keep: str):
        """下完新包之后，把上一次那个安装包删掉 —— 只留当下这一个。

        为什么非删不可：资产名里带版本号（`ImgSnag_1.11.0_android_arm64.apk`），
        所以新包**不会**覆盖上一版，而是并排躺着。每更新一次就在应用私有目录里
        多留一个 ~20MB 的包，**而且完全无声** —— 用户只会隐约觉得"App 怎么越来越大"。
        顺带把残留的 `.part` 也扫掉（取消、或者进程被杀在写盘中途会留下）。

        `keep` 一定要排掉：那是刚下好的成品，正要交给安装器。
        """
        try:
            names = os.listdir(self.update_dir)
        except OSError:
            return
        for n in names:
            p = os.path.join(self.update_dir, n)
            if p == keep:
                continue
            if n.endswith('.apk') or n.endswith('.part'):
                _silent_remove(p)

    def _dl_set(self, **kw):
        """写下载进度（只改这几个字段，别整体替换 —— 会把 path/name 丢掉）。"""
        with self.lock:
            self.dl.update(kw)


# ============================================================================
#  HTTP 服务
# ============================================================================

class _Server(ThreadingMixIn, HTTPServer):
    """每连接一个线程的最简 HTTP 服务。

    `serve_forever` 自己写：标准实现要过 selectors（select/epoll），
    而这里只需要一个阻塞 accept 循环 —— 少一层依赖，就少一种"在手机上
    表现不一样"的可能。`shutdown()` 靠关掉监听套接字把 accept 叫醒。
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler):
        self._stopped = threading.Event()
        super().__init__(addr, handler)

    def serve_forever(self, poll_interval=0.5):
        while not self._stopped.is_set():
            try:
                conn, addr = self.socket.accept()
            except OSError:
                if self._stopped.is_set():
                    break
                time.sleep(0.05)
                continue
            try:
                self.process_request(conn, addr)
            except Exception:                                       # noqa: BLE001
                try:
                    conn.close()
                except OSError:
                    pass

    def shutdown(self):
        self._stopped.set()
        try:
            self.socket.close()
        except OSError:
            pass


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'ImgSnag'
        timeout = 60                       # keep-alive 空闲上限，防止线程被吊死

        # ── 日志：只记出错的请求，不然日志会被每张缩略图刷屏 ──
        def log_request(self, code='-', size='-'):                  # noqa: A003
            try:
                if int(code) >= 400:
                    engine.log_line(f'http {code} {self.path}')
            except (TypeError, ValueError):
                pass

        def log_message(self, fmt, *args):                          # noqa: A003
            pass

        # ── 输出 ──
        def _send(self, code, body: bytes, ctype: str, cache='no-store'):
            try:
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', cache)
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass                                            # 用户划走了，正常

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

        def _body(self) -> dict:
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode('utf-8')) or {}
            except Exception:                                       # noqa: BLE001
                return {}

        # ── 路由 ──
        def do_GET(self):                                           # noqa: N802
            path = unquote(urlparse(self.path).path)

            if path in ('/', '/index.html'):
                return self._send(200, webui.PAGE.encode('utf-8'),
                                  'text/html; charset=utf-8')

            if path == '/api/state':
                return self._json(engine.state())

            if path == '/api/poll':
                # 轻量轮询：把「分享过来的文本」捎带回去，顺带最新状态。
                # ⚠ **不能在这里清掉** share —— 页面若正忙（正在解析/保存），
                # 这一轮取走了却没法处理，分享的内容就丢了。清空交给
                # `Engine.parse()`：真正开始干活的那一刻才算消耗掉。
                st = engine.state()
                with engine.lock:
                    st['share'] = engine.pending_share
                return self._json(st)

            if path == '/api/history':
                return self._json({'items': engine.history()})

            if path == '/api/update':
                # 本机安装时间由网页带上来（它从 Java 的桥上取，见 webui.py）。
                # 这是**同步**请求，最坏要等一个网络超时 —— 服务是每连接一个
                # 线程的，所以它不会把轮询和缩略图一起卡住。
                q = parse_qs(urlparse(self.path).query)
                return self._json(engine.check_update(
                    installed_at=as_int((q.get('at') or ['0'])[0], 0),
                    force=(q.get('force') or [''])[0] in ('1', 'true', 'yes'),
                ))

            if path.startswith('/img/'):
                parts = path.strip('/').split('/')          # img/<sid>/<kind>/<i>
                if len(parts) == 4 and parts[2] in ('t', 'f'):
                    try:
                        i = int(parts[3])
                    except ValueError:
                        return self._json({'error': 'bad index'}, 404)
                    try:
                        p, mime = engine.image(parts[1], parts[2], i)
                    except NotFound as e:
                        return self._json({'error': str(e)}, 404)
                    except Failed as e:
                        return self._json({'error': str(e)}, 502)
                    try:
                        with open(p, 'rb') as f:
                            data = f.read()
                    except OSError as e:
                        return self._json({'error': str(e)}, 500)
                    cache = ('public, max-age=86400' if parts[2] == 'f'
                             else 'public, max-age=600')
                    return self._send(200, data, mime, cache)
            return self._json({'error': 'not found'}, 404)

        def do_POST(self):                                          # noqa: N802
            path = unquote(urlparse(self.path).path)
            data = self._body()

            if path == '/api/parse':
                ok = engine.parse(str(data.get('source') or ''),
                                  str(data.get('html') or ''))
                return self._json({'ok': ok, 'state': engine.state()})

            if path == '/api/save':
                ok = engine.save(data.get('idxs') or [],
                                 data.get('original'))
                return self._json({'ok': ok, 'state': engine.state()})

            if path == '/api/settings':
                patch = data.get('settings') if 'settings' in data else data
                return self._json({'ok': True, 'settings': engine.set_settings(patch)})

            if path == '/api/history/open':
                return self._json(engine.open_history(data.get('id') or 0))

            if path == '/api/history/delete':
                return self._json({'ok': engine.delete_history(data.get('id') or 0)})

            if path == '/api/update/download':
                # 开始下载安装包。`installed_at` 还是要带上：万一还没检查过，
                # 这一下会顺带把检查做掉（用户点「更新」不该再被要求先点「检查」）。
                return self._json(engine.start_download(
                    installed_at=as_int(data.get('at'), 0),
                    force=bool(data.get('force')),
                ))

            if path == '/api/update/cancel':
                return self._json(engine.cancel_download())

            return self._json({'error': 'not found'}, 404)

    return Handler


# ============================================================================
#  入口（Java 调用 / 电脑上手动跑）
# ============================================================================

_ENGINE = None
_SERVER = None
_THREAD = None


def push_share(text: str) -> bool:
    """Java 把「分享过来的文本」投递进来，页面下一轮轮询就取走。

    这里**不直接解析**：页面可能还没加载完、或用户正在看上一篇文章，
    由界面决定什么时候开始 —— 服务端只负责把话带到。
    """
    if _ENGINE is None:
        return False
    with _ENGINE.lock:
        _ENGINE.pending_share = text or ''
    _ENGINE.log_line('收到分享内容')
    return True


def engine() -> Engine:
    return _ENGINE


def start(ctx=None, version: str = '', root: str = None, get=None,
          publish=None, host: str = '127.0.0.1', port: int = 0,
          files_dir: str = None, apk_opener=None) -> int:
    """起本地服务，返回端口号（Java 用它拼出要加载的网址）。

    只绑 `127.0.0.1`：同一 Wi-Fi 下的别的设备也连不上；而且清单里
    只需要为这一条回环地址放行明文 HTTP。

    `ctx` 是安卓的 Application 上下文（电脑上传 None）。
    `files_dir` 是安卓的应用私有目录（Java 传，见 MainActivity 里的 Kwarg）：
    应用内更新的安装包必须下在那底下 —— FileProvider 只开放了那一个子目录。
    `apk_opener` 是给测试留的注入口（假安装包下载），不注入就走真实网络。
    """
    global _ENGINE, _SERVER, _THREAD
    if _SERVER is not None:
        return _SERVER.server_address[1]

    _ENGINE = Engine(root=root, get=get, publish=publish or _java_publish(ctx),
                     version=version, files_dir=files_dir, apk_opener=apk_opener)
    _ENGINE.log_line(f'{APP_LABEL} {version}'.strip())
    _SERVER = _Server((host, port), make_handler(_ENGINE))
    _THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    _THREAD.start()
    _ENGINE.log_line(f'服务已就绪：http://{host}:{_SERVER.server_address[1]}/')
    return _SERVER.server_address[1]


def _java_publish(ctx):
    """把"写进相册"接到 Java 的 `Gallery.publishDir`；没有 ctx 就返回 None。"""
    if ctx is None:
        return None

    def _pub(out_dir: str, album_sub: str) -> int:
        from java import jclass                                    # noqa: PLC0415
        Gallery = jclass(GALLERY_CLASS)
        return int(Gallery.publishDir(ctx, out_dir, album_sub))

    return _pub


def stop():
    """关服务（测试与退出时用）。"""
    global _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER = None


if __name__ == '__main__':
    # 电脑上手动跑：起服务，然后浏览器打开打印出来的地址
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    work = os.path.join(os.path.expanduser('~'), '.imgsnag_web_try')

    def _fake_album(out_dir, sub):
        dest = os.path.join(work, 'album', sub)
        os.makedirs(dest, exist_ok=True)
        n = 0
        for name in sorted(os.listdir(out_dir)):
            shutil.copyfile(os.path.join(out_dir, name), os.path.join(dest, name))
            n += 1
        print(f'（假相册）拷了 {n} 张到 {dest}')
        return n

    p = start(root=work, version='dev', publish=_fake_album)
    print(f'打开： http://127.0.0.1:{p}/')
    print(f'数据目录：{work}   Ctrl+C 结束')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n结束')
