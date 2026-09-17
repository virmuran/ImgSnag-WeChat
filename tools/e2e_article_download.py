"""端到端验收：用真实公众号文章跑一遍**生产下载链路**（FetchWorker），量化画质收益。

跑两遍同一篇文章：
  1. prefer_original=False → 模拟旧版行为（一律下载文章里的 640 压缩档）
  2. prefer_original=True  → 当前版本行为（原图优先，失败回退压缩档）
对比两遍的体积与分辨率，得出这次修复的真实收益。

用法：
    python tools/e2e_article_download.py <文章链接> [输出文件]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication

from web_image_dl.worker import FetchWorker


out = open(sys.argv[2], 'w', encoding='utf-8') if len(sys.argv) > 2 else None


def log(m=''):
    print(m, flush=True)
    if out:
        out.write(m + '\n')
        out.flush()


def run(url, prefer_original):
    """跑一遍 FetchWorker，同步等它结束，返回图片列表。"""
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    result = {}

    w = FetchWorker(url, is_url=True, prefer_original=prefer_original)
    w.all_done.connect(lambda lst: (result.setdefault('images', lst), app.quit()))
    w.error.connect(lambda msg: (result.setdefault('error', msg), app.quit()))
    w.start()
    app.exec()
    w.wait(5000)
    return result


url = sys.argv[1]
log(f'文章: {url}\n')

log('=' * 68)
log('第一遍：prefer_original=False（旧版行为，一律 640 压缩档）')
log('=' * 68)
old = run(url, prefer_original=False)
if 'error' in old:
    log(f'失败: {old["error"]}')
    sys.exit(1)
old_imgs = old.get('images', [])
old_total = sum(len(i.data) for i in old_imgs)
for i, info in enumerate(old_imgs, 1):
    log(f'  {i:>2}  {len(info.data)/1024:>8.1f} KB   {info.width}x{info.height}')
log(f'  合计 {old_total/1024:.1f} KB\n')

log('=' * 68)
log('第二遍：prefer_original=True（当前版本，原图优先）')
log('=' * 68)
new = run(url, prefer_original=True)
if 'error' in new:
    log(f'失败: {new["error"]}')
    sys.exit(1)
new_imgs = new.get('images', [])
new_total = sum(len(i.data) for i in new_imgs)
for i, info in enumerate(new_imgs, 1):
    tag = '原图' if info.used_original else '回退压缩档'
    log(f'  {i:>2}  {len(info.data)/1024:>8.1f} KB   {info.width}x{info.height}   {tag}')
log(f'  合计 {new_total/1024:.1f} KB\n')

log('=' * 68)
log('对比')
log('=' * 68)
log(f'{"#":>3}  {"旧版":>11}  {"新版":>11}  {"提升":>7}   {"旧分辨率":>12}  {"新分辨率":>12}  {"像素比":>7}')
log('-' * 78)
for i, (o, n) in enumerate(zip(old_imgs, new_imgs), 1):
    ob, nb = len(o.data), len(n.data)
    op = o.width * o.height
    np_ = n.width * n.height
    ratio = nb / ob if ob else 0
    pratio = np_ / op if op else 0
    log(f'{i:>3}  {ob/1024:>8.1f} KB  {nb/1024:>8.1f} KB  {ratio:>6.2f}x   '
        f'{o.width}x{o.height:<6}  {n.width}x{n.height:<6}  {pratio:>6.2f}x')
log('-' * 78)
ratio = new_total / old_total if old_total else 0
log(f'合计  {old_total/1024:>8.1f} KB  {new_total/1024:>8.1f} KB  {ratio:>6.2f}x')

fell_back = sum(1 for i in new_imgs if not i.used_original)
log(f'\n回退到压缩档的张数: {fell_back} / {len(new_imgs)}')
if ratio >= 1.5:
    log('结论: 画质提升显著，修复有效')
elif ratio > 1.0:
    log('结论: 有小幅提升')
else:
    log('结论: 无提升——可能这篇文章的图本来就是 /0，或原图档取不到')
