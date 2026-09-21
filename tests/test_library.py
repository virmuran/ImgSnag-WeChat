# -*- coding: utf-8 -*-
"""历史图库浏览 —— 批次文件夹扫描回归测试（纯逻辑，不依赖 Qt）

运行：
    .venv/Scripts/python.exe tests/test_library.py

为什么单独一个文件：v1.8.0 让历史里已下载的图**能在软件里重新打开**，
「列哪些文件、按什么顺序列、名字怎么看」这层规则必须独立可测 —— 它决定了
用户翻看旧图时的第一印象，出错的表现是「图少了」「顺序乱了」「名字对不上」，
全靠肉眼很难发现。锚点：

一、什么算图片
   只认 Qt 真能解码的扩展名（不含 .svg —— Qt 默认不带 SVG 图像插件，
   列出来只会得到一张空白缩略图）。大小写不敏感。

二、顺序是自然序而不是字典序
   纯字典序会把 img_10 排在 img_2 前面 —— 旧图库恰好是最多的那种
   （一篇 30 张图，序号都是两位，混着用户自己改过的名字）。

三、容错：翻旧图不该崩
   目录不存在/不可读 → 空列表；单个文件取不到大小 → 跳过；子目录忽略。

四、上限
   手滑选中一个塞满文件的大目录时不能让内存爆掉（张数上限 + 调用方按字节数封顶）。

五、文件夹名 → 标题
   `2026-09-18_164633_秋天的第一杯奶茶` 反推出「秋天的第一杯奶茶」，
   否则「另存到…」会把它当标题再拼一层日期。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_image_dl.library import (      # noqa: E402
    IMAGE_EXTS,
    MAX_BATCH_FILES,
    img_number,
    is_image_file,
    natural_key,
    scan_batch,
    title_from_folder,
)

_passed = 0
_failed = []


def check(cond, label):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(label)
        print(f'  \u2717 {label}')


def eq(got, want, label):
    check(got == want, f'{label}（期望 {want!r}，实测 {got!r}）')


def touch(folder, name, size=16):
    path = os.path.join(folder, name)
    with open(path, 'wb') as f:
        f.write(b'x' * size)
    return path


# ----------------------------------------------------------------------
def test_ext_filter():
    print('\n[L-1] 什么算图片：扩展名判定')
    for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'):
        check(is_image_file('a' + ext), f'{ext} 算图片')
    for ext in ('.JPG', '.Png', '.WEBP'):
        check(is_image_file('a' + ext), f'{ext} 大小写不敏感')
    for name in ('a.txt', 'a.svg', 'a.mp4', 'a', 'a.jpg.txt', 'a.', ''):
        check(not is_image_file(name), f'{name!r} 不算图片')
    # .svg 明确排除：Qt 默认没有 SVG 图像插件，列进列表只会得到空白缩略图
    check('.svg' not in IMAGE_EXTS, '.svg 不在支持列表里（避免空白缩略图）')
    eq(is_image_file(None), False, 'None 输入不炸')


def test_natural_order():
    print('\n[L-2] 顺序：自然序，img_2 在 img_10 前面')
    names = ['img_10.jpg', 'img_2.jpg', 'img_1.jpg', 'img_02.jpg', 'img_01.jpg']
    # 同序号（img_1 与 img_01）按完整文件名兜底比较，所以 01 在前 —— 关键不是这个
    # 顺序本身，而是**确定性**：同一批文件每次打开顺序都一样，且 2 一定在 10 前面
    eq(sorted(names, key=natural_key),
       ['img_01.jpg', 'img_1.jpg', 'img_02.jpg', 'img_2.jpg', 'img_10.jpg'],
       '下载命名按数值排序，不是字典序')

    # 用户自己放进去的文件：没有 img 序号 → 排在下载图之后，组内按自然序
    # （同组内中文按码位排在英文后面，同样是确定性的）
    mixed = ['封面.png', 'img_2.jpg', 'figure10.png', 'img_10.jpg', 'figure2.png']
    eq(sorted(mixed, key=natural_key),
       ['img_2.jpg', 'img_10.jpg', 'figure2.png', 'figure10.png', '封面.png'],
       '下载图在前、其余在后；其余内部也是自然序')

    # 中文与英文混排不该抛异常，且顺序稳定（两次排序结果一致）
    messy = ['微信图片.jpg', 'a.png', '中.png', 'Z.jpg']
    once = sorted(messy, key=natural_key)
    eq(sorted(list(reversed(messy)), key=natural_key), once, '排序稳定（与输入顺序无关）')
    eq(len(once), 4, '排序不丢文件')

    eq(img_number('img_07.png'), 7, 'img_07 → 7')
    eq(img_number('img-12.jpg'), 12, 'img-12 → 12（兼容下划线/连字符）')
    eq(img_number('IMG_3.JPG'), 3, 'IMG_3 → 3（大小写不敏感）')
    eq(img_number('封面.jpg'), None, '没有序号返回 None')
    eq(img_number(''), None, '空名字返回 None')
    eq(img_number(None), None, 'None 不炸')


def test_scan_basic():
    print('\n[L-3] 扫描：只列图片、跳过子目录与杂物')
    with tempfile.TemporaryDirectory(prefix='imgsnag_lib_') as d:
        touch(d, 'img_01.png')
        touch(d, 'img_02.jpg')
        touch(d, 'img_10.webp')
        touch(d, 'readme.txt')                 # 非图片
        touch(d, 'note.svg')                   # 明确不支持
        os.makedirs(os.path.join(d, 'sub'))    # 子目录
        touch(os.path.join(d, 'sub'), 'img_99.png')

        got = scan_batch(d)
        eq([f.name for f in got], ['img_01.png', 'img_02.jpg', 'img_10.webp'],
           '只列目录里的图片，子目录与非图片文件排除')
        eq(all(f.ext.startswith('.') and f.ext.islower() for f in got), True,
           '扩展名统一小写并带点')
        eq(all(os.path.isabs(f.path) for f in got), True, 'path 是完整路径')
        eq([f.size > 0 for f in got], [True, True, True], '每个都有大小')

        # 排序已经排好（调用方不用再排一遍）
        eq([f.name for f in got], sorted([f.name for f in got], key=natural_key),
           'scan_batch 返回时已按自然序排好')


def test_scan_tolerance():
    print('\n[L-4] 容错：翻旧图不该崩')
    eq(scan_batch(''), [], '空路径 → 空列表')
    eq(scan_batch(None), [], 'None → 空列表')
    eq(scan_batch(r'C:\__imgsnag_never_exists__'), [], '不存在的目录 → 空列表')

    with tempfile.TemporaryDirectory(prefix='imgsnag_lib_') as d:
        eq(scan_batch(d), [], '空目录 → 空列表')

        # 目录里只有非图片
        touch(d, 'a.txt')
        eq(scan_batch(d), [], '只有非图片 → 空列表')

        touch(d, 'img_01.png')
        # 路径传成文件而不是目录，也要容错
        eq(scan_batch(os.path.join(d, 'img_01.png')), [], '传的是文件而不是目录 → 空列表')
        eq(len(scan_batch(d)), 1, '正常情况仍有 1 张')


def test_scan_cap():
    print('\n[L-5] 上限：手滑选中大目录也不能把内存吃光')
    with tempfile.TemporaryDirectory(prefix='imgsnag_lib_') as d:
        for i in range(12):
            touch(d, f'img_{i:02d}.png')
        eq(len(scan_batch(d)), 12, '默认上限内全部列出')
        eq(len(scan_batch(d, max_files=5)), 5, 'max_files 生效')
        eq([f.name for f in scan_batch(d, max_files=3)],
           ['img_00.png', 'img_01.png', 'img_02.png'],
           '截断的是排好序之后的尾部，不是随机丢')
        eq(len(scan_batch(d, max_files=0)), 12, 'max_files=0 表示不限制')
        check(MAX_BATCH_FILES >= 100, '默认上限足够覆盖一篇文章（≥100）')


def test_title_from_folder():
    print('\n[L-6] 文件夹名 → 文章标题')
    eq(title_from_folder(r'C:\图片\ImgSnagWeChat\2026-09-18_164633_秋天的第一杯奶茶'),
       '秋天的第一杯奶茶', '剥掉日期_时分秒_前缀')
    eq(title_from_folder(r'C:\x\2026-09-18_164633_标题'), '标题', 'Windows 路径分隔符')
    eq(title_from_folder('2026-09-18_164633_标题'), '标题', '纯文件夹名（无父目录）')
    eq(title_from_folder(r'C:\x\2026-01-02_030405_标题'), '标题', '个位数月日也要认')
    eq(title_from_folder(r'C:\x\没有日期前缀的标题'), '没有日期前缀的标题',
       '没有前缀就原样返回')
    eq(title_from_folder(r'C:\x\2026-09-18_164633'), '2026-09-18_164633',
       '只有前缀没有标题时，宁可返回前缀也不返回空串')
    eq(title_from_folder('C:\\图片\\ImgSnagWeChat\\旧图库\\'), '旧图库', '结尾斜杠不影响')
    eq(title_from_folder('http://x/2026-09-18_164633_标题'), '标题',
       '带 http 前缀也认得（历史里可能是 url 风格路径）')
    eq(title_from_folder(''), '', '空串不炸')


def main():
    test_ext_filter()
    test_natural_order()
    test_scan_basic()
    test_scan_tolerance()
    test_scan_cap()
    test_title_from_folder()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  - {f}')
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
