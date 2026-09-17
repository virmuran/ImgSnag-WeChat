"""诊断：摊开 picture_page_info_list 每个 item，对比「顶层 cdn_url」与「watermark_info 里的 cdn_url」

要点：
  1. 顶层 cdn_url 是否真的无水印、尺寸档位是多少
  2. 把两者的尺寸段都改成 /0 后，谁更大 —— 决定提取器该取哪个
  3. URL 里的 \\x26amp;amp; 转义清洗后是否可用
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
from web_image_dl.extractor import to_original_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://mp.weixin.qq.com/',
    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
}

url = sys.argv[1]
out = open(sys.argv[2], 'w', encoding='utf-8') if len(sys.argv) > 2 else None


def log(m=''):
    print(m, flush=True)
    if out:
        out.write(m + '\n')


def clean(u):
    """洗掉 JS/HTML 转义：\\x26 → &，&amp; → &"""
    u = u.replace('\\x26', '&').replace('&amp;', '&')
    return u


def size_of(u):
    try:
        r = requests.get(u, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            return f'HTTP {r.status_code}'
        return f'{len(r.content)/1024:.1f} KB'
    except Exception as e:
        return f'ERR {type(e).__name__}'


html = requests.get(url, headers=HEADERS, timeout=30).text

i = html.find('picture_page_info_list')
j = html.find('[', i)
depth, end = 0, -1
for k in range(j, min(len(html), j + 300000)):
    c = html[k]
    if c == '[':
        depth += 1
    elif c == ']':
        depth -= 1
        if depth == 0:
            end = k
            break
block = html[j + 1:end]
log(f'数组长度 {len(block)} 字符')

# 按顶层大括号切分 item
items, depth, start = [], 0, None
for idx, c in enumerate(block):
    if c == '{':
        if depth == 0:
            start = idx
        depth += 1
    elif c == '}':
        depth -= 1
        if depth == 0 and start is not None:
            items.append(block[start + 1:idx])
            start = None

log(f'item 数: {len(items)}\n')

for n, ib in enumerate(items, 1):
    all_cdn = re.findall(r"cdn_url:\s*'([^']+)'", ib)
    log(f'--- item {n}: 含 {len(all_cdn)} 个 cdn_url ---')
    for m in all_cdn:
        log(f'    原始: {m[:150]}')
    top = all_cdn[0] if all_cdn else None
    if top:
        c = clean(top)
        log(f'    清洗后(顶层): {c[:130]}')
        log(f'    顶层 /640 → {size_of(re.sub(r"/[0-9]+(\\?|$)", "/640", c))}')
        log(f'    顶层 /0   → {size_of(to_original_url(c))}')
    if len(all_cdn) > 1:
        w = clean(all_cdn[-1])
        log(f'    水印层 /0 → {size_of(to_original_url(w))}')
    log('')

log('=' * 60)
log('顶层 vs 水印层 是否同一 FILEID:')
for n, ib in enumerate(items, 1):
    ids = re.findall(r'mmbiz_[A-Za-z_]+/([^/?\']+)', ib)
    log(f'  item {n}: FILEID {[x[:16] + "..." for x in dict.fromkeys(ids)]}')
