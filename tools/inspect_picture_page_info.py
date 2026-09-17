"""诊断：把真实文章 HTML 里 picture_page_info_list 附近的原文打印出来

提取器当前假设该结构是 JS 对象字面量、且 cdn_url 用单引号：
    picture_page_info_list:[{cdn_url:'https://...'}]
若微信实际给的是 JSON 双引号形式，现有解析会静默返回空集。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,*/*',
}

RE_SINGLE = re.compile(r"cdn_url:\s*'(https?://mmbiz\.qpic\.cn/[^']+)'")
RE_DOUBLE = re.compile(r'cdn_url"?\s*:\s*"(https?://mmbiz\.qpic\.cn/[^"]+)"')
RE_BOTH_QUOTES = re.compile(r"""cdn_url["']?\s*:\s*["']([^"']+)["']""")

url = sys.argv[1]
out_path = sys.argv[2] if len(sys.argv) > 2 else None
out = open(out_path, 'w', encoding='utf-8') if out_path else None


def log(m=''):
    print(m, flush=True)
    if out:
        out.write(m + '\n')


html = requests.get(url, headers=HEADERS, timeout=30).text
log(f'HTML {len(html)/1024:.0f} KB')

for key in ['picture_page_info_list', 'watermark_info', 'cdn_url', 'round_head_img']:
    log(f'{key}: {html.count(key)} 次')

for m in list(re.finditer('picture_page_info_list', html))[:3]:
    s = max(0, m.start() - 40)
    e = min(len(html), m.start() + 420)
    log('\n' + '=' * 70)
    log(f'--- 出现位置 {m.start()} 附近 ---')
    log(html[s:e])

i = html.find('picture_page_info_list')
if i >= 0:
    seg = html[i:i + 3000]
    log('\n引号风格统计（其后 3000 字符内）:')
    log(f'  现有正则（单引号）命中: {len(RE_SINGLE.findall(seg))}')
    log(f'  双引号形式命中: {len(RE_DOUBLE.findall(seg))}')
    log(f'  任意引号形式命中: {len(RE_BOTH_QUOTES.findall(seg))}')

    j = html.find('[', i)
    if j > 0:
        depth, end = 0, -1
        for k in range(j, min(len(html), j + 200000)):
            c = html[k]
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    end = k
                    break
        log(f'\n数组区间: {j} ~ {end}')
        if end > 0:
            block = html[j + 1:end]
            log(f'  数组长度 {len(block)}，其中 cdn_url 出现 {block.count("cdn_url")} 次')
            log(f'  现有正则（单引号）命中: {len(RE_SINGLE.findall(block))} 条')
            log(f'  双引号形式命中: {len(RE_DOUBLE.findall(block))} 条')
            log(f'  任意引号形式命中: {len(RE_BOTH_QUOTES.findall(block))} 条')
            for h in RE_BOTH_QUOTES.findall(block)[:5]:
                log('     ' + h[:110])
