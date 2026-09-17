"""
网络请求与下载线程
FetchWorker：后台线程，请求微信文章 → 提取图片URL → 逐个下载 → 发射信号
纯 requests 实现，无浏览器依赖
"""
import hashlib
from dataclasses import dataclass

import requests
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .extractor import extract_image_urls
from .blocked_config import blocked_config


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
}


@dataclass
class ImageInfo:
    url: str
    index: int
    data: bytes = b""
    content_hash: str = ""
    is_duplicate: bool = False
    dup_of: int = -1
    width: int = 0
    height: int = 0
    ext: str = ".jpg"
    is_mini_square: bool = False


class FetchWorker(QThread):
    progress = Signal(int, int)
    image_loaded = Signal(int, object)
    all_done = Signal(list)
    error = Signal(str)

    def __init__(self, source, is_url=True, include_script_sources=False):
        super().__init__()
        self.source = source
        self.is_url = is_url
        self.include_script_sources = include_script_sources

    def run(self):
        try:
            if self.is_url:
                self.progress.emit(0, 0)
                resp = requests.get(self.source, headers=HEADERS, timeout=30, allow_redirects=True)
                resp.raise_for_status()
                html = resp.text
            else:
                html = self.source

            urls = extract_image_urls(html, include_scripts=self.include_script_sources)
            if not urls:
                self.error.emit(
                    "未在内容中找到图片链接。\n\n"
                    "可能原因：\n"
                    "1. 文章里确实没有图片\n"
                    "2. 页面需要登录/验证才能查看\n"
                    "3. 勾选「补充Script扫描」重新解析试试"
                )
                return

            dl_headers = dict(HEADERS)
            if self.is_url:
                dl_headers['Referer'] = self.source

            images = []
            # 从配置加载屏蔽尺寸（支持热更新：右键屏蔽后下次解析立即生效）
            _UI_EXACT, _AVATAR_EXACT, _COVER_EXACT = blocked_config.get_blocked_sets()

            for i, url in enumerate(urls):
                self.progress.emit(i + 1, len(urls))
                try:
                    r = requests.get(url, headers=dl_headers, timeout=20)
                    r.raise_for_status()
                    data = r.content
                    if len(data) < 500:
                        continue
                    ext = ".jpg"
                    ul = url.lower()
                    if '.png' in ul or 'wx_fmt=png' in ul:
                        ext = ".png"
                    elif '.webp' in ul:
                        ext = ".webp"
                    elif '.gif' in ul:
                        ext = ".gif"
                    img = QImage()
                    img.loadFromData(data)
                    w, h = img.width(), img.height()
                    if w == 0 or h == 0:
                        continue
                    info = ImageInfo(url=url, index=i, data=data, width=w, height=h, ext=ext)
                    max_dim, min_dim = max(w, h), min(w, h)
                    size_key = (w, h)

                    # ── 从 JSON 加载的精确命中尺寸表（blocked_config.get_blocked_sets()）──
                    is_ui_exact = size_key in _UI_EXACT
                    is_avatar_exact = size_key in _AVATAR_EXACT
                    is_cover_exact = size_key in _COVER_EXACT

                    # ── 兜底规则 ──
                    # 小图（最长边 ≤ 200px）
                    is_small = max_dim <= 200 and min_dim > 0
                    # 微信 UI 装饰（RGBA 透明 PNG + 文件 <10KB，不限尺寸）
                    is_ui_noise = (
                        ext == ".png"
                        and img.hasAlphaChannel()
                        and len(data) < 10_000
                    )

                    if is_small or is_ui_noise or is_ui_exact or is_avatar_exact or is_cover_exact:
                        info.is_mini_square = True
                    images.append(info)
                    self.image_loaded.emit(i, info)
                except Exception as e:
                    print(f"  skip [{i}] {url[:80]}... : {e}")

            hash_groups = {}
            for info in images:
                h = hashlib.md5(info.data).hexdigest()
                info.content_hash = h
                hash_groups.setdefault(h, []).append(info)
            for group in hash_groups.values():
                if len(group) > 1:
                    group.sort(key=lambda x: len(x.data), reverse=True)
                    for dup in group[1:]:
                        dup.is_duplicate = True
                        dup.dup_of = group[0].index
            images.sort(key=lambda x: (x.is_duplicate, x.index))
            self.progress.emit(len(urls), len(urls))
            self.all_done.emit(images)
        except requests.RequestException as e:
            self.error.emit(f"网络请求失败: {e}")
        except Exception as e:
            self.error.emit(f"解析失败: {e}")
