"""批次文件夹扫描 —— 「历史里已下载的图，在软件里再看一遍」

只做纯逻辑（不依赖 Qt），方便单测：给一个批次目录，列出其中可显示的图片文件，
按文件名自然序排好，交给界面去读像素。

为什么单独一个模块而不是塞进 app.py：
    · 「哪些文件算图片」「排序怎么排」是规则，不是界面；
    · 历史浏览的画质/顺序/文件名显示都要靠它，规则必须能被测试钉住；
    · 文件名里有用户的痕迹（删过、改过名、另存过副本），按名字自然序才符合直觉。
"""
import os
import re
from dataclasses import dataclass

#: 能显示的图片扩展名（小写比较）。刻意不含 .svg —— Qt 默认不带 SVG 图像格式插件，
#: 列出来只会得到一张空白缩略图，不如直接不列
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff')

#: 单批次最多读回多少张 —— 防手滑选中一个塞满文件的大目录把内存吃光
MAX_BATCH_FILES = 500

#: 一次浏览读入内存的总字节上限（200MB）。超了就不再往界面里加，
#: 因为 ImageInfo.data 是整张图常驻内存，几十张原图叠加起来是实打实的占用
MAX_BATCH_BYTES = 200 * 1024 * 1024

#: 下载时的命名 `img_01.jpg`，取中间的序号（用于显示兜底与排序）
_IMG_NUM_RE = re.compile(r'^img[_-]?(\d+)', re.IGNORECASE)

#: 批次文件夹名前缀 `2026-09-18_164633_`（naming.folder_name 生成）
_FOLDER_PREFIX_RE = re.compile(r'^\d{4}-\d{2}-\d{2}_\d{6}[_\-\s]*')


@dataclass
class BatchFile:
    """批次目录里的一个图片文件"""
    path: str
    name: str
    ext: str        # 小写，含点（如 .jpg）
    size: int = 0


def is_image_file(name: str) -> bool:
    """按扩展名判断是不是能显示的图片"""
    return os.path.splitext(name or "")[1].lower() in IMAGE_EXTS


def img_number(name: str) -> int | None:
    """从 `img_07.jpg` 里取出 7；不像下载命名就返回 None"""
    m = _IMG_NUM_RE.match(name or "")
    return int(m.group(1)) if m else None


def natural_key(name: str):
    """自然序排序键：`img_2` 排在 `img_10` 前面（纯字典序会反过来）。

    两段式：先按「有没有 img 序号」分组（下载来的图在前，用户自己放的在后面），
    组内再按数字/小写名字比。数字段按数值比、文字段按字典序比，
    这样 `img_1`、`img_2`、`img_10` 与 `封面.jpg`、`figure2.png` 都有确定位置。
    """
    name = name or ""
    num = img_number(name)
    if num is not None:
        return (0, num, "", name.lower())
    parts = [p for p in re.split(r'(\d+)', name.lower()) if p != ""]
    key = tuple((1, int(p)) if p.isdigit() else (0, p) for p in parts)
    return (1, 0, key, name.lower())


def scan_batch(folder: str, max_files: int = MAX_BATCH_FILES) -> list:
    """列出批次目录里可显示的图片，按自然序排好。

    容错原则：这是「翻看以前的图」，任何异常都不该变成崩溃或空白页面 ——
    目录不存在返回空列表、单个文件读不到属性就跳过、非图片与子目录一律忽略。
    """
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not is_image_file(name):
            continue
        path = os.path.join(folder, name)
        try:
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
        except OSError:
            continue
        out.append(BatchFile(path=path, name=name,
                             ext=os.path.splitext(name)[1].lower(), size=size))
    out.sort(key=lambda f: natural_key(f.name))
    if max_files and max_files > 0:
        out = out[:max_files]
    return out


def title_from_folder(folder: str) -> str:
    """从批次文件夹名反推文章标题：剥掉 `2026-09-18_164633_` 前缀。

    浏览历史时用得上 —— 标题会参与「另存到…」的新文件夹命名，
    要是把日期前缀也当标题带上，就会变成 `日期_时分秒_日期_时分秒_标题`。
    """
    base = os.path.basename((folder or "").rstrip("\\/"))
    return _FOLDER_PREFIX_RE.sub("", base) or base
