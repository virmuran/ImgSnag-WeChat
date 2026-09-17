"""微信图片画质探针 — 量化「文章里的 640 压缩版」与「/0 原图」的体积差

用途：
  1. 验证提取器输出的地址确实是原图（应为 .../<图片ID>/0）
  2. 量化画质提升幅度，判断某篇公众号文章值不值得开「原图画质」

用法:
    python tools/probe_wechat_quality.py <文章URL> [--out 报告.txt]
    python tools/probe_wechat_quality.py --url <单个mmbiz图片URL> [--out 报告.txt]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from web_image_dl.extractor import extract_image_urls, to_compressed_url, to_original_url

_LOGFILE = None


def log(msg=''):
    print(msg, flush=True)
    if _LOGFILE:
        _LOGFILE.write(msg + '\n')


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://mp.weixin.qq.com/',
    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
}


def fetch_size(url):
    """返回 (字节数, 内容类型)；失败返回 (None, 错误信息)"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            return None, f'HTTP {r.status_code}'
        return len(r.content), (r.headers.get('Content-Type') or '').split(';')[0]
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def compare(urls, title=''):
    log(f'\n=== {title}（共 {len(urls)} 张）===')
    log(f'{"#":>3}  {"640压缩版":>12}  {"0 原图":>12}  {"提升":>8}  格式')
    log('-' * 56)
    sum_old = sum_new = 0
    for i, u in enumerate(urls, 1):
        orig = to_original_url(u)
        comp = to_compressed_url(u)
        b_comp, t_comp = fetch_size(comp)
        b_orig, t_orig = fetch_size(orig)
        if b_comp is None and b_orig is None:
            log(f'{i:>3}  {"失败":>12}  {"失败":>12}  {"-":>8}  {t_orig}')
            continue
        bc = b_comp or 0
        bo = b_orig or 0
        sum_old += bc
        sum_new += bo
        ratio = f'{bo / bc:.2f}x' if bc else '-'
        log(f'{i:>3}  {bc/1024:>9.1f} KB  {bo/1024:>9.1f} KB  {ratio:>8}  {t_orig}')

    if sum_old:
        log('-' * 56)
        log(f'{"合计":>3}  {sum_old/1024:>9.1f} KB  {sum_new/1024:>9.1f} KB  '
            f'{sum_new/sum_old:>7.2f}x')
        log(f'     提升 {sum_new/sum_old:.2f} 倍，多出 {(sum_new-sum_old)/1024:.1f} KB')


def compare_raw(raw_urls, title=''):
    """对比「文章原始 data-src（旧版直接下载的地址）」与「规范化后的 /0 原图」

    这才是真实收益：文章里的 data-src 常带 tp=webp，会强制转成有损图，比干净的 640 还小。
    """
    log(f'\n=== {title}（共 {len(raw_urls)} 张）===')
    log(f'{"#":>3}  {"旧版直接下载":>13}  {"新版 /0 原图":>13}  {"提升":>8}')
    log('-' * 52)
    sum_old = sum_new = 0
    for i, raw in enumerate(raw_urls, 1):
        orig = to_original_url(raw)
        b_old, t_old = fetch_size(raw)
        b_new, t_new = fetch_size(orig)
        if b_old is None and b_new is None:
            log(f'{i:>3}  {"失败":>13}  {"失败":>13}  {"-":>8}')
            continue
        bo, bn = b_old or 0, b_new or 0
        sum_old += bo
        sum_new += bn
        ratio = f'{bn / bo:.2f}x' if bo else '-'
        log(f'{i:>3}  {bo/1024:>10.1f} KB  {bn/1024:>10.1f} KB  {ratio:>8}')
    if sum_old:
        log('-' * 52)
        log(f'{"合计":>3}  {sum_old/1024:>10.1f} KB  {sum_new/1024:>10.1f} KB  '
            f'{sum_new/sum_old:>7.2f}x')
        log(f'     {sum_old/1024/1024:.2f} MB → {sum_new/1024/1024:.2f} MB')


def compare_dims(urls, title=''):
    """对比分辨率：确认 /0 拿到的像素确实比 /640 大"""
    from PySide6.QtGui import QImage

    log(f'\n=== {title}（共 {len(urls)} 张）===')
    log(f'{"#":>3}  {"640 分辨率":>14}  {"0 分辨率":>14}   {"像素比":>7}  {"体积比":>7}')
    log('-' * 62)
    for i, u in enumerate(urls, 1):
        orig = to_original_url(u)
        comp = to_compressed_url(u)

        def probe(x):
            try:
                r = requests.get(x, headers=HEADERS, timeout=25)
                img = QImage()
                img.loadFromData(r.content)
                return len(r.content), img.width(), img.height()
            except Exception:
                return 0, 0, 0

        bc, wc, hc = probe(comp)
        bo, wo, ho = probe(orig)
        px = (wo * ho) / (wc * hc) if wc * hc else 0
        by = bo / bc if bc else 0
        log(f'{i:>3}  {f"{wc}x{hc}":>14}  {f"{wo}x{ho}":>14}   '
            f'{px:>6.2f}x  {by:>6.2f}x')


def main():
    global _LOGFILE
    args = sys.argv[1:]
    if '--out' in args:
        i = args.index('--out')
        _LOGFILE = open(args[i + 1], 'w', encoding='utf-8')
        del args[i:i + 2]
    if not args:
        log(__doc__)
        return

    if args[0] == '--url':
        compare([args[1]], '单图对比')
        compare_dims([args[1]], '单图分辨率对比')
        return

    url = args[0]
    r = requests.get(url, headers={**HEADERS, 'Accept': 'text/html,*/*'}, timeout=30)
    log(f'文章 HTTP {r.status_code}，HTML {len(r.text)/1024:.0f} KB')

    # 旧版会直接下载的地址（文章里怎么写就怎么下）
    raw = re.findall(r'data-src=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', r.text)
    raw = [u for u in dict.fromkeys(raw) if len(u) > 30]

    imgs = extract_image_urls(r.text)
    log(f'\n提取器输出 {len(imgs)} 张，地址尾部均应为 /0：')
    for u in imgs:
        tail = re.search(r'/(\d+)$', u)
        flag = 'OK' if tail and tail.group(1) == '0' else '!! 非原图'
        log(f'   [{flag}] ...{u[-42:]}')

    if raw:
        compare_raw(raw, '旧版地址 vs 新版原图')
    if imgs:
        compare(imgs, '净 640 vs /0（不含 tp=webp 干扰）')
        compare_dims(imgs, '分辨率对比')


if __name__ == '__main__':
    main()
