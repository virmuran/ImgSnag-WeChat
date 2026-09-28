"""
站点适配器接口 —— 一个站点一个实现

**这个模块只有接口定义、默认实现与文档，不含任何站点特有的知识。**

设计意图：让「新增一个站点」变成 **只新增一个文件 + 注册表加一行**，
而不是回头改 `worker.py` / `app.py` / `naming.py` 里的散落判断。

接口共 7 个方法，一一对应改造前散落在各处的 7 个耦合点（见 SITE_ADAPTER_PLAN.md）：

    #  方法             改造前长在哪
    1  matches()        app.py 的 is_wechat_url
    2  extract()        worker.py → extractor.extract_image_urls
    3  extract_title()  worker.py → extractor.extract_title
    4  candidates()     worker.py._candidate_urls（画质档位知识）
    5  ext_for()        worker.py 里内联的 wx_fmt=png 判断
    6  display_url()    app.py._short_url 的固定前缀裁剪
    7  sniff()          新增：粘贴 HTML 时识别它属于哪个站点

**只有 matches / extract 必须由子类实现**，其余都有合理的默认实现 ——
一个结构简单的站点可以只写这两条，不必为了凑接口写空方法。
"""
import re


def guess_ext_from_url(url: str, default: str = '.jpg') -> str:
    """由 URL 推断图片扩展名（通用规则，站点无关）。

    判据是「地址里有没有出现格式字样」——路径段（`mmbiz_png/...`）与
    查询串（`?wx_fmt=png`）都算，所以子串匹配必须同时覆盖这两种写法。
    `png` 的判定必须放在最前：微信的 png 地址查询串里还可能挂着别的标记。

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
