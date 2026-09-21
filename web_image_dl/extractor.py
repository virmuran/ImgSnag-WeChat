"""
微信公众号文章图片 URL 提取器 — 专精 mp.weixin.qq.com
只保留微信策略：mmbiz.qpic.cn 图片识别、CDN 变体合并、水印版剔除、原图画质提升
"""
import re
from html import unescape as _html_unescape
from urllib.parse import urlparse


WECHAT_HOST = 'mp.weixin.qq.com'

#: 标题来源，按可靠性排序：(正则, 取值分组序号)
#: 注意本模块多处用 `html` 作参数名，所以 html 模块必须带别名导入，否则被参数遮住。
_TITLE_SOURCES = (
    # <meta property="og:title" content="...">（属性顺序不固定，两个方向都试）
    (re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]*?content=["\'](.*?)["\']',
                re.I | re.S), 1),
    (re.compile(r'<meta[^>]+content=["\'](.*?)["\'][^>]*?property=["\']og:title["\']',
                re.I | re.S), 1),
    # var msg_title = '...'（微信正文页的 JS 变量，最接近真实标题；值里可能有转义引号）
    (re.compile(r"""msg_title\s*=\s*(['"])((?:\\.|(?!\1).)*)\1""", re.S), 2),
    # 最后兜底 <title>
    (re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S), 1),
)

#: mmbiz 图片地址骨架：CDN 前缀 + 图片 ID（第一个路径段即身份），尺寸段与查询串都属可变部分
_MMBIZ_RE = re.compile(
    r'^(https?://mmbiz\.qpic\.cn/(?:sz_)?mmbiz_[A-Za-z_]+/[^/?]+)(?:/\d+)?(?:\?.*)?$'
)


def is_wechat_url(url: str) -> bool:
    """判断是否为微信公众号文章链接。

    只认 **主机名正好等于** mp.weixin.qq.com 的地址。
    早先的实现是 `WECHAT_HOST in url`，属于子串匹配：
        https://evil.com/mp.weixin.qq.com/x   → 放行（会把请求真的发到 evil.com）
        https://mp.weixin.qq.com.evil.com/x   → 放行
        https://notmp.weixin.qq.com.cn/x      → 放行
    改成解析 URL 后比对 hostname，上述地址一律拒绝；端口、查询串、
    大小写（MP.WEIXIN.QQ.COM）都不影响判定。
    """
    raw = (url or '').strip()
    if not raw:
        return False
    if '://' not in raw:          # 没写协议的裸域名也认，补一个再解析
        raw = 'https://' + raw
    try:
        host = urlparse(raw).hostname or ''
    except ValueError:            # 畸形 URL（如非法 IPv6 字面量）
        return False
    return host.lower() == WECHAT_HOST


def resize_mmbiz_url(url: str, size: str) -> str:
    """改写 mmbiz 图片地址的尺寸段。

    微信正文图地址形如：
        https://mmbiz.qpic.cn/mmbiz_jpg/<图片ID>/640?wx_fmt=jpeg&wxfrom=5&wx_lazy=1
    路径里 `/640` 表示**等比缩放到宽 640 的压缩版**，`/0` 才是原图。
    实测差异（2026-09-17）：
        · 640 → 29.9 KB   vs   0 → 120.7 KB   （4 倍）
        · 640 → 101.6 KB  vs   0 → 128.8 KB   （+27%）
    图片本身宽度 ≤ 640 时两者体积相同，所以改 /0 只会更好、不会更差。

    同时剥掉 tp=webp / wx_lazy / wxfrom / wx_co 等噪声参数（`tp=webp` 会强制转成有损 WebP）。
    非 mmbiz 地址原样返回，避免误伤。
    """
    m = _MMBIZ_RE.match(url or '')
    if not m:
        return url
    return f'{m.group(1)}/{size}'


def to_original_url(url: str) -> str:
    """取原图地址（尺寸段 /0）"""
    return resize_mmbiz_url(url, '0')


def to_compressed_url(url: str, width: int = 640) -> str:
    """取压缩版地址（尺寸段 /640），作为原图不可用时的回退"""
    return resize_mmbiz_url(url, str(width))


def _normalize_urls(urls):
    """补全协议前缀，**保持传入顺序**。

    这里绝对不能排序。旧实现用 `sorted(urls)`，而 mmbiz 地址前缀高度雷同
    （`https://mmbiz.qpic.cn/sz_mmbiz_jpg/<ID>...`），字母序会把正文顺序彻底打乱：
    实测一篇 8 图文章，正文顺序被排成 8,3,4,5,6,1,7,2。

    后果很实际：导出名是 `img_01.jpg` 起的连号，顺序一乱，
    `img_01` 就不是正文第一张，下漫画/长图/教程步骤这类图集只能手动重排。
    顺序由调用方按地址在 HTML 中首次出现的位置给出（见 extract_image_urls）。
    """
    result = []
    for u in urls:
        if u.startswith('//'):
            u = 'https:' + u
        result.append(u)
    return result


def clean_url(url: str) -> str:
    """洗掉 HTML / JS 里的转义残留与尾部锚点。

    微信把图片地址塞进 JS 变量时会顺手转义，实测原文长这样：
        .../640?wx_fmt=gif\\x26amp;amp;from=appmsg
        .../640?wx_fmt=jpeg&tp=webp#imgIndex=4
    不清洗的话，query 里会带上 `\\x26amp;amp;` 这种垃圾，请求直接拿到错误响应。
    """
    if not url:
        return url
    u = url.replace('\\x26', '&').replace('\\x3d', '=').replace('\\/', '/')
    u = u.replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
    u = u.split('#', 1)[0]          # 去掉 #imgIndex=4 这类锚点
    return u.strip()


def _decode_title_text(raw: str) -> str:
    """把标题里的转义还原成正常文字。

    微信会先做 HTML 实体转义、再塞进 JS 字符串，于是出现 `\\x26amp;#39;` 这种
    多重转义；只解一层是不够的，所以先还原 `\\xNN` 与 JS 反斜杠转义，再解两轮实体。
    """
    if not raw:
        return ''
    s = raw
    for esc, ch in (('\\x26', '&'), ('\\x3d', '='), ('\\x3c', '<'), ('\\x3e', '>'),
                    ('\\/', '/'), ("\\'", "'"), ('\\"', '"')):
        s = s.replace(esc, ch)
    s = _html_unescape(_html_unescape(s))
    return re.sub(r'\s+', ' ', s).strip()


def extract_title(html) -> str:
    """从文章 HTML 里取标题，给下载文件夹命名用。

    依次尝试 og:title → `var msg_title` → `<title>`，取到第一个非空即返回；
    都取不到返回空串（由 naming.FALLBACK_TITLE 兜底成「微信图片」）。

    这里**不做文件名清洗** —— 非法字符、长度、保留名都是 naming.sanitize_title 的事，
    两处各洗一遍迟早对不上。
    """
    if not html:
        return ''
    for pat, group in _TITLE_SOURCES:
        for m in pat.finditer(html):
            text = _decode_title_text(m.group(group))
            if text:
                return text
    return ''


def _wechat_image_key(url):
    """从 WeChat mmbiz URL 提取图片身份标识，用于合并 CDN 变体

    同一张图会被微信从多个 CDN 节点、多个尺寸发出，URL 形如：
      https://mmbiz.qpic.cn/mmbiz_jpg/FILEID/0?wx_fmt=jpeg
      https://mmbiz.qpic.cn/sz_mmbiz_jpg/FILEID/640?wx_fmt=jpeg
      https://mmbiz.qpic.cn/mmbiz_png/FILEID/0?wx_fmt=jpeg
    FILEID（第一个路径段）才是唯一身份；尺寸段（/0、/640）和 CDN 前缀（mmbiz_jpg/sz_mmbiz_jpg）
    都要忽略，否则同一张图会被算成多张。
    """
    m = re.search(r'mmbiz\.qpic\.cn/(?:sz_)?mmbiz_[^/]+/([^/?]+)', url)
    if m:
        return m.group(1)
    return url


def _extract_wechat_picture_page_info(html):
    """部分微信文章（尤其"图片消息/贴图"）把正文图放在 JS 变量 picture_page_info_list 中。

    **实测结构（2026-09-17，从真实文章原文摊开确认）**：数组每个 item 含两条 cdn_url，
    而且是**两个不同的文件**（FILEID 不同，不是同一张图的不同档位）：

        picture_page_info_list: [
            {
                cdn_url: 'https://mmbiz.qpic.cn/sz_mmbiz_png/AAA.../640?wx_fmt=png\\x26amp;amp;from=appmsg',   ← 无水印版
                width: '640' * 1, height: '216' * 1,
                watermark_info: { cdn_url: 'http://mmbiz.qpic.cn/mmbiz_png/BBB.../0?wx_fmt=png' }            ← 水印版（另一次上传）
            }, ...
        ]

    取第一条 cdn_url 即无水印版（本项目的目标）；第二条带水印，且 FILEID 是另一张图，
    不能拿去替换。原实现只认单引号，这里放宽到单/双引号都吃，避免微信改写法后静默失效。

    返回**数组顺序**的地址列表——该顺序即正文顺序（见 _merge_order）。
    若页面无此结构（传统 data-src 文章）返回空列表，不影响既有逻辑。
    """
    key = 'picture_page_info_list'
    i = html.find(key + ':')
    if i < 0:
        return []
    j = html.find('[', i)
    if j < 0:
        return []
    # 括号配对，定位数组结束位置
    depth = 0
    end = -1
    for k in range(j, len(html)):
        c = html[k]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                end = k
                break
    if end < 0:
        return []
    block = html[j + 1:end]
    # 按顶层对象（大括号深度）切分 item，取每个 item 第一条 cdn_url
    items = []
    depth = 0
    start = None
    for idx, c in enumerate(block):
        if c == '{':
            if depth == 0:
                start = idx
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                items.append(block[start + 1:idx])
                start = None
    found = []
    for ib in items:
        # 只取第一条 cdn_url：item 内嵌套的 watermark_info 里的那条是带水印的另一张图
        m = re.search(r"""cdn_url["']?\s*:\s*["'](https?://mmbiz\.qpic\.cn/[^"']+)["']""", ib)
        if m and len(m.group(1)) > 30:
            found.append(m.group(1))
    return found


def _merge_order(ppi_urls, body_urls):
    """把「ppi 数组顺序」与「正文标签顺序」合并为最终输出顺序。

    两个来源都反映正文顺序，但覆盖面不同（2026-09-17 两篇真实文章实测）：

      · 纯 ppi 文章（图片消息/贴图）：正文里几乎没有 data-src，
        8 张图只有 ppi 数组能完整覆盖 —— 此时必须以 ppi 为主序
      · 混合文章：正文 data-src/img src 覆盖全部 6 张，而 ppi 只列了 5 张（漏 1 张）——
        此时正文顺序才对；若按 ppi 偏移排，那张被 ppi 漏掉的图会被挤到最后

    所以这里不能简单按「地址在 HTML 里的偏移」排（ppi 数组所在的 script 可能整体早于正文，
    实测偏移 486k vs 正文 571k）。规则改为：

      1. 正文已覆盖 ppi 的全部图片 → 直接用正文顺序（DOM 顺序就是阅读顺序，最可靠）
      2. 否则以 ppi 数组顺序为主序，把「只在正文出现」的图按它在正文中前后的相对位置插回原处
      3. 任一来源为空 → 用另一来源
    """
    if not ppi_urls:
        return list(body_urls)
    if not body_urls:
        return list(ppi_urls)

    body_keys = [_wechat_image_key(u) for u in body_urls]
    ppi_keys = {_wechat_image_key(u) for u in ppi_urls}

    if ppi_keys <= set(body_keys):
        # 正文覆盖完整，不必插值
        return list(body_urls)

    order = list(ppi_urls)
    idx_of = {_wechat_image_key(u): i for i, u in enumerate(order)}
    for i, key in enumerate(body_keys):
        if key in idx_of:
            continue
        # 锚点 = 正文中它后面第一张已在序列里的图，插到它前面
        anchor = None
        for later in body_keys[i + 1:]:
            j = idx_of.get(later)
            if j is not None:
                anchor = j
                break
        if anchor is None:
            order.append(body_urls[i])
        else:
            order.insert(anchor, body_urls[i])
        # 插入会移动后续下标，重建索引（图片数量级很小，代价可忽略）
        idx_of = {_wechat_image_key(u): k for k, u in enumerate(order)}
    return order


def _wechat_dedup(urls):
    """按图片身份合并同一张图的多个 CDN 变体，**保持传入的先后顺序**。

    同一张图微信会从多个 CDN 前缀、多个画质档位发出，必须按 FILEID 合并；
    合并时若遇到 jpeg 变体而当前保留的是 png，则换成 jpeg（体积更小），
    但顺序位次仍取最早那次出现——否则一张图的顺序会跟着变体选择一起漂移。
    """
    best = {}  # key -> [在输入中的位次, 地址]
    for i, u in enumerate(urls):
        key = _wechat_image_key(u)
        cur = best.get(key)
        if cur is None:
            best[key] = [i, u]
        elif 'wx_fmt=jpeg' in u.lower() and 'wx_fmt=jpeg' not in cur[1].lower():
            cur[1] = u
    return [u for _i, u in sorted(best.values(), key=lambda x: x[0])]


def extract_image_urls(html, include_scripts=False):
    """从微信公众号文章 HTML 中提取正文图片的**原图** URL。

    提取来源（按优先级自动叠加，无需配置）：
      1. picture_page_info_list JS 变量（新结构文章，data-src 为空时生效，取无水印原图）
      2. <img data-src>（正文图懒加载源）
      3. <img src>（旧文章/封面兜底）
      4. include_scripts=True 时全量补充扫描 <script> 内的 mmbiz 链接

    自动排除作者头像（round_head_img），按图片身份合并 CDN 变体，
    最后统一把尺寸段改写为 /0 —— 文章里的 data-src 给的多是 640px 压缩版，
    直接下载会得到只有几百 KB 的图（见 resize_mmbiz_url 的实测数据）。

    **输出顺序 = 图片在文章正文里的顺序**。这一点很重要——导出名是 `img_01.jpg` 起的连号，
    顺序错了（下漫画、长图、教程步骤这类图集）就只能手动重排。
    旧版最后一步是 `sorted(urls)`，而 mmbiz 地址前缀高度雷同，字母序会把正文顺序彻底打乱：
    实测一篇 8 图文章被排成 8,3,4,5,6,1,7,2，`img_01` 拿到的不是正文第一张。
    现在由 _merge_order 合并「ppi 数组顺序」与「正文标签顺序」得出（两来源的取舍理由见该函数）。
    """
    # ── ppi：数组顺序即正文顺序 ──
    ppi_urls = [clean_url(u) for u in _extract_wechat_picture_page_info(html)]

    # ── 正文标签：按在 HTML 中出现的位置排序 ──
    body = []  # [(偏移, 地址)]

    # data-src（微信正文图懒加载源）
    for m in re.finditer(r'data-src=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', html):
        u = m.group(1)
        if len(u) > 30:
            body.append((m.start(), u))

    # img src（某些旧文章或封面图）
    for m in re.finditer(r'<img[^>]+src=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', html, re.IGNORECASE):
        u = m.group(1)
        if len(u) > 30:
            body.append((m.start(), u))

    # 全文补充扫描（捕获 script/CSS/json 内的 mmbiz 图片）
    # 某些微信文章不使用 data-src，而是把图片藏在 JS 变量里
    # 仅当用户主动勾选「补充 Script 扫描」时才触发，避免引入 CDN 变体噪声
    if include_scripts:
        for m in re.finditer(r'https?://mmbiz\.qpic\.cn/[^\"\s<>\(\)\'\\]+', html):
            u = re.sub(r'["\'\);,\\]+$', '', m.group(0))
            if len(u) > 30:
                body.append((m.start(), u))

    # 按出现位置排序（sort 稳定，同位置的保持来源优先级）
    body.sort(key=lambda p: p[0])

    # 洗掉 JS/HTML 转义残留与 #imgIndex 锚点（否则 query 会带 \x26amp;amp; 这类垃圾）
    body_urls = [clean_url(u) for _pos, u in body]

    # 排除作者头像（round_head_img 字段，通常为 mmbiz_png）
    avatars = {clean_url(u) for u in re.findall(
        r"round_head_img[\"']?\s*:\s*[\"'](https?://mmbiz\.qpic\.cn/[^\"']+)[\"']", html)}
    if avatars:
        ppi_urls = [u for u in ppi_urls if u not in avatars]
        body_urls = [u for u in body_urls if u not in avatars]

    # 合并出正文顺序 → 按图片身份去重（mmbiz_jpg/sz_mmbiz_jpg 各档位算一张）
    ordered = _merge_order(ppi_urls, body_urls)
    ordered = _wechat_dedup(ordered)

    # 统一提升为原图地址（改尺寸段为 /0，剥掉 tp=webp 等噪声参数）
    return _normalize_urls([to_original_url(u) for u in ordered])
