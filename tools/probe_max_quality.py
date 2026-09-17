"""诊断：同一张图穷举各种地址写法，确认 /0 是否已是微信提供的最大档位。

如果存在比 /0 更大的档位（例如 /1080、/2000、去掉 CDN 前缀等），那才是画质的终极上限，
值得写进提取器；如果 /0 就是天花板，那画质问题只能到此为止，不必再折腾。

同时打印每个档位的**分辨率**——体积大不等于画质高（可能只是压缩率不同），
分辨率才是判断"哪个写法真的更大"的硬指标。

用法：
    python tools/probe_max_quality.py <mmbiz 图片地址> [输出文件]
    python tools/probe_max_quality.py <公众号文章链接> [输出文件]   # 自动取正文最大的一张来试
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
}

out = open(sys.argv[2], 'w', encoding='utf-8') if len(sys.argv) > 2 else None


def log(m=''):
    print(m, flush=True)
    if out:
        out.write(m + '\n')
        out.flush()


def pick_target(arg):
    """传图片地址就直接用；传文章链接就抓页面、取正文体积最大的一张。"""
    if 'mmbiz.qpic.cn' in arg:
        return arg
    from web_image_dl.extractor import extract_image_urls
    r = requests.get(arg, headers=HEADERS, timeout=30)
    log(f'文章 HTTP {r.status_code}，HTML {len(r.text)/1024:.0f} KB')
    urls = extract_image_urls(r.text)
    log(f'提取到 {len(urls)} 张，逐一探测取最大')
    best, best_n = None, 0
    for u in urls:
        try:
            n = len(requests.get(u, headers=HEADERS, timeout=25).content)
            log(f'   {n/1024:8.1f} KB  ...{u[-38:]}')
            if n > best_n:
                best, best_n = u, n
        except Exception as e:
            log(f'   ERR {type(e).__name__}')
    if not best:
        log('未能取得可用图片')
        sys.exit(1)
    log(f'\n选定最大的一张（{best_n/1024:.1f} KB）作为样本：\n{best}\n')
    return best


def resolution(data):
    """优先用 stdlib 之外的方式拿宽高，拿不到就返回 None。"""
    try:
        from PIL import Image
        return Image.open(io.BytesIO(data)).size
    except Exception:
        pass
    try:
        from PySide6.QtGui import QImage
        img = QImage()
        img.loadFromData(data)
        if img.width():
            return img.width(), img.height()
    except Exception:
        pass
    return None


url = pick_target(sys.argv[1])

m = re.match(r'(https?://mmbiz\.qpic\.cn/)((?:sz_)?mmbiz_[A-Za-z_]+)/([^/?]+)', url)
if not m:
    log(f'不是标准 mmbiz 地址: {url}')
    sys.exit(1)

scheme, prefix, img_id = m.group(1), m.group(2), m.group(3)
log(f'CDN 前缀: {prefix}')
log(f'图片 ID:  {img_id[:40]}...\n')

cands = []
for size in ['0', '640', '1080', '1280', '2000', '3000']:
    cands.append((f'/{size} 同前缀', f'{scheme}{prefix}/{img_id}/{size}'))
# 换 CDN 前缀：mmbiz_jpg → mmbiz / mmbiz_png / sz_mmbiz_jpg
bare = re.sub(r'^(sz_)?mmbiz_[A-Za-z_]+$', r'\1mmbiz', prefix)
if bare != prefix:
    cands.append(('换裸前缀 mmbiz /0', f'{scheme}{bare}/{img_id}/0'))
for alt in ['mmbiz_jpg', 'mmbiz_png', 'sz_mmbiz_jpg', 'sz_mmbiz_png']:
    if alt != prefix:
        cands.append((f'换前缀 {alt} /0', f'{scheme}{alt}/{img_id}/0'))
cands.append(('无尺寸段', f'{scheme}{prefix}/{img_id}/'))
cands.append(('wx_fmt=other /0', f'{scheme}{prefix}/{img_id}/0?wx_fmt=other'))
cands.append(('去 wx_fmt /0', f'{scheme}{prefix}/{img_id}/0'))
cands.append(('wxt_fmt=gif /0', f'{scheme}{prefix}/{img_id}/0?wx_fmt=gif'))
cands.append(('jpg 后缀 /0', f'{scheme}{prefix}/{img_id}/0.jpg'))

log(f'{"写法":<24}{"体积":>11}  {"分辨率":>13}   类型')
log('-' * 62)
best = (0, '', None)
for name, u in cands:
    try:
        r = requests.get(u, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            log(f'{name:<24}{"HTTP " + str(r.status_code):>11}')
            continue
        n = len(r.content)
        ctype = (r.headers.get('Content-Type') or '').split(';')[0]
        res = resolution(r.content)
        res_s = f'{res[0]}x{res[1]}' if res else '-'
        log(f'{name:<24}{n/1024:>8.1f} KB  {res_s:>13}   {ctype}')
        pixels = res[0] * res[1] if res else 0
        if pixels > (best[2][0] * best[2][1] if best[2] else 0) or (pixels == 0 and n > best[0]):
            best = (n, name, res)
    except Exception as e:
        log(f'{name:<24}{"ERR":>11}   {type(e).__name__}')

log('-' * 62)
res_s = f'{best[2][0]}x{best[2][1]}' if best[2] else '-'
log(f'最高画质写法: {best[1]}   {best[0]/1024:.1f} KB   {res_s}')
if best[1].startswith('/0'):
    log('结论: /0 即天花板，无更高档位可挖')
else:
    log(f'⚠ 发现比 /0 更大的写法: {best[1]} —— 值得改进提取器')
