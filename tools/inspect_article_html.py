"""诊断：公众号文章 HTML 里图片地址的真实结构

统计 data-src 的尺寸段分布（/0 vs /640 vs 无）、params 组合、是否含 picture_page_info_list，
用来判断一篇文章的画质上限在哪，以及提取器是否还有可挖的空间。
"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,*/*',
}

url = sys.argv[1]
out = open(sys.argv[2], 'w', encoding='utf-8') if len(sys.argv) > 2 else None


def log(m=''):
    print(m, flush=True)
    if out:
        out.write(m + '\n')


r = requests.get(url, headers=HEADERS, timeout=30)
html = r.text
log(f'HTML {len(html)/1024:.0f} KB')

log(f'picture_page_info_list 出现次数: {html.count("picture_page_info_list")}')
log(f'round_head_img 出现次数: {html.count("round_head_img")}')
log(f'mmbiz.qpic.cn 总出现次数: {html.count("mmbiz.qpic.cn")}')

raw = re.findall(r'data-src=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', html)
raw = list(dict.fromkeys(raw))
log(f'\ndata-src 去重后 {len(raw)} 条')

seg = Counter()
params = Counter()
for u in raw:
    m = re.search(r'/(\d+)\?', u + '?')
    seg[m.group(1) if m else '无尺寸段'] += 1
    q = u.split('?', 1)[1] if '?' in u else ''
    params[q[:70]] += 1

log('\n尺寸段分布:')
for k, v in seg.most_common():
    log(f'   /{k}: {v} 条')
log('\n参数组合分布:')
for k, v in params.most_common(8):
    log(f'   [{v} 条] {k}')

log('\n前 8 条原始 data-src:')
for u in raw[:8]:
    log('   ' + u)

# 正文里所有 mmbiz 链接（含 script）按尺寸段统计
allmb = re.findall(r'https?://mmbiz\.qpic\.cn/[^\s"\'<>\\)]+', html)
allmb = list(dict.fromkeys(allmb))
seg2 = Counter()
for u in allmb:
    m = re.search(r'/(\d+)(\?|$)', u)
    seg2[m.group(1) if m else '无尺寸段'] += 1
log(f'\n全量 mmbiz 链接去重后 {len(allmb)} 条，尺寸段分布:')
for k, v in seg2.most_common(10):
    log(f'   /{k}: {v} 条')
