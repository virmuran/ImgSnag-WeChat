"""
微信公众号文章图片 URL 提取器 — 专精 mp.weixin.qq.com
只保留微信策略：mmbiz.qpic.cn 图片识别、CDN 变体合并、水印版剔除
"""
import re


WECHAT_HOST = 'mp.weixin.qq.com'


def is_wechat_url(url: str) -> bool:
    return WECHAT_HOST in (url or '')


def _normalize_urls(urls):
    result = []
    for u in sorted(urls):
        if u.startswith('//'):
            u = 'https:' + u
        result.append(u)
    return result


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
    """部分微信文章把正文图放在 JS 变量 picture_page_info_list 中。
    每个 item 含两层 cdn_url：顶层为无 watermark 原图，watermark_info 内为水印版。
    只取每个 item 顶层的第一条 cdn_url（无 watermark 原图）。
    若页面无此结构（传统 data-src 文章）返回空集，不影响既有逻辑。"""
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
        m = re.search(r"cdn_url:\s*'(https?://mmbiz\.qpic\.cn/[^']+)'", ib)
        if m and len(m.group(1)) > 30:
            found.add(m.group(1))
    return found


def extract_image_urls(html, include_scripts=False):
    """从微信公众号文章 HTML 中提取正文图片 URL。

    提取来源（按优先级自动叠加，无需配置）：
      1. picture_page_info_list JS 变量（新结构文章，data-src 为空时生效，取无水印原图）
      2. <img data-src>（正文图懒加载源，高分辨率原图）
      3. <img src>（旧文章/封面兜底）
      4. include_scripts=True 时全量补充扫描 <script> 内的 mmbiz 链接

    自动排除作者头像（round_head_img），并按图片身份合并 CDN 变体。
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
    avatars = set(re.findall(
        r"round_head_img:\s*'(https?://mmbiz\.qpic\.cn/[^']+)'", html))
    if avatars:
        found -= avatars

    # 按图片身份去重：合并 mmbiz_jpg/sz_mmbiz_jpg/mmbiz_png 同一张图的多个变体
    found = _wechat_dedup(found)

    return _normalize_urls(found)
