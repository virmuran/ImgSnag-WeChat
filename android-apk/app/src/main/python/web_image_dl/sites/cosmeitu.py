"""
cosmeitu.com 适配器 —— 这个站点的全部站点知识都封在这个文件里。

站点画像（2026-10-10 实测）：

  · WordPress + Zibll 主题的 coser 美图图集站，一篇一个图集；
    另有 coser 合集页（`/<数字>.html`）列出该 coser 的各期
  · 正文图**懒加载**：真实地址在 `data-src`，`src` 只是占位 svg
  · 两个图床的 Referer 脾气**正好相反**（2026-10-10 实测，各反复试过几遍）：
      - `hk.66372188.xyz`   → 带不带都行（两种都是 200 / 104,310 B，完全一致）
      - `img.ciyuandao.com` → **带了就 403**。阿里云 OSS 的防盗链被配成了
        「有 Referer 就拒」，所以本站覆盖 download_referer 返回空串，
        下载图片**一律不带 Referer** —— 否则整批图全 403，用户看到的是 0 张。
        ⚠ 这条与开发时第一版实测结论相反：那时两个图床都不挑 Referer，
        而**没人测过「带 Referer」这条路径**（程序里默认就带着），所以没暴露。
  · 同一张图有**两个地址**（这是本站最容易踩的坑）：
      - 主图床 `hk.66372188.xyz/video/cos_<id>_NN.jpg`  → 页面 data-src
      - 兜底 `img.ciyuandao.com//works/<hash>.jpg`       → 写在 onerror 属性里
    两者内容相同（实测字节数一模一样），但**路径完全不同、无法互推**。
  · `img.ciyuandao.com` 是阿里云 OSS，地址带 `?x-oss-process=…resize,m_lfit,w_1440…`：
    **去掉这个查询串就是原图**（实测 1,592,009 B → 2,255,303 B，大 1.42 倍）。
    结构与微信的 `/640` 压缩档 vs `/0` 原图档同构，正好落在 candidates() 的位置。

判据：**正文图都带序号，侧栏噪音都没有** —— 这才是区分正文图集与页面噪音的真正
分界线，比按域名过滤强得多（侧栏缩略图也在同一个图床域名下，光看域名挡不住）。

序号（同时给出顺序）有两种写法，本站两类页面各用一套：

    · 详情页 `/22252.html`        → `title="标题 (N/总数)"`
    · 预览页 `/pic-online/vol/…`  → `alt="标题 … 第N张"`（Gutenberg 区块，没有 title）

所以判据是「**title 或 alt 里任一带序号**」，而不是只认 title。两类页面上，侧栏
推荐图与随机缩略图两种写法都没有，照样滤掉；详情页的正文图两种写法都有、值一致，
此时以 title 为准。

输出顺序直接取编号 N，不依赖 HTML 里的先后 —— 编号是页面自己标的，最可靠。
"""
import re
from html import unescape as _html_unescape
from urllib.parse import urlparse

from ..extractor import clean_url
from .base import SiteAdapter

#: 认哪些主机名算本站（裸域名与 www 都算）
COSMEITU_HOSTS = ('www.cosmeitu.com', 'cosmeitu.com')

#: 页面里属于「图集正文」的两个图床。其余域名一律不收：
#:   · cosmeitu.com/wp-content/themes/… → 主题资源（logo、头像、占位 svg）
#:   · i1/i2.hdslb.com                  → 相关文章里转载的 B 站视频封面
_IMAGE_HOSTS = ('hk.66372188.xyz', 'img.ciyuandao.com')

#: 阿里云 OSS 的图片处理参数。**去掉它 = 原图**，带着它 = 页面显示用的压缩档。
_OSS_PROCESS_RE = re.compile(r'[?&]x-oss-process=[^&\s"\']*', re.I)

#: 逐个 `<img …>` 标签处理，而不是满 HTML 撒网找 URL。
#: 这样 `onerror` 里那个「同一张图的另一个地址」天然不会被误当成第二张图 ——
#: 撒网式的写法会让每张图都翻倍（9 张变 18 张，且用户很难看出为什么）。
_IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.I)

#: img 标签里取地址：data-src 优先（懒加载的真实地址），src 兜底
_ATTR_DATA_SRC_RE = re.compile(r'\bdata-src=["\']([^"\']+)["\']', re.I)
_ATTR_SRC_RE = re.compile(r'\bsrc=["\']([^"\']+)["\']', re.I)

#: onerror="this.onerror=null;this.src='http://img.ciyuandao.com/…'"
#: 外层双引号、内层单引号 —— 只匹配这种写法，认不出就退回 data-src，不会出错
_ATTR_ONERROR_URL_RE = re.compile(r'\bonerror=["\'][^"\']*?["\'](https?://[^"\']+)["\']', re.I)

_ATTR_TITLE_RE = re.compile(r'\btitle=["\']([^"\']*)["\']')
_ATTR_ALT_RE = re.compile(r'\balt=["\']([^"\']*)["\']')

#: `标题 (3/9)` —— 详情页正文图的编号，同时给出顺序
_TITLE_NUM_RE = re.compile(r'\(\s*(\d+)\s*/\s*(\d+)\s*\)')

#: `标题 第3张` —— 预览页（Gutenberg 区块）正文图的编号。
#: 详情页的正文图两种写法都带、值也一致，那种情况以 title 为准。
_ALT_NUM_RE = re.compile(r'第\s*(\d+)\s*张')

#: 判定「这段 HTML 属于本站」的特征词。挑域名这类改版也不容易动的东西，
#: 不匹配会随模板调整的 HTML 结构。
_SNIFF_MARKS = ('cosmeitu.com', '66372188.xyz', 'ciyuandao.com')

#: 作者名的合理长度上限（防误抓一整段正文进来）
_MAX_AUTHOR_LEN = 40


# ============================================================================
#  URL 判定
# ============================================================================

def _host_of(url: str) -> str:
    raw = (url or '').strip()
    if not raw:
        return ''
    if raw.startswith('//'):
        raw = 'https:' + raw
    if '://' not in raw:               # 没写协议的裸域名也认，补一个再解析
        raw = 'https://' + raw
    try:
        return (urlparse(raw).hostname or '').lower()
    except ValueError:                 # 畸形 URL（如非法 IPv6 字面量）
        return ''


def is_cosmeitu_url(url: str) -> bool:
    """是否本站链接。**按主机名精确比对**，不做子串匹配。

    子串匹配会放行 `https://cosmeitu.com.evil.com/x` 这类伪装地址，
    后果是程序真的把请求发到那个域名去（微信适配器在 v1.1.2 踩过同样的坑）。
    """
    return _host_of(url) in COSMEITU_HOSTS


def _is_target_image(url: str) -> bool:
    """是否本站图集正文用的图床地址"""
    return _host_of(url) in _IMAGE_HOSTS


# ============================================================================
#  画质档位
# ============================================================================

def strip_oss_process(url: str) -> str:
    """去掉阿里云 OSS 的图片处理参数，拿到原图地址。

    页面上的地址形如：
        http://img.ciyuandao.com//works/<hash>.jpg?x-oss-process=image/auto-orient,1/resize,m_lfit,w_1440/quality,q_100
    实测（2026-10-10，同一张图各反复测过）：
        · 带参数 → 1,592,009 B（1440 宽压缩档，页面显示用的就是它）
        · 去参数 → 2,255,303 B（原图，**大 1.42 倍**）
    结构与微信的 `/640` vs `/0` 同构，所以这里也做成"剥掉查询串"的形式。

    非 OSS 地址原样返回，避免误伤（hk 图床的地址本来就没有参数）。
    """
    u = (url or '').strip()
    if not u:
        return ''
    stripped = _OSS_PROCESS_RE.sub('', u)
    return stripped.rstrip('?&')       # 只带这一个参数时，剥完会剩一个孤零零的 ?


# ============================================================================
#  标题与作者
# ============================================================================

def extract_title(html) -> str:
    """取文章标题（`<h1 class="article-title">`）给下载文件夹命名。

    标题里内层还套着 `<a>`，所以先剥标签再解实体。取不到返回空串，
    由 CosmeituAdapter.fallback_title 兜底。

    这里**不做文件名清洗** —— 非法字符、长度、保留名都是 naming.sanitize_title 的事。
    """
    if not html:
        return ''
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.I | re.S)
    if not m:
        return ''
    text = re.sub(r'<[^>]+>', '', m.group(1))
    text = _html_unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def extract_author(html) -> str:
    """取**原作者**（coser 名）。

    页面正文里有一行 ``📸 Coser：洛城雪Yuki``，这是本站唯一能拿到的原作者信息。
    ⚠ 右边的 ``<a href="/author/1">``（"52cos"）是**发布者/站长**，不是作者 ——
    本站是收集站，发布的是站长自己，把它当作者等于给错了署名。

    取不到返回空串（有些篇目是"美图精选"，没有确切的 coser 名），
    界面会退化成「来源：cosmeitu.com」+ 通用版权提醒 —— 如实说，不编。
    """
    if not html:
        return ''
    m = re.search(r'Coser\s*[：:]\s*([^<\n]{1,%d})' % _MAX_AUTHOR_LEN, html)
    if not m:
        return ''
    return re.sub(r'\s+', ' ', m.group(1)).strip(' \u3000')


# ============================================================================
#  图片提取
# ============================================================================

def _pick_image_url(tag: str) -> str:
    """从一个 `<img …>` 标签里挑出「能拿到最大画质」的那个地址。

    优先 `onerror` 里的 ciyuandao 地址 —— 它是同一张图的另一个文件，
    但**路径可推出原图**（去掉 OSS 参数即可）；hk 图床那份推不出原图路径来。
    没有 onerror 就退回 `data-src`（懒加载的真实地址），再退回 `src`。
    """
    m = _ATTR_ONERROR_URL_RE.search(tag)
    if m:
        u = clean_url(m.group(1))
        if _is_target_image(u):
            return u
    for pat in (_ATTR_DATA_SRC_RE, _ATTR_SRC_RE):
        m = pat.search(tag)
        if m:
            u = clean_url(m.group(1))
            if _is_target_image(u):
                return u
    return ''


def extract_image_urls(html, include_scripts=False):
    """提取图集里的图片地址，**按页面标注的编号顺序**返回。

    两条路径（依次尝试）：

      1. **带编号的正文图** —— `title="标题 (N/总数)"`。编号直接给出顺序，
         不依赖 HTML 里的先后，也不受模板把图挪来挪去的影响。
      2. **兜底：任何带 title 的目标图床图** —— 万一下次改版去掉了编号，
         至少还能抓到，只是顺序退化成"出现顺序"。

    为什么必须要求 title 存在：侧栏「相关推荐」的缩略图**也在同一个图床域名下**，
    只看域名挡不住；它们没有 title，这一条才是真正的分界线。

    为什么逐个 img 标签处理、不做全文撒网：`onerror` 里那个地址属于**同一张图的
    另一个文件**，全文撒网会让每张图被算成两张（9 张变 18 张）。

    `include_scripts` 本站用不上（图上都是普通 img 标签），保留参数只为接口一致。
    """
    if not html:
        return []

    numbered = []      # [(编号, 地址)]
    titled = []        # [地址]，有 title 但没编号

    for m in _IMG_TAG_RE.finditer(html):
        tag = m.group(0)
        url = _pick_image_url(tag)
        if not url:
            continue

        # 序号优先从 title 取（详情页），没有就退到 alt 里的「第N张」（预览页）。
        # 两种都没有 = 页面噪音，在这里滤掉 —— 侧栏相关推荐与随机缩略图正是如此。
        tm = _ATTR_TITLE_RE.search(tag)
        title = tm.group(1) if tm else ''
        n = None
        if title:
            nm = _TITLE_NUM_RE.search(title)
            if nm:
                n = int(nm.group(1))
            else:
                titled.append(url)        # 有 title 却没编号：留着兜底，看下一张
                continue
        else:
            am = _ATTR_ALT_RE.search(tag)
            nm = _ALT_NUM_RE.search(am.group(1)) if am else None
            if not nm:
                continue                  # 无 title 也无 alt 序号 —— 侧栏噪音
            n = int(nm.group(1))

        numbered.append((n, url))

    if numbered:
        numbered.sort(key=lambda x: x[0])
        seen = set()
        out = []
        for n, u in numbered:
            if n in seen:                 # 同一编号只留首次出现（防模板重复输出）
                continue
            seen.add(n)
            out.append(u)
        return out
    return titled


# ============================================================================
#  适配器
# ============================================================================

class CosmeituAdapter(SiteAdapter):
    """cosmeitu.com 图集图片适配器。"""

    name = 'cosmeitu'
    display_name = 'cosmeitu.com'
    fallback_title = 'coser美图'

    def matches(self, url: str) -> bool:
        return is_cosmeitu_url(url)

    def extract(self, html: str, include_scripts: bool = False) -> list:
        return extract_image_urls(html, include_scripts=include_scripts)

    def extract_title(self, html: str) -> str:
        return extract_title(html)

    def extract_author(self, html: str) -> str:
        return extract_author(html)

    def candidates(self, url: str) -> list:
        """原图档在前，压缩档兜底。

        ciyuandao 的地址带着 `?x-oss-process=…w_1440…`，去掉它就是原图
        （实测大 1.42 倍）。hk 图床的地址本来就没有参数、也推不出原图路径，
        此时返回单候选 —— **不假装有档位**，否则 worker 会去下一个候选，
        白白多一次失败请求。
        """
        u = (url or '').strip()
        if not u:
            return []
        original = strip_oss_process(u)
        if not original or original == u:
            return [u]
        return [original, u]

    def sniff(self, html: str) -> int:
        """按特征词命中数判断这段 HTML 是不是本站页面。

        挑的都是**改版也不容易动**的东西：站点域名与两个图床域名。
        不匹配 HTML 结构（模板一改就失效）。
        """
        if not html:
            return 0
        text = html[:400000]      # 域名必然出现在前部，全文扫描在大页面上是浪费
        return sum(1 for mark in _SNIFF_MARKS if mark in text)

    def download_referer(self, page_url: str) -> str:
        """**不带 Referer** —— 本站图床的防盗链是反着配的。

        实测（2026-10-10），同一张 ciyuandao 的图：

            · 不带 Referer        → 200 / 1,546,703 B
            · 带 Referer=页面地址  → 403 Forbidden

        hk 图床两种都 200，所以「不带」对两个图床都安全。默认行为是把页面地址
        当 Referer，在 ciyuandao 上会让**整批图全部 403**，用户看到一张都没下来。
        """
        return ''


#: 单例。注册表里放它即可
cosmeitu = CosmeituAdapter()
