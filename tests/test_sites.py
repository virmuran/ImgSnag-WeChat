# -*- coding: utf-8 -*-
"""站点适配器接口 / 注册表 回归测试（纯逻辑，不联网）

运行：
    .venv/Scripts/python.exe tests/test_sites.py

为什么单独一个文件：2026-09-23 做了一次「划边界」重构 —— 把微信专属逻辑
从 worker / app / naming / extractor 里摘出来，收进 sites/weixin.py，
对外只留一个七方法的接口。**这个重构对用户是不可见的**，所以它最大的风险
不是"功能坏了"（那会被别的测试抓到），而是**边界根本没生效**：
接口写了、注册表建了，但实际调用仍然绕过适配器直接摸微信函数 ——
那样下次加站点时该改的地方一个都没少，等于白做。

因此这里钉的是「**结构承诺**」本身：

一、注册表能自由增删
   临时塞一个假适配器进去，get_adapter 必须立刻能识别它、supported_names 要带上它，
   移除后又不认识。这条过了，才说明「新增站点 = 加一个文件 + 加一行」不是空话。

二、URL 归属判定必须精确
   只认主机名等于 mp.weixin.qq.com。`https://evil.com/mp.weixin.qq.com/x`、
   `https://mp.weixin.qq.com.evil.com/x` 这类子串匹配会放行的地址必须拒绝 ——
   放行的后果是程序真的把请求发到那个域名去（v1.1.2 修过一次，接口化时不能再退化）。

三、HTML 与 URL 两条路要分开
   URL 能精确判定归属，认不出就该拒绝（get_adapter 返回 None）；
   一段 HTML 没有"归属声明"，只能按特征猜，猜不出要能退回默认（resolve_adapter）。
   两者混淆的后果：要么该拒的链接被放过，要么粘贴的正常源码被冤拒。

四、接口的默认实现真的存在
   一个结构简单的站点应该只写 matches + extract 就能跑，其余方法要有可用默认值。
   这里用一个最小子类逐个验证默认行为，免得将来往接口里加"必须实现"的方法
   把新站点作者卡住。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_image_dl.sites as sites_mod                                   # noqa: E402
from web_image_dl.sites import (                                         # noqa: E402
    ADAPTERS,
    SiteAdapter,
    get_adapter,
    resolve_adapter,
    supported_names,
)
from web_image_dl.sites.base import guess_ext_from_url                   # noqa: E402
from web_image_dl.sites.weixin import weixin                             # noqa: E402

MMBIZ = 'https://mmbiz.qpic.cn/mmbiz_jpg/AAAABBBBCCCCDDDD/640?wx_fmt=jpeg'
WX_HTML = (
    '<html><head><meta property="og:title" content="标题"></head><body>'
    'var msg_title = "标题";'
    '<img data-src="' + MMBIZ + '">'
    '</body></html>'
)

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  ✗ {label}')


def eq(got, want, label):
    check(got == want, f'{label}\n      得到: {got!r}\n      期望: {want!r}')


# ──────────────────────────────────────────────── 一、注册表本身
def test_registry_basics():
    check(len(ADAPTERS) >= 1, '注册表至少有一个站点')
    names = [a.name for a in ADAPTERS]
    eq(len(names), len(set(names)), f'站点标识不重复（{names}）')

    for a in ADAPTERS:
        # 这三个属性都要直接上界面/进目录名，空了会显出空白或建出怪目录
        check(bool(a.name), f'{type(a).__name__}.name 非空')
        check(bool(a.display_name), f'{type(a).__name__}.display_name 非空')
        check(bool(a.fallback_title), f'{type(a).__name__}.fallback_title 非空')
        # fallback_title 要能当文件夹名用（不能含路径分隔符之类）
        check(a.fallback_title.strip() == a.fallback_title, f'{a.name} 兜底名无首尾空白')
        check(not any(c in a.fallback_title for c in '\\/:*?"<>|'),
              f'{a.name} 兜底名可直接当文件夹名')

    check('微信公众号' in supported_names(), 'supported_names 含微信')
    eq(supported_names('+'), '+'.join(a.display_name for a in ADAPTERS),
       'supported_names 用指定分隔符拼接')


def test_registry_is_extensible():
    """核心结构承诺：加站点只需注册，不用改任何现有文件。"""
    class FakeAdapter(SiteAdapter):
        name = 'fake'
        display_name = '假站点'
        fallback_title = '假图'

        def matches(self, url):
            return 'fake.example' in url

        def extract(self, html, include_scripts=False):
            return ['https://fake.example/a.jpg']

        def sniff(self, html):
            return 9 if 'FAKEMARK' in html else 0

    fake = FakeAdapter()
    ADAPTERS.append(fake)
    try:
        check(get_adapter('https://fake.example/p/1') is fake,
              '新注册的站点立刻能被 URL 识别')
        check(get_adapter('<div>FAKEMARK</div>') is fake,
              '新注册的站点立刻能被 HTML 特征识别')
        check('假站点' in supported_names(), 'supported_names 立刻带上新站点')
        eq(fake.display_url('https://fake.example/p/1'), 'https://fake.example/p/1',
           '新站点不覆盖 display_url 时原样返回')
    finally:
        ADAPTERS.remove(fake)
    check(get_adapter('https://fake.example/p/1') is None, '移除后不再识别它')
    check(get_adapter('https://mp.weixin.qq.com/s/x') is weixin, '移除假站点后微信仍正常')


# ──────────────────────────────────────────────── 二、URL 归属
def test_url_matching():
    check(get_adapter('https://mp.weixin.qq.com/s/abc') is weixin, 'mp.weixin.qq.com 命中')
    check(get_adapter('http://mp.weixin.qq.com/s/abc') is weixin, 'http 也命中')
    check(get_adapter('MP.WEIXIN.QQ.COM/s/abc') is weixin, '裸域名 + 大写也命中')
    check(get_adapter('https://mp.weixin.qq.com:443/s/abc') is weixin, '带端口不影响')
    check(get_adapter('https://mp.weixin.qq.com/s/abc?x=1#frag') is weixin, '查询串与锚点不影响')

    eq(get_adapter('https://www.douyin.com/x'), None, '未支持的站点 → None（界面据此拒绝）')
    eq(get_adapter('https://example.com/a'), None, '普通站点 → None')
    eq(get_adapter(''), None, '空串 → None')
    eq(get_adapter(None), None, 'None → None')
    eq(get_adapter('   '), None, '纯空白 → None')

    # 子串匹配会误放行、把请求真的发到 evil.com 去（v1.1.2 修过，接口化后不许退化）
    for bad in ('https://evil.com/mp.weixin.qq.com/x',
                'https://mp.weixin.qq.com.evil.com/x',
                'https://notmp.weixin.qq.com.cn/x',
                'https://mp.weixin.qq.com.cn/x'):
        eq(get_adapter(bad), None, f'拒绝伪装域名 {bad}')


# ──────────────────────────────────────────────── 三、HTML 特征识别
def test_html_sniffing():
    check(get_adapter(WX_HTML) is weixin, '公众号原文 HTML → 识别为微信')
    check(get_adapter('<!DOCTYPE html><html><body>hello</body></html>') is None,
          '完全无关的 HTML → None（交给 resolve_adapter 兜底）')
    eq(get_adapter(''), None, '空 HTML → None')

    # 特征越多分越高（将来多站点时靠比大小取最像的那个）
    one = weixin.sniff('<img src="https://mmbiz.qpic.cn/x">')
    three = weixin.sniff(WX_HTML)
    check(one > 0, '单个特征也命中')
    check(three > one, f'特征越多分越高（1 个={one}，多个={three}）')
    eq(weixin.sniff(''), 0, '空 HTML 打 0 分')


# ──────────────────────────────────────────────── 四、两条路的不同兜底
def test_resolve_semantics():
    """get_adapter 认不出返回 None；resolve_adapter 认不出退回默认站点。"""
    check(get_adapter('https://unknown.site/x') is None, 'get_adapter 严格：认不出 → None')
    check(resolve_adapter('https://unknown.site/x') is ADAPTERS[0],
          'resolve_adapter 宽松：认不出 → 默认站点')
    check(resolve_adapter('<p>毫无特征的源码</p>') is ADAPTERS[0],
          '陌生 HTML 也能解析（单站点时代等价于"一律按微信处理"）')
    check(resolve_adapter('https://mp.weixin.qq.com/s/x') is weixin,
          '认得出时 resolve 与 get 结果一致')
    check(resolve_adapter('') is ADAPTERS[0],
          '空内容也退回默认 —— 后续流程会给出更贴切的「没找到图片」而不是「没有适配器」')


# ──────────────────────────────────────────────── 五、接口默认实现
def test_interface_defaults():
    """最小子类：只实现 matches + extract，其余必须开箱可用。"""
    class Minimal(SiteAdapter):
        name = 'minimal'
        display_name = '极简站点'
        fallback_title = '极简图'

        def matches(self, url):
            return url.startswith('https://minimal.example/')

        def extract(self, html, include_scripts=False):
            return []

    m = Minimal()
    eq(m.extract_title('<title>标题</title>'), '', '默认不解析标题（交给子类覆盖）')
    eq(m.candidates('https://minimal.example/a.jpg'), ['https://minimal.example/a.jpg'],
       '默认没有画质档位，原样返回一个候选')
    eq(m.candidates(''), [], '空地址没有候选')
    eq(m.display_url('https://minimal.example/a.jpg'), 'https://minimal.example/a.jpg',
       '默认显示地址不裁剪')
    eq(m.sniff('<html></html>'), 0, '默认不认任何 HTML 特征')
    eq(m.ext_for('https://minimal.example/a.png'), '.png', '默认按通用规则推断扩展名')
    check(isinstance(m, SiteAdapter), '子类可被注册表识别为适配器')

    # 未实现的两个方法必须明确报错，而不是静默返回空 ——
    # 静默返回空会让"忘记实现"表现成"这个站点的图抓不到"，极难排查
    base = SiteAdapter()
    for meth, args, label in (('matches', ('https://x/',), 'matches'),
                              ('extract', ('<html></html>',), 'extract')):
        try:
            getattr(base, meth)(*args)
            check(False, f'基类的 {label} 未实现时应抛 NotImplementedError')
        except NotImplementedError:
            check(True, f'基类的 {label} 未实现时抛 NotImplementedError')


# ──────────────────────────────────────────────── 六、扩展名推断
def test_guess_ext():
    eq(guess_ext_from_url('https://x/a.jpg'), '.jpg', '普通 .jpg')
    eq(guess_ext_from_url('https://mmbiz.qpic.cn/mmbiz_png/ID/0?wx_fmt=png'), '.png',
       '路径含 png 字样的 CDN 地址')
    eq(guess_ext_from_url('https://x/a?fmt=png'), '.png', '查询串声明 png')
    eq(guess_ext_from_url('https://x/a.webp'), '.webp', '.webp')
    eq(guess_ext_from_url('https://x/a.gif'), '.gif', '.gif')
    eq(guess_ext_from_url('https://x/noext'), '.jpg', '推不出 → 默认 .jpg')
    eq(guess_ext_from_url(''), '.jpg', '空串 → 默认')
    eq(guess_ext_from_url('https://x/a.png', default='.jpeg'), '.png',
       '有结论时不受 default 影响')
    eq(guess_ext_from_url('https://x/a', default='.jpeg'), '.jpeg', '无结论时才用 default')


# ──────────────────────────────────────────────── 七、微信适配器的对外行为
def test_weixin_adapter():
    eq(weixin.name, 'weixin', '站点标识')
    eq(weixin.display_name, '微信公众号', '界面名')
    eq(weixin.fallback_title, '微信图片', '兜底文件夹名')

    check(weixin.matches('https://mp.weixin.qq.com/s/abc'), 'matches 认公众号链接')
    check(not weixin.matches('https://www.douyin.com/x'), 'matches 拒绝其他站点')

    urls = weixin.extract(WX_HTML)
    eq(len(urls), 1, 'extract 提到正文图')
    check(urls[0].endswith('/0'), f'extract 给的是原图档（{urls[0][-24:]}）')

    eq(weixin.extract_title(WX_HTML), '标题', 'extract_title 取到 og:title')

    cands = weixin.candidates(MMBIZ)
    eq(len(cands), 2, '微信有两个画质档位')
    check(cands[0].endswith('/0'), '首选是原图档 /0')
    check(cands[1].endswith('/640'), '兜底是压缩档 /640')
    eq(weixin.candidates('https://example.com/a.jpg'), ['https://example.com/a.jpg'],
       '非 mmbiz 地址没有档位，原样返回')
    eq(weixin.candidates(''), [], '空地址返回空候选')

    eq(weixin.display_url('https://mp.weixin.qq.com/s/AAA111'), 'AAA111', '剥掉冗长前缀')
    eq(weixin.display_url('http://mp.weixin.qq.com/s/AAA111'), 'AAA111', 'http 前缀也剥')
    eq(weixin.display_url('https://other.site/x'), 'https://other.site/x', '其他域名不动')
    eq(weixin.display_url(''), '', '空串安全')

    # 扩展名必须从**路径段**读得出来。真实下载用的是被剥掉查询串的 `/0` 档地址，
    # 早先这里只测了带 `?wx_fmt=png` 的那种 —— 恰好是能判对的一侧，
    # 于是「png / gif 图被静默存成 .jpg」一直没被抓到（手机版整链路测试才发现）。
    eq(weixin.ext_for('https://mmbiz.qpic.cn/mmbiz_png/ID/0'), '.png',
       '剥掉查询串的原图档地址仍能判出 png')
    eq(weixin.ext_for('https://mmbiz.qpic.cn/mmbiz_png/ID/640?wx_fmt=png'), '.png',
       '带查询串的地址也判对')
    eq(weixin.ext_for('https://mmbiz.qpic.cn/sz_mmbiz_gif/ID/0'), '.gif',
       'sz_ 前缀 + gif')
    eq(weixin.ext_for('https://mmbiz.qpic.cn/sz_mmbiz_webp/ID/0'), '.webp', 'webp')
    eq(weixin.ext_for('https://mmbiz.qpic.cn/mmbiz_jpg/ID/0'), '.jpg', 'jpg 仍是 .jpg')
    eq(weixin.ext_for('https://mmbiz.qpic.cn/mmbiz_jpeg/ID/0'), '.jpg',
       'jpeg 归一成 .jpg')
    eq(weixin.ext_for('https://example.com/a.png'), '.png',
       '非微信地址退回通用规则')
    eq(weixin.ext_for('https://example.com/noext'), '.jpg', '通用规则兜底')


# ──────────────────────────────────────────────── 八、worker 的接入契约
def test_worker_wiring():
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None

    from web_image_dl.worker import FetchWorker

    url = 'https://mp.weixin.qq.com/s/AAA111'
    w = FetchWorker(url)
    check(w.adapter is weixin, 'URL 来源：worker 自动挑了微信适配器')
    check(w._candidate_urls(MMBIZ)[0].endswith('/0'), '偏好原图时首选 /0')
    check(w._candidate_urls(MMBIZ)[-1].endswith('/640'), '偏好原图时兜底 /640')

    w2 = FetchWorker(MMBIZ, prefer_original=False)
    check(w2._candidate_urls(MMBIZ)[0].endswith('/640'), '关掉"原图优先"后首选 /640')
    check(w2._candidate_urls(MMBIZ)[-1].endswith('/0'), '关掉后原图退为兜底')

    check(FetchWorker(WX_HTML, is_url=False).adapter is weixin,
          'HTML 来源：worker 按特征挑到微信')

    # 允许显式注入替身（测试与将来"由界面指定站点"都靠它）
    class Stub(SiteAdapter):
        name = 'stub'
        display_name = '替身'
        fallback_title = '替身图'

        def matches(self, url):
            return False

        def extract(self, html, include_scripts=False):
            return []

        def candidates(self, url):
            return [url + '?stub=1']

    stub = Stub()
    w3 = FetchWorker(url, adapter=stub)
    check(w3.adapter is stub, '显式注入的适配器优先于自动挑选')
    eq(w3._candidate_urls('https://x/a.jpg'), ['https://x/a.jpg?stub=1'],
       '下载候选真的走了注入的适配器')


def test_worker_runs_through_adapter():
    """worker 的下载主链路真的由适配器驱动（而不是"接口写了但没人用"）。

    重构最大的风险是边界形同虚设：接口写了、注册表建了，可实际跑到下载时
    仍然写死站点假设 —— 那样下次加站点，该改的地方一个都没少。
    所以这里不只断言 `_candidate_urls`，而是**把 run() 真的跑起来**，
    配一个行为刻意的替身适配器（首选地址与传入的不同、扩展名给一个猜不出来的值），
    worker 里只要有一处绕过适配器自行判断，下面就会挂。
    """
    from unittest import mock

    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage

    def make_png(w=320, h=200):
        """造一张真 PNG，而且**必须大于 worker 的 MIN_IMAGE_BYTES(500)** ——
        纯色图压缩后只有三百来字节，会被当成"错误页占位图"直接跳过，
        测试看起来就像"适配器没生效"，实际只是图太小。所以填随机噪声。"""
        img = QImage(w, h, QImage.Format_RGB32)
        mv = img.bits()
        mv[:] = os.urandom(len(mv))
        buf = QBuffer()
        buf.open(QBuffer.ReadWrite)
        assert img.save(buf, 'PNG'), '测试用的 PNG 造不出来'
        data = bytes(buf.data())
        assert len(data) > 500, f'测试图必须超过无效图阈值，实际 {len(data)} 字节'
        return data

    class R:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    class Stub(SiteAdapter):
        name = 'stub-run'
        display_name = '替身'
        fallback_title = '替身图'

        def matches(self, url):
            return True

        def extract(self, html, include_scripts=False):
            return ['https://stub.example/a.img', 'https://stub.example/b.png']

        def candidates(self, url):
            return [url + '?best=1']     # 首选地址与传入的不同，一眼看出谁说了算

        def ext_for(self, url):
            return '.stub'               # 任何"自己猜扩展名"的实现都猜不出这个值

    png = make_png()
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return R(png)

    from web_image_dl.blocked_config import blocked_config
    from web_image_dl.worker import FetchWorker

    got = []
    with mock.patch('web_image_dl.worker.requests.get', side_effect=fake_get), \
            mock.patch.object(blocked_config, 'add_hits', lambda *a, **k: None):
        w = FetchWorker('<html>stub</html>', is_url=False, adapter=Stub())
        w.all_done.connect(lambda imgs: got.extend(imgs))
        w.run()

    eq(len(got), 2, '两张图都下下来了')
    eq(sorted(seen), ['https://stub.example/a.img?best=1',
                      'https://stub.example/b.png?best=1'],
       '实际下载的地址来自适配器的 candidates')
    eq(sorted(i.ext for i in got), ['.stub', '.stub'],
       '扩展名来自适配器的 ext_for（写死 .jpg 的实现在这里必挂）')
    eq(got[0].width, 320, '尺寸仍从下载到的数据里读（通用链路没被改坏）')


def main():
    test_registry_basics()
    test_registry_is_extensible()
    test_url_matching()
    test_html_sniffing()
    test_resolve_semantics()
    test_interface_defaults()
    test_guess_ext()
    test_weixin_adapter()
    test_worker_wiring()
    test_worker_runs_through_adapter()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
