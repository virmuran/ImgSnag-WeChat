"""
网络请求与下载线程
FetchWorker：后台线程，请求文章页面 → 经站点适配器提取图片URL → 逐个下载 → 发射信号
纯 requests 实现，无浏览器依赖

站点相关的一切（怎么提取图片、有哪些画质档位、扩展名怎么判）都由
`web_image_dl/sites/` 里的适配器回答，本模块只管「下载 → 校验 → 去重 → 发信号」
这条与站点无关的通用链路。

画质策略：按适配器给出的候选顺序下载（微信是「原图 /0 优先，取不到退 640 压缩档」），
保证「能拿到原图就拿原图，拿不到也不至于整张失败」。
"""
import hashlib
from dataclasses import dataclass

import requests
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .sites import resolve_adapter
from .blocked_config import blocked_config, classify


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
}

#: 小于此字节数的响应视为无效图（错误页/占位图都很小）
MIN_IMAGE_BYTES = 500


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
    has_alpha: bool = False      # 原图是否带透明通道（判定「排版装饰」要用）
    #: 命中的「该隐藏」规则名，见 blocked_config.classify。界面撤销屏蔽时按它重算
    block_reasons: tuple = ()
    original_url: str = ""       # 首选的画质档地址（通常是原图）
    fallback_url: str = ""       # 备选档地址（原图取不到时实际用的那个）
    #: 显示用的真实文件名（历史浏览来自磁盘）。空则界面按序号拼 img_XX.ext ——
    #: 浏览旧图库时用户可能删过或改过名字，按序号显示会张冠李戴
    name_hint: str = ""
    used_original: bool = True   # 实际下载的是否为首选档（原图）

    def classify_reasons(self, blocked_sets=None) -> tuple:
        """按统一规则重算这张图该不该藏（界面撤销屏蔽后调它）"""
        return classify(self.width, self.height, self.ext, len(self.data),
                        self.has_alpha, blocked_sets)


class FetchWorker(QThread):
    progress = Signal(int, int)
    image_loaded = Signal(int, object)
    all_done = Signal(list)
    error = Signal(str)
    cancelled = Signal(list)

    def __init__(self, source, is_url=True, include_script_sources=False,
                 prefer_original=True, adapter=None):
        super().__init__()
        self.source = source
        self.is_url = is_url
        self.include_script_sources = include_script_sources
        self.prefer_original = prefer_original
        #: 站点适配器。默认按来源自动挑；`resolve_adapter` 在认不出时会退回默认站点
        #: （单站点时代等价于「HTML 一律按微信处理」，与改造前行为一致）。
        self.adapter = adapter or resolve_adapter(source, is_url=is_url)
        self._cancelled = False
        #: 文章标题（解析时从 HTML 取出），给下载文件夹命名用。
        #: 不用信号回传是为了不改 all_done 的签名（改了会连带改动别处）；
        #: 界面在 all_done 之后才读，那时 run() 早已写完，不存在竞态。
        self.article_title = ""

    def cancel(self):
        """请求取消。最坏情况要等当前这张图的请求超时（20s）后才会退出循环。"""
        self._cancelled = True

    def _candidate_urls(self, original_url):
        """给出下载候选地址，首选项在前。

        适配器只回答「有哪些画质档位、哪个更好」（首选项在前）；
        「用户想要原图还是压缩版」是界面开关的事，所以偏好重排放在这里 ——
        适配器不需要知道用户偏好，将来新增站点也不必重复实现这个开关。
        """
        candidates = list(self.adapter.candidates(original_url)) if self.adapter else []
        if not candidates:
            candidates = [original_url]
        if not self.prefer_original:
            candidates = list(reversed(candidates))
        return candidates

    @staticmethod
    def _download_one(urls, headers):
        """按候选顺序下载，返回 (实际使用的URL, 数据)；全部失败返回 (None, None)"""
        for u in urls:
            try:
                r = requests.get(u, headers=headers, timeout=20)
                r.raise_for_status()
                if len(r.content) < MIN_IMAGE_BYTES:
                    continue
                return u, r.content
            except Exception as e:
                print(f"  retry next candidate: {u[:70]}... : {e}")
        return None, None

    def run(self):
        try:
            if self.is_url:
                self.progress.emit(0, 0)
                resp = requests.get(self.source, headers=HEADERS, timeout=30, allow_redirects=True)
                resp.raise_for_status()
                html = resp.text
            else:
                html = self.source

            adapter = self.adapter
            if adapter is None:      # 只有注册表为空才可能，但别让线程静默死掉
                self.error.emit("没有可用的站点适配器，无法解析该内容。")
                return

            # 标题先取出来：后面无论从哪条路径返回，界面都能拿到它给文件夹命名
            self.article_title = adapter.extract_title(html)

            urls = []
            if not self._cancelled:
                urls = adapter.extract(html, include_scripts=self.include_script_sources)

            if not urls and not self._cancelled:
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
            hit_counter = {}          # {(分类, 宽, 高): 命中张数}，解析收尾时一次性落盘

            for i, url in enumerate(urls):
                if self._cancelled:
                    break                                   # 跳出后仍走去重/收尾，保留已下载部分
                self.progress.emit(i + 1, len(urls))
                try:
                    candidates = self._candidate_urls(url)
                    used_url, data = self._download_one(candidates, dl_headers)
                    if data is None:
                        continue
                    ext = adapter.ext_for(used_url)
                    img = QImage()
                    img.loadFromData(data)
                    w, h = img.width(), img.height()
                    if w == 0 or h == 0:
                        continue
                    info = ImageInfo(
                        url=used_url, index=i, data=data, width=w, height=h, ext=ext,
                        original_url=url,
                        fallback_url=candidates[-1],
                        used_original=(used_url == url),
                    )
                    # 判定规则只在 blocked_config.classify 里写一份：界面撤销屏蔽后
                    # 要用同一套规则重算，两处各写一份迟早对不上（旧版就是这么散的）
                    has_alpha = img.hasAlphaChannel()
                    reasons = classify(w, h, ext, len(data), has_alpha,
                                       (_UI_EXACT, _AVATAR_EXACT, _COVER_EXACT))
                    info.has_alpha = has_alpha
                    info.block_reasons = reasons
                    info.is_mini_square = bool(reasons)
                    for r in reasons:
                        if r.startswith("blocked:"):
                            k = (r.split(":", 1)[1], w, h)
                            hit_counter[k] = hit_counter.get(k, 0) + 1
                    images.append(info)
                    self.image_loaded.emit(i, info)
                except Exception as e:
                    print(f"  skip [{i}] {url[:80]}... : {e}")

            blocked_config.add_hits(hit_counter)

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

            # 中途取消：去重与排序照常做完，已下载的部分直接交付给界面，只是不发「完成」信号
            if self._cancelled:
                self.cancelled.emit(images)
                return

            self.progress.emit(len(urls), len(urls))
            self.all_done.emit(images)
        except requests.RequestException as e:
            self.error.emit(f"网络请求失败: {e}")
        except Exception as e:
            self.error.emit(f"解析失败: {e}")
