"""
通用 URL 清洗工具 —— **不含任何站点知识**

这个模块只做两件与「是哪个网站」无关的事：
  · 洗掉 HTML / JS 里的转义残留与尾部锚点
  · 把 `//` 开头的协议相对地址补成 `https:`

站点特有的提取逻辑（图片识别、画质档位、正文顺序）住在 `web_image_dl/sites/` 里。
这里之所以独立成一个模块，是因为**多个站点都会用到这套清洗**，
放在某一个站点文件里会让其他站点反向依赖它。

历史：它曾经是微信公众号提取器的主体（`extractor.extract_image_urls` 等），
2026-09-23 划边界时把微信部分整体迁到 `sites/weixin.py`，只留下这两个通用函数。
"""
import re


def clean_url(url: str) -> str:
    """洗掉 HTML / JS 里的转义残留与尾部锚点。

    站点把图片地址塞进 JS 变量时会顺手转义，实测微信公众号原文长这样：
        .../640?wx_fmt=gif\\x26amp;amp;from=appmsg
        .../640?wx_fmt=jpeg&tp=webp#imgIndex=4
    不清洗的话，query 里会带上 `\\x26amp;amp;` 这种垃圾，请求直接拿到错误响应。

    这里刻意不校验域名 —— 它对任何 URL 都安全：
    没转义、没锚点时原样返回（只多一次 strip）。
    """
    if not url:
        return url
    u = url.replace('\\x26', '&').replace('\\x3d', '=').replace('\\/', '/')
    u = u.replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
    u = u.split('#', 1)[0]          # 去掉 #imgIndex=4 这类锚点
    return u.strip()


def normalize_urls(urls):
    """补全协议前缀，**保持传入顺序**。

    这里绝对不能排序。旧实现用 `sorted(urls)`，而图片 CDN 地址前缀高度雷同
    （`https://mmbiz.qpic.cn/sz_mmbiz_jpg/<ID>...`），字母序会把正文顺序彻底打乱：
    实测一篇 8 图文章的正文顺序被排成 8,3,4,5,6,1,7,2。

    后果很实际：导出名是 `img_01.jpg` 起的连号，顺序一乱，
    `img_01` 就不是正文第一张，下漫画/长图/教程步骤这类图集只能手动重排。
    顺序由调用方按地址在 HTML 中首次出现的位置给出（见 sites.weixin.extract_image_urls）。
    """
    result = []
    for u in urls:
        if u.startswith('//'):
            u = 'https:' + u
        result.append(u)
    return result
