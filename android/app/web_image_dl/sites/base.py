"""
站点适配器接口 —— 一个站点一个实现

**这个模块只有接口定义、默认实现与文档，不含任何站点特有的知识。**

设计意图：让「新增一个站点」变成 **只新增一个文件 + 注册表加一行**，
而不是回头改 `worker.py` / `app.py` / `naming.py` 里的散落判断。

接口共 9 个方法：前 7 个一一对应改造前散落在各处的 7 个耦合点
（见 SITE_ADAPTER_PLAN.md），后 2 个是接第二个站点时补上的：

    #  方法                 改造前长在哪
    1  matches()            app.py 的 is_wechat_url
    2  extract()            worker.py → extractor.extract_image_urls
    3  extract_title()      worker.py → extractor.extract_title
    4  candidates()         worker.py._candidate_urls（画质档位知识）
    5  ext_for()            worker.py 里内联的 wx_fmt=png 判断
    6  display_url()        app.py._short_url 的固定前缀裁剪
    7  sniff()              新增：粘贴 HTML 时识别它属于哪个站点
    8  extract_author()     新增：来源署名，给界面上的版权提示用
    9  download_referer()   新增：下载图片时带不带 Referer（每个图床脾气不同）

**只有 matches / extract 必须由子类实现**，其余都有合理的默认实现 ——
一个结构简单的站点可以只写这两条，不必为了凑接口写空方法。
"""
import re


def guess_ext_from_url(url: str, default: str = '.jpg') -> str:
    """由 URL 推断图片扩展名（**通用规则，不认识任何特定站点**）。

    判据是「地址里有没有点号写法或查询串声明」：`.png` / `.gif` / `.webp` 这类
    扩展名，以及 `?fmt=png` 这类显式声明。`png` 的判定放在最前 —— 有些地址在
    查询串里还挂着别的标记，先判 png 才不会被抢先。

    ⚠ **它认不出「路径里用下划线写格式」的写法**（微信的 `mmbiz_png` 就是）。
    这类站点知识必须由该站点适配器在自己的 `ext_for` 里补，不要往这里加站点判断，
    否则"通用"就名不副实，下一个站点又得回来改它。
    （微信上实测踩过：下载用的 /0 档地址会被剥掉查询串，只剩 `mmbiz_png`，
      通用规则判不出来 → 整类图被静默存成 .jpg，而带查询串的地址恰好能判对，
      所以只测后者就一直没暴露。）

    推断不出来时返回 default（调用方给 `.jpg` 是合理默认——绝大多数图是 jpeg，
    而且拿不准扩展名只是文件名不好看，不影响图片本身能否打开）。
    """
    u = (url or '').lower()
    if '.png' in u or 'fmt=png' in u:
        return '.png'
    if '.webp' in u:
        return '.webp'
    if '.gif' in u:
        return '.gif'
    return default


# ============================================================================
#  来源署名与版权提示
# ============================================================================

#: 版权提醒正文。**只有这一份** —— 界面、落盘、将来任何要显示它的地方都从这里取，
#: 免得同一句话在两处各写一遍、改一处漏一处（本项目已经吃过好几次这种亏）。
#:
#: 口径（2026-10-10 与沐然定）：ImgSnag 只是个提取工具，用户点开链接的那一刻
#: 就知道图是从哪来的，所以软件**不去追溯版权归属**，只把来源如实标出来、
#: 再补一句提醒。取不到具体署名不是错误，如实说「来源：<站点名>」即可。
CREDIT_NOTICE = (
    '本工具只提取图片，版权归原作者所有。请勿用于商业用途，转发请注明出处。'
)

#: 落到批次目录里的来源说明文件名。桌面版与手机端都用这一个名字 ——
#: 两处各写一遍字面量，改一处漏一处（本项目已经吃过好几次这种亏）。
CREDIT_FILE = '来源说明.txt'


def credit_line(author: str, display_name: str) -> str:
    """拼一行来源说明。

    有署名 → ``图片来源：<作者>（<站点>）``
    没署名 → ``来源：<站点>``            —— 认不出作者不是错误，如实说即可
    """
    name = (author or '').strip()
    site = (display_name or '').strip()
    if name and site:
        return f'图片来源：{name}（{site}）'
    if name:
        return f'图片来源：{name}'
    if site:
        return f'来源：{site}'
    return '来源：未知'


class SiteAdapter:
    """一个站点的图片提取策略。

    ── 必须实现 ──
      matches(url)                     这个 URL 归本站点管吗
      extract(html, include_scripts)   从 HTML 里按正文顺序提取图片 URL

    ── 有默认实现，按需覆盖 ──
      extract_title(html)   取文章标题（默认取不到，交给 fallback_title 兜底）
      candidates(url)       下载候选地址，首选项在前（默认只有一个，即原地址）
      ext_for(url)          由 URL 推断扩展名（默认用通用规则）
      display_url(url)      界面上显示的短地址（默认原样）
      sniff(html)           这段 HTML 像不像本站点，返回命中特征数（默认 0 = 不认识）
      extract_author(html)  来源署名（默认取不到，界面退化成只写站点名）
      download_referer(url) 下载图片时用哪个 Referer（默认沿用页面地址；空串＝不带）

    ── 类属性 ──
      name            内部标识，小写无空格，用于日志（如 "weixin"）
      display_name    界面上显示的站点名（如 "微信公众号"）
      fallback_title  抓不到标题时的兜底文件夹名（如 "微信图片"）
    """

    #: 内部标识（日志/调试用）
    name: str = ""
    #: 界面文案
    display_name: str = ""
    #: 抓不到文章标题时的兜底文件夹名
    fallback_title: str = "图片"

    # ────────────────────────── 必须实现 ──────────────────────────

    def matches(self, url: str) -> bool:
        """② 这个 URL 是否归本站点管。

        **必须解析 URL 后比对主机名，不能拿域名做子串匹配** ——
        `https://evil.com/mp.weixin.qq.com/x` 这种地址子串匹配会放行，
        程序就真的把请求发到 evil.com 去了（v1.1.2 踩过）。
        """
        raise NotImplementedError

    def extract(self, html: str, include_scripts: bool = False) -> list:
        """① 从 HTML 提取图片 URL，**按图片在正文中的顺序**返回。

        顺序是硬要求：导出的文件名是 `img_01` 起的连号，顺序乱了
        （下漫画、长图、教程步骤这类图集）就只能手动重排。

        `include_scripts=True` 时额外扫描 `<script>` 内的图片地址 ——
        有些站点把图放在 JS 变量里，普通标签扫不到。
        """
        raise NotImplementedError

    # ────────────────────────── 默认实现 ──────────────────────────

    def extract_title(self, html: str) -> str:
        """③ 取文章标题，给下载文件夹命名。

        **不做文件名清洗** —— 非法字符、长度、保留名都是 naming.sanitize_title 的事，
        两处各洗一遍迟早对不上。取不到返回空串，由 fallback_title 兜底。
        """
        return ''

    def candidates(self, url: str) -> list:
        """④ 下载候选地址，**首选项在前**。

        站点存在「同一张图多个画质档位」时才需要覆盖（如微信的 `/0` 原图档
        与 `/640` 压缩档）。返回单元素列表即表示没有档位可选。

        用户偏好（原图优先 or 压缩优先）不在这里处理 —— 那是界面开关的事，
        由 worker 负责按偏好重排，适配器只回答「有哪些档位、哪个更好」。
        """
        return [url] if url else []

    def ext_for(self, url: str) -> str:
        """⑤ 由 URL 推断扩展名（`.jpg` / `.png` / `.webp` / `.gif`）"""
        return guess_ext_from_url(url)

    def display_url(self, url: str) -> str:
        """⑥ 界面上显示的短地址。默认原样返回。

        覆盖它的场景：站点链接有固定且冗长的前缀（如微信公众号的
        `https://mp.weixin.qq.com/s/`），列表里白占大半列宽。
        """
        return url or ''

    def sniff(self, html: str) -> int:
        """⑦ 这段 HTML 像不像本站点，返回**特征命中数**（0 = 不像）。

        用途：用户直接粘贴 HTML 源码时，没有 URL 可以用来 matches()，
        只能靠内容特征反推站点。命中数而非布尔值是为了将来多站点时
        能比大小取最像的那个。

        实现要点：挑**改版也不容易动**的特征（图片 CDN 域名、特有的 JS 变量名），
        不要去匹配会随模板调整的 HTML 结构。
        """
        return 0

    def extract_author(self, html: str) -> str:
        """⑧ 取来源署名 —— 公众号名 / 作者名 / coser 名，取不到返回空串。

        用途：界面上的版权提示。ImgSnag 只是个提取工具，用户点开链接的那一刻
        就知道图是从哪来的，所以这里**不负责追溯版权归属**，只把来源如实标出来。

        **取不到就返回空串，不要编一个** —— 调用方会退化成
        `credit_line('', display_name)`，也就是「来源：<站点名>」+ 通用提醒。
        编出来的假署名比没有署名更糟。
        """
        return ''

    def download_referer(self, page_url: str) -> str:
        """⑨ 下载图片时带哪个 Referer。**返回空串表示不带这个头。**

        默认沿用页面地址 —— 有些图床会校验来源页，带上更保险。

        但**也确实有反着来的图床**：阿里云 OSS 的防盗链可以配成「带了 Referer
        就拒」，这时带上反而每次都 403，整批图全下不来。这类站点的适配器在
        这里返回空串即可，下载环节会照做（见 worker._download_one 的调用处）。

        ⚠ 这是**下载图片**时用的头，与抓页面那次请求无关。
        """
        return page_url or ''
