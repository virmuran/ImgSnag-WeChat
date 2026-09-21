# -*- coding: utf-8 -*-
"""图库文件夹命名 / 清洗 回归测试（纯逻辑，不联网、不起界面）

运行：
    .venv/Scripts/python.exe tests/test_naming.py

为什么单独一个文件：这是 v1.7.0 补上的「每次下载一个专属文件夹」。旧版每次点下载
都要选一次目录，图片平铺命名（img_01、img_02…）直接扔进所选目录 —— 一直存「下载」
的话，第二篇文章的 img_01 就变成 img_01_1，越用越乱，也看不出哪张属于哪篇文章。

文件夹名要过 Windows 文件系统的七道关，每一条错了都表现为「保存失败」或
「文件不见了」，而且**都不是崩溃**，很难发现。所以这里逐条钉：

一、非法字符与控制字符
   ``\\ / : * ? " < > |`` 必须删掉（留着就建不出目录）。控制字符（换行、制表）要换成
   **空格**再压 —— 直接删会让「空格\\n换行」粘成一个词。

二、结尾的点与空格
   Windows 不允许文件名以点或空格结尾，而「标题被截断」恰好最容易露出尾巴的点，
   所以必须在**截断之后再清一次**（顺序反了就漏）。

三、保留设备名
   CON/PRN/AUX/NUL/COM1-9/LPT1-9 哪怕带扩展名也建不出来，命中要加前缀。

四、长度上限
   Windows 传统路径上限 260 字符，超了普通 API 直接打不开（要 \\\\?\\ 前缀）。
   所以标题的可用长度要按父目录长度反推，宁可截短也不许整条路径超长。

五、重名
   同一秒重复下载（或用户连点两次）会撞名，必须加 _2、_3…… 绝不覆盖已有内容。

六、兜底
   标题取不到（或全是非法字符）时必须返回一个合法名字，绝不能拿空串或 None 去建目录。
"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_image_dl.naming import (              # noqa: E402
    FALLBACK_TITLE,
    MAX_PATH,
    MAX_TITLE_LEN,
    folder_name,
    folder_name_for,
    sanitize_title,
    unique_dir,
)

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
    check(got == want, f'{label}\n      得到: {got!r}\n      期望: {want!r}')


#: 固定的时间戳，避免用例依赖「现在几点」
DT = datetime(2026, 9, 18, 16, 46, 33)


def _is_legal_name(name):
    """一个名字能不能当 Windows 文件名（不含路径分隔符之前的部分）"""
    if not name:
        return False
    if any(c in name for c in '\\/:*?"<>|'):
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return False
    if name != name.strip():
        return False
    if name.endswith('.'):
        return False
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} \
        | {f'LPT{i}' for i in range(1, 10)}
    if name.upper() in reserved:
        return False
    return True


def test_illegal_chars():
    print('\n[NAME-1] 非法字符与控制字符')
    eq(sanitize_title('秋天/的第:一杯*奶茶?'), '秋天的第一杯奶茶', '非法字符被删掉')
    eq(sanitize_title(r'a\b:c*d?e"f<g>h|i'), 'abcdefghi', '九种非法字符全部删掉')
    eq(sanitize_title('甲\\乙'), '甲乙', '反斜杠删掉')
    # 控制字符换空格：直接删会把前后两个词粘成一个
    eq(sanitize_title('多   空格\n换行'), '多 空格 换行', '换行换成空格再压缩（不是直接删）')
    eq(sanitize_title('制表\t分隔'), '制表 分隔', '制表符同上')
    eq(sanitize_title('回车\r\n换行'), '回车 换行', 'CRLF 也是一个分隔')
    eq(sanitize_title('控制\x00字符'), '控制 字符', 'NUL 字符换成空格')
    eq(sanitize_title('零宽\x7f符'), '零宽 符', 'DEL 字符换成空格')
    eq(sanitize_title('  两边空白  '), '两边空白', '首尾空白去掉')
    eq(sanitize_title('中间   多个    空格'), '中间 多个 空格', '连续空白压成一个')


def test_trailing_dots_and_spaces():
    print('\n[NAME-2] 结尾的点与空格（Windows 硬性限制）')
    eq(sanitize_title('结尾的点...'), '结尾的点', '结尾的点去掉')
    eq(sanitize_title('结尾空格   '), '结尾空格', '结尾空格去掉')
    eq(sanitize_title('点 和 空格 . . '), '点 和 空格', '点与空格交替结尾一并清掉')
    eq(sanitize_title('. . .'), '', '点与空格交替且无正文 → 空串')
    eq(sanitize_title('标题 . '), '标题', '正文+空格+点 → 正文')
    eq(sanitize_title('标题. '), '标题', '点后跟空格 → 正文')
    eq(sanitize_title('.'), '', '只有一个点 → 空串（交给兜底）')
    eq(sanitize_title(' . '), '', '点加空格 → 空串')


def test_reserved_names():
    print('\n[NAME-3] Windows 保留设备名')
    for name in ('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM9', 'LPT1', 'LPT9'):
        eq(sanitize_title(name), '_' + name, f'{name} 加前缀（否则建不出目录）')
    eq(sanitize_title('con'), '_con', '保留名判定不区分大小写')
    eq(sanitize_title('Con'), '_Con', '混合大小写同样命中')
    eq(sanitize_title('CONSOLE'), 'CONSOLE', '前缀相同但不是保留名的不动')
    eq(sanitize_title('COM10'), 'COM10', 'COM10 不是保留名（只有 1-9）')
    eq(sanitize_title('我的CON'), '我的CON', '只是含 CON 不算命中')


def test_length_limit():
    print('\n[NAME-4] 标题长度截断')
    eq(len(sanitize_title('啊' * 100)), MAX_TITLE_LEN, f'标题截到 {MAX_TITLE_LEN} 字')
    eq(sanitize_title('啊' * 100), '啊' * MAX_TITLE_LEN, '截断内容正确')
    eq(sanitize_title('啊' * 100, max_len=5), '啊啊啊啊啊', 'max_len 可覆盖')
    eq(sanitize_title('啊' * 100, max_len=0), '', 'max_len=0 → 空串')
    eq(sanitize_title('啊' * 100, max_len=-3), '', '负数长度不炸，回空串')
    # 关键顺序：截断之后可能又露出结尾的点/空格，必须再清一次
    eq(sanitize_title('啊' * 24 + ' ...', max_len=25), '啊' * 24, '截断后露出的点被清掉')
    eq(sanitize_title('啊' * 24 + '  x', max_len=25), '啊' * 24, '截断后露出的空格被清掉')
    eq(sanitize_title('x' * 300), 'x' * MAX_TITLE_LEN, '超长英文同样截断')


def test_empty_and_none():
    print('\n[NAME-5] 空值与无有效字符')
    eq(sanitize_title(''), '', '空串 → 空串')
    eq(sanitize_title(None), '', 'None → 空串（不能让调用方炸）')
    eq(sanitize_title('///???***'), '', '全是非法字符 → 空串')
    eq(sanitize_title('   '), '', '全是空白 → 空串')
    eq(sanitize_title('\n\t\r'), '', '全是控制字符 → 空串')


def test_folder_name():
    print('\n[NAME-6] 文件夹名格式')
    eq(folder_name('秋天的第一杯奶茶', DT), '2026-09-18_164633_秋天的第一杯奶茶', '日期_时分秒_标题')
    eq(folder_name('标题', datetime(2026, 1, 2, 3, 4, 5)), '2026-01-02_030405_标题',
       '月日时分秒都补零')
    eq(folder_name('', DT), f'2026-09-18_164633_{FALLBACK_TITLE}', '无标题 → 兜底名')
    eq(folder_name(None, DT), f'2026-09-18_164633_{FALLBACK_TITLE}', 'None 标题 → 兜底名')
    eq(folder_name('///', DT), f'2026-09-18_164633_{FALLBACK_TITLE}', '标题全是非法字符 → 兜底名')
    eq(folder_name('a/b:c', DT), '2026-09-18_164633_abc', '命名前先清洗')
    check(folder_name('任意', DT) != folder_name('任意', datetime(2026, 9, 18, 16, 46, 34)),
          '差一秒的名字不同（同一天下多篇不会撞车）')
    # 兜底名本身也得是合法目录名
    check(_is_legal_name(FALLBACK_TITLE), f'兜底名 {FALLBACK_TITLE} 本身合法')


def test_folder_name_always_legal():
    """把一堆难缠的标题过一遍，结果必须**条条**都是合法目录名。

    这条是防「清洗漏了一种字符」——单点断言容易漏，属性式检查覆盖面更广。
    """
    print('\n[NAME-7] 难缠标题的属性检查（结果必须是合法目录名）')
    nasty = [
        '普通标题', '', None, '   ', '。。。', '/', '..', 'CON', 'nul', 'COM1',
        'a' * 300, '标题' * 100, '带\\反斜杠', '带/斜杠', '带:冒号', '带*星号',
        '带?问号', '带"双引号', '带<小于>', '带|竖线', '换\n行', '制表\t符',
        '\x00空字节', '结尾点.', '结尾空格 ', '  两边有  ', '$特殊@符号#', 'emoji😀标题',
        '半角 全角混排．', '数字123标题456',
        '点 和 空格 . . ', '. . .', '标题 . ', '标题. ', '标题 .', '标题...  ',
    ]
    for raw in nasty:
        name = folder_name(raw, DT)
        ok = _is_legal_name(name) and len(name) <= 10 + 1 + 6 + 1 + MAX_TITLE_LEN
        check(ok, f'标题 {raw!r} → 合法目录名（实测 {name!r}）')


def test_folder_name_for_path_budget():
    print('\n[NAME-8] 按父目录长度反推标题预算（守住 260 上限）')
    short_parent = r'C:\Users\Administrator\Pictures\ImgSnagWeChat'
    nm = folder_name_for(short_parent, '标题' * 30, DT)
    eq(nm, '2026-09-18_164633_' + '标题' * 12, '父目录短时用满 24 字')
    full = os.path.join(short_parent, nm)
    check(len(full) < MAX_PATH, f'短父目录下路径不超上限（{len(full)} 字符）')

    # 逐段加长父目录：只要还有预算，整条路径就不许超 260。
    # 上加到「预算刚好耗尽」为止（再长就只剩兜底名可用，见下一条）
    for extra in range(0, 180, 6):
        parent = short_parent + '\\' + 'x' * extra
        nm = folder_name_for(parent, '标题' * 50, DT)
        total = len(os.path.join(parent, nm))
        check(total < MAX_PATH, f'父目录加长 {extra} 字后路径仍 <260（实测 {total}）')
        check(nm.startswith('2026-09-18_164633_'), f'父目录加长 {extra} 字后日期前缀仍完整')

    # 预算真的被压光时：标题退回兜底名（绝不返回空标题的名字，那等于没有标识）
    parent = short_parent + '\\' + 'x' * 300
    nm = folder_name_for(parent, '标题' * 50, DT)
    check(nm.endswith(FALLBACK_TITLE), f'预算耗尽时标题退回兜底名（实测 {nm!r}）')
    check(nm.startswith('2026-09-18_164633_'), '预算耗尽时前缀仍完整')
    check(_is_legal_name(nm), '预算耗尽时返回的仍是合法目录名')


def test_unique_dir():
    print('\n[NAME-9] 撞名处理（绝不覆盖已有内容）')
    tmp = tempfile.mkdtemp(prefix='imgsnag_naming_')
    with_empty = unique_dir(tmp, '2026-09-18_164633_标题')
    eq(os.path.basename(with_empty), '2026-09-18_164633_标题', '不冲突时原样返回')
    eq(os.path.dirname(with_empty), tmp, '路径拼在父目录下')

    d = unique_dir(tmp, 'abc')
    os.makedirs(d)
    eq(os.path.basename(d), 'abc', '不冲突时用原名')
    d2 = unique_dir(tmp, 'abc')
    eq(os.path.basename(d2), 'abc_2', '第一次撞名 → _2')
    os.makedirs(d2)
    d3 = unique_dir(tmp, 'abc')
    eq(os.path.basename(d3), 'abc_3', '第二次撞名 → _3')
    os.makedirs(d3)
    eq(os.path.basename(unique_dir(tmp, 'abc')), 'abc_4', '继续撞名 → _4')

    # 冲突对象是「文件」而不是目录时也得让开（同秒下过一次、用户又放了个同名文件）
    open(os.path.join(tmp, 'file_like'), 'w').close()
    eq(os.path.basename(unique_dir(tmp, 'file_like')), 'file_like_2', '同名文件也让开')

    # 不冲突的名字必须原样返回（别手贱给所有目录都加后缀）
    eq(os.path.basename(unique_dir(tmp, '全新的名字')), '全新的名字', '不冲突时不加后缀')
    eq(unique_dir(tmp, 'x'), os.path.join(tmp, 'x'), '返回的是完整路径')


def main():
    test_illegal_chars()
    test_trailing_dots_and_spaces()
    test_reserved_names()
    test_length_limit()
    test_empty_and_none()
    test_folder_name()
    test_folder_name_always_legal()
    test_folder_name_for_path_budget()
    test_unique_dir()
    print(f'\n{"=" * 46}')
    print(f'通过 {_passed} 项，失败 {len(_failed)} 项')
    if _failed:
        for f in _failed:
            print(f'  ✗ {f}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
