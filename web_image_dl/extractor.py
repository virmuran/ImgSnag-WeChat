"""
微信公众号文章图片 URL 提取器 — 专精 mp.weixin.qq.com
只保留微信策略：mmbiz.qpic.cn 图片识别、CDN 变体合并、水印版剔除、原图画质提升
"""
import re


WECHAT_HOST = 'mp.weixin.qq.com'

#: mmbiz 图片地址骨架：CDN 前缀 + 图片 ID（第一个路径段即身份），尺寸段与查询串都属可变部分
_MMBIZ_RE = re.compile(
    r'^(https?://mmbiz\.qpic\.cn/(?:sz_)?mmbiz_[A-Za-z_]+/[^/?]+)(?:/\d+)?(?:\?.*)?$'
)


def is_wechat_url(url: str) -> bool:
    return WECHAT_HOST in (url or '')


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
    result = []
    for u in sorted(urls):
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


def _wechat_dedup(urls):
    """合并相同身份图片的多个 CDN URL，优先保留 jpeg 变体（更小）"""
    seen = {}  # key -> best url
    for u in urls:
        key = _wechat_image_key(u)
        if key not in seen:
            seen[key] = u
        else:
            # 已有则看是否要替换：优先 wx_fmt=jpeg
            if 'wx_fmt=jpeg' in u.lower() and 'wx_fmt=jpeg' not in seen[key].lower():
                seen[key] = u
    return set(seen.values())


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
    若页面无此结构（传统 data-src 文章）返回空集，不影响既有逻辑。
    """
    key = 'picture_page_info_list'
    i = html.find(key + ':')
    if i < 0:
        return set()
    j = html.find('[', i)
    if j < 0:
        return set()
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
        return set()
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
    found = set()
    for ib in items:
        # 只取第一条 cdn_url：item 内嵌套的 watermark_info 里的那条是带水印的另一张图
        m = re.search(r"""cdn_url["']?\s*:\s*["'](https?://mmbiz\.qpic\.cn/[^"']+)["']""", ib)
        if m and len(m.group(1)) > 30:
            found.add(m.group(1))
    return found


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
    """
    found = set()

    # 微信新结构：正文图嵌在 picture_page_info_list JS 变量里（data-src 为空的文章走这里）
    found |= _extract_wechat_picture_page_info(html)

    # 优先从 data-src 提取（微信正文图懒加载源，高分辨率原图）
    for m in re.finditer(r'data-src=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', html):
        u = m.group(1)
        if len(u) > 30:
            found.add(u)

    # img src（某些旧文章或封面图）
    for m in re.finditer(r'<img[^>]+src=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', html, re.IGNORECASE):
        u = m.group(1)
        if len(u) > 30:
            found.add(u)

    # 全文补充扫描（捕获 script/CSS/json 内的 mmbiz 图片）
    # 某些微信文章不使用 data-src，而是把图片藏在 JS 变量里
    # 仅当用户主动勾选「补充 Script 扫描」时才触发，避免引入 CDN 变体噪声
    if include_scripts:
        for m in re.finditer(r'https?://mmbiz\.qpic\.cn/[^\"\s<>\(\)\'\\]+', html):
            u = re.sub(r'["\'\);,\\]+$', '', m.group(0))
            if len(u) > 30:
                found.add(u)

    # 排除作者头像（round_head_img 字段，通常为 mmbiz_png）
    avatars = {clean_url(u) for u in re.findall(
        r"round_head_img[\"']?\s*:\s*[\"'](https?://mmbiz\.qpic\.cn/[^\"']+)[\"']", html)}

    # 洗掉 JS/HTML 转义残留与 #imgIndex 锚点（否则 query 会带 \x26amp;amp; 这类垃圾）
    found = {clean_url(u) for u in found}
    if avatars:
        found -= avatars

    # 按图片身份去重：合并 mmbiz_jpg/sz_mmbiz_jpg/mmbiz_png 同一张图的多个变体
    found = _wechat_dedup(found)

    # 统一提升为原图地址（改尺寸段为 /0，剥掉 tp=webp 等噪声参数）
    found = {to_original_url(u) for u in found}

    return _normalize_urls(found)
