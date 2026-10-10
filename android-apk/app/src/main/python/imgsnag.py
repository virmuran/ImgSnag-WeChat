#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ImgSnag Termux —— 在手机上抓取微信公众号文章的原图

用法（在 Termux 里）：

    imgsnag <文章链接>           抓取并保存到手机相册目录
    imgsnag --list <链接>        只列出会下载哪些图，不下载
    imgsnag --out DIR <链接>     存到指定目录
    imgsnag --html 文件.html     从本地 HTML 解析（链接打不开时的退路）
    imgsnag -h                   全部参数

也可以直接从别的 App「分享 → Termux」，链接会由 termux-url-opener 自动传进来。

────────────────────────────────────────────────────────────────────────
它和桌面版是什么关系
────────────────────────────────────────────────────────────────────────
**解析图片的那套逻辑一行都没重写** —— 完全复用项目里 `web_image_dl/` 下的纯逻辑
模块（`sites/` 站点适配器 + `naming/` 命名 + `extractor/` 清洗）。那几个模块零 Qt、
零第三方依赖，所以能原样搬到手机上；它们在 `app/web_image_dl/` 下有一份由
`android/sync_core.py` **自动生成**的副本，**不要手改副本**（改了会被下次同步覆盖，
而且有条测试专门盯着这一点）。

本文件负责的是桌面版里由 Qt 承担的那部分：HTTP 抓取 → 按画质档位下载 →
校验去重 → 落盘 → 进度输出。也就是说：

    桌面版的 app.py / worker.py  ←(重写)←  imgsnag.py
    桌面版的 web_image_dl/sites/ ←(原样搬)←  app/web_image_dl/sites/

所以微信规则一改，两边同时受益（跑一次 sync_core.py 即可）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

# 让脚本无论从哪个目录被调起，都能 import 到同级的 web_image_dl/
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from web_image_dl.naming import folder_name, unique_dir          # noqa: E402
from web_image_dl.sites import get_adapter, supported_names      # noqa: E402
from web_image_dl.sites.base import (                            # noqa: E402
    CREDIT_FILE, CREDIT_NOTICE, credit_line)


APP_NAME = 'ImgSnag Termux'
APP_VERSION = '1.0'

#: 与桌面版 worker.py 保持一致的请求头。少一个都可能被 CDN 当爬虫拒绝
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
}

#: 小于此字节数的响应视为无效图（错误页/占位图都很小）。与桌面版同值
MIN_IMAGE_BYTES = 500

#: 手机相册目录下给本工具用的子文件夹名
DEFAULT_SUBDIR = 'ImgSnag'

#: 从「分享」文本里抠链接。微信分享出来常常是「标题 + 换行 + 链接」，
#: 所以不能假设整段就是 URL。右侧字符类要排除中文标点与各种括号，
#: 否则会把后面紧跟着的中文一起吞进地址里。
_URL_RE = re.compile(r'https?://[^\s"\'<>()\[\]{}，。；、）】》]+', re.I)


# ============================================================================
#  输入解析
# ============================================================================

def extract_url(text: str) -> str:
    """从一段文本里取出第一个 http(s) 链接；取不到返回空串。

    存在的理由很具体：从微信「分享 → Termux」，Termux 传过来的是**整段分享文本**
    （标题、来源、链接混在一起），不是一个干净的 URL。直接拿去请求会失败。
    地址末尾可能粘上英文标点（`…/abc.`、`…/abc,`），所以还要剥一层尾巴。
    """
    m = _URL_RE.search(text or '')
    if not m:
        return ''
    return m.group(0).rstrip('.,;:!?、')


def resolve_source(raw: str):
    """判断用户给的是「链接/分享文本」还是「直接粘的 HTML 源码」。

    返回 (文本, 是否当作网址)。判据是首个非空字符是不是 `<` ——
    HTML 文档一定以标签开头，而网址不会。这个判定和桌面版 `sites.get_adapter`
    里的自动判定保持一致（那边也是看首字符）。
    """
    s = (raw or '').strip()
    if not s:
        return '', True
    if s.lstrip().startswith('<'):
        return s, False
    url = extract_url(s)
    if url:
        return url, True
    return s, True          # 没有协议头也交给适配器判（它自己会补 https:// 再解析）


# ============================================================================
#  保存位置
# ============================================================================

def shared_storage() -> str:
    """手机内部存储根目录（相册会索引的地方）。没授权存储权限时返回空串。

    Termux 里 `~/storage/shared` 是指向 `/storage/emulated/0` 的软链，
    由 `termux-setup-storage` 创建。它不存在 = 用户还没授权，
    这时**不要硬写** —— 写了也读不到，还会让用户以为图片丢了。
    """
    cand = os.path.join(os.path.expanduser('~'), 'storage', 'shared')
    return cand if os.path.isdir(cand) else ''


def default_out_dir() -> str:
    """决定默认保存到哪儿。三级回退，顺序即优先级：

      1. 环境变量 `IMGSNAG_OUT` —— 给愿意自己定路径的人留的口子
      2. 手机内部存储 `Pictures/ImgSnag/` —— 相册能扫到，**正常走这条**
      3. `~/imgsnag_downloads/` —— 没授权存储权限时的退路（在 Termux 私有目录里，
         相册看不到，需要文件管理器手动翻）
    """
    env = os.environ.get('IMGSNAG_OUT', '').strip()
    if env:
        return env
    shared = shared_storage()
    if shared:
        return os.path.join(shared, 'Pictures', DEFAULT_SUBDIR)
    return os.path.join(os.path.expanduser('~'), 'imgsnag_downloads')


# ============================================================================
#  网络
# ============================================================================

def make_get(insecure: bool = False):
    """返回一个 `get(url, **kw)` 函数。

    统一成这个形态是为了让测试能整体替换掉网络层（传 `get=` 进来即可），
    也让 `--insecure` 的实现只改一处。
    """
    import requests

    if not insecure:
        return requests.get

    import urllib3
    urllib3.disable_warnings()

    def _get(url, **kw):
        kw['verify'] = False
        return requests.get(url, **kw)

    return _get


def build_headers(referer: str = '') -> dict:
    """构造请求头。Referer 由**站点适配器**给出（SiteAdapter.download_referer）。

    大部分图床需要它校验来源页，但 cosmeitu 的 ciyuandao 正相反 —— 阿里云 OSS
    的防盗链被配成「带了 Referer 就拒」，带上反而整批图全 403。所以这个值不写死，
    由调用方从适配器取；**空串表示不带这个头**。
    """
    h = dict(HEADERS)
    if referer:
        h['Referer'] = referer
    return h


def fetch_html(url: str, get) -> str:
    r = get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text


def download_one(candidates, headers, get):
    """按候选顺序下载一张图。

    `candidates` 是画质档位列表，首选项在前（微信是「/0 原图 → /640 压缩」）。
    逐个试，能拿到哪个用哪个 —— 保证「能拿原图就拿原图，拿不到也不至于整张失败」。
    全部失败返回 (None, None)。

    小于 MIN_IMAGE_BYTES 的响应直接当失败：CDN 出错时返回的不是 HTTP 错误码，
    而是一个几百字节的占位图，光看状态码发现不了。
    """
    for u in candidates:
        try:
            r = get(u, headers=headers, timeout=20)
            r.raise_for_status()
            if len(r.content) < MIN_IMAGE_BYTES:
                continue
            return u, r.content
        except Exception:
            continue
    return None, None


# ============================================================================
#  落盘
# ============================================================================

def unique_path(path: str) -> str:
    """路径已存在就加 `_2`、`_3`…… 绝不覆盖。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f'{stem}_{i}{ext}'):
        i += 1
    return f'{stem}_{i}{ext}'


def human_size(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / 1048576:.1f} MB'


def dedup_by_content(records):
    """按内容哈希去重，**保持原顺序**，同一份内容只留最先出现的那条。

    同一张图在文章里被贴两次（或大小图指向同一文件）时会命中。
    与桌面版行为一致：留一张，其余丢弃。

    `records` 是 [(序号, 地址, 数据, 扩展名)]。
    """
    seen, out = {}, []
    for idx, url, data, ext in records:
        h = hashlib.md5(data).hexdigest()
        if h in seen:
            continue
        seen[h] = idx
        out.append((idx, url, data, ext))
    return out


def rescan_dir(folder: str) -> bool:
    """让系统相册立刻索引新目录（需装 Termux:API，没装就静默跳过）。

    不装也不是不行 —— Android 会自己扫到 Pictures/ 下的新内容，
    只是可能要等一会儿、或者需要重启相册 App。装了就是即时的。
    """
    exe = shutil.which('termux-media-scan')
    if not exe:
        return False
    try:
        subprocess.run([exe, '-r', folder], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def clipboard_text() -> str:
    """读系统剪贴板（需 Termux:API）。这是「复制链接后直接跑 imgsnag」的路子。"""
    exe = shutil.which('termux-clipboard-get')
    if not exe:
        return ''
    try:
        r = subprocess.run([exe], capture_output=True, timeout=15)
        return r.stdout.decode('utf-8', 'replace').strip()
    except Exception:
        return ''


# ============================================================================
#  主流程
# ============================================================================

def write_credit_file(folder: str, line: str) -> bool:
    """在批次目录里落一份来源说明 —— 图离开这个软件之后也还带着出处。

    扩展名是 .txt，图库浏览只认图片扩展名，所以它不会混进图里。
    写不进去（目录只读之类）只是少一个说明文件，**绝不该让整次抓取失败**。
    """
    if not folder or not line:
        return False
    try:
        with open(os.path.join(folder, CREDIT_FILE),
                  'w', encoding='utf-8') as f:
            f.write(f'{line}\n\n{CREDIT_NOTICE}\n')
        return True
    except OSError:
        return False


@dataclass
class Result:
    """一次抓取的结果。给调用方（和测试）用的结构化返回值，取代"只看打印"。"""
    folder: str = ''
    saved: list = field(default_factory=list)     # 落盘的文件路径
    total: int = 0                                # 解析出的图片数
    total_bytes: int = 0
    skipped: int = 0                              # 下载失败 + 去重丢掉的张数
    title: str = ''
    message: str = ''                             # 没成功时的人类可读说明
    #: 来源署名与一句话来源说明。取不到署名**不是错误** —— 这时 credit
    #: 退化成「来源：<站点名>」，界面如实标出来即可，绝不编造一个名字。
    author: str = ''
    credit: str = ''

    @property
    def ok(self) -> bool:
        return bool(self.saved)


def snag(source, is_url=True, out_root=None, include_scripts=False,
         get=None, dry_run=False, log=print, adapter=None, when=None,
         credit_file=True):
    """完整流程：拿到 HTML → 提取图片 → 按档位下载 → 去重 → 落盘。

    所有会碰网络/文件系统的步骤都通过参数注入（`get` / `out_root` / `when`），
    所以测试可以在完全不联网、不写用户目录的前提下跑完整条链路 ——
    这比"只测几个纯函数"有价值得多：出问题的往往正是它们连接的接缝处。

    `credit_file=False` 给 **APK** 用：那边落盘的中转目录会被 Java
    **原样**搬进系统相册（`Gallery.publishDir` 只按扩展名跳过 .part），
    塞一个 .txt 进去会被当成 image/jpeg 收下 —— 相册里多一个打不开的
    "图"，成功张数还会多算一张。桌面版与 Termux 版照常写。
    """
    log = log or (lambda *a, **k: None)
    get = get or make_get()

    adapter = adapter or get_adapter(source, is_url=is_url)
    if adapter is None:
        return Result(message=f'这个链接不认识。目前支持：{supported_names()}')

    # ① 拿 HTML
    if is_url:
        log(f'打开文章… {adapter.display_url(source)}')
        try:
            html = fetch_html(source, get=get)
        except Exception as e:
            msg = f'打开文章失败：{e}'
            if 'CERTIFICATE_VERIFY_FAILED' in str(e) or 'SSL' in str(e):
                msg += '\n（像是证书/网络问题，可加 --insecure 重试）'
            return Result(message=msg)
        headers = build_headers(adapter.download_referer(source))
    else:
        log('从 HTML 源码解析…')
        html = source
        headers = build_headers('')      # 粘贴源码时没有来源页，本就不该带

    # ② 标题、来源署名与图片列表
    title = adapter.extract_title(html)
    author = adapter.extract_author(html)
    credit = credit_line(author, adapter.display_name)
    urls = adapter.extract(html, include_scripts=include_scripts)
    log(f'标题：{title or adapter.fallback_title}')
    log(f'找到 {len(urls)} 张图')

    if not urls:
        return Result(title=title, author=author, credit=credit, message=(
            '这篇文章里没找到图片。\n'
            '可能原因：文章确实没图 / 页面要求登录才能看 / '
            '图藏在脚本里（加 --scripts 再试一次）。'))

    if dry_run:
        for i, u in enumerate(urls, 1):
            log(f'  {i:>3}. {u}')
        log(f'\n（仅列出，未下载。共 {len(urls)} 张）')
        return Result(title=title, total=len(urls),
                      author=author, credit=credit)

    # ③ 建目录（用适配器的兜底名，保证标题为空也有合法目录名）
    out_root = out_root or default_out_dir()
    folder = unique_dir(out_root, folder_name(title, when=when,
                                              fallback=adapter.fallback_title))
    os.makedirs(folder, exist_ok=True)

    # ④ 逐张下载
    records, failed = [], 0
    for i, url in enumerate(urls, 1):
        candidates = list(adapter.candidates(url)) or [url]
        used, data = download_one(candidates, headers, get)
        if data is None:
            failed += 1
            log(f'[{i}/{len(urls)}] 拿不到，跳过')
            continue
        tag = '' if used == url else '（原图取不到，用了压缩档）'
        log(f'[{i}/{len(urls)}] {human_size(len(data))}{tag}')
        records.append((i, used, data, adapter.ext_for(used)))

    # ⑤ 去重后落盘（文件用连续编号，跳过的不留空号）
    kept = dedup_by_content(records)
    saved, total_bytes = [], 0
    for n, (_idx, _url, data, ext) in enumerate(kept, 1):
        path = unique_path(os.path.join(folder, f'img_{n:02d}{ext}'))
        with open(path, 'wb') as f:
            f.write(data)
        saved.append(path)
        total_bytes += len(data)

    skipped = (len(urls) - len(saved))
    if saved:
        if credit_file:
            write_credit_file(folder, credit)
        rescan_dir(folder)

    # 一张都没存下来时，把目录收掉 —— 留个空文件夹比报错更让人困惑
    if not saved:
        try:
            os.rmdir(folder)
        except OSError:
            pass

    return Result(folder=folder, saved=saved, total=len(urls),
                  total_bytes=total_bytes, skipped=skipped, title=title,
                  author=author, credit=credit,
                  message='' if saved else '一张图都没下下来（网络问题？可重试一次）')


# ============================================================================
#  命令行
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='imgsnag',
        description=f'{APP_NAME} —— 抓取微信公众号文章的原图，存到手机相册目录。',
        epilog='例：imgsnag https://mp.weixin.qq.com/s/xxxx   /   imgsnag --list <链接>',
    )
    p.add_argument('source', nargs='?',
                   help='文章链接（也接受含链接的整段分享文本；直接粘 HTML 源码也行）')
    p.add_argument('--html', metavar='文件', help='从本地 HTML 文件解析')
    p.add_argument('--out', metavar='目录', help='保存到指定目录（默认：相册/Pictures/ImgSnag）')
    p.add_argument('--list', dest='dry_run', action='store_true',
                   help='只列出会下载哪些图，不实际下载')
    p.add_argument('--scripts', dest='include_scripts', action='store_true',
                   help='补充扫描 <script> 里的图片（普通模式找不到图时试）')
    p.add_argument('--insecure', action='store_true',
                   help='跳过 HTTPS 证书校验（网络环境特殊导致连不上时用）')
    p.add_argument('--quiet', action='store_true', help='静默（只输出结果与错误）')
    p.add_argument('--version', action='version', version=f'{APP_NAME} {APP_VERSION}')
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    log = (lambda *a, **k: None) if args.quiet else print

    # ── 决定解析什么 ──
    if args.html:
        try:
            with open(args.html, encoding='utf-8', errors='replace') as f:
                source, is_url = f.read(), False
        except OSError as e:
            print(f'❌ 读不到文件：{e}')
            return 1
    elif args.source:
        source, is_url = resolve_source(args.source)
    else:
        clip = clipboard_text()
        if not clip:
            build_parser().print_help()
            print('\n提示：也可以复制链接后重跑本命令（需装 Termux:API），'
                  '或把链接作为参数传进来。')
            return 2
        log('（从剪贴板读取）')
        source, is_url = resolve_source(clip)

    if not args.dry_run:
        print(f'{APP_NAME} {APP_VERSION}\n')
    log(f'保存位置：{args.out or default_out_dir()}')

    res = snag(source, is_url=is_url, out_root=args.out,
               include_scripts=args.include_scripts,
               get=make_get(insecure=args.insecure), dry_run=args.dry_run, log=log)

    if args.dry_run:
        return 0 if res.total else 1

    if not res.ok:
        print(f'\n❌ {res.message or "一张图都没拿到。"}')
        return 1

    print(f'\n✅ 完成：{len(res.saved)} 张，共 {human_size(res.total_bytes)}')
    if res.skipped:
        print(f'   跳过 {res.skipped} 张（重复或下载失败）')
    if res.credit:
        print(f'🔗 {res.credit}')
    print(f'📁 {res.folder}')
    if not shutil.which('termux-media-scan'):
        print('   （相册里没看到的话，等一会儿或重启相册；'
              '装 Termux:API 后可立即刷新）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
