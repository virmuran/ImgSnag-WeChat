# -*- coding: utf-8 -*-
"""提取器与画质策略回归测试

运行：
    .venv/Scripts/python.exe tests/test_extractor.py

覆盖的锚点与来历：

一、原图地址改写（extractors.to_original_url / to_compressed_url）
   微信正文图地址形如 .../mmbiz_jpg/<图片ID>/640?wx_fmt=jpeg&tp=webp&wxfrom=5&wx_lazy=1
   路径里的尺寸段是画质档位，`/0` 才是原图。2026-09-17 实测同一张图：
       640 档 1080x1423 / 101.6 KB   →   /0 档 1280x1687 / 128.8 KB（1.41x 像素）
   改造前旧版直接下载 data-src，拿到的是 640 档，这就是"只有几百 KB"的根因。
   同时必须剥掉 tp=webp —— 它强制转有损图，实测 29.9 KB vs 原图 120.7 KB（4 倍）。

二、绝不误伤非 mmbiz 地址
   正则只认 mmbiz.qpic.cn/(sz_)mmbiz_xxx/<ID> 结构，其他域名原样返回。

三、下载回退
   原图取不到（404/超时/返回过小）时必须自动退到 640 档，而不是整张失败。

四、提取结果的既有行为不许回退
   头像排除、CDN 变体合并（同一 FILEID 的 mmbiz_jpg / sz_mmbiz_jpg / 不同尺寸算一张）、
   picture_page_info_list 无水印原图、Script 补充扫描，改造后仍须全部成立。

五、输出顺序 = 正文顺序（v1.1.1 修复）
   旧实现最后一步是 sorted(urls)，而 mmbiz 地址前缀高度雷同，字母序会把正文顺序打乱——
   实测一篇 8 图文章被排成 8,3,4,5,6,1,7,2，导致 img_01.jpg 不是正文第一张。
   现改为按地址在 HTML 中首次出现的位置排序，这里用「字母序与正文序相反」的构造钉住回归。

六、同名文件不覆盖（v1.1.1 修复）
   旧实现在同名时只补一次 _count 后缀且不校验占用，同一目录下载第三次会覆盖 img_01_0.jpg。

七、链接校验只认主机名（v1.1.2 修复）
   旧实现 `WECHAT_HOST in url` 是子串匹配：`https://evil.com/mp.weixin.qq.com/x`、
   `https://mp.weixin.qq.com.evil.com/x` 这类地址全被放行，程序会真的去请求它。
   改为 urlparse 后比对 hostname。

八、保存链路与取消（v1.1.2）
   保存原本在 UI 线程里同步跑（读文件 + QImage 转码 + 写盘），图一多界面就冻住、
   进度条也不动，现挪到 SaveWorker 线程。这里钉住四件事：
     · 原格式保存零重编码（字节原样落盘，不损失画质）
     · 转码是真转码（PNG→JPG 后文件头必须是 JPEG），失败则回退原格式
     · 回退时不留 0 字节垃圾文件，也不丢图
     · 整批中单张失败只记账，不拖垮其余
   解析支持中途取消：已下载的部分照常交付，不发「完成」也不弹错误。
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_image_dl.extractor import (
    clean_url,
    extract_image_urls,
    extract_title,
    is_wechat_url,
    to_compressed_url,
    to_original_url,
)
from web_image_dl.file_utils import unique_path
from web_image_dl.worker import FetchWorker
from web_image_dl.save_worker import SaveWorker, write_image

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication
from PySide6.QtGui import QImage

ID_A = 'ibPmoa8nWl5JwSUWB6Fet9ZiaXEq7yyiajSX5xzsmr0kGVprH0zAwR9dqbSXoWNkd2aAAwEuXPBpiaj7fevvGbRBcg'
ID_B = 'j2kibbLtVMmd2oa0HKwrOdAhHlLS1hCnnE1uLgJLpfMT7AXDEjDpWtfX2vpmfpAhJvQCqbFdKhfAvwbLQCrroia3Hq1gbH2Y8hwGaFfydLXwk'
HEAD_A = f'https://mmbiz.qpic.cn/mmbiz_jpg/{ID_A}'
HEAD_B = f'https://mmbiz.qpic.cn/sz_mmbiz_jpg/{ID_B}'

# 专供顺序测试：字母序（aaa… < zzz…）与正文顺序（zzz 在前）刻意相反
ID_FIRST = 'zzzZZZ0000000000000000000000000000000000000000000000000000000000'
ID_SECOND = 'aaaAAA0000000000000000000000000000000000000000000000000000000000'
ID_MID = 'mmmMMM0000000000000000000000000000000000000000000000000000000000'
HEAD_FIRST = f'https://mmbiz.qpic.cn/mmbiz_jpg/{ID_FIRST}'
HEAD_SECOND = f'https://mmbiz.qpic.cn/mmbiz_jpg/{ID_SECOND}'
HEAD_MID = f'https://mmbiz.qpic.cn/mmbiz_jpg/{ID_MID}'


def ids_of(urls):
    """从 mmbiz 地址取出图片 ID（即倒数第二段）"""
    return [u.split('/')[-2] for u in urls]

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
    check(got == want, f'{label}\n      得到: {got}\n      期望: {want}')


# ──────────────────────────────────────────────────────────── 一、地址改写

def test_url_rewrite():
    print('\n[1] 原图地址改写')

    eq(to_original_url(f'{HEAD_A}/640?wx_fmt=jpeg&tp=webp&wxfrom=5&wx_lazy=1&wx_co=1'),
       f'{HEAD_A}/0', '640 + 噪声参数 → /0 且参数被剥净')

    eq(to_original_url(f'{HEAD_A}/0?wx_fmt=jpeg&from=appmsg'),
       f'{HEAD_A}/0', '已是 /0 → 保持 /0')

    eq(to_original_url(f'{HEAD_A}?wx_fmt=jpeg'),
       f'{HEAD_A}/0', '无尺寸段 → 补 /0')

    eq(to_original_url(f'{HEAD_A}/1080'),
       f'{HEAD_A}/0', '1080 档 → /0')

    eq(to_original_url(f'https://mmbiz.qpic.cn/sz_mmbiz_png/{ID_B}/640?wx_fmt=png'),
       f'https://mmbiz.qpic.cn/sz_mmbiz_png/{ID_B}/0', 'sz_ 前缀同样处理')

    eq(to_original_url(f'https://mmbiz.qpic.cn/sz_mmbiz_gif/{ID_B}/640?wx_fmt=gif&from=appmsg'),
       f'https://mmbiz.qpic.cn/sz_mmbiz_gif/{ID_B}/0', '动图 gif 同样处理')

    eq(to_compressed_url(f'{HEAD_A}/0'), f'{HEAD_A}/640', '回退地址 → /640')
    eq(to_compressed_url(f'{HEAD_A}'), f'{HEAD_A}/640', '无尺寸段 → 回退地址补 /640')

    print('\n[2] 不误伤非 mmbiz 地址')
    for u in ['https://example.com/a/640?x=1',
              'https://mmbiz.qlogo.cn/mmbiz/abc/640',
              'https://mmbiz.qpic.cn/mmbiz_png',
              '',
              'not a url']:
        eq(to_original_url(u), u, f'原样返回: {u[:40]}')

    check(is_wechat_url('https://mp.weixin.qq.com/s/abc'), '识别公众号链接')
    check(not is_wechat_url('https://www.douyin.com/x'), '抖音链接不算公众号')


# ──────────────────────────────────────────────────────── 三、提取结果

HTML_BASIC = f'''<html><body>
<img data-src="{HEAD_A}/640?wx_fmt=jpeg&tp=webp&wxfrom=5&wx_lazy=1">
<img src="{HEAD_B}/0?wx_fmt=jpeg">
<img data-src="https://mmbiz.qpic.cn/mmbiz_png/XYZ789abcDEfghijklmnopqrstuvwxyz0123456789/640?wx_fmt=png">
<script>var round_head_img = 'https://mmbiz.qpic.cn/mmbiz_png/AVATAR00000000000000000000000000000000000000000000000000000/0?wx_fmt=png';</script>
</body></html>'''


def test_extract():
    print('\n[3] 提取结果：全部输出原图地址')
    urls = extract_image_urls(HTML_BASIC)
    eq(len(urls), 3, '3 张正文图（头像已排除）')
    for u in urls:
        check(u.endswith('/0'), f'输出为原图地址: ...{u[-24:]}')
        check('tp=webp' not in u and 'wxfrom' not in u, f'噪声参数已剥离: ...{u[-24:]}')

    print('\n[4] CDN 变体合并：同一 FILEID 多个档位算一张')
    html = (f'<img data-src="{HEAD_A}/640?wx_fmt=jpeg">'
            f'<img data-src="{HEAD_A}/0?wx_fmt=png">'
            f'<img data-src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/{ID_A}/1080?wx_fmt=jpeg">')
    eq(len(extract_image_urls(html)), 1, 'jpeg/png/sz_ 三个变体 → 1 张')

    print('\n[5] picture_page_info_list：取顶层无水印版，跳过 watermark_info 水印版')
    # 真实结构是单引号 JS 字面量，且两条 cdn_url 是两个不同 FILEID
    html = ('<script>var picture_page_info_list:[{'
            f"cdn_url: '{HEAD_A}/640?wx_fmt=png\\x26amp;amp;from=appmsg',"
            'width: \'640\' * 1,'
            f"watermark_info: {{cdn_url: 'http://mmbiz.qpic.cn/mmbiz_png/WATERMARK{'X' * 60}/0?wx_fmt=png'}}"
            '}]</script>')
    urls = extract_image_urls(html)
    eq(len(urls), 1, 'JS 变量结构可提取')
    eq(urls[0], f'{HEAD_A}/0', '取顶层（无水印）而非 watermark_info')
    check('\\x26' not in urls[0] and 'amp;' not in urls[0], '转义残留已清洗')
    check(not any('WATERMARK' in u for u in urls), '水印版地址未被混入')

    print('\n[5b] 双引号写法同样兼容（防微信改写法后静默失效）')
    html = ('<script>picture_page_info_list: [{"cdn_url": '
            f'"{HEAD_B}/640?wx_fmt=jpeg"}}]</script>')
    urls = extract_image_urls(html)
    eq(len(urls), 1, '双引号 JSON 风格可提取')
    eq(urls[0], f'{HEAD_B}/0', '双引号地址同样提升为原图')

    print('\n[6] 作者头像排除')
    html = (f"<script>var round_head_img = '{HEAD_A}/0?wx_fmt=png';</script>"
            f'<img data-src="{HEAD_B}/640?wx_fmt=jpeg">')
    urls = extract_image_urls(html)
    eq(len(urls), 1, 'round_head_img 不算正文图')

    print('\n[7] 补充 Script 扫描（默认关闭，勾选才扫）')
    html = f'<script>"cover_img":"{HEAD_A}/0?wx_fmt=jpeg"</script>'
    eq(len(extract_image_urls(html)), 0, '默认不扫 script 内的图')
    eq(len(extract_image_urls(html, include_scripts=True)), 1, '勾选后扫到')

    print('\n[8] 转义残留与锚点清洗')
    eq(clean_url(f'{HEAD_A}/640?wx_fmt=gif\\x26amp;amp;from=appmsg'),
       f'{HEAD_A}/640?wx_fmt=gif&amp;from=appmsg', '\\x26 → &')
    eq(clean_url(f'{HEAD_A}/640?wx_fmt=jpeg#imgIndex=4'),
       f'{HEAD_A}/640?wx_fmt=jpeg', '去掉 #imgIndex 锚点')
    eq(clean_url('&amp;&quot;'), '&"', 'HTML 实体还原')
    eq(to_original_url(clean_url(f'{HEAD_A}/640?wx_fmt=gif\\x26amp;amp;from=appmsg')),
       f'{HEAD_A}/0', '清洗后仍能正确提升为原图')


# ──────────────────────────────────────────────────────── 四、下载回退

def test_candidates():
    print('\n[9] 下载候选顺序')
    w = FetchWorker('x', is_url=False, prefer_original=True)
    eq(w._candidate_urls(f'{HEAD_A}/0'), [f'{HEAD_A}/0', f'{HEAD_A}/640'], '默认原图优先')
    w2 = FetchWorker('x', is_url=False, prefer_original=False)
    eq(w2._candidate_urls(f'{HEAD_A}/0'), [f'{HEAD_A}/640', f'{HEAD_A}/0'], '取消勾选则压缩版优先')
    w3 = FetchWorker('x', is_url=False)
    eq(w3._candidate_urls('https://example.com/a.jpg'), ['https://example.com/a.jpg'],
       '非 mmbiz 地址无变体可切')


def test_fallback_download():
    print('\n[10] 原图失败自动回退压缩版')
    w = FetchWorker('x', is_url=False, prefer_original=True)

    class R:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        if url.endswith('/0'):
            raise OSError('模拟原图 404')
        return R(b'\xff\xd8\xff' + b'x' * 2000)

    with mock.patch('web_image_dl.worker.requests.get', side_effect=fake_get):
        used, data = w._download_one(w._candidate_urls(f'{HEAD_A}/0'), {})
    eq(used, f'{HEAD_A}/640', '原图失败 → 用回退地址')
    check(data and len(data) > 500, '拿到回退数据')

    print('\n[11] 两个候选都失败则放弃该张（不抛异常）')
    with mock.patch('web_image_dl.worker.requests.get', side_effect=OSError('全挂')):
        used, data = w._download_one(w._candidate_urls(f'{HEAD_A}/0'), {})
    eq((used, data), (None, None), '返回 (None, None)')

    print('\n[12] 响应过小视为无效（微信错误页），改用下一候选')
    calls = []

    def tiny_then_ok(url, **kw):
        calls.append(url)
        return R(b'tiny') if url.endswith('/0') else R(b'\xff\xd8\xff' + b'y' * 3000)

    with mock.patch('web_image_dl.worker.requests.get', side_effect=tiny_then_ok):
        used, data = w._download_one(w._candidate_urls(f'{HEAD_A}/0'), {})
    eq(used, f'{HEAD_A}/640', '过小响应被跳过')
    eq(len(calls), 2, '两个候选都试过')


# ──────────────────────────────────────────────────── 五、输出顺序

def test_output_order():
    print('\n[13] 输出顺序 = 正文出现顺序（不再按字母序排）')
    # 字母序会把 aaa… 排在 zzz… 前面，而正文里 zzz… 在前 —— 专盯这个回归
    html = (f'<img data-src="{HEAD_FIRST}/640?wx_fmt=jpeg&tp=webp&wxfrom=5">'
            f'<img data-src="{HEAD_SECOND}/640?wx_fmt=jpeg">')
    urls = extract_image_urls(html)
    eq(len(urls), 2, '两张图')
    eq(ids_of(urls), [ID_FIRST, ID_SECOND], '输出顺序与正文一致（旧实现在此会反序）')

    print('\n[13b] data-src 与 img src 混合时统一按出现位置排序')
    html = (f'<img src="{HEAD_SECOND}/0?wx_fmt=jpeg">'
            f'<img data-src="{HEAD_FIRST}/640?wx_fmt=jpeg">')
    eq(ids_of(extract_image_urls(html)), [ID_SECOND, ID_FIRST], '两种来源按位置交错')

    print('\n[13c] picture_page_info_list 的数组顺序被保持')
    html = ('<script>var picture_page_info_list:['
            f"{{cdn_url: '{HEAD_FIRST}/640?wx_fmt=jpeg\\x26amp;amp;from=appmsg',"
            "width: '640' * 1},"
            f"{{cdn_url: '{HEAD_SECOND}/640?wx_fmt=jpeg'}}"
            ']</script>')
    urls = extract_image_urls(html)
    eq(ids_of(urls), [ID_FIRST, ID_SECOND], '数组第一个 → 输出第一张')

    print('\n[14] 变体合并后位置仍取最早一次出现（不会因换 jpeg 变体而搬家）')
    html = (f'<img data-src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/{ID_FIRST}/1080?wx_fmt=png">'
            f'<img data-src="{HEAD_FIRST}/640?wx_fmt=jpeg">'
            f'<img data-src="{HEAD_SECOND}/640?wx_fmt=jpeg">')
    urls = extract_image_urls(html)
    eq(len(urls), 2, '同一 FILEID 的 sz_ 前缀/1080 档/png 变体合并为一张')
    eq(ids_of(urls), [ID_FIRST, ID_SECOND], '合并后顺序不变')

    print('\n[14b] 混合结构：ppi 漏图时以正文顺序为准（漏的那张不能挤到最后）')
    # 真实场景：ppi 只列了 zzz 与 aaa，正文里中间还夹着一张 mmm
    html = ('<script>var picture_page_info_list:['
            f"{{cdn_url: '{HEAD_FIRST}/640?wx_fmt=jpeg'}},"
            f"{{cdn_url: '{HEAD_SECOND}/640?wx_fmt=jpeg'}}"
            ']</script>'
            f'<img data-src="{HEAD_FIRST}/640?wx_fmt=jpeg">'
            f'<img data-src="{HEAD_MID}/640?wx_fmt=jpeg">'
            f'<img data-src="{HEAD_SECOND}/640?wx_fmt=jpeg">')
    eq(ids_of(extract_image_urls(html)), [ID_FIRST, ID_MID, ID_SECOND],
       '按正文顺序，mmm 插在两者之间（若以 ppi 偏移排会掉到最后）')

    print('\n[14c] 纯 ppi 文章（正文无对应图）时以 ppi 数组顺序为准')
    html = ('<script>var picture_page_info_list:['
            f"{{cdn_url: '{HEAD_FIRST}/640?wx_fmt=jpeg'}},"
            f"{{cdn_url: '{HEAD_MID}/640?wx_fmt=jpeg'}},"
            f"{{cdn_url: '{HEAD_SECOND}/640?wx_fmt=jpeg'}}"
            ']</script>')
    eq(ids_of(extract_image_urls(html)), [ID_FIRST, ID_MID, ID_SECOND], '数组顺序被完整保留')


# ──────────────────────────────────────────────── 六、同名不覆盖

def test_unique_path():
    print('\n[15] 同名文件不覆盖：冲突时自动加序号')
    with tempfile.TemporaryDirectory() as d:
        p1 = unique_path(d, 'img_01', '.jpg')
        eq(os.path.basename(p1), 'img_01.jpg', '首次沿用原名字')
        with open(p1, 'wb') as f:
            f.write(b'a')

        p2 = unique_path(d, 'img_01', '.jpg')
        eq(os.path.basename(p2), 'img_01_1.jpg', '第二次加 _1')
        with open(p2, 'wb') as f:
            f.write(b'b')

        p3 = unique_path(d, 'img_01', '.jpg')
        eq(os.path.basename(p3), 'img_01_2.jpg', '第三次加 _2（旧实现会覆盖 img_01_0.jpg）')
        with open(p3, 'wb') as f:
            f.write(b'c')

        eq(os.path.getsize(p1), 1, '第一次的文件仍在，没被覆盖')

        print('\n[16] 序号被占用时继续向后探测')
        with open(os.path.join(d, 'img_02.jpg'), 'wb') as f:
            f.write(b'x')
        with open(os.path.join(d, 'img_02_1.jpg'), 'wb') as f:
            f.write(b'x')
        eq(os.path.basename(unique_path(d, 'img_02', '.jpg')), 'img_02_2.jpg',
           '原名字与 _1 都被占用 → 落到 _2')

        print('\n[17] 连下三轮不丢文件')
        for _ in range(3):
            with open(unique_path(d, 'img_09', '.jpg'), 'wb') as f:
                f.write(b'z')
        got = sorted(n for n in os.listdir(d) if n.startswith('img_09'))
        eq(got, ['img_09.jpg', 'img_09_1.jpg', 'img_09_2.jpg'], '三轮各留一份')


# ──────────────────────────────────────────────── 七、链接校验（v1.1.2 收紧）

def test_domain_check():
    print('\n[18] 链接校验只认 mp.weixin.qq.com 主机')

    for u in [
        'https://mp.weixin.qq.com/s/EoKPKXS7ec89I5cdBrRgQQ',
        'http://mp.weixin.qq.com/s/xxx',
        'https://mp.weixin.qq.com/s/xxx#anchor',
        'https://MP.WEIXIN.QQ.COM/s/xxx',
        'https://mp.weixin.qq.com:443/s/xxx',
        'mp.weixin.qq.com/s/xxx',                              # 裸域名，没写协议
    ]:
        check(is_wechat_url(u), f'应放行: {u}')

    # 旧实现是 `WECHAT_HOST in url` 的子串匹配，下面这些全部会被误放行，
    # 然后程序真的去请求那个地址。
    for u in [
        'https://evil.com/mp.weixin.qq.com/s/xxx',             # 藏在路径里
        'https://mp.weixin.qq.com.evil.com/s/xxx',             # 后缀伪装
        'https://notmp.weixin.qq.com.cn/s/xxx',                # 前缀伪装
        'https://evil.com/?u=https://mp.weixin.qq.com/s/xxx',  # 藏在查询串里
        'https://mp.weixin.qq.com@evil.com/s/xxx',             # userinfo 伪装
        'https://weixin.qq.com/s/xxx',
        '',
        '   ',
    ]:
        check(not is_wechat_url(u), f'应拒绝: {u!r}')


# ──────────────────────────────────────────────── 八、保存链路（v1.1.2 后台化）

def _png_bytes(w=6, h=4):
    """造一张真 PNG，用来走完整的保存链路"""
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0xFF3366FF)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    buf.close()
    return bytes(ba)


def test_save_write():
    print('\n[19] 原格式保存：字节原样落盘，不重编码')
    png = _png_bytes()
    with tempfile.TemporaryDirectory() as d:
        eq(write_image(d, 'img_01', '.png', png, ''), False, '原格式不触发回退')
        with open(os.path.join(d, 'img_01.png'), 'rb') as f:
            eq(f.read(), png, '落盘字节与内存里完全一致（零重编码）')

        print('\n[20] 格式转换：PNG → JPG 是真转码，不是只改后缀')
        eq(write_image(d, 'img_02', '.png', png, '.jpg'), False, '正常转换不触发回退')
        jp = os.path.join(d, 'img_02.jpg')
        check(os.path.exists(jp), 'img_02.jpg 已生成')
        with open(jp, 'rb') as f:
            eq(f.read(3), b'\xff\xd8\xff', '文件头是 JPEG')

        print('\n[21] 转换失败 → 回退原格式，既不留垃圾也不丢图')
        junk = b'this-is-not-an-image' * 20
        eq(write_image(d, 'img_03', '.png', junk, '.jpg'), True,
           '无法解码的字节 → 报告「已回退」')
        check(not os.path.exists(os.path.join(d, 'img_03.jpg')),
              '失败过程中产生的空 img_03.jpg 已被清掉')
        with open(os.path.join(d, 'img_03.png'), 'rb') as f:
            eq(f.read(), junk, '原字节完整落在 img_03.png，图没丢')

        print('\n[22] 保存同样走 unique_path：同一目录连存不覆盖')
        write_image(d, 'img_04', '.png', png, '')
        write_image(d, 'img_04', '.png', png, '')
        got = sorted(n for n in os.listdir(d) if n.startswith('img_04'))
        eq(got, ['img_04.png', 'img_04_1.png'], '第二次自动加 _1')


def test_save_worker():
    print('\n[23] SaveWorker 整批落盘：进度、计数、单张失败不拖垮整批')
    QCoreApplication.instance() or QCoreApplication([])

    png = _png_bytes()
    with tempfile.TemporaryDirectory() as d:
        targets = [
            (png, 'img_01', '.png'),
            (png, 'img_02', '.png'),
            (b'broken-bytes' * 10, 'img_03', '.png'),   # 转 JPG 会失败 → 回退原格式
        ]
        w = SaveWorker(targets, d, '.jpg')
        got = {'progress': [], 'done': None, 'failed': None}
        w.progress.connect(lambda c, t: got['progress'].append((c, t)))
        w.done.connect(lambda s, f, e: got.update(done=(s, f, e)))
        w.failed.connect(lambda m: got.update(failed=m))
        w.run()                       # 同步跑：不起线程就不需要事件循环

        eq(got['failed'], None, '没有整体失败')
        eq(got['progress'], [(1, 3), (2, 3), (3, 3)], '进度逐张上报')
        eq(got['done'][0], 3, '3 张全部写出')
        eq(got['done'][1], 1, '其中 1 张转换失败、回退原格式')
        eq(got['done'][2], [], '没有异常')
        eq(sorted(os.listdir(d)), ['img_01.jpg', 'img_02.jpg', 'img_03.png'],
           '目录产物正确（img_03 回退成原格式）')
        check(os.path.getsize(os.path.join(d, 'img_03.png')) > 0, '回退文件非空')


def test_cancel():
    print('\n[24] 解析中途取消：不报错、不发完成、已下载部分照常交付')
    QCoreApplication.instance() or QCoreApplication([])

    # (a) 还没开始就取消
    w = FetchWorker(f'<html><img data-src="{HEAD_A}/0"></html>', is_url=False)
    got = {'cancelled': None, 'done': None, 'error': None}
    w.cancelled.connect(lambda imgs: got.update(cancelled=imgs))
    w.all_done.connect(lambda imgs: got.update(done=imgs))
    w.error.connect(lambda m: got.update(error=m))
    w.cancel()
    w.run()
    eq(got['error'], None, '取消不该弹「未找到图片」这类错误')
    eq(got['done'], None, '取消不发「解析完成」')
    eq(got['cancelled'], [], '发的是 cancelled，已下载列表为空')

    # (b) 第 1 张下完再取消：第 2 张根本不该发请求
    html2 = (f'<html><img data-src="{HEAD_A}/0">'
             f'<img data-src="{HEAD_B}/0"></html>')
    png = _png_bytes()
    calls = []

    class _Resp:
        content = png

        @staticmethod
        def raise_for_status():
            pass

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) > 1:
            raise AssertionError('取消之后不该再发请求')
        return _Resp()

    w2 = FetchWorker(html2, is_url=False)
    got2 = {'cancelled': None, 'done': None}
    w2.cancelled.connect(lambda imgs: got2.update(cancelled=imgs))
    w2.all_done.connect(lambda imgs: got2.update(done=imgs))
    w2.image_loaded.connect(lambda i, info: w2.cancel())   # 第 1 张到手就取消
    with mock.patch('web_image_dl.worker.requests.get', fake_get), \
         mock.patch('web_image_dl.worker.MIN_IMAGE_BYTES', 1):
        w2.run()

    eq(len(calls), 1, '只请求了第 1 张')
    eq(got2['done'], None, '取消时不发「解析完成」')
    eq(len(got2['cancelled']), 1, '已下载的 1 张仍然交付给界面')


def test_extract_title():
    """v1.7.0：文章标题提取。下载文件夹名要用它（日期_时分秒_标题）。

    这条最容易「静默失效」：抓不到标题不会报错，只是文件夹名字退化成
    「2026-09-18_164633_微信图片」，用户完全看不出是坏了。所以三种来源都钉。
    """
    print('\n[EXTRA] 文章标题提取')

    # (a) og:title 最优先
    eq(extract_title('<meta property="og:title" content="OG 标题">'), 'OG 标题', 'og:title 取到')
    eq(extract_title('<meta content="反序标题" property="og:title">'), '反序标题',
       'content 与 property 顺序反过来也能取')
    eq(extract_title("<meta property='og:title' content='单引号标题'>"), '单引号标题',
       '属性用单引号也能取')

    # (b) 微信正文页的 JS 变量
    eq(extract_title("var msg_title = '微信变量标题';"), '微信变量标题', 'msg_title 取到')
    eq(extract_title('var msg_title = "双引号标题";'), '双引号标题', 'msg_title 用双引号也能取')
    eq(extract_title(r"var msg_title = '带\'引号\'的标题';"), "带'引号'的标题",
       r"值里的 \' 转义被还原")
    eq(extract_title("var msg_title = '换行\n标题';"), '换行 标题', '标题里的换行压成空格')

    # (c) <title> 兜底
    eq(extract_title('<title>页面标题</title>'), '页面标题', 'title 兜底')
    eq(extract_title('<title data-x="1">带属性</title>'), '带属性', 'title 带属性也能取')
    eq(extract_title('<TITLE>大写标签</TITLE>'), '大写标签', '标签大小写不敏感')

    # (d) 优先级
    eq(extract_title('<meta property="og:title" content="优先"/>'
                     "var msg_title = '次选';"
                     '<title>再次</title>'), '优先', 'og:title 先于 msg_title')
    eq(extract_title("var msg_title = '次选';<title>再次</title>"), '次选',
       'msg_title 先于 title')
    # og:title 为空时应该继续往下找，而不是直接返回空
    eq(extract_title('<meta property="og:title" content=""/>'
                     '<title>空了就往下找</title>'), '空了就往下找', 'og:title 为空时继续下一来源')

    # (e) 转义还原
    eq(extract_title('<meta property="og:title" content="A &amp; B">'), 'A & B',
       'HTML 实体被还原')
    eq(extract_title('<meta property="og:title" content="&amp;#39;双层&amp;#39;">'),
       "'双层'", '多重实体也能还原（微信会二次转义）')
    eq(extract_title('<meta property="og:title" content=" 两边有空格 ">'), '两边有空格',
       '标题首尾空白去掉')

    # (f) 取不到时返回空串（由 naming 兜底成「微信图片」），绝不能返回 None
    eq(extract_title(''), '', '空 HTML → 空串')
    eq(extract_title(None), '', 'None → 空串（不让调用方炸）')
    eq(extract_title('<p>没有任何标题信息</p>'), '', '真的没有标题 → 空串')
    eq(extract_title('<title></title>'), '', 'title 为空 → 空串')

    # (g) worker 真的会把标题带出来（folder naming 依赖这个属性）
    w = FetchWorker('<meta property="og:title" content="喂给 worker 的标题">', is_url=False)
    eq(w.article_title, '', 'worker 初始标题是空串而不是 None')
    w.run()          # 没有 mmbiz 图会走 error 分支，但标题应先写好了
    eq(w.article_title, '喂给 worker 的标题', 'worker.run() 后带出标题')


def main():
    test_url_rewrite()
    test_extract()
    test_candidates()
    test_fallback_download()
    test_output_order()
    test_unique_path()
    test_domain_check()
    test_save_write()
    test_save_worker()
    test_cancel()
    test_extract_title()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
