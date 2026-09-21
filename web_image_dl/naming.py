"""图库目录与下载文件夹命名 —— 纯逻辑，不依赖 Qt、不碰界面。

为什么单独一个模块：文件夹名要过 Windows 文件系统的七道关（非法字符、结尾的
点或空格、保留设备名、长度上限、重名……）。每一条错了都表现为「保存失败」或者
「文件找不着了」，而这类规则最适合脱离界面单测。

命名规则（用户可见）：``2026-09-18_164633_秋天的第一杯奶茶``
    · 日期在前 —— 按名字排序就是时间顺序
    · 带时分秒 —— 同一天可能下好几篇，只到「日」会撞车
    · 标题     —— 一眼看出这堆图是哪篇文章的；取不到就兜底「微信图片」
"""
import os
import re
from datetime import datetime

#: 文件夹名里「标题」部分最多几个字
MAX_TITLE_LEN = 24

#: Windows 文件名非法字符（会被直接删掉）
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')

#: 控制字符（含换行、制表）——换成空格而不是删掉，否则「空格\n换行」会粘成一个词
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')

#: Windows 保留设备名。哪怕带扩展名一样建不出来，必须避开
_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}

#: 抓不到文章标题时的兜底名
FALLBACK_TITLE = '微信图片'

#: Windows 传统路径上限。普通 API 超过这个长度直接打不开（要 \\?\ 前缀）
MAX_PATH = 260

#: 给 unique_dir 的后缀（"_99"）与分隔符留的余量
_LEN_RESERVE = 8


def sanitize_title(raw, max_len: int = MAX_TITLE_LEN) -> str:
    """    把文章标题洗成能当文件夹名用的字符串。

    - 控制字符（含换行、制表）先换成**空格**再压 —— 直接删会让「空格\\n换行」粘成一个词
    - 非法字符 ``\\ / : * ? " < > |`` 直接删掉
    - 连续空白压成一个空格
    - 去掉首尾空白与**结尾的点**：Windows 不允许名字以点或空格结尾。
      注意要一次剥掉「点与空格交替」的尾巴（`'… . . '`）—— 只 rstrip('.') 会留下
      一个尾空格，名字照样非法（这条是测试里抓出来的）
    - 截到 max_len；截断后可能又露出空格或点，所以清完再截、截完再清
    - 命中 Windows 保留设备名（CON/NUL/COM1…）时加前缀，否则目录根本建不出来

    取不到有效字符时返回空串，由调用方决定兜底（见 folder_name）。
    """
    if not raw:
        return ''
    s = _CONTROL_RE.sub(' ', str(raw))
    s = _ILLEGAL_RE.sub('', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if max_len > 0:
        s = s[:max_len]
    else:
        s = ''
    s = s.strip().rstrip(' .')         # 点与空格交替的尾巴要一次剥干净（只剥点会留尾空格）
    if s and s.upper() in _RESERVED:
        s = '_' + s
    return s


def folder_name(title, when: datetime | None = None, max_len: int = MAX_TITLE_LEN) -> str:
    """生成一次下载的专属文件夹名：``2026-09-18_164633_标题``。

    标题取不到时用 FALLBACK_TITLE 兜底 —— 保证**永远**返回一个合法名字，
    这样调用方不必到处判空。
    """
    when = when or datetime.now()
    safe = sanitize_title(title, max_len) or FALLBACK_TITLE
    return f'{when:%Y-%m-%d}_{when:%H%M%S}_{safe}'


def folder_name_for(parent, title, when: datetime | None = None,
                    max_path: int = MAX_PATH) -> str:
    """按父目录长度反推标题能占多少字，保证最终全路径仍在 Windows 上限内。

    超长路径的后果不是报错而是「写不进去」，所以宁可把标题截短，也不能让路径超长。

    边界：父目录自己就长到放不下兜底名时（现实里是几十层嵌套才会遇到），
    这里只能退回兜底名尽力而为 —— 不能去截父目录，那已经不是这次下载能决定的了。
    此时返回值仍保证是合法目录名，只是整条路径可能超上限。
    """
    when = when or datetime.now()
    prefix = f'{when:%Y-%m-%d}_{when:%H%M%S}_'
    budget = max_path - len(os.path.abspath(parent)) - 1 - len(prefix) - _LEN_RESERVE
    max_len = min(MAX_TITLE_LEN, budget)
    return folder_name(title, when, max_len=max_len)


def unique_dir(parent, name) -> str:
    """在 parent 下给 name 找一个**不冲突**的目录路径（只算路径，不创建）。

    同一秒里重复下载同一篇（或用户连点两次）会撞名，加 ``_2``、``_3``……
    与 file_utils.unique_path 同样策略：宁可多一个后缀，也不覆盖已有内容。
    """
    candidate = os.path.join(parent, name)
    if not os.path.exists(candidate):
        return candidate
    i = 2
    while True:
        candidate = os.path.join(parent, f'{name}_{i}')
        if not os.path.exists(candidate):
            return candidate
        i += 1
