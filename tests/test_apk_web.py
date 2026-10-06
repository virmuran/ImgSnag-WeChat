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

TITLE = '秋天的第一杯奶茶'


def article(pics=(PIC_A, PIC_B)) -> str:
    body = ''.join(f'<img data-src="{u}">' for u in pics)
    return ('<html><head><meta property="og:title" content="' + TITLE + '"></head>'
            f'<body><script>var msg_title = "{TITLE}";</script>{body}</body></html>')


class FakeGet:
    """假网络：区分原图档 / 压缩档，并记下每个请求。"""

    def __init__(self, pages=None, full=None, thumb=None):
        self.pages = dict(pages or {})
        self.full = full or FULL_A
        self.thumb = thumb or THUMB_A
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url in self.pages:
            return android._Response(200, self.pages[url].encode('utf-8'))
        if 'mmbiz.qpic.cn' in url:
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
    port = web.start(root=tmp, version='9.9.9', get=fake, publish=album, **kw)
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
        cli, fake, album = _boot(tmp)

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
        eq(fake.hits('mmbiz.qpic.cn'), 0, '**解析阶段一张图都不下**（缩略图按需加载）')
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
#  三、静态契约（跨语言、跨文件的对应关系）
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


def test_java_contract():
    print('[10] 与 Java 的握手点（静态比对）')
    gallery = read(os.path.join(JAVA_DIR, 'Gallery.java'))
    main = read(os.path.join(JAVA_DIR, 'MainActivity.java'))
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

    # ⑥ 主路还在：分享意图 + 回环明文放行
    man = read(os.path.join(SRC_MAIN, 'AndroidManifest.xml'))
    check('android.intent.action.SEND' in man, '清单里仍然接收分享')
    check('android:networkSecurityConfig' in man, '清单里指向了网络安全配置')
    nsc = os.path.join(SRC_MAIN, 'res', 'xml', 'network_security_config.xml')
    check(os.path.exists(nsc), '网络安全配置文件存在')
    if os.path.exists(nsc):
        body = read(nsc)
        check('127.0.0.1' in body, '放行的是回环地址')
        check('cleartextTrafficPermitted="true"' in body, '明确放行明文')
        eq(len(re.findall(r'<domain[ >]', body)), 1,
           '只放行一个域，不是全局开明文')

    # ⑦ 手写的文件不能被同步覆盖
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
               test_album_failure, test_page_contract, test_java_contract,
               test_isolation):
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
