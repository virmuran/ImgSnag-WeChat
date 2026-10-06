# -*- coding: utf-8 -*-
"""手机版（Termux）主程序回归测试 —— 纯逻辑 + 假网络，不联网、不碰用户目录

运行：
    .venv/Scripts/python.exe tests/test_imgsnag.py

为什么值得单独一个文件
----------------------
手机版**没有重写解析逻辑**，它把项目根的 `web_image_dl/` 那几个纯逻辑模块
拷了一份到 `android/app/` 下。这个设计的好处是两边规则永远一致；
坏处是把风险从"功能写错"换成了两种**很难被用户描述清楚**的故障：

一、副本漂移
    有人在电脑上改了 `sites/weixin.py`（比如微信又改版了），忘了跑同步脚本。
    电脑版照常工作，手机版还在用旧规则 —— 用户只会说"手机上抓不到图了"。
    所以第一条测试就是**逐字比对副本与源文件**，忘了同步当场变红。

二、只在手机上才暴露的接缝
    抓页面 → 提图 → 按画质档位下载 → 去重 → 落盘，这条链路的每一段在电脑上
    都由 Qt 线程池承担，跟手机版实现完全不同。只测几个纯函数是不够的
    （比如 extract_url 全绿，但下载时把 Referer 丢了照样失败），
    所以这里注入假 requests，把**整条链路真跑一遍**，落盘到临时目录再回读。

三、命令行与分享入口的约定
    Termux 分享过来的是整段文本而不是干净 URL；`--html` 是链接打不开时的退路。
    这些是"用户唯一的输入通道"，判错了就等于工具不可用。
"""
import io
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID = os.path.join(ROOT, 'android')
APP_DIR = os.path.join(ANDROID, 'app')

sys.path.insert(0, ANDROID)
sys.path.insert(0, APP_DIR)

import sync_core                                                          # noqa: E402
import imgsnag                                                            # noqa: E402

# ── 测试用的文章与图片 ──
MMBIZ_A = 'https://mmbiz.qpic.cn/mmbiz_jpg/AAAAAAAABBBBCCCC/640?wx_fmt=jpeg'
MMBIZ_B = 'https://mmbiz.qpic.cn/mmbiz_png/DDDDDDDDEEEEFFFF/640?wx_fmt=png'
ARTICLE = (
    '<html><head><meta property="og:title" content="秋天的第一杯奶茶"></head>'
    '<body>'
    'var msg_title = "秋天的第一杯奶茶";'
    '<img data-src="' + MMBIZ_A + '">'
    '<img data-src="' + MMBIZ_B + '">'
    '</body></html>'
)
JPEG = b'\xff\xd8\xff\xe0' + b'J' * 800          # > MIN_IMAGE_BYTES，够假
PNG = b'\x89PNG\r\n\x1a\n' + b'P' * 900
ARTICLE_URL = 'https://mp.weixin.qq.com/s/abcdefg'

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  ✗ {label}')


def quiet(*_a, **_k):
    pass


class FakeResp:
    def __init__(self, content=b'', text='', code=200):
        self.content = content
        self.text = text
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


def make_fake_get(html=ARTICLE, images=None, calls=None):
    """假 requests。images 是 {地址片段: 字节}，命中才返回成功。

    calls 传一个 list 的话，会把每次请求的地址记进去 —— 用于断言
    「下载的到底是哪一档」（原图 /0 还是压缩 /640）。
    """
    images = images if images is not None else {'AAAAAAAABBBBCCCC': JPEG,
                                                'DDDDDDDDEEEEFFFF': PNG}

    def _get(url, headers=None, timeout=None, allow_redirects=None, **kw):
        if calls is not None:
            calls.append(url)
        if url.startswith('https://mp.weixin.qq.com/'):
            return FakeResp(text=html)
        for key, data in images.items():
            if key in url:
                return FakeResp(content=data)
        return FakeResp(code=404)

    return _get


# ============================================================================
#  一、副本与源必须逐字一致
# ============================================================================

def test_sync():
    print('[1] 手机端副本与主项目同步')
    missing, stale, extra = sync_core.diff()
    check(not missing, f'没有缺失文件（缺: {missing}）')
    check(not stale, f'没有内容漂移（漂移: {stale}）')
    check(not extra, f'副本里没有多余文件（多: {extra}）')

    for rel in sync_core.FILES:
        s = os.path.join(sync_core.SRC, rel)
        d = os.path.join(sync_core.DST, rel)
        check(os.path.exists(s), f'源文件存在: {rel}')
        check(os.path.exists(d), f'副本存在: {rel}')
        if os.path.exists(s) and os.path.exists(d):
            with open(s, 'rb') as f1, open(d, 'rb') as f2:
                check(f1.read() == f2.read(), f'逐字一致: {rel}')

    # web_image_dl 的包标识必须在副本里 —— 少了它 import 会退化成命名空间包，
    # 在电脑上碰巧还能跑（因为主项目也在 sys.path 上），到了手机上必炸
    check(os.path.exists(os.path.join(sync_core.DST, '__init__.py')),
          '副本里存在 web_image_dl/__init__.py')
    check(os.path.exists(os.path.join(sync_core.DST, 'sites', '__init__.py')),
          '副本里存在 web_image_dl/sites/__init__.py')

    # 手机端实际 import 到的应当是副本，而不是主项目那份
    check(os.path.abspath(imgsnag.__file__).startswith(os.path.abspath(APP_DIR)),
          'imgsnag 从 android/app 载入')
    import web_image_dl
    check(os.path.abspath(web_image_dl.__file__).startswith(os.path.abspath(APP_DIR)),
          '手机端 import 到的 web_image_dl 是副本')


# ============================================================================
#  二、输入解析（分享文本 / HTML / 裸链接）
# ============================================================================

def test_extract_url():
    print('[2] 从分享文本里抠链接')
    u = 'https://mp.weixin.qq.com/s/AbCdEf'
    check(imgsnag.extract_url(u) == u, '纯链接原样返回')
    check(imgsnag.extract_url(f'秋天的第一杯奶茶\n{u}') == u, '标题+换行+链接')
    check(imgsnag.extract_url(f'分享 | {u} 来自微信') == u, '链接夹在文字中间')
    check(imgsnag.extract_url(f'{u}。') == u, '剥离中文句号')
    check(imgsnag.extract_url(f'{u}.') == u, '剥离英文句点')
    check(imgsnag.extract_url(f'（{u}）') == u, '剥离中文括号')
    check(imgsnag.extract_url('这篇文章没有链接') == '', '无链接返回空串')
    check(imgsnag.extract_url('') == '', '空输入返回空串')
    check(imgsnag.extract_url(None) == '', 'None 也不炸')
    # 带查询串的地址不能被截断
    uq = 'https://mp.weixin.qq.com/s/AbCd?chksm=1a2b3c&scene=27#wechat_redirect'
    check(imgsnag.extract_url(f'标题\n{uq}') == uq, '保留查询串与锚点')


def test_resolve_source():
    print('[3] 判断给的是链接还是 HTML 源码')
    text, is_url = imgsnag.resolve_source('<html><body>x</body></html>')
    check(is_url is False and text.startswith('<html>'), '以 < 开头 → 当 HTML')

    text, is_url = imgsnag.resolve_source('  \n<html>')
    check(is_url is False, '容忍前导空白后再判 HTML')

    text, is_url = imgsnag.resolve_source('https://mp.weixin.qq.com/s/x')
    check(is_url is True and text == 'https://mp.weixin.qq.com/s/x', '纯链接 → 当网址')

    text, is_url = imgsnag.resolve_source('看这个 https://mp.weixin.qq.com/s/x 很好')
    check(is_url is True and text == 'https://mp.weixin.qq.com/s/x', '分享文本 → 抽出网址')

    text, is_url = imgsnag.resolve_source('mp.weixin.qq.com/s/x')
    check(is_url is True and text == 'mp.weixin.qq.com/s/x', '无协议头仍当网址（交给适配器补）')

    text, is_url = imgsnag.resolve_source('')
    check(is_url is True and text == '', '空输入不炸')


# ============================================================================
#  三、保存位置的选择
# ============================================================================

def test_out_dir():
    print('[4] 保存目录决策')
    old = os.environ.get('IMGSNAG_OUT')
    try:
        os.environ['IMGSNAG_OUT'] = '/tmp/mydir'
        check(imgsnag.default_out_dir() == '/tmp/mydir', '环境变量优先级最高')

        os.environ['IMGSNAG_OUT'] = '  '
        d = imgsnag.default_out_dir()
        check('ImgSnagWeChat' in d or d.endswith('imgsnag_downloads'),
              '空白环境变量被忽略，走默认')
    finally:
        if old is None:
            os.environ.pop('IMGSNAG_OUT', None)
        else:
            os.environ['IMGSNAG_OUT'] = old

    # 没有存储权限（本机就属于这种）时要退到 Termux 私有目录，不能报错
    d = imgsnag.default_out_dir()
    check(isinstance(d, str) and d, '默认目录永远是个非空字符串')
    check(imgsnag.shared_storage() == '' or os.path.isdir(imgsnag.shared_storage()),
          'shared_storage 只返回真实存在的目录')


def test_unique_path():
    print('[5] 落盘路径不覆盖')
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, 'img_01.jpg')
        check(imgsnag.unique_path(p) == p, '不存在时原样返回')
        open(p, 'wb').write(b'x')
        p2 = imgsnag.unique_path(p)
        check(p2.endswith('img_01_2.jpg'), f'已存在时加后缀（得到 {os.path.basename(p2)}）')
        open(p2, 'wb').write(b'x')
        check(imgsnag.unique_path(p).endswith('img_01_3.jpg'), '再加一个后缀')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_human_size():
    print('[6] 体积显示')
    check(imgsnag.human_size(512) == '512 B', 'B')
    check(imgsnag.human_size(2048) == '2.0 KB', 'KB')
    check(imgsnag.human_size(5 * 1048576) == '5.0 MB', 'MB')


# ============================================================================
#  四、去重
# ============================================================================

def test_dedup():
    print('[7] 按内容去重')
    a = (1, 'u1', b'AAAA' * 200, '.jpg')
    b = (2, 'u2', b'BBBB' * 200, '.jpg')
    a2 = (3, 'u3', b'AAAA' * 200, '.jpg')      # 与 a 内容相同
    out = imgsnag.dedup_by_content([a, b, a2])
    check(len(out) == 2, '重复内容只留一条')
    check([r[0] for r in out] == [1, 2], '保持原顺序、保留最先出现的')
    check(imgsnag.dedup_by_content([]) == [], '空列表不炸')
    out = imgsnag.dedup_by_content([a, b])
    check(len(out) == 2, '内容不同则都保留')


# ============================================================================
#  五、整条链路（假网络 + 真落盘）
# ============================================================================

def test_full_flow():
    print('[8] 完整链路：抓页面 → 提取 → 下载 → 落盘')
    tmp = tempfile.mkdtemp()
    try:
        calls = []
        res = imgsnag.snag(ARTICLE_URL, out_root=tmp, get=make_fake_get(calls=calls),
                           log=quiet, when=datetime(2026, 10, 5, 15, 55, 3))
        check(res.ok, '抓取成功')
        check(res.total == 2, f'解析出 2 张（得到 {res.total}）')
        check(len(res.saved) == 2, f'落盘 2 张（得到 {len(res.saved)}）')
        check(os.path.basename(res.folder) == '2026-10-05_155503_秋天的第一杯奶茶',
              f'目录名带日期与标题（得到 {os.path.basename(res.folder)}）')
        check(os.path.isdir(res.folder), '目录真的建出来了')
        names = sorted(os.path.basename(p) for p in res.saved)
        check(names == ['img_01.jpg', 'img_02.png'],
              f'文件名连号且扩展名跟地址走（得到 {names}）')
        check(all(os.path.getsize(p) > 500 for p in res.saved), '文件非空')

        # 下载的必须是 /0 原图档，而不是文章里的 /640
        dl = [u for u in calls if 'mmbiz.qpic.cn' in u]
        check(all(u.endswith('/0') for u in dl),
              f'下载走原图档 /0（实际: {[u.split("/")[-1] for u in dl]}）')
        check(any(u.startswith(ARTICLE_URL) for u in calls), '抓页面时带上了文章地址')

        # 标题取自 HTML
        check(res.title == '秋天的第一杯奶茶', f'标题提取正确（得到 {res.title!r}）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_fallback():
    print('[9] 原图档取不到 → 退回压缩档')
    tmp = tempfile.mkdtemp()
    try:
        calls = []
        # 只让"压缩档"能下到：地址里带 /640 的才给数据
        def _get(url, headers=None, timeout=None, allow_redirects=None, **kw):
            calls.append(url)
            if url.startswith('https://mp.weixin.qq.com/'):
                return FakeResp(text=ARTICLE)
            if url.endswith('/640') and 'AAAAAAAABBBBCCCC' in url:
                return FakeResp(content=JPEG)
            return FakeResp(code=404)

        res = imgsnag.snag(ARTICLE_URL, out_root=tmp, get=_get, log=quiet)
        check(len(res.saved) == 1, f'能退回压缩档，保住 1 张（得到 {len(res.saved)}）')
        tried = [u for u in calls if 'AAAAAAAABBBBCCCC' in u]
        check(any(u.endswith('/0') for u in tried) and any(u.endswith('/640') for u in tried),
              '先试原图档、失败后试压缩档')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dedup_in_flow():
    print('[10] 文章里同一张图出现两次 → 只存一张')
    tmp = tempfile.mkdtemp()
    try:
        html = ('<html><head><meta property="og:title" content="重复图"></head><body>'
                '<img data-src="' + MMBIZ_A + '">'
                '<img data-src="' + MMBIZ_A.replace('/mmbiz_jpg/', '/sz_mmbiz_jpg/') + '">'
                '</body></html>')
        res = imgsnag.snag(ARTICLE_URL, out_root=tmp,
                           get=make_fake_get(html=html), log=quiet)
        check(res.total == 1, f'同一个 FILEID 被合并成 1 张（得到 {res.total}）')
        check(len(res.saved) == 1, f'只落盘 1 张（得到 {len(res.saved)}）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_skipped_and_empty():
    print('[11] 下载失败 / 无图 的处理')
    tmp = tempfile.mkdtemp()
    try:
        # 所有图都下不到（404）
        res = imgsnag.snag(ARTICLE_URL, out_root=tmp,
                           get=make_fake_get(images={}), log=quiet)
        check(not res.ok, '全失败时 ok 为假')
        check(res.saved == [], '没有落盘文件')
        check(res.message, '给出了人类可读的原因')
        check(not os.path.exists(res.folder) or os.listdir(res.folder) == [],
              '一张都没成功时不留下空目录')

        # 页面里没有图
        res = imgsnag.snag(ARTICLE_URL, out_root=tmp,
                           get=make_fake_get(html='<html><body>没有图</body></html>'),
                           log=quiet)
        check(not res.ok and '没找到图片' in res.message, '无图时给出明确提示')

        # 页面打不开
        def _boom(url, **kw):
            raise RuntimeError('boom')
        res = imgsnag.snag(ARTICLE_URL, out_root=tmp, get=_boom, log=quiet)
        check('打开文章失败' in res.message, '页面请求异常时不崩，返回说明')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reject_unknown_site():
    print('[12] 不认识的链接要明确拒绝')
    tmp = tempfile.mkdtemp()
    try:
        res = imgsnag.snag('https://example.com/s/abc', out_root=tmp,
                           get=make_fake_get(), log=quiet)
        check(not res.ok, '非微信链接不下载')
        check('不认识' in res.message, f'提示里说明不支持（得到 {res.message!r}）')
        check('微信公众号' in res.message, '提示里列出支持的站点')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 子串骗局：域名里出现 mp.weixin.qq.com 但不是它的主机
    for bad in ['https://evil.com/mp.weixin.qq.com/x',
                'https://mp.weixin.qq.com.evil.com/x']:
        res = imgsnag.snag(bad, out_root=tempfile.gettempdir(), get=make_fake_get(), log=quiet)
        check(not res.ok, f'拒绝伪装域名 {bad}')


def test_dry_run():
    print('[13] --list 只列不落盘')
    tmp = tempfile.mkdtemp()
    try:
        res = imgsnag.snag(ARTICLE_URL, out_root=tmp, get=make_fake_get(),
                           dry_run=True, log=quiet)
        check(res.total == 2, '列出了 2 张')
        check(res.saved == [], '没有落盘')
        check(os.listdir(tmp) == [], '连目录都没建')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_html_source():
    print('[14] 直接从 HTML 源码解析（不走网络）')
    tmp = tempfile.mkdtemp()
    try:
        res = imgsnag.snag(ARTICLE, is_url=False, out_root=tmp,
                           get=make_fake_get(), log=quiet,
                           when=datetime(2026, 10, 5, 16, 0, 0))
        check(res.ok and len(res.saved) == 2, '从源码也能抓（拿到图靠的是假网络，页面本身不请求）')
        check('秋天的第一杯奶茶' in res.folder, '标题从源码里取到了')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_headers():
    print('[15] 请求头')
    h = imgsnag.build_headers(ARTICLE_URL)
    check('Referer' in h and h['Referer'] == ARTICLE_URL, '下载时带 Referer')
    check('User-Agent' in h, '带 User-Agent')
    check('Referer' not in imgsnag.build_headers(''), '没来源时不带 Referer')
    check('Referer' not in imgsnag.HEADERS, '模块级 HEADERS 不被污染')


def test_make_get_verify():
    print('[16] --insecure 开关')
    plain = imgsnag.make_get(False)
    check(plain.__name__ == 'get' or callable(plain), '默认返回可调用的 get')
    insecure = imgsnag.make_get(True)
    check(callable(insecure), 'insecure 版也是可调用的')
    check(insecure is not plain, '两者不是同一个对象')


# ============================================================================
#  六、命令行入口
# ============================================================================

def test_cli():
    print('[17] 命令行入口')
    # --version 由 argparse 直接 SystemExit(0)
    try:
        with redirect_stdout(io.StringIO()) as buf:
            imgsnag.main(['--version'])
        check(False, '--version 应当抛出 SystemExit')
    except SystemExit as e:
        check(e.code == 0, '--version 正常退出')
        check('ImgSnag Termux' in buf.getvalue(), '打印了程序名')

    tmp = tempfile.mkdtemp()
    try:
        html_path = os.path.join(tmp, 'article.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(ARTICLE)

        with redirect_stdout(io.StringIO()) as buf:
            rc = imgsnag.main(['--html', html_path, '--list'])
        check(rc == 0, f'--html + --list 返回 0（得到 {rc}）')
        check('mmbiz.qpic.cn' in buf.getvalue(), '列出了图片地址')
        check(os.listdir(tmp) == ['article.html'], '--list 不产生任何输出文件')

        # 不认识的链接：退出码应为 1，且不抛异常
        with redirect_stdout(io.StringIO()) as buf:
            rc = imgsnag.main(['https://example.com/x', '--out', tmp])
        check(rc == 1, f'不支持的链接返回 1（得到 {rc}）')
        check('不支持' in buf.getvalue() or '不认识' in buf.getvalue(), '提示里说明了不支持')

        # 文件不存在
        with redirect_stdout(io.StringIO()) as buf:
            rc = imgsnag.main(['--html', os.path.join(tmp, 'nope.html')])
        check(rc == 1, '文件不存在返回 1')
        check('读不到文件' in buf.getvalue(), '提示里说明读不到文件')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 没有参数、也读不到剪贴板时，打印帮助并返回 2
    old_clip = imgsnag.clipboard_text
    imgsnag.clipboard_text = lambda: ''
    try:
        with redirect_stdout(io.StringIO()) as buf:
            rc = imgsnag.main([])
        check(rc == 2, f'无输入返回 2（得到 {rc}）')
        check('usage' in buf.getvalue(), '打印了用法')
    finally:
        imgsnag.clipboard_text = old_clip


def test_image_source_with_clipboard():
    print('[18] 剪贴板里的分享文本')
    # 剪贴板给的是**整段分享文本**而不是干净 URL —— 这条链路正是 Termux
    # 「分享 → Termux」的真实输入形态。网络层换成假的，避免测试真的联网
    # （那会让结果依赖网络状况，是测试里最不该有的不确定性）。
    old_clip = imgsnag.clipboard_text
    old_get = imgsnag.make_get
    tmp = tempfile.mkdtemp()
    imgsnag.clipboard_text = lambda: f'秋天的第一杯奶茶\n{ARTICLE_URL}'
    imgsnag.make_get = lambda insecure=False: make_fake_get()
    try:
        with redirect_stdout(io.StringIO()) as buf:
            rc = imgsnag.main(['--out', tmp, '--list'])
        check(rc == 0, f'从剪贴板抽链接成功（得到 rc={rc}）')
        check('找到 2 张图' in buf.getvalue(),
              '链接真的被用去解析了（说明分享文本里的地址被抠出来了）')
        check('abcdefg' in buf.getvalue(), '解析的是抽出来的那个地址')
    finally:
        imgsnag.clipboard_text = old_clip
        imgsnag.make_get = old_get
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in [test_sync, test_extract_url, test_resolve_source, test_out_dir,
               test_unique_path, test_human_size, test_dedup, test_full_flow,
               test_quality_fallback, test_dedup_in_flow, test_skipped_and_empty,
               test_reject_unknown_site, test_dry_run, test_html_source,
               test_headers, test_make_get_verify, test_cli,
               test_image_source_with_clipboard]:
        fn()

    total = _passed + len(_failed)
    if _failed:
        print(f'\n✗ 失败 {len(_failed)} 项 / 共 {total} 项')
        for label in _failed:
            print(f'    - {label}')
        return 1
    print(f'\n✅ 通过 {_passed} 项 / 共 {total} 项，0 失败')
    return 0


if __name__ == '__main__':
    sys.exit(main())
