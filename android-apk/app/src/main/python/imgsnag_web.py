# -*- coding: utf-8 -*-
"""ImgSnag 安卓版 —— 界面后端（本地 HTTP 服务 + 会话模型）

────────────────────────────────────────────────────────────────────────
为什么界面做成"内嵌网页"而不是原生控件
────────────────────────────────────────────────────────────────────────
手机端真正麻烦的三件事 —— 图片网格、点开看大图、双指缩放 ——
浏览器天生就做好了：布局、懒加载、图片解码与内存回收、手势、深浅色。
换成原生控件手写要几百行，而且**开发机上没有任何安卓模拟器**，
那几百行一个字都验不了（这个项目的规律：验不了的地方就是出 bug 的地方）。

网页界面则能在电脑上原样跑起来、看到效果、截图比对。于是：

    WebView  ── 加载 ──▶  本模块起的本地服务（只绑 127.0.0.1）
              页面与接口**同源** → 不跨域、不需要 JS↔Java 桥

────────────────────────────────────────────────────────────────────────
分工与握手点
────────────────────────────────────────────────────────────────────────
    imgsnag_android.py   接平台：urllib 网络、安卓证书合并、报告文本
    imgsnag.py           抓取主流程的公共零件（下载、去重、命名、体积）
    web_image_dl/        站点知识（与桌面版/Termux 版同一份，别手改）
    imgsnag_web.py       本文件：会话模型 + HTTP 接口 + 相册对接
    webui.py             页面（HTML/CSS/JS，单文件）

**与 Java 的握手点只有三个**，测试逐条钉住：
    ① 相册入库：本模块调 `Gallery.publishDir(ctx, 目录, 相册子目录)`，拿回成功张数
    ② 分享投递：Java 调 `push_share(文本)`
    ③ 启动：Java 调 `start(ctx, 版本号)`，拿回端口
除这三处之外两边互不依赖 —— Java 越薄，能出错的地方越少。

────────────────────────────────────────────────────────────────────────
画质档位：缩略图与"保存时"用不同档
────────────────────────────────────────────────────────────────────────
微信同一张图有两个地址：`/0` 原图档（实测最大差 4 倍体积）与 `/640` 压缩档。
适配器的 `candidates()` 是**首选项在前**，于是这里：
    缩略图 用候选表**末位**（最小的档）—— 一屏几十张也不心疼流量
    保存时 用候选表**首位**（原图档）  —— 挑中的才下原图
比桌面版更省：桌面版是先把每张按原图拉进内存、再挑哪些落盘。

格式不用地址猜、**用文件头判**（`sniff_ext`）—— 地址猜格式在这个项目上
已经出过一次"整类 png 被存成 .jpg"，内容骗不了人。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import unquote, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import imgsnag                                                       # noqa: E402
import imgsnag_android                                               # noqa: E402
import webui                                                         # noqa: E402
from web_image_dl.history_manager import HistoryManager              # noqa: E402
from web_image_dl.naming import folder_name                          # noqa: E402
from web_image_dl.sites import get_adapter, supported_names          # noqa: E402

APP_LABEL = 'ImgSnag 安卓版'

#: 相册里的子目录名（最终路径 `Pictures/ImgSnagWeChat/<文章文件夹>`）。
#: 取值来自同步进来的 `imgsnag.DEFAULT_SUBDIR` —— 只有一处定义。
#: Java 侧 `Gallery.ALBUM` 必须与它一致，测试钉住这条；不一致的表现是
#: "图抓到了但相册里没有"，而且两边都不报错（本项目踩过一次的坑）。
ALBUM_SUBDIR = imgsnag.DEFAULT_SUBDIR

#: 相册入库的 Java 类。**全限定名** —— Chaquopy 的 `java.jclass()` 只认这个写法。
GALLERY_CLASS = 'com.virmuran.imgsnag.Gallery'

#: 应用私有目录下的布局
SESSION_DIR = 'sessions'
THUMB_DIR = 'thumb'
FULL_DIR = 'full'
OUT_DIR = 'out'
HISTORY_DB = 'history.db'

#: 保留几份会话缓存（含缩略图）。图本身在相册里，这里只留到"能再打开一次"的程度。
MAX_SESSIONS = 5

#: 同时最多几张图在下载（浏览器连接多，不限会一次开几十个）
MAX_CONCURRENT_DOWNLOADS = 4

_LOG_MAX = 200

_MIME = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp',
}


# ============================================================================
#  小工具
# ============================================================================

def session_id(source: str) -> str:
    """由文章地址推出会话目录名。

    用地址而不是时间戳：同一篇文章重新解析时**复用同一份缓存**；
    历史库里一行也就能直接对应到缓存目录 —— 不必给共享的数据库加字段。
    """
    return hashlib.md5((source or '').encode('utf-8')).hexdigest()[:12]


def sniff_ext(data: bytes, fallback: str = '.jpg') -> str:
    """从文件头判断图片真实格式，决定存盘扩展名与 Content-Type。"""
    if not data:
        return fallback
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if data[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if data[:4] == b'GIF8':
        return '.gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return fallback


def mime_of(ext: str) -> str:
    return _MIME.get((ext or '').lower(), 'application/octet-stream')


def write_atomic(path: str, data: bytes) -> None:
    """先写临时文件再 os.replace —— 中途被杀不会留下半截图片。"""
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def clear_dir(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def read_head(path: str, n: int = 16) -> bytes:
    try:
        with open(path, 'rb') as f:
            return f.read(n)
    except OSError:
        return b''


def as_int(value, default=0) -> int:
    """把 JSON 里的数字/数字字符串取成 int（前端传来的东西不能信）。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def pretty_time(iso: str) -> str:
    """把 ISO 时间转成人话：今天 14:03 / 昨天 09:12 / 10-04 20:31"""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ''
    now = datetime.now()
    if dt.date() == now.date():
        return f'今天 {dt:%H:%M}'
    if (now.date() - dt.date()).days == 1:
        return f'昨天 {dt:%H:%M}'
    if dt.year == now.year:
        return f'{dt:%m-%d %H:%M}'
    return f'{dt:%Y-%m-%d}'


def title_from_path(path: str) -> str:
    """从 `Pictures/ImgSnagWeChat/2026-10-06_1133_标题` 里取回标题。

    历史库里没有标题列，而文件夹名本来就是 `日期_时分秒_标题` ——
    标题就在里面，比给共享的数据库加一列划算。
    """
    name = (path or '').replace('\\', '/').rstrip('/').split('/')[-1]
    parts = name.split('_', 2)
    return parts[2] if len(parts) == 3 else ''


@dataclass
class Item:
    """一张待处理的图：两个档位的地址都留着，用哪档由调用方决定。"""
    i: int
    cands: list = field(default_factory=list)      # 首选项在前（原图档在前）

    @property
    def original(self) -> str:
        return self.cands[0] if self.cands else ''

    def thumb_candidates(self) -> list:
        """缩略图用最小档优先，失败才退回大档（顺序颠倒即可）。"""
        return list(reversed(self.cands))

    def to_json(self) -> dict:
        return {'i': self.i, 'cands': list(self.cands)}

    @staticmethod
    def from_json(d) -> 'Item':
        d = d or {}
        return Item(i=as_int(d.get('i')), cands=[str(u) for u in (d.get('cands') or [])])


class NotFound(Exception):
    """不是本次会话的图 / 序号越界。"""


class Failed(Exception):
    """图确实下不下来。"""


# ============================================================================
#  会话引擎
# ============================================================================

class Engine:
    """一次「解析 → 挑图 → 保存」的过程，以及它在磁盘上的缓存。

    状态机：idle → parsing → ready → saving → saved
                      ↘ error（任何一步失败都落到这里，并带上人话说明）

    `saved` 之后仍然可以再保存（改选几张、补存漏掉的），所以 save() 同时接受
    ready 与 saved 两个状态 —— 不允许"只能保存一次"，那是数据丢失式的设计。
    """

    def __init__(self, root=None, get=None, publish=None, when=None, version=''):
        self.root = root or os.path.expanduser('~')
        os.makedirs(self.root, exist_ok=True)
        #: 注入点：测试用假网络、假相册；None = 走真实实现
        self.get = get
        self.publish = publish
        #: 注入点：给文件夹名一个「假装是别的时刻」的机会；不注入就用当前时间。
        #: 写成 `lambda: None` 而不是留着 None —— 调用处是 `self.when()`，
        #: 留着 None 会炸在 "'NoneType' object is not callable"（测试当场抓到过）。
        self.when = when or (lambda: None)
        self.version = version

        self.lock = threading.RLock()
        self._sem = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self._locks: dict = {}
        self.hist = HistoryManager(db_path=os.path.join(self.root, HISTORY_DB))

        self._reset()

    # ── 状态 ────────────────────────────────────────────────────────────────

    def _reset(self):
        """回到"没有会话"的状态。调用方需持有 self.lock（构造时除外）。"""
        self.phase = 'idle'
        self.message = ''
        self.title = ''
        self.folder = ''
        self.source = ''
        self.is_url = True
        self.sid = ''
        self.items: list = []
        self.progress = {'done': 0, 'total': 0}
        #: 保存进度。**名字必须与 save() 方法区分开** —— 早先这里叫 `self.save`，
        #: 于是把同名方法覆盖成了字典，`engine.save(...)` 当场报
        #: "'dict' object is not callable"（测试抓到的）。加实例属性前先看一眼
        #: 有没有同名方法：Python 不会为此报警，只会在这个方法被调时炸。
        self.save_progress = {'done': 0, 'total': 0}
        self.entry_id = 0
        self.pending_share = ''
        self.logs: list = []
        self._headers = imgsnag.build_headers('')
        self._locks = {}

    def log_line(self, text: str):
        with self.lock:
            self.logs.append(f'{datetime.now():%H:%M:%S} {text}')
            if len(self.logs) > _LOG_MAX:
                del self.logs[:len(self.logs) - _LOG_MAX]

    def state(self) -> dict:
        with self.lock:
            return {
                'phase': self.phase,
                'message': self.message,
                'title': self.title,
                'folder': self.folder,
                'source': self.source,
                'sid': self.sid,
                'count': len(self.items),
                'progress': dict(self.progress),
                'save': dict(self.save_progress),
                'log': list(self.logs),
                'sites': supported_names(),
                'album': ALBUM_SUBDIR,
                'label': APP_LABEL,
                'version': self.version,
            }

    # ── 路径 ────────────────────────────────────────────────────────────────

    def session_dir(self, sid: str) -> str:
        return os.path.join(self.root, SESSION_DIR, sid or '_')

    def _session_json(self, sid: str) -> str:
        return os.path.join(self.session_dir(sid), 'session.json')

    def _net(self):
        """真实网络函数：urllib + 安卓证书合并（电脑上就是普通 urllib）。"""
        if self.get is None:
            self.get = imgsnag_android.make_get(
                imgsnag_android.ssl_context(cache_dir=self.root))
        return self.get

    # ── ① 解析 ──────────────────────────────────────────────────────────────

    def parse(self, text: str) -> bool:
        """开始解析（链接 / 分享文本 / 网页源码）。正在解析或保存时拒绝。"""
        with self.lock:
            if self.phase in ('parsing', 'saving'):
                return False
            self._reset()
            self.phase = 'parsing'
            self.message = '正在打开文章…'
        self.log_line(f'开始解析：{(text or "")[:120]}')
        threading.Thread(target=self._parse_worker, args=(text or '',),
                         daemon=True).start()
        return True

    def _parse_worker(self, text: str):
        try:
            source, is_url = imgsnag.resolve_source(text)
            if not source:
                return self._fail('没能从这段内容里找到链接。\n'
                                  '可以复制文章链接后再试一次。')

            adapter = get_adapter(source, is_url=is_url)
            if adapter is None:
                return self._fail(f'这个链接不认识。\n目前支持：{supported_names()}\n'
                                  f'（认出来的是 {source[:80]}）')

            self.is_url = is_url
            if is_url:
                self.source = source
                try:
                    html = imgsnag.fetch_html(source, get=self._net())
                except Exception as e:                              # noqa: BLE001
                    return self._fail('打开文章失败：'
                                      + imgsnag_android.polish(str(e)))
                self._headers = imgsnag.build_headers(source)
            else:
                # 粘的是源码：历史里不能把整页 HTML 塞进 URL 列，压成一个短标记
                self.source = 'html:' + hashlib.md5(source.encode('utf-8')).hexdigest()[:12]
                html = source
                self._headers = imgsnag.build_headers('')

            title = adapter.extract_title(html)
            urls = adapter.extract(html, include_scripts=False)
            if not urls and is_url:
                self.log_line('正文里没直接看到图，补扫 <script> 再找一遍…')
                urls = adapter.extract(html, include_scripts=True)
            if not urls:
                return self._fail('这篇文章里没找到图片。\n'
                                  '可能原因：文章确实没图 / 需要登录才能看 / '
                                  '图藏在脚本里（已自动补扫过一次）。')

            items = [Item(i=i, cands=list(adapter.candidates(u)) or [u])
                     for i, u in enumerate(urls, 1)]
            sid = session_id(self.source)
            title_ok = title or adapter.fallback_title
            folder = folder_name(title, when=self.when(),
                                 fallback=adapter.fallback_title)
            self._write_session(sid, title, folder, items, adapter)

            with self.lock:
                self.items = items
                self.sid = sid
                self.title = title_ok
                self.folder = folder
                self.phase = 'ready'
                self.message = f'找到 {len(items)} 张图'
            self.log_line(f'标题：{title_ok}')
            self.log_line(f'共 {len(items)} 张；缩略图按需加载，选中保存时才取原图')

            # 历史先落一条"已解析"，保存成功后同一条会被更新成"已下载"
            try:
                self.entry_id = self.hist.add(self.source, total_images=len(items),
                                              status='scanned', note=title_ok)
            except Exception as e:                                  # noqa: BLE001
                self.log_line(f'历史写入失败：{e}')
            self._prune()

        except Exception as e:                                      # noqa: BLE001
            traceback.print_exc()
            self._fail(f'{type(e).__name__}: {e}')

    def _fail(self, message: str):
        with self.lock:
            self.phase = 'error'
            self.message = imgsnag_android.polish(message)
        self.log_line('失败：' + (message or '').split('\n')[0])

    def _write_session(self, sid: str, title: str, folder: str, items, adapter):
        """落一份 session.json —— 历史里"点开这一次"全靠它恢复。

        **必须存下每张图的地址**：不然"打开上次那篇"就只剩一排空壳，
        选了也下不动（这是本文件第一版的 bug，写在这里免得再犯）。
        """
        base = self.session_dir(sid)
        os.makedirs(base, exist_ok=True)
        clear_dir(os.path.join(base, THUMB_DIR))
        clear_dir(os.path.join(base, FULL_DIR))
        data = {
            'source': self.source,
            'is_url': self.is_url,
            'title': title,
            'folder': folder,
            'adapter': adapter.name,
            'when': datetime.now().isoformat(),
            'items': [it.to_json() for it in items],
        }
        write_atomic(self._session_json(sid),
                     json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _prune(self):
        """只留最近 MAX_SESSIONS 份会话缓存（缩略图占地，图本身在相册里）。"""
        base = os.path.join(self.root, SESSION_DIR)
        try:
            subs = [(os.path.getmtime(os.path.join(base, n)), os.path.join(base, n))
                    for n in os.listdir(base)]
        except OSError:
            return
        subs.sort(reverse=True)
        for _, path in subs[MAX_SESSIONS:]:
            clear_dir(path)

    # ── ② 图片（按需下载 + 磁盘缓存）─────────────────────────────────────────

    def _item(self, sid: str, i: int) -> Item:
        with self.lock:
            if not sid or sid != self.sid:
                raise NotFound('会话已切换')
            if not (1 <= i <= len(self.items)):
                raise NotFound('没有这张图')
            return self.items[i - 1]

    def _lock_for(self, key):
        with self.lock:
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    def _fetch_item(self, sid: str, kind: str, i: int) -> tuple:
        """确保某张图的某个档位已在本机，返回 (路径, 扩展名)。

        `kind`：`t` 缩略图（最小档优先）/ `f` 原图（原图档优先）。
        同一张图被多个请求同时要时，只有第一个真的去下，其余等它。
        """
        it = self._item(sid, i)
        sub = THUMB_DIR if kind == 't' else FULL_DIR
        folder = os.path.join(self.session_dir(sid), sub)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{i}.img')

        if os.path.exists(path):
            return path, sniff_ext(read_head(path))

        with self._lock_for((sid, kind, i)):
            if os.path.exists(path):
                return path, sniff_ext(read_head(path))
            cands = it.thumb_candidates() if kind == 't' else list(it.cands)
            if kind == 'f' and len(self.items) > 1:
                self.log_line(f'取原图 {i}/{len(self.items)}…')
            try:
                self._sem.acquire()
                try:
                    used, data = imgsnag.download_one(cands, self._headers, self._net())
                finally:
                    self._sem.release()
            except Exception as e:                                  # noqa: BLE001
                self.log_line(f'第 {i} 张出错：{type(e).__name__}: {e}')
                used, data = None, None
            if not data:
                raise Failed(f'第 {i} 张没下下来')
            write_atomic(path, data)
            if used and used != it.original:
                self.log_line(f'第 {i} 张用了压缩档（原图档取不到）')
            return path, sniff_ext(data)

    def image(self, sid: str, kind: str, i: int) -> tuple:
        """给 HTTP 层用：返回 (路径, mime)。失败抛 NotFound / Failed。"""
        path, ext = self._fetch_item(sid, kind, i)
        return path, mime_of(ext)

    # ── ③ 保存到相册 ────────────────────────────────────────────────────────

    def save(self, idxs) -> bool:
        """把选中的图按原图档下载、去重，交给 Java 写进相册。"""
        with self.lock:
            if self.phase not in ('ready', 'saved'):
                return False
            want = sorted({as_int(x) for x in (idxs or [])})
            want = [x for x in want if 1 <= x <= len(self.items)]
            if not want:
                return False
            self.save_progress = {'done': 0, 'total': len(want)}
            self.phase = 'saving'
            self.message = f'正在保存 0/{len(want)}…'
        self.log_line(f'开始保存 {len(want)} 张')
        threading.Thread(target=self._save_worker, args=(want,), daemon=True).start()
        return True

    def _save_worker(self, idxs):
        sid, folder = self.sid, self.folder
        out = os.path.join(self.session_dir(sid), OUT_DIR)
        clear_dir(out)
        os.makedirs(out, exist_ok=True)

        seen, kept, dup, fail = set(), 0, 0, 0
        try:
            for n, i in enumerate(idxs, 1):
                try:
                    path, ext = self._fetch_item(sid, 'f', i)
                    with open(path, 'rb') as f:
                        data = f.read()
                except (Failed, NotFound, OSError) as e:
                    fail += 1
                    self.log_line(f'第 {i} 张跳过：{e}')
                else:
                    h = hashlib.md5(data).hexdigest()
                    if h in seen:
                        dup += 1
                        self.log_line(f'第 {i} 张与前面某张重复，跳过')
                    else:
                        seen.add(h)
                        kept += 1
                        ext = sniff_ext(data, ext)
                        write_atomic(os.path.join(out, f'img_{kept:02d}{ext}'), data)
                with self.lock:
                    self.save_progress['done'] = n
                    self.message = f'正在保存 {n}/{len(idxs)}…'
        except Exception as e:                                      # noqa: BLE001
            traceback.print_exc()
            clear_dir(out)
            self._fail(f'保存出错：{type(e).__name__}: {e}')
            return

        if not kept:
            return self._finish(out, 0, dup, fail, folder)

        try:
            published = int(self._publish(out, folder) or 0)
        except Exception as e:                                      # noqa: BLE001
            traceback.print_exc()
            clear_dir(out)
            return self._fail(f'写进相册失败：{type(e).__name__}: {e}')
        self._finish(out, published, dup, fail, folder)

    def _publish(self, out_dir: str, folder: str) -> int:
        """交给 Java 写系统相册。

        **只有这一处需要 Context**（MediaStore 是安卓特有的），所以 Java 在
        `start()` 里把 Application 上下文交过来，这里转手传给 Gallery。
        电脑上跑（本地开发/测试）没有 Java —— 这时必须由调用方注入 publish。
        """
        if self.publish is None:
            raise RuntimeError('没有接入相册（电脑上跑时应当注入一个假的）')
        return self.publish(out_dir, folder)

    def _finish(self, out: str, published: int, dup: int, fail: int, folder: str):
        clear_dir(out)
        # 原图缓存不留在磁盘上 —— 图已经在相册里了，留着白占几十兆
        clear_dir(os.path.join(self.session_dir(self.sid), FULL_DIR))

        path = f'Pictures/{ALBUM_SUBDIR}/{folder}'
        if published:
            msg = f'已存入相册：{path}（{published} 张）'
            if dup:
                msg += f'\n另有 {dup} 张与前面的重复，已跳过'
            if fail:
                msg += f'\n{fail} 张没下下来'
        elif dup and not fail:
            msg = f'选中的图都已经在相册里了（{dup} 张全部重复）'
        else:
            msg = '没有可保存的图片：都没下下来，换个网络再试'

        status = 'done' if (published and not fail) else ('partial' if published else 'failed')
        try:
            if self.entry_id:
                self.hist.update(self.entry_id, success_images=published,
                                 save_path=path if published else '',
                                 status=status, note=self.title)
            else:
                self.entry_id = self.hist.add(self.source, total_images=len(self.items),
                                              success_images=published,
                                              save_path=path if published else '',
                                              status=status, note=self.title)
        except Exception as e:                                      # noqa: BLE001
            self.log_line(f'历史写入失败：{e}')

        with self.lock:
            self.phase = 'saved'
            self.message = msg
        self.log_line(msg.replace('\n', ' '))

    # ── ④ 历史 ──────────────────────────────────────────────────────────────

    def history(self) -> list:
        out = []
        for e in self.hist.get_all(limit=200):
            sid = session_id(e.source_url)
            out.append({
                'id': e.id,
                'url': e.source_url,
                'title': e.note or title_from_path(e.save_path) or '（无标题）',
                'time': pretty_time(e.parsed_at),
                'total': e.total_images,
                'success': e.success_images,
                'status': e.status,
                'status_text': e.status_text(),
                'path': e.save_path,
                'has_cache': os.path.exists(self._session_json(sid)),
            })
        return out

    def open_history(self, entry_id) -> dict:
        """把某条历史恢复成"当前会话"，之后就能正常挑图 / 补存 / 重新保存。"""
        target = as_int(entry_id)
        row = next((e for e in self.hist.get_all(limit=200) if e.id == target), None)
        if row is None:
            return {'ok': False, 'reason': 'missing', 'message': '这条记录已经不在了'}

        sid = session_id(row.source_url)
        json_path = self._session_json(sid)
        if not os.path.exists(json_path):
            return {'ok': False, 'reason': 'nocache', 'source': row.source_url,
                    'message': '这次的图片缓存已经清掉了，可以重新解析一次'}
        try:
            with open(json_path, encoding='utf-8') as f:
                data = json.load(f)
            items = [Item.from_json(d) for d in (data.get('items') or [])]
        except Exception as e:                                      # noqa: BLE001
            return {'ok': False, 'reason': 'error', 'message': f'读缓存失败：{e}'}
        if not items:
            return {'ok': False, 'reason': 'nocache', 'source': row.source_url,
                    'message': '缓存不完整，重新解析一次吧'}

        folder = (data.get('folder')
                  or (row.save_path or '').replace('\\', '/').rstrip('/').split('/')[-1]
                  or title_from_path(row.save_path)
                  or '图片')
        with self.lock:
            if self.phase in ('parsing', 'saving'):
                return {'ok': False, 'reason': 'busy', 'message': '正在忙，稍等一下'}
            self._reset()
            self.source = data.get('source') or row.source_url
            self.is_url = bool(data.get('is_url', True))
            self.sid = sid
            self.title = data.get('title') or title_from_path(row.save_path) or '（无标题）'
            self.folder = folder
            self.items = items
            self.entry_id = row.id
            self.phase = 'ready'
            self.message = f'已载入上次保存的 {len(items)} 张图'
            if self.is_url:
                self._headers = imgsnag.build_headers(self.source)
        self.log_line(f'载入历史会话 {sid}（{len(items)} 张）')
        return {'ok': True, 'count': len(items), 'title': self.title}

    def delete_history(self, entry_id) -> bool:
        try:
            self.hist.delete(as_int(entry_id))
            return True
        except Exception:                                           # noqa: BLE001
            return False


# ============================================================================
#  HTTP 服务
# ============================================================================

class _Server(ThreadingMixIn, HTTPServer):
    """每连接一个线程的最简 HTTP 服务。

    `serve_forever` 自己写：标准实现要过 selectors（select/epoll），
    而这里只需要一个阻塞 accept 循环 —— 少一层依赖，就少一种"在手机上
    表现不一样"的可能。`shutdown()` 靠关掉监听套接字把 accept 叫醒。
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler):
        self._stopped = threading.Event()
        super().__init__(addr, handler)

    def serve_forever(self, poll_interval=0.5):
        while not self._stopped.is_set():
            try:
                conn, addr = self.socket.accept()
            except OSError:
                if self._stopped.is_set():
                    break
                time.sleep(0.05)
                continue
            try:
                self.process_request(conn, addr)
            except Exception:                                       # noqa: BLE001
                try:
                    conn.close()
                except OSError:
                    pass

    def shutdown(self):
        self._stopped.set()
        try:
            self.socket.close()
        except OSError:
            pass


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'ImgSnag'
        timeout = 60                       # keep-alive 空闲上限，防止线程被吊死

        # ── 日志：只记出错的请求，不然日志会被每张缩略图刷屏 ──
        def log_request(self, code='-', size='-'):                  # noqa: A003
            try:
                if int(code) >= 400:
                    engine.log_line(f'http {code} {self.path}')
            except (TypeError, ValueError):
                pass

        def log_message(self, fmt, *args):                          # noqa: A003
            pass

        # ── 输出 ──
        def _send(self, code, body: bytes, ctype: str, cache='no-store'):
            try:
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', cache)
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass                                            # 用户划走了，正常

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

        def _body(self) -> dict:
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode('utf-8')) or {}
            except Exception:                                       # noqa: BLE001
                return {}

        # ── 路由 ──
        def do_GET(self):                                           # noqa: N802
            path = unquote(urlparse(self.path).path)

            if path in ('/', '/index.html'):
                return self._send(200, webui.PAGE.encode('utf-8'),
                                  'text/html; charset=utf-8')

            if path == '/api/state':
                return self._json(engine.state())

            if path == '/api/poll':
                # 轻量轮询：把「分享过来的文本」捎带回去，顺带最新状态。
                # ⚠ **不能在这里清掉** share —— 页面若正忙（正在解析/保存），
                # 这一轮取走了却没法处理，分享的内容就丢了。清空交给
                # `Engine.parse()`：真正开始干活的那一刻才算消耗掉。
                st = engine.state()
                with engine.lock:
                    st['share'] = engine.pending_share
                return self._json(st)

            if path == '/api/history':
                return self._json({'items': engine.history()})

            if path.startswith('/img/'):
                parts = path.strip('/').split('/')          # img/<sid>/<kind>/<i>
                if len(parts) == 4 and parts[2] in ('t', 'f'):
                    try:
                        i = int(parts[3])
                    except ValueError:
                        return self._json({'error': 'bad index'}, 404)
                    try:
                        p, mime = engine.image(parts[1], parts[2], i)
                    except NotFound as e:
                        return self._json({'error': str(e)}, 404)
                    except Failed as e:
                        return self._json({'error': str(e)}, 502)
                    try:
                        with open(p, 'rb') as f:
                            data = f.read()
                    except OSError as e:
                        return self._json({'error': str(e)}, 500)
                    cache = ('public, max-age=86400' if parts[2] == 'f'
                             else 'public, max-age=600')
                    return self._send(200, data, mime, cache)
            return self._json({'error': 'not found'}, 404)

        def do_POST(self):                                          # noqa: N802
            path = unquote(urlparse(self.path).path)
            data = self._body()

            if path == '/api/parse':
                ok = engine.parse(str(data.get('source') or ''))
                return self._json({'ok': ok, 'state': engine.state()})

            if path == '/api/save':
                ok = engine.save(data.get('idxs') or [])
                return self._json({'ok': ok, 'state': engine.state()})

            if path == '/api/history/open':
                return self._json(engine.open_history(data.get('id') or 0))

            if path == '/api/history/delete':
                return self._json({'ok': engine.delete_history(data.get('id') or 0)})

            return self._json({'error': 'not found'}, 404)

    return Handler


# ============================================================================
#  入口（Java 调用 / 电脑上手动跑）
# ============================================================================

_ENGINE = None
_SERVER = None
_THREAD = None


def push_share(text: str) -> bool:
    """Java 把「分享过来的文本」投递进来，页面下一轮轮询就取走。

    这里**不直接解析**：页面可能还没加载完、或用户正在看上一篇文章，
    由界面决定什么时候开始 —— 服务端只负责把话带到。
    """
    if _ENGINE is None:
        return False
    with _ENGINE.lock:
        _ENGINE.pending_share = text or ''
    _ENGINE.log_line('收到分享内容')
    return True


def engine() -> Engine:
    return _ENGINE


def start(ctx=None, version: str = '', root: str = None, get=None,
          publish=None, host: str = '127.0.0.1', port: int = 0) -> int:
    """起本地服务，返回端口号（Java 用它拼出要加载的网址）。

    只绑 `127.0.0.1`：同一 Wi-Fi 下的别的设备也连不上；而且清单里
    只需要为这一条回环地址放行明文 HTTP。

    `ctx` 是安卓的 Application 上下文（电脑上传 None）。
    """
    global _ENGINE, _SERVER, _THREAD
    if _SERVER is not None:
        return _SERVER.server_address[1]

    _ENGINE = Engine(root=root, get=get, publish=publish or _java_publish(ctx),
                     version=version)
    _ENGINE.log_line(f'{APP_LABEL} {version}'.strip())
    _SERVER = _Server((host, port), make_handler(_ENGINE))
    _THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    _THREAD.start()
    _ENGINE.log_line(f'服务已就绪：http://{host}:{_SERVER.server_address[1]}/')
    return _SERVER.server_address[1]


def _java_publish(ctx):
    """把"写进相册"接到 Java 的 `Gallery.publishDir`；没有 ctx 就返回 None。"""
    if ctx is None:
        return None

    def _pub(out_dir: str, album_sub: str) -> int:
        from java import jclass                                    # noqa: PLC0415
        Gallery = jclass(GALLERY_CLASS)
        return int(Gallery.publishDir(ctx, out_dir, album_sub))

    return _pub


def stop():
    """关服务（测试与退出时用）。"""
    global _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER = None


if __name__ == '__main__':
    # 电脑上手动跑：起服务，然后浏览器打开打印出来的地址
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    work = os.path.join(os.path.expanduser('~'), '.imgsnag_web_try')

    def _fake_album(out_dir, sub):
        dest = os.path.join(work, 'album', sub)
        os.makedirs(dest, exist_ok=True)
        n = 0
        for name in sorted(os.listdir(out_dir)):
            shutil.copyfile(os.path.join(out_dir, name), os.path.join(dest, name))
            n += 1
        print(f'（假相册）拷了 {n} 张到 {dest}')
        return n

    p = start(root=work, version='dev', publish=_fake_album)
    print(f'打开： http://127.0.0.1:{p}/')
    print(f'数据目录：{work}   Ctrl+C 结束')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n结束')
