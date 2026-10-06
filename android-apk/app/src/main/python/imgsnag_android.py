# -*- coding: utf-8 -*-
"""ImgSnag 安卓版 —— Python 侧入口（Java 通过 Chaquopy 调用本模块）

它只负责**把桌面/Termux 版已经有的能力接到安卓上**，自己不实现抓取逻辑：

    安卓界面（Java）
        └── imgsnag_android.py（本文件）      接平台：网络、证书、报告文本
              ├── imgsnag.py                  抓取主流程（与 Termux 版同一份）
              └── web_image_dl/               站点知识（与所有平台同一份）

后两者是 `android/sync_core.py` **自动生成**的副本，不要手改。

────────────────────────────────────────────────────────────────────────
为什么用 urllib 而不是 requests
────────────────────────────────────────────────────────────────────────
Termux 版用 requests，是因为那台机器上 pip 装它很轻松。安卓这边不一样：

  · Chaquopy 装 pip 包要走它自己的仓库，且要为 arm64 现编 —— 这是整条构建链路里
    最容易失败的一环（本地还验不了，只能在云上等十几分钟看结果）
  · 而这里要的只是「GET 一个 URL，拿字节」，urllib 完全够用

所以安卓版**一个 pip 包都不装**。代价是要自己处理两件事，本文件都做了：
证书（见下）与把 urllib 的响应包装成 requests 那样的对象（`_Response`）。

────────────────────────────────────────────────────────────────────────
证书这件事
────────────────────────────────────────────────────────────────────────
Chaquopy 把 `ssl` 配成使用它打包进去的 certifi 根证书库，**不看系统的证书**。
好处是开箱即用；坏处是遇到企业网络/加速器做 TLS 中转（用自己签的根证书）时会
直接 CERTIFICATE_VERIFY_FAILED —— 桌面版的 v1.8.2 就是这么被坑过的，
当时表现还是"连不上网"，查了很久。

所以这里照桌面版 `updater.system_ca_bundle()` 的思路做了一遍：把安卓系统里
**系统预装的**与**用户自己装的**根证书拼成一个 PEM 包，加载进 SSL 上下文。
两者合起来用 —— certifi 保底，系统证书兜特殊环境。

────────────────────────────────────────────────────────────────────────
和中转目录的约定（与 Java 侧握手的地方）
────────────────────────────────────────────────────────────────────────
Python 把图下到 `<workdir>/pending/<文章文件夹>/img_01.jpg`，然后**由 Java 搬进
系统相册**（写相册要走 MediaStore，是安卓平台特有的活）。

`<workdir>` 由 Java 传入 —— ⚠ 传的是**根目录**（应用的 filesDir），
`pending/` 这一层由本模块自己建（名字就是下面的 PENDING_DIR_NAME）。
Java 侧扫描与清理的正是同一个 pending 层。要是 Java 把 pending 层本身当成
workdir 传进来，路径会嵌成 pending/pending/…，表现是"图抓到了但相册里没有"，
两边都不报错 —— 所以测试把这条约定钉死在两边。
"""
from __future__ import annotations

import os
import shutil
import ssl
import sys
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import imgsnag                                                       # noqa: E402
from web_image_dl.sites import get_adapter, supported_names, ADAPTERS  # noqa: E402

APP_LABEL = 'ImgSnag 安卓版'

#: 与 Java 侧 `MainActivity.PENDING_DIR` 必须一致的中转目录名。
#: 这两个字面量是 Java 与 Python 唯一的握手点 —— 改了一边忘了另一边，
#: 表现是"图抓到了但相册里没有"，而且两边都不报错。测试里有一条专门钉住它。
PENDING_DIR_NAME = 'pending'

#: 安卓上根证书的存放位置（一证书一个 PEM 文件）。
#: 前一个是系统预装的，后一个是用户自己装的 —— 两个都要合并，
#: 否则「装了公司根证书」的环境依旧握手失败。
CA_DIRS = (
    '/system/etc/security/cacerts',
    '/data/misc/user/0/cacerts-added',
)

#: 合并后的证书包名（放在 workdir 里，随应用数据一起被清）
CA_BUNDLE_NAME = 'system_ca_bundle.pem'

#: `imgsnag.py` 里那句命令行味的证书提示。安卓版没有 --insecure 这个开关，
#: 直接照搬会让用户去敲一个不存在的参数 —— 所以要替换掉。
#: 测试里有一条比对它确实还存在于 imgsnag.py（那边改了措辞这边会红）。
CLI_CERT_HINT = '（像是证书/网络问题，可加 --insecure 重试）'
PHONE_CERT_HINT = '（像是证书或网络的问题，换个网络再试一次）'

#: 请求超时（秒）。手机蜂窝网络比电脑慢，给得比 Termux 版宽松些。
HTTP_TIMEOUT = 30


# ============================================================================
#  证书
# ============================================================================

def system_ca_bundle(dest: str, dirs=None):
    """把系统与用户安装的根证书拼成一个 PEM 包，返回它的路径。

    一个证书都没读到（没有目录、没有权限、或目录是空的）时返回 None ——
    这时候**不要**写出一个空文件：空包会让 ssl 认为"一个可信证书都没有"，
    比不加载还糟。调用方拿到 None 就该保持 certifi 的默认行为。

    Android 的证书目录里可能混着元数据文件，所以按内容判断（必须含
    `-----BEGIN CERTIFICATE-----`）而不是按文件名。
    """
    dirs = CA_DIRS if dirs is None else dirs
    chunks = []
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue                       # 目录不存在 / 没读权限，静默跳过
        for name in names:
            path = os.path.join(d, name)
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except OSError:
                continue
            if b'-----BEGIN CERTIFICATE-----' not in data:
                continue
            chunks.append(data.rstrip() + b'\n')

    if not chunks:
        return None

    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(b'\n'.join(chunks) + b'\n')
    return dest


def ssl_context(cache_dir=None, ca_dirs=None):
    """构造 SSL 上下文：certifi（Chaquopy 自带）打底 + 叠加系统/用户根证书。

    叠加用的是 `load_verify_locations` —— 它是**追加**而不是替换，
    所以叠加失败也不会让原本能用的证书失效。
    """
    ctx = ssl.create_default_context()
    if cache_dir:
        bundle = system_ca_bundle(os.path.join(cache_dir, CA_BUNDLE_NAME), dirs=ca_dirs)
        if bundle:
            try:
                ctx.load_verify_locations(cafile=bundle)
            except (OSError, ssl.SSLError):
                pass                        # 半截/格式不对的证书包不该让抓取直接失败
    return ctx


# ============================================================================
#  网络
# ============================================================================

class _Response:
    """把 urllib 的响应包装成 requests 那样的最小接口。

    `imgsnag.py` 只用到三样东西：`.text`、`.content`、`.raise_for_status()`，
    所以这里只实现这三样。刻意不做得更全 —— 接口越像 requests，
    越容易让人以为"这儿能用 requests 的别的特性"。
    """

    __slots__ = ('status', 'content', 'encoding')

    def __init__(self, status, content, encoding=None):
        self.status = status
        self.content = content
        self.encoding = encoding or 'utf-8'

    @property
    def text(self):
        return self.content.decode(self.encoding, 'replace')

    def raise_for_status(self):
        if not 200 <= self.status < 300:
            raise RuntimeError(f'HTTP {self.status}')


def make_get(ctx=None, opener=None):
    """返回 `get(url, **kw)`。

    签名与 Termux 版的 requests.get 兼容（只实现用到的 `headers` / `timeout`），
    这样 `imgsnag.py` 不用为安卓端改任何一行。

    `opener` 是给测试留的注入口 —— 不注入就走真实网络。
    """
    ctx = ctx if ctx is not None else ssl_context()
    opener = opener or urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx))

    def _get(url, **kw):
        headers = kw.get('headers') or {}
        timeout = kw.get('timeout') or HTTP_TIMEOUT
        req = urllib.request.Request(url, headers=dict(headers))
        try:
            with opener.open(req, timeout=timeout) as resp:
                return _Response(resp.status, resp.read(),
                                 resp.headers.get_content_charset())
        except urllib.error.HTTPError as e:
            # urllib 对 4xx/5xx 是抛异常而不是返回响应对象；
            # imgsnag.download_one 靠"抛异常就换下一个档位"，所以这里保持抛。
            raise RuntimeError(f'HTTP {e.code} {e.reason}') from None

    return _get


# ============================================================================
#  报告文本
# ============================================================================

def polish(message: str) -> str:
    """把命令行味的措辞换成手机上说得通的话。"""
    if not message:
        return message
    return message.replace(CLI_CERT_HINT, PHONE_CERT_HINT)


def build_report(res, lines, retried=False) -> str:
    """把抓取结果拼成用户能看懂的一段话。

    刻意保留 Python 侧的过程输出（`lines`）：手机上出了问题，用户唯一的
    反馈渠道就是把这段文字截图/复制发出来。少写一行，就少一条线索。
    """
    body = '\n'.join(lines)
    head = f'《{res.title}》' if res.title else ''

    if res.ok:
        tail = f'共 {len(res.saved)} 张，{imgsnag.human_size(res.total_bytes)}'
        if res.skipped:
            tail += f'（跳过 {res.skipped} 张：重复或没下下来）'
        parts = [p for p in (head, body, tail) if p]
        return '\n'.join(parts)

    msg = polish(res.message) or '一张图都没拿到。'
    if retried:
        msg += '\n（已经换过一种解析方式，还是没找到）'
    parts = [p for p in (head, body, msg) if p]
    return '\n'.join(parts)


# ============================================================================
#  入口（Java 调用）
# ============================================================================

def snag_text(text, workdir, get=None):
    """从一段文本（分享内容 / 剪贴板 / 裸链接）抓取图片，返回给人看的报告。

    图片落在 `<workdir>/pending/<文章文件夹>/` 下，由 Java 侧搬进系统相册。
    ⚠ `workdir` 是根目录（Java 传的是应用 filesDir），`pending/` 由本模块自己建。
    Java 调这个函数**只传两个参数**（`get` 是给测试注入假网络用的）。
    """
    try:
        source, is_url = imgsnag.resolve_source(text)
        if not source:
            return ('没能从这段内容里找到链接。\n'
                    '可以试试先复制链接，再回到本页点「抓剪贴板链接」。')

        adapter = get_adapter(source, is_url=is_url)
        if adapter is None:
            return (f'这个链接不认识。\n目前支持：{supported_names()}\n\n'
                    f'（认出来的是 {source[:80]}）')

        pending = os.path.join(workdir, PENDING_DIR_NAME)
        _clear(pending)

        get = get or make_get(ssl_context(cache_dir=workdir))
        lines = []
        res = imgsnag.snag(source, is_url=is_url, out_root=pending,
                           adapter=adapter, get=get, log=lines.append)

        # 一篇文章的图常常藏在页面脚本里（模板改版时尤其明显）。
        # 桌面版为此留了 --scripts，手机上没有命令行开关，
        # 所以这里在"一张都没找到"时自动再扫一遍，并把这一步写进日志，
        # 免得用户以为是卡住了。
        retried = False
        if res.total == 0 and is_url:
            retried = True
            lines.append('这篇文章里没直接看到图，换一种方式再找一遍…')
            res = imgsnag.snag(source, is_url=is_url, out_root=pending,
                               include_scripts=True, adapter=adapter,
                               get=get, log=lines.append)

        return build_report(res, lines, retried=retried)

    except Exception as e:                                  # noqa: BLE001
        # 这里必须给出可读的错误：Java 侧拿到的只是一段字符串，
        # 用户在手机上没法看 logcat。
        import traceback
        return ('❌ 抓取过程中出错：\n'
                f'{type(e).__name__}: {e}\n\n'
                + traceback.format_exc(limit=3))


def _clear(path):
    """清掉上一次的中转目录（图片已经进相册了，留着白占空间）。"""
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def app_info():
    """给界面显示用的版本信息。"""
    return {'label': APP_LABEL, 'sites': [a.display_name for a in ADAPTERS]}


if __name__ == '__main__':
    # 在电脑上手动跑一下（不经过安卓）：python imgsnag_android.py <链接>
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    work = os.path.join(os.path.expanduser('~'), '.imgsnag_android_try')
    arg = ' '.join(sys.argv[1:]) or 'https://mp.weixin.qq.com/s/xxxx'
    print(snag_text(arg, work))
    print(f'\n（中转目录：{work}）')
