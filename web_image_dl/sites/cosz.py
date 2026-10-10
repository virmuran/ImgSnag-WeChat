"""
cosz.com 适配器 —— 这个站点的全部站点知识都封在这个文件里。

站点画像（2026-10-10 实测，cosplay / pixiv / jk / lolita / hanfu 五个分类各验一篇）：

  · WordPress + B2 主题的 coser 图集 / 画师作品站，一篇一个图集
  · 文章地址统一为 `/<分类>/<id>.html`。
    ⚠ **分类名不可信**：`/jk/60361.html` 打开是《汉服第874期》—— 别拿 URL 里的
      分类名去判断内容，本适配器也从不依赖它。
  · 正文容器 = `<div class="entry-content">`（WordPress 的标准类名，五篇上都在）
  · 正文图**懒加载**：`src` 是主题占位图 `themes/b2/…/default-img.jpg`，
    真实地址在 `data-src`，指向本站自有域名 `/wp-content/uploads/YYYY/MM/<原名>.jpg`
  · 图片**全部自托管**在 cosz.com，没有第三方图床 → Referer 带不带都行。
    实测同一张图三种请求（不带 / 页面地址 / 首页地址）都是 200 且**字节数逐字节
    相同**（249,045 B），所以本适配器不覆盖 download_referer，沿用默认行为。
  · 网页里**没有付费墙 / 隐藏内容**，免费可见的就是正文那几张（实测 7~12 张，
    随篇目不同）；完整套图走 App / 金币 / 会员，HTML 里没有多余的东西。
  · **没有「原图档位」**：`_post_<hash>` 就是唯一地址。试过去掉后缀的几种变体
    （`cosz.com-001.jpg` / `_1.jpg` / `_1_post.jpg`）全是 404，推不出更大的一张，
    所以也不覆盖 candidates —— 如实回答"没有档位可选"，不假装有。
  · **取不到作者**：B2 主题的作者区是 Vue 渲染的，静态 HTML 里没有可用的署名
    （页面里能匹配到的 `author` 都是"取消关注"这类按钮文案）。本适配器因此
    **不实现 extract_author** —— 按 base.py 的口径，取不到就交给默认空串，
    界面退化成「来源：cosz.com」+ 通用版权提醒。**绝不拿标题里的名字去凑**
    （《C站·第3449期》刹那 的"刹那"未必是作者），编出来的署名比没有更糟。

判据：**只看 entry-content 容器内的图** —— 这是区分正文图与页面噪音最可靠的分界。
侧栏缩略图、头像、页脚友情链接**都在同一个 cosz.com 域名下**，光按域名过滤挡不住：

    · 侧栏相关推荐缩略图 → `/wp-content/uploads/thumb/…fill_w100_h62_…`
    · 评论 / 作者头像      → `…fill_w120_h120_…`（B2 生成的缩略图都带 `fill_w`）
    · 页脚友情链接图标     → `www.cosz.com/images/link1.png`
    · 正文顶部那张大图     → 是正文某张的重复，按 URL 去重即可
    · 站长的内网残留图     → `http://192.168.1.5:2256/…`（非本站域名，天然被滤）

容器万一改版找不到，退回整页扫描，靠下面几条 URL 特征兜底（见 _is_body_image）：
主机名必须是 cosz.com，路径必须含 `/wp-content/uploads/`（主题资源在
`/wp-content/themes/` 下），且不含 `/thumb/` 与 `fill_w`。

输出顺序 = 图片在正文里的**出现顺序**（本站正文不标序号，页面顺序就是图集顺序）。
"""
import re
from html import unescape as _html_unescape
from urllib.parse import urlparse

from ..extractor import clean_url
from .base import SiteAdapter

#: 认哪些主机名算本站（裸域名与 www 都算；比对用 urlparse 出的主机名，不做子串匹配）
COSZ_HOSTS = ('cosz.com', 'www.cosz.com')

#: 正文容器类名，按优先级依次尝试。`entry-content` 是 WordPress 的标准类名，
#: 五篇上都在；后面两个是同类主题的常见写法，留作改版兜底。
_CONTENT_CLASSES = ('entry-content', 'post-content', 'article-content')

#: 逐个 `<img …>` 标签处理，而不是满 HTML 撒网找 URL。
#: 撒网会把 JS 变量（`:data-src="item.thumb"` 这类）也当成图，且拿不到标签内的
#: 其他属性，没法做"是不是正文图"的判断。
_IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.I)

#: 取 img 标签的地址：**data-src 优先**（懒加载的真实地址），src 兜底。
#: ⚠ 前置字符不能是 `-`/`:`/词字符 —— 否则会把 Vue 的 `:data-src="item.thumb"`
#: 也匹配上（那个值不是 URL，后面会被 _is_body_image 挡掉，但没必要先放进来）。
_ATTR_DATA_SRC_RE = re.compile(r'(?<![-\w:])data-src\s*=\s*["\']([^"\']+)["\']', re.I)
_ATTR_SRC_RE = re.compile(r'(?<![-\w:])src\s*=\s*["\']([^"\']+)["\']', re.I)

#: div 配平用：开标签与闭标签
_DIV_TAG_RE = re.compile(r'<div\b[^>]*>', re.I)
_DIV_TOK_RE = re.compile(r'<div\b[^>]*>|</div\s*>', re.I)
_CLASS_ATTR_RE = re.compile(r'\bclass\s*=\s*["\']([^"\']*)["\']', re.I)

#: B2 生成的缩略图标记：侧栏相关推荐（/thumb/…fill_w100_h62）与头像（fill_w120_h120）
_THUMB_MARKS = ('/thumb/', 'fill_w')

#: 标题里站点名的尾巴（`《…》刹那 &#8211; C站 CosZ`）—— 只在退回 <title> 时用
_SITE_SUFFIX_RE = re.compile(r'\s*[-–—|]\s*C站\s*CosZ\s*$', re.I)

#: 判定「这段 HTML 属于本站」的特征词。挑域名/主题路径这类改版也不容易动的东西，
#: 不去匹配会随模板调整的 HTML 结构。比对前统一小写。
_SNIFF_MARKS = ('cosz.com', 'ecy.cn', 'b2/assets')


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


def is_cosz_url(url: str) -> bool:
    """是否本站链接。**按主机名精确比对**，不做子串匹配。

    子串匹配会放行 `https://cosz.com.evil.com/x` 这类伪装地址，后果是程序真的
    把请求发到那个域名去（微信适配器在 v1.1.2 踩过同样的坑）。
    """
    return _host_of(url) in COSZ_HOSTS


def _is_body_image(url: str) -> bool:
    """这个地址是不是本站**正文里**的图。

    三个条件缺一不可：
      · 主机名是 cosz.com / www.cosz.com（页脚友情链接图标也是本站，再靠下面两条挡）
      · 路径含 `/wp-content/uploads/`（主题占位图与主题资源在 `/wp-content/themes/`）
      · 路径不含 `/thumb/` 也不含 `fill_w`（B2 生成的缩略图与头像都带这两个标记）
    """
    if not url:
        return False
    if _host_of(url) not in COSZ_HOSTS:
        return False
    path = url.split('?', 1)[0]
    if '/wp-content/uploads/' not in path:
        return False
    if any(mark in path for mark in _THUMB_MARKS):
        return False
    return True


# ============================================================================
#  正文容器
# ============================================================================

def _slice_div(html: str, cls: str) -> str:
    """取出 class 含 `cls` 的那个 `<div>` 的完整片段（按 div 配平）；找不到返回 ''。

    为什么要配平而不是用 `(.*?)</div>`：正文容器里还嵌着别的 div
    （`<div class="content-excerpt">…</div>`），非贪婪匹配会在第一个内层
    `</div>` 就截断，把正文图全切掉 —— 表现是"一张都提不出来"。
    """
    if not html:
        return ''
    for m in _DIV_TAG_RE.finditer(html):
        cm = _CLASS_ATTR_RE.search(m.group(0))
        if not cm or cls not in cm.group(1).split():
            continue
        depth = 0
        for t in _DIV_TOK_RE.finditer(html, m.start()):
            depth += -1 if t.group(0).startswith('</') else 1
            if depth == 0:
                return html[m.start():t.end()]
        return html[m.start():]        # 畸形页面（div 不配平）→ 给到末尾，别丢内容
    return ''


def _content_html(html: str) -> str:
    """正文容器片段；都找不到时返回整页 HTML，由 _is_body_image 兜底过滤。"""
    for cls in _CONTENT_CLASSES:
        seg = _slice_div(html, cls)
        if seg:
            return seg
    return html


# ============================================================================
#  标题
# ============================================================================

def extract_title(html) -> str:
    """取文章标题（`<h1>`）给下载文件夹命名。

    `<h1>` 就是干净的标题（实测 `《C站·第3449期》刹那`）；取不到再退回 `<title>`，
    并把站点名尾巴（`– C站 CosZ`）剪掉。都没有返回空串，由 fallback_title 兜底。

    这里**不做文件名清洗** —— 非法字符、长度、保留名都是 naming.sanitize_title 的事。
    """
    h = html or ''
    m = re.search(r'<h1[^>]*>(.*?)</h1>', h, re.I | re.S)
    if m:
        text = re.sub(r'<[^>]+>', '', m.group(1))
        text = re.sub(r'\s+', ' ', _html_unescape(text)).strip()
        if text:
            return text

    m = re.search(r'<title[^>]*>(.*?)</title>', h, re.I | re.S)
    if m:
        text = re.sub(r'\s+', ' ', _html_unescape(m.group(1))).strip()
        text = _SITE_SUFFIX_RE.sub('', text).strip()
        if text:
            return text
    return ''


# ============================================================================
#  图片提取
# ============================================================================

def _pick_image_url(tag: str) -> str:
    """从一个 `<img …>` 标签里挑出正文图地址：**data-src 优先**，src 兜底。

    正文图的 `src` 是主题占位图（`themes/b2/…/default-img.jpg`），真图在 `data-src`；
    只有 `data-src` 缺失或不可用时才看 `src`（那种情况下 src 可能已是真图）。
    """
    for pat in (_ATTR_DATA_SRC_RE, _ATTR_SRC_RE):
        m = pat.search(tag)
        if not m:
            continue
        u = clean_url(m.group(1))
        if _is_body_image(u):
            return u
    return ''


def _scan(seg: str) -> list:
    """在给定片段里按出现顺序收正文图，并按 URL 去重（防顶部封面重复输出）。"""
    out, seen = [], set()
    for m in _IMG_TAG_RE.finditer(seg or ''):
        u = _pick_image_url(m.group(0))
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_image_urls(html, include_scripts=False):
    """提取正文图地址，**按图片在正文里的出现顺序**返回。

    先扫 `entry-content` 容器；容器里一张都没收到（改版 / 结构不认识）就退回整页，
    由 `_is_body_image` 的域名与路径特征兜底。

    本站正文**不标图片序号**（与 cosmeitu 不同），所以顺序只能取页面顺序 ——
    WordPress 的正文顺序就是图集顺序，实测与浏览器里看到的一致。

    `include_scripts` 本站用不上（图都在普通 `<img>` 标签里，B2 的正文是服务端
    直出的），保留参数只为接口一致。
    """
    if not html:
        return []
    seg = _content_html(html)
    urls = _scan(seg)
    if not urls and seg is not html:
        urls = _scan(html)             # 容器里没有 → 退回整页兜底
    return urls


# ============================================================================
#  适配器
# ============================================================================

class CoszAdapter(SiteAdapter):
    """cosz.com 图集图片适配器。

    只覆盖必要的方法 —— 其余交给 base.py 的默认实现，理由见模块头部说明：
      · 不覆盖 extract_author   → B2 用 Vue 渲染作者区，静态 HTML 取不到，如实留空
      · 不覆盖 candidates       → 没有原图档位，去掉后缀全是 404，不假装有
      · 不覆盖 ext_for          → 正文图就是普通 .jpg，通用规则够用
      · 不覆盖 download_referer → 本站图床不校验来源，带不带都 200，沿用默认即可
    """

    name = 'cosz'
    display_name = 'cosz.com'
    fallback_title = 'C站图片'

    def matches(self, url: str) -> bool:
        return is_cosz_url(url)

    def extract(self, html: str, include_scripts: bool = False) -> list:
        return extract_image_urls(html, include_scripts=include_scripts)

    def extract_title(self, html: str) -> str:
        return extract_title(html)

    def sniff(self, html: str) -> int:
        """按特征词命中数判断这段 HTML 是不是本站页面。

        挑的都是**改版也不容易动**的东西：站点域名、站内图床域名、B2 主题路径。
        不匹配 HTML 结构（模板一改就失效）。
        """
        if not html:
            return 0
        text = html[:400000].lower()   # 域名必然出现在前部，全文扫描在大页面上是浪费
        return sum(1 for mark in _SNIFF_MARKS if mark in text)


#: 单例。注册表里放它即可
cosz = CoszAdapter()
