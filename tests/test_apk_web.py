# -*- coding: utf-8 -*-
"""安卓版「网页界面后端」回归测试 —— **真起一个本地 HTTP 服务**跑完整条链路

运行：
    .venv/Scripts/python.exe tests/test_apk_web.py

────────────────────────────────────────────────────────────────────────
这个文件为什么值得单独存在
────────────────────────────────────────────────────────────────────────
安卓版界面从"原生控件"改成"内嵌网页"以后，多出一条新的接缝：

    WebView  ──HTTP──▶  imgsnag_web.py（本机回环服务）──▶ 抓取逻辑
                              │
                              └──▶ Java Gallery（写相册）

原生控件那条路我**一个字都验不了**（没有模拟器）；而这条路上，除了
"WebView 能不能加载 127.0.0.1"和"Python 能不能调到那个 Java 方法"，
**其余部分在 Windows 上都能真跑**。所以：

  · 真起服务、真发 HTTP 请求（不是调用函数了事）
  · 假网络 —— 但**区分 /0 原图档与 /640 压缩档**，从而验证
    "缩略图用压缩档、保存时才取原图"这条省流量/省内存的核心设计
  · 假相册 —— 验证"选中哪些就存哪些、重复图去重、存完清缓存"
  · 真历史库 —— 验证解析落一条、保存后同一条被更新（不是新增一条）

剩下那两件验不了的，用静态契约钉住（Java 侧的类名/方法名/参数、页面
与接口的字段对应），出了错也能在推送前发现。

⚠ 不导入 PySide6，CI 上能跑。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK = os.path.join(ROOT, 'android-apk')
SRC_MAIN = os.path.join(APK, 'app', 'src', 'main')
PY_DIR = os.path.join(SRC_MAIN, 'python')
JAVA_DIR = os.path.join(SRC_MAIN, 'java', 'com', 'virmuran', 'imgsnag')
WEB_PY = os.path.join(PY_DIR, 'imgsnag_web.py')

sys.path.insert(0, PY_DIR)
sys.path.insert(0, os.path.join(ROOT, 'android'))       # sync_core

import imgsnag                                            # noqa: E402
import imgsnag_android as android                        # noqa: E402
import imgsnag_web as web                                # noqa: E402
import webui                                             # noqa: E402
import sync_core                                         # noqa: E402

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  ✗ {label}')


def eq(actual, expected, label):
    check(actual == expected, f'{label}（实际 {actual!r}，期望 {expected!r}）')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def strip_comments(text):
    """摘掉 Java 的 `//` 与 `/* */` 注释，只留真正的代码。

    为什么非摘不可：源码里写着"`canGoBack()` 为假就退出 App"这种说明注释，
    于是 `'canGoBack()' in src` **光靠注释就凑齐了** —— 把真正的代码删掉，
    断言照样绿。同理"桥方法必须标 `@JavascriptInterface`"那句注释，
    会让"漏注解"这件事查不出来。

    一句话规矩：**凡是"在源码里找某个字符串"的检查，都要先摘掉注释再找。**
    （反向验证抓出来的，不是想出来的 —— 抓出两条。）
    """
    text = re.sub(r'/\*[\s\S]*?\*/', '', text)
    return re.sub(r'//[^\n]*', '', text)


def strip_xml_comments(text):
    """摘掉 XML 的 `<!-- -->`。**XML 一律用这个，不要用 `strip_comments`。**

    两件事别混：
      · XML 里 `//` 与 `/* */` **不是**注释 —— 而清单/配置里 `http://` 就带 `//`，
        拿 Java 那套去摘会把真代码剪坏（剪坏了反而"看不出来"，因为断言只是字符串查找）。
      · 反过来说，XML 的 `<!-- -->` 在 Java 那套规则下**一点都不会被摘掉**。

    为什么非摘不可（反向验证抓出来的第二条，跟 Java 那条同一个病）：
      `AndroidManifest.xml` 顶上有一大块说明注释，把 `REQUEST_INSTALL_PACKAGES`、
      `enableOnBackInvokedCallback`、`networkSecurityConfig`、`FileProvider`…
      几乎全提到的词挨个复述了一遍。于是"把真正的 `<uses-permission>` 删掉"
      这条拆坏**测试照绿** —— 断言被注释喂饱了。
      资源 XML 同理：`file_paths.xml` 注释里有 `update/`，
      `network_security_config.xml` 注释里连 `cleartextTrafficPermitted="true"`
      和 `127.0.0.1` 都有（那段注释就是在讲"退路是改成 base-config"）。

    一句话：**凡是"在源码/配置里找某个字符串"的检查，都要先摘掉注释再找 ——
    摘哪种注释，取决于文件类型。**
    """
    return re.sub(r'<!--[\s\S]*?-->', '', text)


# ============================================================================
#  假环境
# ============================================================================

ARTICLE_URL = 'https://mp.weixin.qq.com/s/abcdefg'
PIC_A = 'https://mmbiz.qpic.cn/mmbiz_jpg/AAAAAAAABBBBCCCC/640?wx_fmt=jpeg'
PIC_B = 'https://mmbiz.qpic.cn/mmbiz_png/DDDDDDDDEEEEFFFF/640?wx_fmt=png'
#: 与 PIC_A **文件号不同、但下载回来字节相同** —— 专门用来验"保存时按内容去重"。
#: 用 PIC_A 贴两遍验不了这件事：解析阶段就按图片身份合并成一张了（桌面版也是）。
PIC_C = 'https://mmbiz.qpic.cn/mmbiz_jpg/CCCCCCCCDDDDDDDD/640?wx_fmt=jpeg'

#: 两个档位给**不同的字节**，这样才能证明"缩略图取哪一档、保存取哪一档"
FULL_A = b'\xff\xd8\xff\xe0' + b'A' * 900         # /0 档（原图）
FULL_B = b'\x89PNG\r\n\x1a\n' + b'B' * 900
THUMB_A = b'\xff\xd8\xff\xe0' + b'a' * 600        # /640 档（压缩）
THUMB_B = b'\x89PNG\r\n\x1a\n' + b'b' * 600

#: 小图体检要用的一对：一张明显大于阈值（默认 12 KB）、一张明显小于。
#: 阈值是**按体积**判的，所以这两张的字节数要卡在阈值两侧才有意义。
#:
#: ⚠ 小图那张不能小于 `imgsnag.MIN_IMAGE_BYTES`（500 字节）：比它还小的响应
#: 在下载那一步就被当成失败丢掉了（CDN 出错时返回的占位图就那么大），
#: 根本轮不到体检 —— 拿它当"小图"用例，会得到"体积 0 = 没量到"，
#: 而 0 不算小图，测试红得莫名其妙（这条就是踩过之后写下的）。
BIG_A = b'\xff\xd8\xff\xe0' + b'A' * 20000        # 约 20 KB：正常配图
TINY_T = b'\x89PNG\r\n\x1a\n' + b't' * 800        # 约 0.8 KB：表情/二维码那一类
PIC_T = 'https://mmbiz.qpic.cn/mmbiz_png/TINYTINYTINY/640?wx_fmt=png'

TITLE = '秋天的第一杯奶茶'


def article(pics=(PIC_A, PIC_B)) -> str:
    body = ''.join(f'<img data-src="{u}">' for u in pics)
    return ('<html><head><meta property="og:title" content="' + TITLE + '"></head>'
            f'<body><script>var msg_title = "{TITLE}";</script>{body}</body></html>')


class FakeGet:
    """假网络：区分原图档 / 压缩档，并记下每个请求。"""

    def __init__(self, pages=None, full=None, thumb=None, sizes=None):
        self.pages = dict(pages or {})
        self.full = full or FULL_A
        self.thumb = thumb or THUMB_A
        #: 指定某些图用多大的字节：{地址里的关键字: (原图档字节, 压缩档字节)}。
        #: 小图体检是**按体积**判的，不给几张大小悬殊的图就验不了。
        self.sizes = dict(sizes or {})
        self.calls = []

    def _sized(self, url):
        for key, pair in self.sizes.items():
            if key in url:
                return pair[0] if url.endswith('/0') else pair[1]
        return None

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url in self.pages:
            return android._Response(200, self.pages[url].encode('utf-8'))
        if 'mmbiz.qpic.cn' in url:
            sized = self._sized(url)
            if sized is not None:
                return android._Response(200, sized)
            png = 'png' in url
            if url.endswith('/0'):
                return android._Response(200, FULL_B if png else self.full)
            return android._Response(200, THUMB_B if png else self.thumb)
        return android._Response(404, b'not found')

    def hits(self, needle):
        return sum(1 for u in self.calls if needle in u)

    def reset(self):
        self.calls = []


class FakeAlbum:
    """假相册：把收到的文件拷到临时目录并记下调用。"""

    def __init__(self, base):
        self.base = base
        self.calls = []

    def __call__(self, out_dir, folder):
        dest = os.path.join(self.base, folder)
        os.makedirs(dest, exist_ok=True)
        n = 0
        for name in sorted(os.listdir(out_dir)):
            shutil.copyfile(os.path.join(out_dir, name), os.path.join(dest, name))
            n += 1
        self.calls.append((folder, n))
        return n


# ============================================================================
#  HTTP 小客户端
# ============================================================================

class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.status, r.headers, r.read()

    def json(self, path):
        return json.loads(self.get(path)[2].decode('utf-8'))

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, method='POST',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))

    def wait(self, want=('ready', 'saved', 'error'), timeout=25):
        """等后台线程把状态推到目标阶段（解析与保存都是异步的）。"""
        end = time.time() + timeout
        st = {}
        while time.time() < end:
            st = self.json('/api/state')
            if st.get('phase') in want:
                return st
            time.sleep(0.08)
        return st


def _boot(tmp, **kw):
    fake = kw.pop('fake', None) or FakeGet(pages={ARTICLE_URL: article()})
    album = kw.pop('album', None) or FakeAlbum(os.path.join(tmp, 'album'))
    seed = kw.pop('settings', None)
    #: 本机版本号与下载注入点都要能换：更新检查现在**比版本号**，
    #: 写死一个版本号就没法验"本机比远端新/旧/一样"这三种情形。
    version = kw.pop('version', '9.9.9')
    opener = kw.pop('apk_opener', None)
    if seed is not None:
        # 偏好是 Engine 构造时从磁盘读的 —— 所以要在 start() 之前落盘才算数
        with open(os.path.join(tmp, web.SETTINGS_FILE), 'w', encoding='utf-8') as f:
            json.dump(seed, f, ensure_ascii=False)
    port = web.start(root=tmp, version=version, get=fake, publish=album,
                     apk_opener=opener, **kw)
    return Client(port), fake, album


# ============================================================================
#  一、小工具（纯函数）
# ============================================================================

def test_helpers():
    print('[1] 格式嗅探与杂项')
    eq(web.sniff_ext(b'\x89PNG\r\n\x1a\n' + b'x'), '.png', 'PNG 头')
    eq(web.sniff_ext(b'\xff\xd8\xff\xe0x'), '.jpg', 'JPEG 头')
    eq(web.sniff_ext(b'GIF89axyz'), '.gif', 'GIF 头')
    eq(web.sniff_ext(b'RIFF\x00\x00\x00\x00WEBPVP8 '), '.webp', 'WEBP 头')
    eq(web.sniff_ext(b'xxxx', '.jpg'), '.jpg', '认不出时用调用方给的兜底值')
    eq(web.sniff_ext(b'', '.png'), '.png', '空数据时退回调用方给的兜底值（不能崩）')
    eq(web.mime_of('.png'), 'image/png', 'MIME 映射')
    eq(web.mime_of('.unknown'), 'application/octet-stream', '未知扩展名给通用类型')

    eq(web.as_int('12'), 12, '数字字符串')
    eq(web.as_int('abc', -1), -1, '乱七八糟的输入给默认值')
    eq(web.as_int(None), 0, 'None 不炸')

    eq(web.session_id('https://a'), web.session_id('https://a'), '同一地址会话号稳定')
    check(web.session_id('https://a') != web.session_id('https://b'), '不同地址不同号')
    eq(len(web.session_id('x')), 12, '会话号是 12 位')

    eq(web.title_from_path('Pictures/ImgSnagWeChat/2026-10-06_1133_标题在这'),
       '标题在这', '从相册路径里取回标题')
    eq(web.title_from_path(''), '', '空路径不炸')
    check('今天' in web.pretty_time(datetime.now().isoformat()),
          '当天时间显示成"今天 HH:MM"')
    eq(web.pretty_time('2000-01-02T03:04:05'), '2000-01-02', '很久以前显示日期')

    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, 'x.bin')
        web.write_atomic(p, b'hello')
        eq(open(p, 'rb').read(), b'hello', '原子写入内容正确')
        check(not os.path.exists(p + '.part'), '不留 .part 临时文件')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
#  二、整条链路（真 HTTP 服务 + 假网络 + 假相册）
# ============================================================================

def test_full_chain():
    print('[2] 解析 → 按需缩略图 → 挑图保存（真服务）')
    tmp = tempfile.mkdtemp()
    try:
        # 这条链路只关心"抓 → 挑 → 存"，所以先把「跳过小图」关掉：
        # 开着的话解析完会紧接着体检（那会把缩略图都下一遍，见 [9]）。
        # 这也顺带证明了关掉开关真的零额外下载。
        cli, fake, album = _boot(tmp, settings={'skip_tiny': False})

        st = cli.json('/api/state')
        eq(st['phase'], 'idle', '刚起来是空闲态')

        r = cli.post('/api/parse', {'source': TITLE + '\n' + ARTICLE_URL})
        check(r['ok'] is True, '解析请求被受理（内容是分享文本，不是干净链接）')
        st = cli.wait()
        eq(st['phase'], 'ready', '解析完成')
        eq(st['title'], TITLE, '标题取到了')
        eq(st['count'], 2, '找到 2 张图')
        eq(st['source'], ARTICLE_URL, '存的是从分享文本里抠出来的链接')
        eq(st['sid'], hashlib.md5(ARTICLE_URL.encode()).hexdigest()[:12], '会话号由地址推出')
        check(re.match(r'^\d{4}-\d{2}-\d{2}_\d{6}_', st['folder']),
              f"文件夹名形如日期_时分秒_标题：{st['folder']}")
        check(TITLE in st['folder'], '文件夹名里带标题')
        eq(st['version'], '9.9.9', '版本号透传给页面')
        eq(st['album'], web.ALBUM_SUBDIR, '页面知道相册子目录名')

        eq(fake.hits('mp.weixin.qq.com'), 1, '文章只抓一次')
        eq(fake.hits('mmbiz.qpic.cn'), 0,
           '**关掉小图过滤后，解析阶段一张图都不下**（缩略图纯按需加载）')
        check(any('开始解析' in ln for ln in st['log']), '日志里有起点，出问题能截图')

        # ── 缩略图：应当取 /640 压缩档 ──
        fake.reset()
        code, hdr, body = cli.get(f"/img/{st['sid']}/t/1")
        eq(code, 200, '缩略图 200')
        eq(hdr['Content-Type'], 'image/jpeg', '缩略图 Content-Type 按真实内容给')
        eq(body, THUMB_A, '缩略图拿到的确实是压缩档的字节')
        eq(fake.hits('/0'), 0, '取缩略图时**没有**去碰原图档（省流量）')
        eq(fake.hits('/640'), 1, '取缩略图用的就是压缩档')

        cli.get(f"/img/{st['sid']}/t/1")
        eq(len(fake.calls), 1, '同一张缩略图只下一次（第二次走磁盘缓存）')

        # ── 原图：点开看大图时才取，且优先 /0 ──
        fake.reset()
        code, hdr, body = cli.get(f"/img/{st['sid']}/f/1")
        eq(code, 200, '原图 200')
        eq(body, FULL_A, '原图拿到的是 /0 档的字节')
        eq(fake.hits('/0'), 1, '原图优先取 /0 档')
        eq(fake.hits('/640'), 0, '/0 成功时不退压缩档')

        # ── 保存到相册 ──
        r = cli.post('/api/save', {'idxs': [1, 2]})
        check(r['ok'] is True, '保存请求被受理')
        st = cli.wait(want=('saved', 'error'))
        eq(st['phase'], 'saved', '保存完成')
        check('已存入相册' in st['message'], f"结果里说清了存到哪：{st['message']!r}")
        check(f'Pictures/{web.ALBUM_SUBDIR}/' in st['message'], '结果里给出相册路径')
        check('（2 张）' in st['message'], '结果里给出张数')

        eq(len(album.calls), 1, '相册只被写了一次')
        folder, n = album.calls[0]
        eq(n, 2, '写进相册 2 张')
        eq(folder, st['folder'], '相册目录名 = 会话文件夹名')
        eq(sorted(os.listdir(os.path.join(album.base, folder))),
           ['img_01.jpg', 'img_02.png'], '扩展名按真实格式给、编号连续')

        # 中转/原图缓存要清干净（图已经在相册里了），缩略图要留（历史重开靠它）
        sdir = os.path.join(tmp, web.SESSION_DIR, st['sid'])
        out = os.path.join(sdir, web.OUT_DIR)
        check(not os.path.isdir(out) or not os.listdir(out),
              '保存完不留中转目录（避免磁盘上两份图）')
        full = os.path.join(sdir, web.FULL_DIR)
        check(not os.path.isdir(full) or not os.listdir(full),
              '原图缓存也清掉（几十兆，没必要留）')
        check(os.path.isdir(os.path.join(sdir, web.THUMB_DIR)),
              '缩略图留着 —— 历史里"再打开一次"要靠它')
        check(os.path.isfile(os.path.join(sdir, 'session.json')),
              '会话文件在（有它才能重开）')

        # ── 历史 ──
        h = cli.json('/api/history')
        eq(len(h['items']), 1, '解析+保存只产生一条记录')
        row = h['items'][0]
        eq(row['status'], 'done', '状态是已下载')
        eq(row['success'], 2, '记录里写了成功张数')
        eq(row['total'], 2, '记录里写了总张数')
        eq(row['title'], TITLE, '标题存进了备注')
        eq(row['has_cache'], True, '标出"这次还能重开"')
        check(row['time'] and '今天' in row['time'], f"时间是给人看的：{row['time']}")

        # ── 重新打开历史会话 ──
        r = cli.post('/api/history/open', {'id': row['id']})
        check(r['ok'] is True, '能重开历史会话')
        eq(r['count'], 2, '重开后张数一致')
        st = cli.json('/api/state')
        eq(st['phase'], 'ready', '重开后可以直接再保存（补存没选的）')
        eq(st['title'], TITLE, '重开后标题也对')
        eq(st['count'], 2, '重开后仍然知道有哪几张')

        # 重开后还能按原图档取图（session.json 里存下了地址）
        fake.reset()
        code, _, body = cli.get(f"/img/{st['sid']}/f/2")
        eq(code, 200, '重开后仍然能取到图（说明地址存下来了）')
        eq(body, FULL_B, '重开后取的还是原图档的字节')

        # 再存一次不能出现第二条记录（同 URL 只保留一行）
        cli.post('/api/save', {'idxs': [1]})
        cli.wait(want=('saved', 'error'))
        eq(len(cli.json('/api/history')['items']), 1, '同一篇文章再存一次不会新增记录')

        # ── 页面本身 ──
        code, hdr, body = cli.get('/')
        eq(code, 200, '首页 200')
        check('text/html' in hdr['Content-Type'], '首页是 HTML')
        check(b'<!DOCTYPE html>' in body, '返回的确实是完整页面')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_share_push():
    print('[3] 分享投递（Java → 页面）')
    tmp = tempfile.mkdtemp()
    try:
        cli, fake, album = _boot(tmp)
        eq(cli.json('/api/poll')['share'], '', '没分享时是空串')

        eq(web.push_share(TITLE + '\n' + ARTICLE_URL), True, '投递成功')
        eq(cli.json('/api/poll')['share'], TITLE + '\n' + ARTICLE_URL, '轮询取得到分享内容')
        eq(cli.json('/api/poll')['share'], TITLE + '\n' + ARTICLE_URL,
           '**没被消费掉**：页面若正忙，下一轮还能再取（不能丢分享）')

        cli.post('/api/parse', {'source': TITLE + '\n' + ARTICLE_URL})
        cli.wait()
        eq(cli.json('/api/poll')['share'], '', '开始解析后清空，避免重复触发')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_dedup_and_partial():
    print('[4] 重复图去重与"部分成功"')
    tmp = tempfile.mkdtemp()
    try:
        # ① 同一张图在正文里贴两次 —— **解析阶段**就该按"图片身份"合并成一张。
        #    微信同一张图有多个 CDN 变体（前缀/尺寸段不同），不合并就会重复下载。
        #    （所以这里不能拿它来验"保存去重"：到保存那一步已经只剩一张了。）
        fake = FakeGet(pages={ARTICLE_URL: article((PIC_A, PIC_A))})
        cli, _, _ = _boot(tmp, fake=fake)
        cli.post('/api/parse', {'source': ARTICLE_URL})
        st = cli.wait()
        eq(st['count'], 1, '同一张图贴两次只算一张（与桌面版同一条规则）')
        web.stop()

        # ② 两个**文件号不同**、下载回来字节却相同的图 —— 这才是"内容去重"要防的情况：
        #    解析出来是两张（身份确实不同），保存时按 md5 只落一份。
        fake = FakeGet(pages={ARTICLE_URL: article((PIC_A, PIC_C))})
        cli, _, album = _boot(tmp, fake=fake)
        cli.post('/api/parse', {'source': ARTICLE_URL})
        st = cli.wait()
        eq(st['count'], 2, '两个不同的文件号 = 两张图')

        cli.post('/api/save', {'idxs': [1, 2]})
        st = cli.wait(want=('saved', 'error'))
        eq(st['phase'], 'saved', '保存完成')
        check('重复' in st['message'], f'结果里说清了有重复的：{st["message"]!r}')
        folder, n = album.calls[0]
        eq(n, 1, '相册里只落了一份')
        eq(len(os.listdir(os.path.join(album.base, folder))), 1, '磁盘上也只一份')
        eq(cli.json('/api/history')['items'][0]['success'], 1, '记录里的成功张数是 1')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_partial_download():
    print('[5] 有图下不下来 → 部分成功')
    tmp = tempfile.mkdtemp()
    try:
        class Broken(FakeGet):
            def __call__(self, url, **kw):
                if url.endswith('/0') and 'DDDD' in url:      # 第二张的原图档打不开
                    self.calls.append(url)
                    return android._Response(404, b'nope')
                if 'DDDD' in url:                             # 连压缩档也打不开
                    self.calls.append(url)
                    return android._Response(404, b'nope')
                return FakeGet.__call__(self, url, **kw)

        fake = Broken(pages={ARTICLE_URL: article()})
        cli, _, album = _boot(tmp, fake=fake)
        cli.post('/api/parse', {'source': ARTICLE_URL})
        st = cli.wait()
        eq(st['count'], 2, '解析出 2 张')

        cli.post('/api/save', {'idxs': [1, 2]})
        st = cli.wait(want=('saved', 'error'))
        eq(st['phase'], 'saved', '一张失败不该整批失败')
        check('（1 张）' in st['message'], f'只存下 1 张：{st["message"]!r}')
        check('没下下来' in st['message'], '把失败张数也告诉用户')
        eq(cli.json('/api/history')['items'][0]['status'], 'partial',
           '记录标成"部分成功"（以后好找）')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_errors():
    print('[6] 出错路径')
    tmp = tempfile.mkdtemp()
    try:
        cli, fake, album = _boot(tmp)

        # 不支持的站点：明确说不认识，且一个请求都不发
        cli.post('/api/parse', {'source': 'https://example.com/post/1'})
        st = cli.wait(want=('error',))
        eq(st['phase'], 'error', '认不出的链接落到 error')
        check('这个链接不认识' in st['message'], '说清是不认识的链接')
        check('微信' in st['message'], '顺带说支持什么')
        eq(fake.calls, [], '认不出就一个请求都不发')

        # 空输入
        cli.post('/api/parse', {'source': '   '})
        st = cli.wait(want=('error',))
        check('没能' in st['message'] or '找不到' in st['message'],
              f'空输入给人话：{st["message"]!r}')

        # error 状态下不能保存（不能假成功）
        r = cli.post('/api/save', {'idxs': [1]})
        eq(r['ok'], False, 'error 状态拒绝保存')

        # 文章里没图
        web.stop()
        fake2 = FakeGet(pages={ARTICLE_URL:
                               '<html><head><meta property="og:title" content="空的">'
                               '</head><body>没有图</body></html>'})
        cli2, _, _ = _boot(tmp, fake=fake2)
        cli2.post('/api/parse', {'source': ARTICLE_URL})
        st = cli2.wait(want=('error',))
        check('没找到图片' in st['message'], f'没图时给出可能原因：{st["message"]!r}')
        eq(cli2.json('/api/history')['items'], [], '失败的那次不该进历史表')

        # 越界与错会话号的图
        for path in (f'/img/{st["sid"]}/t/99', '/img/deadbeef/t/1', '/img/x/y/1'):
            try:
                cli2.get(path)
                check(False, f'{path} 应当 404')
            except urllib.error.HTTPError as e:
                eq(e.code, 404, f'{path} → 404')
        try:
            cli2.get('/api/nope')
            check(False, '未知接口应当 404')
        except urllib.error.HTTPError as e:
            eq(e.code, 404, '未知接口 → 404')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_net_failure_falls_back():
    print('[7] 原图档取不到时退回压缩档')
    tmp = tempfile.mkdtemp()
    try:
        class NoFull(FakeGet):
            def __call__(self, url, **kw):
                if url.endswith('/0'):
                    self.calls.append(url)
                    return android._Response(404, b'nope')
                return FakeGet.__call__(self, url, **kw)

        fake = NoFull(pages={ARTICLE_URL: article((PIC_A,))})
        cli, _, album = _boot(tmp, fake=fake)
        cli.post('/api/parse', {'source': ARTICLE_URL})
        st = cli.wait()
        eq(st['count'], 1, '解析出 1 张')

        code, hdr, body = cli.get(f"/img/{st['sid']}/f/1")
        eq(code, 200, '原图档挂掉也不至于整张失败')
        eq(body, THUMB_A, '退回用的是压缩档的字节')
        check(fake.hits('/0') >= 1, '确实先试过原图档')
        check(any('压缩档' in ln for ln in cli.json('/api/state')['log']),
              '日志里写明"用了压缩档"，用户看得见差别')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_album_failure():
    print('[8] 相册写不进去时不能假成功')
    tmp = tempfile.mkdtemp()
    try:
        def bad_album(out_dir, folder):
            raise RuntimeError('模拟相册出错')

        cli, fake, _ = _boot(tmp, album=bad_album)
        cli.post('/api/parse', {'source': ARTICLE_URL})
        cli.wait()
        cli.post('/api/save', {'idxs': [1]})
        st = cli.wait(want=('error', 'saved'))
        eq(st['phase'], 'error', '相册失败 → 落到 error')
        check('相册失败' in st['message'], f'说清是相册的问题：{st["message"]!r}')
        check('模拟相册出错' in st['message'], '错误细节保留下来（方便定位）')
        row = cli.json('/api/history')['items'][0]
        check(row['status'] in ('failed', 'scanned'),
              f"记录里不能标成已下载：{row['status']}")
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
#  三、P2：小图体检、保存画质、偏好
# ============================================================================

def _wait_check(cli, want, timeout=25):
    """等体检跑完（它是解析之后在后台接着跑的）。"""
    end = time.time() + timeout
    st = cli.json('/api/state')
    while time.time() < end:
        st = cli.json('/api/state')
        if not st['check']['running'] and st['check']['done'] >= want:
            return st
        time.sleep(0.1)
    return st


def test_inspect_tiny():
    print('[9] 小图体检（默认开着，且不花额外流量）')
    tmp = tempfile.mkdtemp()
    try:
        # ⚠ 匹配用"文件号"那一段，不是整条地址：适配器给出的候选项会把查询串
        # 剥掉（`/640?wx_fmt=jpeg` → `/640`），拿整条 URL 当键一条都匹配不上，
        # 于是所有图都落到默认字节、全都算小图 —— 断言会红，但红得莫名其妙。
        fake = FakeGet(pages={ARTICLE_URL: article((PIC_A, PIC_T))},
                       sizes={'AAAAAAAABBBBCCCC': (BIG_A, BIG_A),
                              'TINYTINYTINY': (TINY_T, TINY_T)})
        cli, fake, _ = _boot(tmp, fake=fake)
        cli.post('/api/parse', {'source': ARTICLE_URL})
        st = cli.wait()
        eq(st['phase'], 'ready', '解析完成')
        eq(st['count'], 2, '两张图')
        eq(st['settings']['skip_tiny'], True, '默认就开着过滤')

        # ⚠ 体检是解析**之后**在后台接着跑的：phase 到 ready 的那一刻，它可能
        #   一张都还没量。所以必须**先等它跑完，再数请求** —— 反过来写就是一条
        #   偶发红灯（三次里红一次、重跑就绿，查起来最费劲的那种）。
        st = _wait_check(cli, 2)
        eq(fake.hits('/0'), 0, '体检只碰压缩档 —— 它就是要显示的那张，零额外流量')
        eq(fake.hits('/640'), 2, '两张的缩略图都量过了')
        eq(st['check']['done'], 2, '两张都体检完')
        eq(st['check']['running'], False, '体检结束')
        eq(st['tiny'], [2], '只有第 2 张被认成小图（第 1 张 20 KB 不算小）')

        # 阈值跟着设置走：调大阈值，第一张也会被算成小图
        cli.post('/api/settings', {'tiny_kb': 50})
        eq(cli.json('/api/state')['tiny'], [1, 2], '改阈值立刻生效（不用重新解析）')

        # 关掉开关之后，不再有"小图"这回事
        cli.post('/api/settings', {'skip_tiny': False})
        eq(cli.json('/api/state')['tiny'], [], '关掉过滤就没有小图清单了')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_toggle():
    print('[10] 保存画质（原图档 / 压缩档）')
    tmp = tempfile.mkdtemp()
    try:
        # ① 默认：存原图
        fake = FakeGet(pages={ARTICLE_URL: article((PIC_A,))})
        album1 = FakeAlbum(os.path.join(tmp, 'album1'))
        cli, fake, _ = _boot(tmp, fake=fake, album=album1,
                             settings={'skip_tiny': False})
        cli.post('/api/parse', {'source': ARTICLE_URL})
        cli.wait()
        fake.reset()
        cli.post('/api/save', {'idxs': [1]})
        st = cli.wait(want=('saved', 'error'))
        eq(st['phase'], 'saved', '存下来了')
        eq(fake.hits('/0'), 1, '默认取原图档')
        folder, _ = album1.calls[0]
        with open(os.path.join(album1.base, folder, 'img_01.jpg'), 'rb') as f:
            eq(f.read(), FULL_A, '落盘的是原图档的字节')
        web.stop()

        # ② 关掉「保存原图」：只用压缩档，一次都不碰原图档
        fake = FakeGet(pages={ARTICLE_URL: article((PIC_A,))})
        album2 = FakeAlbum(os.path.join(tmp, 'album2'))
        cli, fake, _ = _boot(tmp, fake=fake, album=album2,
                             settings={'skip_tiny': False, 'original': False})
        cli.post('/api/parse', {'source': ARTICLE_URL})
        cli.wait()
        fake.reset()
        cli.post('/api/save', {'idxs': [1]})
        st = cli.wait(want=('saved', 'error'))
        eq(st['phase'], 'saved', '存下来了')
        eq(fake.hits('/0'), 0, '关掉之后一次都不碰原图档（这才是省流量）')
        folder, _ = album2.calls[0]
        with open(os.path.join(album2.base, folder, 'img_01.jpg'), 'rb') as f:
            eq(f.read(), THUMB_A, '落盘的是压缩档的字节')
        check('压缩档' in st['message'],
              f'结果里说清了"存的是压缩档"：{st["message"]!r}')
        web.stop()

        # ③ 单张请求可以当场覆盖设置（大图页的"存这张"走的就是这条路）
        fake = FakeGet(pages={ARTICLE_URL: article((PIC_A,))})
        album3 = FakeAlbum(os.path.join(tmp, 'album3'))
        cli, fake, _ = _boot(tmp, fake=fake, album=album3,
                             settings={'skip_tiny': False, 'original': False})
        cli.post('/api/parse', {'source': ARTICLE_URL})
        cli.wait()
        fake.reset()
        cli.post('/api/save', {'idxs': [1], 'original': True})
        st = cli.wait(want=('saved', 'error'))
        eq(st['phase'], 'saved', '存下来了')
        eq(fake.hits('/0'), 1, '显式要求原图时就按原图走，不受设置影响')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_settings():
    print('[11] 偏好：只认认识的键、值不能乱、改完要落盘')
    eq(web.DEFAULT_SETTINGS['original'], True, '默认存原图')
    eq(web.DEFAULT_SETTINGS['skip_tiny'], True, '默认跳过小图')

    # 纯函数：前端传来的东西不能信
    eq(web.normalize_settings({'original': 'false'}), {},
       "字符串 'false' 不算数（写成 bool(value) 会让开关怎么点都是开的）")
    eq(web.normalize_settings({'original': False}), {'original': False}, '真 bool 才收')
    eq(web.normalize_settings({'original': True}), {'original': True}, 'true 照收')
    eq(web.normalize_settings({'tiny_kb': '20'}), {'tiny_kb': 20}, '数字字符串转成整数')
    eq(web.normalize_settings({'tiny_kb': -5}), {'tiny_kb': 0}, '负数压到 0')
    eq(web.normalize_settings({'tiny_kb': 'abc'}), {}, '不是数字就丢掉')
    eq(web.normalize_settings({'nope': 1}), {}, '不认识的键直接扔掉')
    eq(web.normalize_settings(None), {}, 'None 不炸')

    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, 'x.json')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{ 这不是 json')
        eq(web.load_settings(p), web.DEFAULT_SETTINGS, '坏文件退回默认值（不能抛）')
        eq(web.load_settings(os.path.join(tmp, 'nope.json')), web.DEFAULT_SETTINGS,
           '文件不在也退回默认值')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = tempfile.mkdtemp()
    try:
        cli, _, _ = _boot(tmp, settings={'skip_tiny': False})
        eq(cli.json('/api/state')['settings']['skip_tiny'], False, '启动时读到盘上的偏好')

        r = cli.post('/api/settings', {'skip_tiny': True})
        eq(r['settings']['skip_tiny'], True, '改完立刻返回新值')
        eq(cli.json('/api/state')['settings']['skip_tiny'], True, '状态里也跟着变')
        eq(web.load_settings(os.path.join(tmp, web.SETTINGS_FILE))['skip_tiny'], True,
           '确实落盘了（重启还在）')
        web.stop()

        cli, _, _ = _boot(tmp)
        eq(cli.json('/api/state')['settings']['skip_tiny'], True, '重启后仍是改过的值')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_pick_source():
    print('[12] 剪贴板：两份内容里挑哪一份去解析')
    # 只有一段文本时，必须与老函数**完全一致** —— 否则就是悄悄改了老路径，
    # 而那条路（分享进来）是这个 App 的主用法。
    for raw in ('https://mp.weixin.qq.com/s/x',
                '秋天的第一杯奶茶\nhttps://mp.weixin.qq.com/s/x',
                '<html><body><img data-src="a.png"></body></html>',
                'mp.weixin.qq.com/s/x', '', '   '):
        eq(web.pick_source(raw), imgsnag.resolve_source(raw),
           f'只有一段文本时与老规则一致：{raw[:24]!r}')

    html = ('<html><body><img data-src="https://mmbiz.qpic.cn/mmbiz_jpg/AA/640">'
            '</body></html>')
    eq(web.pick_source('秋天的第一杯奶茶', html), (html, False),
       '纯文本没链接 → 把 HTML 当源码（浏览器里复制的正文就是这样）')
    eq(web.pick_source('见 https://mp.weixin.qq.com/s/x', html),
       ('https://mp.weixin.qq.com/s/x', True), '有链接就用链接（最省流量）')
    src = '<html><body>手粘的</body></html>'
    eq(web.pick_source(src, html), (src, False), '自己粘的源码优先于剪贴板里的 HTML')
    # ⚠ HTML 里的第一个链接往往是 <img src>，那是图片地址、不是文章地址
    check(web.pick_source('只有一句话', html)[0] == html,
          '不会把 HTML 里的图片地址当成文章地址去解析')

    # 端到端：纯文本没链接、内容是 HTML
    tmp = tempfile.mkdtemp()
    try:
        cli, fake, _ = _boot(tmp, settings={'skip_tiny': False})
        cli.post('/api/parse', {'source': '秋天的第一杯奶茶', 'html': article()})
        st = cli.wait()
        eq(st['phase'], 'ready', '从剪贴板 HTML 解析成功')
        eq(st['count'], 2, '抓到了 HTML 里的两张图')
        eq(fake.hits('mp.weixin.qq.com'), 0, '当源码用时不需要再去抓网页')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
#  四、P3：版本更新检测
# ============================================================================

#: 更新检测用的假 GitHub 响应。
#: 远端那次构建比本机装上的时刻晚 12 分钟 —— 这样"有新版 / 没新版"两侧
#: 都有明确用例，而不是碰运气。
APK_ASSET = 'ImgSnag_1.10.0_android_arm64.apk'
APK_CREATED = '2026-10-06T07:12:33Z'        # 远端那个安装包的构建时刻（UTC）
APK_INSTALLED = '2026-10-06T07:00:00Z'      # 本机上装着的那一份的时刻（UTC）


def release_json(created=APK_CREATED, name=APK_ASSET, tag='v1.10.0', digest='',
                 size=11500000):
    """GitHub「取最新正式发布」的假响应（只放我们真正会读的字段）。

    故意混一个非 .apk 的资产进去：挑错了就会拿一个文本文件的时间去比，
    而这种错**不会报任何错**，只是"永远说已是最新"。

    ⚠ 这个名字要**排在 .apk 前面**。资产会被按名字排序，而排序是按字符码点：
    '0'(48) < 'A'(65) < 'I'(73) < 'a'(97) —— 大写字母在小写**前面**。
    早先叫 `checksums.txt`、后来改叫 `aaa-readme.txt`，两次都排在 apk 后面，
    于是"随便挑第一个"这个 bug 被排序盖住了，拆坏用例照样绿
    （反向验证抓出来的，抓了两次才对准）。

    `size` 与下载用例的假字节数**必须一致**：下载完会拿它校验完整性，
    不一致的话"正常下载"那条用例会被判成"下载不完整"（反过来也一样）。
    """
    apk = {'name': name, 'size': size,
           'browser_download_url': 'https://example.invalid/ImgSnag.apk',
           'created_at': created}
    if digest:
        apk['digest'] = digest
    return json.dumps({
        'tag_name': tag,
        'html_url': 'https://github.com/virmuran/ImgSnag-WeChat/releases/latest',
        'body': '更新说明',
        'assets': [
            {'name': '0-readme.txt', 'size': 12, 'browser_download_url': '',
             'created_at': '2020-01-01T00:00:00Z'},
            apk,
        ],
    }, ensure_ascii=False)


def test_update_check():
    print('[13] 检查更新：比版本号为主、比发布时间为兜底')
    # 更新检查问的是**正式发布位**。这个 URL 曾经指向滚动的预发布位 apk-latest，
    # 预发布位取消后改读 /releases/latest —— 这里钉死，防止将来被悄悄改回去
    # （改回去的表现完全静默：apk-latest 一删，所有旧包永远"问不到"）。
    # ⚠ 只断言"新 URL 在、旧 URL 形态不在"，不断言 "apk-latest" 这个词 ——
    #   注释里讲历史时还会提到它（反向验证抓过的教训：别把断言绑在注释词上）。
    src = read(WEB_PY)
    check('/releases/latest' in src, '更新检查读的是正式发布位（与桌面版同源）')
    check('releases/tags/' not in src, '不再读按 tag 取的滚动预发布位')
    installed = web.iso_ms(APK_INSTALLED)
    check(installed > 0, '测试自己的时间戳算得出来（ISO 末尾带 Z 也要认）')
    #: 远端那个包的构建时刻，比"本机安装时刻"**早**一小时。
    #   专供"版本号说了算"的用例：此时若还看时间，就会得出"没更新"。
    older_build = web.iso_ms(APK_INSTALLED) - 3600_000

    tmp = tempfile.mkdtemp()
    try:
        fake = FakeGet(pages={ARTICLE_URL: article(), web.APK_API_URL: release_json()})
        # 本机 1.9.0 < 远端 1.10.0
        cli, _, _ = _boot(tmp, fake=fake, version='1.9.0')

        # ① 版本号说了算：远端版本更高 → 有更新。
        #    ⚠ 这里远端的构建时间**故意比本机安装时间早一小时** ——
        #    如果代码还在"比时间"，本条会红。这就是这次改造的重点。
        r = cli.json(f'/api/update?at={installed}&force=1')
        check(r['ok'], f'问到了远端信息：{r.get("error")!r}')
        eq(r['remote_version'], '1.10.0', '版本号从资产名里读出来')
        eq(r['remote_ms'], web.iso_ms(APK_CREATED), '取的是 .apk 那个资产的时间')
        check(r['remote_time'], f'远端时间给成人话：{r["remote_time"]!r}')
        # 显示的必须是**本地时区**的时间。GitHub 给的是 UTC，不转的话
        # "今天 07:12 构建"其实是北京 15:12 —— 用户会以为那不是自己刚推的那次。
        local = datetime.fromisoformat(APK_CREATED.replace('Z', '+00:00')).astimezone()
        check(local.strftime('%H:%M') in r['remote_time'],
              f'时间按本地时区显示：{r["remote_time"]!r} 该包含 {local.strftime("%H:%M")}')
        check(r['has_update'], '远端版本更高 → 有更新（哪怕远端构建时间更早）')
        check('github.com' in r['page_url'], '给出的是去下载的页面')
        eq(r['apk_name'], APK_ASSET, '把要下载的资产名一起带回来')
        eq(r['apk_url'], 'https://example.invalid/ImgSnag.apk', '下载地址带回来了')
        eq(r['apk_size'], 11500000, '字节数带回来了（下载完要拿它校验完整性）')

        # ② 本机版本更高（用户自己装了更新的包）→ 不能说有更新，
        #    哪怕远端那个包**构建时间更晚**（这条反过来钉住"不再只看时间"）
        web.stop()
        cli2, _, _ = _boot(tmp, fake=fake, version='1.11.0')
        r2 = cli2.json(f'/api/update?at={installed}&force=1')
        check(not r2['has_update'],
              '本机版本更高时不提示更新（旧行为：只看时间会误报）')

        # ③ 版本号相同 + 远端构建更晚 → 仍要提示（同版本号重新构建过的情况）
        web.stop()
        cli3, _, _ = _boot(tmp, fake=fake, version='1.10.0')
        check(cli3.json(f'/api/update?at={installed}&force=1')['has_update'],
              '版本号相同但远端时间更晚 → 提示更新（同版本重传资产时的兜底判据）')

        # ④ 宽限期：版本号相同、时间只差 30 秒 → 不算。
        #    手机时钟与 GitHub 服务器总有几秒差，不留余量的话"刚装完就提示有新版本"
        #    会一直出现，用户就学会无视它了。
        edge = web.iso_ms(APK_CREATED) - 30_000
        check(not cli3.json(f'/api/update?at={edge}&force=1')['has_update'],
              '版本号相同、时间只差几十秒 → 不算更新（留着宽限期）')

        # ⑤ 远端资产名里认不出版本号 → 退回比时间（老办法还得在）
        web.stop()
        fake2 = FakeGet(pages={ARTICLE_URL: article(),
                               web.APK_API_URL: release_json(name='ImgSnag_android.apk')})
        cli4, _, _ = _boot(tmp, fake=fake2, version='1.9.0')
        r5 = cli4.json(f'/api/update?at={installed}&force=1')
        eq(r5['remote_version'], '', '认不出来就是空串，不硬猜')
        check(r5['has_update'], '版本号认不出来时退回比时间：远端更晚 → 有更新')

        # ⑥ 本机版本号读不到（浏览器里调试 / 打包异常）→ 退回比时间；
        #    连安装时间也拿不到时，只报远端，**不下结论**
        web.stop()
        cli5, _, _ = _boot(tmp, fake=fake2, version='')
        r6 = cli5.json('/api/update?force=1')
        check(r6['ok'] and r6['unknown'], '两个判据都拿不到时如实说"不知道"')
        check(not r6['has_update'], '不知道的时候不能乱说有更新')
        eq(r6['remote_version'], '', '但仍然把远端信息报出来（版本号认不出就是空）')

        # ⑥′ 安装时间明显不合理（时间戳被 int 截断成负数、或手机时钟跑飞）→ 当"问不到"。
        #     不设防的话：负数在布尔判断里是"真"，会算出"永远有新版本"。
        r7 = cli5.json('/api/update?at=-12345&force=1')
        check(not r7['has_update'], '安装时间被截断成负数时不能报有更新')
        check(r7['unknown'], '这种时候应该老实说"不知道"')

        # ⑦ 节流：不带 force 的重复请求不该再联网
        web.stop()
        cli6, fake3, _ = _boot(tmp, fake=fake, version='1.9.0')
        cli6.json(f'/api/update?at={installed}&force=1')
        before = fake3.hits('api.github.com')
        check(before > 0, '确实问过 GitHub')
        cli6.json('/api/update')
        cli6.json('/api/update')
        eq(fake3.hits('api.github.com'), before, '节流生效：短时间内不再联网')
        cli6.json('/api/update?force=1')
        eq(fake3.hits('api.github.com'), before + 1, '手动点「检查」绕过节流')

        # ⑧ 节流要跨启动生效（存盘），否则每开一次 App 就问一遍 GitHub
        state = json.loads(read(os.path.join(tmp, web.UPDATE_FILE)))
        eq(state.get('remote_version'), '1.10.0', '最近一次结果落了盘')
        check(state.get('_at'), '落盘里带了检查时刻（下次启动据此决定要不要再问）')
        check(state.get('apk_url'), '连"要下载哪个文件"也一起存了（点更新时不必再查一遍）')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    # ⑨ 接口问不通：要说人话，而且不能把服务带崩
    tmp2 = tempfile.mkdtemp()
    try:
        cli2, _, _ = _boot(tmp2, fake=FakeGet(pages={ARTICLE_URL: article()}))
        r = cli2.json('/api/update?force=1')
        check(not r['ok'], '问不到时 ok=False')
        check(r['error'] and 'Traceback' not in r['error'],
              f'给出的是人话原因：{r["error"]!r}')
        check('发布' in r['error'] or '仓库' in r['error'],
              f'认得出"这个位置没有东西"：{r["error"]!r}')
        # 一次检查失败不该把界面弄死
        eq(cli2.json('/api/state')['phase'], 'idle', '检查失败之后服务照常')
    finally:
        web.stop()
        shutil.rmtree(tmp2, ignore_errors=True)


#: 假安装包的字节（不大，但够分几块下；内容随意 —— 验的是流程与校验）
APK_BYTES = b'\x50\x4b\x03\x04' + b'A' * 40000


class FakeApkOpener:
    """假安装包下载：像真网络那样**分块**给，还能按需放慢、截断、报错。

    真实现（urllib 的 opener）也是这样用的：`opener.open(req, timeout=...)`
    拿到的对象能 `read(n)`、能从 headers 里读 Content-Length。
    假替身的签名必须与真的一致 —— 否则测试全绿、真机一跑就炸
    （桌面版在这条上踩过：注入契约写错，真联网那条路从没被走到）。
    """

    def __init__(self, data=APK_BYTES, chunk=4096, delay=0.0, drop=None,
                 http=None, length=None):
        self.data = data
        self.chunk = chunk
        self.delay = delay
        #: 只给前 drop 个字节（模拟企业网络把长响应截断）
        self.drop = drop
        #: 抛 HTTPError（模拟 404/403）
        self.http = http
        self.length = length
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(getattr(req, 'full_url', str(req)))
        if self.http:
            raise urllib.error.HTTPError(getattr(req, 'full_url', ''), self.http,
                                         'fake', None, None)
        data = self.data if self.drop is None else self.data[:self.drop]
        return _FakeApkResp(data, self.chunk, self.delay, self.length)


class _FakeApkResp:
    """够用的假响应：read(n) 分块、headers 里给 Content-Length、可当上下文管理器。"""

    def __init__(self, data, chunk, delay, length):
        self.data = data
        self.chunk = chunk
        self.delay = delay
        self.pos = 0
        self.status = 200
        self.headers = {}
        if length is not None:
            self.headers['Content-Length'] = str(length)

    def read(self, n=None):
        if self.delay:
            time.sleep(self.delay)
        if self.pos >= len(self.data):
            return b''
        step = len(self.data) - self.pos if n is None else min(n, self.chunk)
        out = self.data[self.pos:self.pos + step]
        self.pos += step
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_update_download():
    print('[14] 应用内更新：下载 → 校验 → 落到该落的地方（真服务，假下载）')
    installed = web.iso_ms(APK_INSTALLED)
    sha = 'sha256:' + hashlib.sha256(APK_BYTES).hexdigest()

    def wait_dl(cli, timeout=20):
        end = time.time() + timeout
        d = cli.json('/api/state')['dl']
        while time.time() < end and d.get('phase') not in ('done', 'error'):
            time.sleep(0.05)
            d = cli.json('/api/state')['dl']
        return d

    def case(opener, digest=sha, size=None):
        """起一套全新环境。

        ⚠ **每块用例各自一个数据目录**：更新检查的结果是落盘的（节流要跨启动
        生效），共用一个目录时第二块用例会直接拿到上一块缓存的下载地址和摘要 ——
        那时改假响应里的 sha256 压根不生效，用例看着绿、其实什么都没验到。
        """
        tmp = tempfile.mkdtemp()
        files = os.path.join(tmp, 'appfiles')            # 假装是安卓的应用私有目录
        os.makedirs(files, exist_ok=True)
        fake = FakeGet(pages={
            ARTICLE_URL: article(),
            web.APK_API_URL: release_json(digest=digest,
                                          size=len(APK_BYTES) if size is None else size),
        })
        web.stop()
        cli, _, _ = _boot(tmp, fake=fake, version='1.9.0', files_dir=files,
                          apk_opener=opener)
        return tmp, files, cli

    # ① 正常下载一遍
    tmp, files, cli = case(FakeApkOpener())
    try:
        eq(cli.json('/api/state')['dl']['phase'], 'idle', '一开始没有下载任务')
        # 还没检查过也能直接点「更新」：后端自己补一次检查（用户不该被要求先点检查）
        r = cli.post('/api/update/download', {'at': installed})
        check(r.get('phase') == 'downloading', f'下载受理了：{r}')
        d = wait_dl(cli)
        eq(d['phase'], 'done', f'下载完成（失败原因：{d.get("error")!r}）')
        eq(d['done'], len(APK_BYTES), '进度报的字节数 = 实际拿到的字节数')
        eq(d['version'], '1.10.0', '状态里带着这次下的是哪个版本')

        # 文件落在**传进来的那个目录**下的 update/ 里（安卓上就是 FileProvider
        # 开放的那一层）。落错地方的表现是"下载完成、点安装报文件不存在"，
        # 而且只在真机上才暴露 —— 所以这里连目录名一起钉住。
        check(d['path'].startswith(files + os.sep),
              f'落在 files_dir 下：{d["path"]}')
        check(os.sep + web.UPDATE_DIR_NAME + os.sep in d['path'],
              f'目录名就是约定那个：{d["path"]}')
        eq(os.path.basename(d['path']), APK_ASSET, '文件名用 GitHub 上那个名字')
        eq(open(d['path'], 'rb').read(), APK_BYTES, '下来的字节与远端一致')
        check(not os.path.exists(d['path'] + '.part'), '下完不留半截文件')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    # ② 三种失败：截断 / 摘要不符 / 地址 404 —— 都必须报错并且**不留下半截文件**
    for title, cfg, digest, needle in (
        ('被截断（企业网络出口常见）', dict(opener=FakeApkOpener(drop=20000)), sha, '不完整'),
        ('摘要对不上（传输途中被改）', dict(opener=FakeApkOpener()), 'sha256:' + '0' * 64, '校验'),
        ('下载地址 404', dict(opener=FakeApkOpener(http=404)), sha, '404'),
    ):
        tmp, files, cli = case(opener=cfg['opener'], digest=digest)
        try:
            cli.post('/api/update/download', {'at': installed})
            d = wait_dl(cli)
            eq(d['phase'], 'error', f'{title} → 报错')
            check(needle in d['error'], f'{title} 的原因说得清：{d["error"]!r}')
            check('Traceback' not in d['error'] and 'URLError' not in d['error'],
                  f'{title}：给的是人话，不是异常原文：{d["error"]!r}')
            check(not os.path.exists(os.path.join(files, web.UPDATE_DIR_NAME, APK_ASSET)),
                  f'{title}：没把半截文件留在下载目录里冒充成品')
            # ⚠ 光查"成品名不存在"太弱：半截文件叫 `<名字>.part`，失败后不删它也照样
            #   满足上面那条（反向验证抓出来的）。所以整目录必须空。
            upd_dir = os.path.join(files, web.UPDATE_DIR_NAME)
            eq([], sorted(os.listdir(upd_dir)) if os.path.isdir(upd_dir) else [],
               f'{title}：下载目录里什么都不剩（半截的 .part 也不留）')
        finally:
            web.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    # ③ 取消（手机上流量贵，下到一半反悔是常事）
    tmp, files, cli = case(FakeApkOpener(chunk=2048, delay=0.05))
    try:
        cli.post('/api/update/download', {'at': installed})
        eq(cli.json('/api/state')['dl']['phase'], 'downloading', '确实在下载中')
        r = cli.post('/api/update/cancel', {})
        check(r['phase'] in ('idle', 'downloading'), f'取消被受理：{r}')
        d = r
        end = time.time() + 10
        while time.time() < end and d.get('phase') == 'downloading':
            time.sleep(0.05)
            d = cli.json('/api/state')['dl']
        eq(d['phase'], 'idle', '取消后回到空闲态（界面据此变回「更新」）')
        left = os.path.join(files, web.UPDATE_DIR_NAME)
        check(not (os.path.isdir(left) and os.listdir(left)),
              f'取消后不留半截文件：{os.listdir(left) if os.path.isdir(left) else "（目录都没有）"}')

        # ④ 页面拿进度不用另开轮询：通用状态里就带着 dl
        check('dl' in cli.json('/api/state'), '通用状态里带了 dl 字段')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    # ⑤ 下完新包要把**上一个**删掉。
    #    资产名里带版本号，所以新包不会覆盖上一版、而是并排躺着 ——
    #    不删的话每更新一次就在应用私有目录里多留一个 ~20MB 的包，
    #    而且**完全无声**（用户只会隐约觉得"App 怎么越来越大"）。
    #    顺带验残留的 `.part` 一起扫掉（取消/进程被杀会留下）。
    tmp, files, cli = case(FakeApkOpener())
    try:
        upd_dir = os.path.join(files, web.UPDATE_DIR_NAME)
        os.makedirs(upd_dir, exist_ok=True)
        for n in ('ImgSnag_1.9.0_android_arm64.apk',        # 上一版
                  'ImgSnag_1.8.0_android_arm64.apk.part'):  # 更早一次留下的半截
            with open(os.path.join(upd_dir, n), 'wb') as f:
                f.write(b'x' * 32)

        cli.post('/api/update/download', {'at': installed})
        d = wait_dl(cli)
        eq(d['phase'], 'done', f'下好了（失败原因：{d.get("error")!r}）')
        eq(sorted(os.listdir(upd_dir)), [APK_ASSET],
           '下载目录里只剩这一次下好的那个（旧版本与半截文件都清掉了）')
        eq(open(d['path'], 'rb').read(), APK_BYTES, '留下的那个是完整的新包')
    finally:
        web.stop()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
#  五、静态契约（跨语言、跨文件的对应关系）
# ============================================================================

def test_page_contract():
    print('[9] 页面自检（JS 用到的 id 必须真的在页面里）')
    page = webui.PAGE
    check(page.startswith('<!DOCTYPE html>'), '是完整页面')
    check('name="viewport"' in page and 'viewport-fit=cover' in page,
          '有移动端 viewport（含刘海屏适配）')
    check('prefers-color-scheme: dark' in page, '跟系统深浅色')
    check('env(safe-area-inset-bottom)' in page, '底部按钮避开手势条')

    # 零外部资源：手机上一旦要联网取 CSS/JS，弱网就是白屏
    check('src="http' not in page, '没有外链脚本/图片')
    check('href="http' not in page, '没有外链样式')
    check('<script src' not in page, '脚本内联（单文件）')
    # 带引号一起比对：接口名是"整个字符串"才算数。
    # 只查 `'/img/' in page` 是假断言 —— 改成 `'/imgx/'` 照样命中（子串），
    # 这种"看起来在验证、其实没验证"的检查是反向验证抓出来的。
    check("'/img/'" in page, '图片走本地接口')

    # 页面是**原样发出去**的，不能被 .format() 之类动过（CSS 里全是 % 和 {}）
    src = read(WEB_PY)
    check('webui.PAGE.encode' in src, '页面原样发送，没有做字符串格式化')

    # JS 里 $('#xxx') 用到的 id 必须存在 —— 少一个就是"点了没反应"这类静默故障
    used = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", page))
    defined = set(re.findall(r'id="([A-Za-z0-9_-]+)"', page))
    check(len(used) >= 10, f'页面里确实在用 id 找元素（找到 {len(used)} 个）')
    missing = sorted(used - defined)
    eq(missing, [], f'JS 引用的 id 都存在（缺：{missing}）')

    # 接口字段与页面取值要对得上。
    # folder 不在列：页面**用不着**它 —— 存到哪儿是靠 message 里那句人话告诉用户的，
    # folder 只有测试与相册比对时才看。硬把它塞进页面反而是多余的耦合。
    for field in ('phase', 'message', 'title', 'count', 'sid', 'save', 'log'):
        check(f"'{field}'" in page or f'.{field}' in page,
              f'页面用到了状态字段 {field}')

    # 三档布局能力必须在：网格、勾选、保存
    check('grid-template-columns' in page, '缩略图是多列网格（不是一堆大图堆叠）')
    check('loading' in page, '缩略图懒加载（别一进来就下几十张）')
    check("'/api/save'" in page, '有保存到相册的动作')
    check('全选' in page, '有全选（几十张时不能靠手点）')

    # 大图要能左右滑（用户实测反馈："只能返回再点第二张"）
    # ⚠ 一律带引号或括号一起比对：裸写 `'touchstart' in page`，
    #   把事件名改成 `touchstartx` 照样命中（子串），断言是假的。
    check("'touchstart'" in page and "'touchend'" in page, '大图接了触摸手势')
    check('function step(' in page, '有"上一张/下一张"这个动作')
    check("'ArrowLeft'" in page and "'ArrowRight'" in page,
          '键盘左右键也能翻（电脑上调试方便）')
    check('id="vPrev"' in page and 'id="vNext"' in page, '大图两侧有翻页按钮')

    # 系统侧边滑动返回要能逐层退 —— 靠的就是这些 pushState
    check('history.pushState(' in page, '视图切换会压一层返回栈')
    check('history.replaceState(' in page, '栈底换成自己的条目（否则后退会退穿页面）')
    check("addEventListener('popstate'" in page, '响应系统返回')
    check('history.back()' in page, '页面里的"返回/关闭"走的是同一条回退路径')

    # 剪贴板与偏好
    check(re.search(r'window\.[A-Za-z0-9_$]+\.read\(\)', page) is not None,
          '页面会去读那个剪贴板桥')
    check("'/api/settings'," in page, '页面能改偏好')
    check('id="setOriginal"' in page, '有"保存原图"开关')
    check('id="setSkipTiny"' in page, '有"自动跳过小图"开关')
    check('appearance:none' in page, '开关是自绘的，不靠系统控件（各家 ROM 长得不一样）')
    check("classList.toggle('mini'" in page, '小图在网格里有标记')

    # P3：更新入口
    check('id="updBtn"' in page, '首页有检查更新的入口')
    check("'/api/update?" in page, '页面会去问更新接口（带上参数）')
    check('installedAt()' in page, '把本机安装时间一起带上（后端靠它判断）')
    check(re.search(r'window\.[A-Za-z0-9_$]+\.open\(', page) is not None,
          '去下载走 Java 的桥 —— 页面里点外链在壳里是出不去的')

    # 大图翻页动画作用在 #vstage 上，不在图片本身：
    # 图片自己还要管"适应/原大小"，两个 transform 叠一起会互相覆盖。
    check('id="vstage"' in page, '大图外面单独包了一层用来做动画')
    check('prefers-reduced-motion' in page, '系统开了"减少动效"就不硬播动画')

    # 层级上报：Java 的返回回调只看这个数，网页漏报一处就等于那一步没接。
    # ⚠ 数调用时要带 `;` —— 光数 `reportDepth()` 会把**函数定义**那一行
    #   （`function reportDepth(){`）也算进去，于是少调一次照样"及格"。
    #   这条是反向验证抓出来的：拆掉 setView 里那次上报，断言照样绿。
    check('function reportDepth(' in page, '有"把层级报给 Java"这个动作')
    calls = len(re.findall(r'reportDepth\(\);', page))
    check(calls >= 4, f'reportDepth() 每次进出层级都调了（找到 {calls} 处）')

    # 「复制」：底部条上挨着「保存」的那一个
    check('id="copyBtn"' in page, '底部条有「复制」按钮')
    check(re.search(r'id="copyBtn"[^>]*>复制</button>', page) is not None,
          '按钮文案是「复制」（两字，与桌面版叫法一致）')
    check(re.search(r'id="saveBtn"[^>]*>保存</button>', page) is not None,
          '按钮文案是「保存」（两字）')
    check("$('#copyBtn').onclick" in page, '按钮接了点击动作')
    check('function copyText(' in page, '有"写剪贴板"这个动作')
    # 写剪贴板必须走 Java 的桥：navigator.clipboard 要求安全上下文 + 用户授权，
    # 而这个页面是 http://127.0.0.1，实机行为不可预期。
    check(re.search(r'window\.[A-Za-z0-9_$]+\.copy\(', page) is not None,
          '写剪贴板走 Java 的桥（不是 navigator.clipboard）')
    # 没有标题时按钮要置灰 —— 否则就是"点了没反应"
    check(re.search(r"\$\('#copyBtn'\)\.disabled\s*=", page) is not None,
          '按钮可用状态跟着标题走（没标题时置灰）')
    check(re.search(r'window\.[A-Za-z0-9_$]+\.depth\(', page) is not None,
          '走的是桥上的 depth()')


def test_java_contract():
    print('[10] 与 Java 的握手点（静态比对）')
    # ⚠ 全部先摘注释：这里全是"在源码里找字符串"的检查，
    #   而注释里为了讲清楚道理，往往会把同一个字符串再写一遍。
    gallery = strip_comments(read(os.path.join(JAVA_DIR, 'Gallery.java')))
    main = strip_comments(read(os.path.join(JAVA_DIR, 'MainActivity.java')))
    webpy = read(WEB_PY)

    # ① 相册目录名两边一致
    m = re.search(r'ALBUM\s*=\s*"([^"]+)"', gallery)
    check(m is not None, 'Java 里声明了相册目录名')
    eq(m.group(1) if m else None, web.ALBUM_SUBDIR,
       '相册目录名 Java 与 Python 一致（不一致＝图抓到了相册里却没有，且不报错）')

    # ② 类名：Python 用全限定名找它
    eq(web.GALLERY_CLASS, 'com.virmuran.imgsnag.Gallery',
       'Python 找的类名是固定的全限定名')
    check('package com.virmuran.imgsnag;' in gallery, 'Gallery 的 package 声明对得上')
    check(re.search(r'public\s+(?:final\s+)?class\s+Gallery', gallery) is not None,
          'Gallery 类名与文件名一致')

    # ③ 相册入库的方法签名：Python 按 (ctx, 目录, 相册子目录) 调，拿回张数
    sig = re.search(r'public\s+static\s+int\s+publishDir\s*\(([^)]*)\)', gallery)
    check(sig is not None, 'Java 提供 public static int publishDir(...)')
    if sig:
        params = [p.strip().split()[-1] for p in sig.group(1).split(',')]
        eq(params, ['ctx', 'srcDir', 'albumSub'],
           'publishDir 的三个参数与 Python 的调用顺序一致')
    call = re.search(r'publishDir\(\s*ctx\s*,\s*([A-Za-z0-9_.]+)\s*,\s*([A-Za-z0-9_.]+)\s*\)',
                     webpy)
    check(call is not None, 'Python 侧确实按三个参数调用 publishDir')
    if call:
        eq(call.group(1), 'out_dir', '第二个实参是待入库的目录')
        eq(call.group(2), 'album_sub', '第三个实参是相册子目录（不是别的）')

    # ④ 分享投递
    check('push_share' in main, 'Java 会把分享内容投递给 Python')
    check(re.search(r'callAttr\("push_share",\s*[A-Za-z0-9_.]+\)', main) is not None,
          'Java 调的确实是 push_share（参数个数也对）')
    check('getModule("imgsnag_web")' in main, 'Java 加载的是网页后端模块')

    # ⑤ 启动：参数个数与顺序
    sig = re.search(r'def start\(([^)]*)\)', webpy)
    check(sig is not None, 'Python 提供 start()')
    if sig:
        params = [p.split(':')[0].split('=')[0].strip() for p in sig.group(1).split(',')]
        eq(params[:2], ['ctx', 'version'],
           'start 前两个参数是「上下文 + 版本号」，与 Java 调用一致')
    m = re.search(r'callAttr\("start",\s*([^)]*)\)', main)
    check(m is not None, 'Java 调用了 start(...)')
    if m:
        check('VERSION_NAME' in m.group(1), 'Java 把版本号一起传了进去')
    # ⚠ 应用私有目录必须**由 Java 显式传**（不靠 Python 猜 HOME 在哪）：
    #   Python 猜错的表现是"下载完成、点安装报文件不存在"，只在真机上暴露。
    check(re.search(r'new\s+Kwarg\(\s*"files_dir"\s*,\s*[^)]*getFilesDir\(\)', main)
          is not None,
          'Java 把应用私有目录（filesDir）作为 files_dir 传给 Python')
    check('com.chaquo.python.Kwarg' in main, '用 Chaquopy 的关键字参数（不是位置参数硬塞）')

    # ⑥′ 应用内更新的三方约定：目录名在 Java / 清单 / Python 三处必须一字不差
    man = strip_xml_comments(read(os.path.join(SRC_MAIN, 'AndroidManifest.xml')))
    m = re.search(r'UPDATE_DIR\s*=\s*"([^"]+)"', main)
    check(m is not None, 'Java 里声明了更新包目录名')
    if m:
        eq(m.group(1), web.UPDATE_DIR_NAME, 'Java 的目录名与 Python 一致')
        eq(m.group(1), 'update', '目录名就是 update（换了要三处一起换）')
    paths_xml = os.path.join(SRC_MAIN, 'res', 'xml', 'file_paths.xml')
    check(os.path.exists(paths_xml), 'FileProvider 的路径配置在（没有它安装器读不到包）')
    if os.path.exists(paths_xml):
        body = strip_xml_comments(read(paths_xml))
        eq(re.findall(r'path="([^"]+)"', body), [web.UPDATE_DIR_NAME + '/'],
           'FileProvider 只开放 update/ 这一个子目录（不是整个 files 目录）')
        check('files-path' in body, '基准目录是 getFilesDir()，与 Java 传的那个一致')
    check('androidx.core.content.FileProvider' in man,
          '清单里用的是 FileProvider（安卓 7 起 file:// 不能跨应用传文件）')
    check('android.support.FILE_PROVIDER_PATHS' in man, 'FileProvider 指向了路径配置')
    check('REQUEST_INSTALL_PACKAGES' in man,
          '清单里声明了装包权限（安卓 8 起没它装不了）')
    check('grantUriPermissions="true"' in man, '允许把这个 uri 临时授权给安装器')
    check('exported="false"' in man, 'FileProvider 不对外暴露')

    # ⑥‴ 装包的安全边界：只认自己下载目录里的文件。桥的入参来自网页，
    #     而网页的内容来自抓来的文章 —— 白名单一条比事后追查便宜得多。
    #     只查"方法存在"太弱（空实现照样有那个签名），得钉住判据本身。
    check(re.search(r'private\s+boolean\s+inUpdateDir\s*\(', main) is not None,
          'Java 侧有"这个文件在自己下载目录里吗"的判断')
    check(re.search(r'if\s*\(\s*!inUpdateDir\([^)]*\)', main) is not None,
          '安装前真的拿这个判断拦了一道（不是写了却不用）')
    check('getCanonicalPath' in main,
          '用规范路径比较（`..` 与软链接绕不过去）')
    check(re.search(r'private\s+boolean\s+canRequestInstall\s*\(', main) is not None,
          '有"用户给过安装权限没有"的判断')
    check('ACTION_MANAGE_UNKNOWN_APP_SOURCES' in main,
          '没授权时把用户送去系统设置页（不送的话点了没反应）')
    check('onResume' in main and 'pendingInstallPath' in main,
          '授权回来会接着装（不接着装就像"点了安装什么都没发生"）')
    check('FLAG_GRANT_READ_URI_PERMISSION' in main,
          '给安装器显式授权读那个 uri（少它＝安装器报"解析包时出现问题"）')

    # ⑥″ 网页侧：装包这条路得真的接上桥，并且**留着"去网页下载"的兜底**
    check('canInstall' in webui.PAGE and 'install(' in webui.PAGE,
          '页面会调桥上的安装方法')
    check("'/api/update/download'" in webui.PAGE, '页面会发起应用内下载')
    check("'/api/update/cancel'" in webui.PAGE, '页面能取消下载')
    check('id="updWeb"' in webui.PAGE, '页面上留了"去网页下载"的兜底入口')

    # ⑥ 主路还在：分享意图 + 回环明文放行
    man = strip_xml_comments(read(os.path.join(SRC_MAIN, 'AndroidManifest.xml')))
    check('android.intent.action.SEND' in man, '清单里仍然接收分享')
    check('android:networkSecurityConfig' in man, '清单里指向了网络安全配置')
    nsc = os.path.join(SRC_MAIN, 'res', 'xml', 'network_security_config.xml')
    check(os.path.exists(nsc), '网络安全配置文件存在')
    if os.path.exists(nsc):
        body = strip_xml_comments(read(nsc))
        check('127.0.0.1' in body, '放行的是回环地址')
        check('cleartextTrafficPermitted="true"' in body, '明确放行明文')
        eq(len(re.findall(r'<domain[ >]', body)), 1,
           '只放行一个域，不是全局开明文')

    # ⑦ 返回键与网页桥（都在 Java 侧，本机验不了，只能钉契约）
    #
    # 这一组是"侧滑返回到底管不管用"的必要件。少任何一件，真机表现都一样：
    # **侧滑直接退回桌面**，而且一句错都不报 —— 所以只能这样一条条钉住。
    check(re.search(r'@Override[\s\S]{0,140}?public void onBackPressed\s*\(\s*\)',
                    main) is not None,
          'onBackPressed 是覆写（漏了 @Override 就是新写了个没人调的方法）')
    check('registerOnBackInvokedCallback(' in main,
          'API 33+ 注册了返回回调 —— targetSdk 35+ 上系统只走这条，'
          '不注册的话 onBackPressed 压根不会被调用（真机上踩过）')
    check(re.search(r'Build\.VERSION\.SDK_INT\s*>=\s*Build\.VERSION_CODES\.TIRAMISU',
                    main) is not None,
          '注册前判了系统版本（不判的话老系统上找不到那些新类，一进就崩）')
    # ⚠ 清单的注释里也写着 enableOnBackInvokedCallback 这个词（就为了讲清
    #   为什么要有它），所以不能光查这个词 —— 得连属性名一起查。
    #   `man` 已经过 `strip_xml_comments`（见那个函数的说明），这一条就算不退让
    #   也拦得住；留着完整属性名是双保险。
    check('android:enableOnBackInvokedCallback="true"' in man,
          '清单里显式声明了预测性返回（免得日后动 targetSdk 时行为悄悄变回去）')
    # 光查"字段名出现过"太弱：字段声明还在、但 handleBack 里不再看它，
    # 照样能凑齐这个字符串（反向验证抓出来的）。
    check(re.search(r'if\s*\(\s*webDepth\s*>\s*0', main) is not None,
          '返回的判据真的是"网页报上来的层级 > 0"')
    check(re.search(r'evaluateJavascript\("history\.back\(\)"', main) is not None,
          '有层可退时交给网页自己退（这样点关闭与按返回走的是同一条路）')

    # 网页得真把层级报上来 —— 不报的话 Java 永远以为在最外层
    check('reportDepth' in webui.PAGE and 'window.imgsnag.depth(' in webui.PAGE,
          '网页每次进出层级都会同步给 Java')
    # 翻页**不算**"进去"：只替换当前层。
    # 早先跟"进场"共用同一个 pushState，翻 5 张就要划 5 次才退得出去。
    body = re.search(r'function openViewer\([\s\S]*?\n\}', webui.PAGE)
    check(body is not None, '页面里找得到 openViewer')
    if body:
        eq(body.group(0).count('history.pushState'), 1,
           'openViewer 里只压一层（翻页不能再压，否则翻几张就要划几次）')
        check('history.replaceState' in body.group(0),
              '翻页走 replaceState（同一层里换内容）')

    # 桥上的七个方法：JS 调的每一个都得标注解，否则点了一动不动
    #
    # ⚠ 这里**不能**用"在方法签名前面 160 个字符里找 `@JavascriptInterface`"那种写法。
    #   两个方法挨着写的时候，前一个的注解正好落在这个窗口里 —— 把后一个的注解删掉，
    #   断言照样绿（反向验证抓出来的，第 3 条）。
    #   改成**贴着查**：注解 → （可夹别的注解）→ public → 返回类型 → 方法名 → `(`。
    #   `[^;{}]` 是关键：它挡住"跨过方法体、跳到下一个方法的注解上"这条路。
    #   （`installedAt()` 真的夹了一个 `@SuppressWarnings("deprecation")`，
    #    所以中间那截不能省。）
    for meth in ('read', 'copy', 'depth', 'open', 'installedAt',
                 'canInstall', 'install'):
        check(re.search(r'public\s+[\w<>\[\]]+\s+' + meth + r'\s*\(', main) is not None,
              f'桥上有 {meth}() 方法')
        check(re.search(r'@JavascriptInterface[^;{}]{0,120}?'
                        r'public\s+[\w<>\[\]]+\s+' + meth + r'\s*\(', main) is not None,
              f'{meth}() 标了 @JavascriptInterface（不标＝JS 调不到，还不报错）')
    bridge = re.search(r'BRIDGE_NAME\s*=\s*"([^"]+)"', main)
    check(bridge is not None, 'Java 里定义了注入给网页的对象名')
    # 两边的名字要**互相**对得上。别用 `f'window.{name}' in PAGE` 这种写法：
    # 那是子串匹配，把一边改名成 `imgsnagx`，另一边的 `window.imgsnag` 照样"在"。
    # （反向验证把这条抓出来过一次。）
    js_bridge = re.search(r'window\.([A-Za-z0-9_$]+)\s*&&\s*window\.\1\.read', webui.PAGE)
    check(js_bridge is not None, '页面确实在用那个桥（window.<名字>.read）')
    if bridge and js_bridge:
        eq(js_bridge.group(1), bridge.group(1),
           '注入名与页面里读的名字一致（不一致＝按钮永远说读不到，且一点报错都没有）')
    check('@JavascriptInterface' in main, '桥方法标了注解（不标就不暴露给 JS）')
    check('addJavascriptInterface' in main, '确实注入了桥')
    check('getHtmlText' in main, '连剪贴板里的 HTML 一起取（浏览器复制的正文在里面）')
    # 「复制标题」得真的写进剪贴板。只查 `copy(String` 不够 —— 空实现照样有那个签名。
    check('setPrimaryClip' in main, '「复制标题」真的写进了系统剪贴板')
    check('ClipData.newPlainText' in main, '写的是纯文本（不要富文本，粘到别处会带一堆格式）')

    # ⑧ 手写的文件不能被同步覆盖
    planned = [rel for rel, _src, _d in sync_core.planned()]
    check('imgsnag_web.py' not in planned, 'imgsnag_web.py 是手写的，不进同步清单')
    check('webui.py' not in planned, 'webui.py 是手写的，不进同步清单')
    check('imgsnag_android.py' not in planned, 'imgsnag_android.py 是手写的（沿用原约定）')
    check('web_image_dl/history_manager.py' in planned, '历史模块是同步来的（别手改副本）')
    # diff() 返回的是 (缺失, 内容漂移, 多余) 三组，不是平铺的一层列表 ——
    # 直接拿它跟 [] 比会永远不相等（这个错法在本项目犯过不止一次）。
    missing, stale, extra = sync_core.diff()
    check(not missing and not stale and not extra,
          f'副本与主项目一致（不一致就跑 sync_core.py）：'
          f'缺 {missing} / 漂移 {stale} / 多余 {extra}')


def test_isolation():
    print('[11] 不许碰用户数据')
    src = read(WEB_PY)
    check('from web_image_dl.history_manager import HistoryManager' in src,
          '用的是类，不是全局单例')
    check('import history_manager' not in src, '没有引用全局单例（否则会往用户主目录建库）')
    check('HistoryManager(db_path=os.path.join(self.root, HISTORY_DB))' in src,
          '历史库建在会话根目录下（测试与安卓都指向临时/应用目录）')

    # 子进程换个"家目录"，确认"导入 history_manager"不再产生任何文件
    tmp = tempfile.mkdtemp()
    try:
        code = (
            "import os, sys\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "import web_image_dl.history_manager as hm\n"
            "d = os.path.join(os.path.expanduser('~'), '.imgsnag_wechat')\n"
            "print('DIR' if os.path.isdir(d) else 'NONE')\n"
            "hm.history_manager\n"
            "print('DIR' if os.path.isdir(d) else 'NONE')\n"
        )
        env = dict(os.environ, USERPROFILE=tmp, HOME=tmp, PYTHONIOENCODING='utf-8')
        r = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True,
                           text=True, timeout=90)
        eq((r.stdout or '').split()[:2], ['NONE', 'DIR'],
           '导入时不建库；真正用到单例时才建（测试不该碰用户数据）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    for fn in (test_helpers, test_full_chain, test_share_push, test_dedup_and_partial,
               test_partial_download, test_errors, test_net_failure_falls_back,
               test_album_failure, test_inspect_tiny, test_quality_toggle,
               test_settings, test_pick_source, test_update_check, test_update_download,
               test_page_contract,
               test_java_contract, test_isolation):
        try:
            fn()
        except Exception:                                       # noqa: BLE001
            import traceback
            _failed.append(f'{fn.__name__} 抛异常')
            traceback.print_exc()
        finally:
            try:
                web.stop()
            except Exception:                                   # noqa: BLE001
                pass
    print('\n' + '=' * 46)
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    for name in _failed:
        print(f'  ✗ {name}')
    if not _failed:
        print('全部通过')
    return 1 if _failed else 0


if __name__ == '__main__':
    sys.exit(main())
