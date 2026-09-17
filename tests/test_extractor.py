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
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_image_dl.extractor import (
    clean_url,
    extract_image_urls,
    is_wechat_url,
    to_compressed_url,
    to_original_url,
)
from web_image_dl.worker import FetchWorker

ID_A = 'ibPmoa8nWl5JwSUWB6Fet9ZiaXEq7yyiajSX5xzsmr0kGVprH0zAwR9dqbSXoWNkd2aAAwEuXPBpiaj7fevvGbRBcg'
ID_B = 'j2kibbLtVMmd2oa0HKwrOdAhHlLS1hCnnE1uLgJLpfMT7AXDEjDpWtfX2vpmfpAhJvQCqbFdKhfAvwbLQCrroia3Hq1gbH2Y8hwGaFfydLXwk'
HEAD_A = f'https://mmbiz.qpic.cn/mmbiz_jpg/{ID_A}'
HEAD_B = f'https://mmbiz.qpic.cn/sz_mmbiz_jpg/{ID_B}'

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


def main():
    test_url_rewrite()
    test_extract()
    test_candidates()
    test_fallback_download()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
